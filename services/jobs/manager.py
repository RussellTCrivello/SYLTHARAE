"""Unified job manager (spec sections 5, 11, 12, 14, 34).

* Persistent jobs in the `jobs` table (migration 0006, additive).
* Worker threads with explicit concurrency limits (configurable).
* Real progress from worker state - never fake percentages.
* Cooperative cancellation and pause at safe file boundaries.
* Crash recovery: RUNNING jobs not updated within a stale window are
  marked FAILED (identifiable) on startup; resumable jobs keep checkpoints
  so already-stored files are not duplicated (checkpoint + dedup).
* Structured events for the live UI (SSE + polling).
"""
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from services.jobs import job_state
from services.jobs.repository import JobRepository

logger = logging.getLogger(__name__)


class JobsConfig:
    """Job-system configuration (defaults < settings file < env, read-time)."""

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default

    @classmethod
    def max_concurrent_jobs(cls) -> int:
        return max(1, cls._env_int("JOBS_MAX_CONCURRENT", 2))

    @classmethod
    def stale_seconds(cls) -> int:
        return max(60, cls._env_int("JOBS_STALE_SECONDS", 6 * 3600))

    @classmethod
    def progress_min_interval(cls) -> float:
        return max(0.2, cls._env_float("JOBS_PROGRESS_INTERVAL", 1.0))

    @classmethod
    def event_sample_threshold(cls) -> int:
        """Per-job FILE_* event cap; above this, per-file events are sampled."""
        return cls._env_int("JOBS_EVENT_SAMPLE_THRESHOLD", 500)

    @classmethod
    def _env_float(cls, name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except (TypeError, ValueError):
            return default


class EventThrottle:
    """Aggregates per-file events for large jobs; keeps errors/duplicates."""

    def __init__(self, sample_threshold: int):
        self.sample_threshold = max(1, int(sample_threshold))
        self.counts: Dict[str, int] = {}
        self.samples: Dict[str, int] = {}
        self.pending: List[Dict[str, Any]] = []

    def add(self, event_type: str, payload: Dict[str, Any]) -> None:
        n = self.counts.get(event_type, 0) + 1
        self.counts[event_type] = n
        always = event_type in ("ERROR", "WARNING", "JOB_CREATED", "JOB_STARTED",
                                "JOB_COMPLETED", "JOB_CANCELLED", "PHASE_STARTED",
                                "PHASE_COMPLETED", "DUPLICATE_DETECTED")
        if always or n <= self.sample_threshold:
            self.pending.append({"event_type": event_type, "payload": payload})
        elif n == self.sample_threshold + 1:
            self.pending.append({
                "event_type": "WARNING",
                "payload": {
                    "message": (
                        f"Per-file events for {event_type} exceeded "
                        f"{self.sample_threshold}; further events are sampled"
                    ),
                },
            })

    def flush(self, repo: JobRepository, job_id: str) -> None:
        if self.pending:
            try:
                repo.add_events(job_id, self.pending)
            except Exception:
                logger.exception("Failed to persist job events")
            self.pending = []


class JobManager:
    """Creates, executes and controls long-running operations."""

    _instance_lock = threading.Lock()
    _instance: Optional["JobManager"] = None

    @classmethod
    def get_instance(cls) -> "JobManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = JobManager()
            return cls._instance

    def __init__(self, repository: Optional[JobRepository] = None,
                 synchronous: bool = False):
        self.repo = repository or JobRepository()
        self.synchronous = synchronous  # tests/CLI: execute inline
        self._active: Dict[str, Dict[str, Any]] = {}
        self._active_lock = threading.Lock()
        self._subscribers: List[Any] = []  # queue.Queue per SSE client
        self._sub_lock = threading.Lock()
        self._lock_held = threading.Lock()

    # ------------------------------------------------------------------
    # pub/sub for live events (SSE; polling fallback reads the DB)
    # ------------------------------------------------------------------
    def subscribe(self, q) -> None:
        with self._sub_lock:
            self._subscribers.append(q)

    def unsubscribe(self, q) -> None:
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, job_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._sub_lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait({"job_id": job_id, "event_type": event_type,
                              "payload": payload, "ts": time.time()})
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    def create_job(self, job_type: str, source: str, options: Dict[str, Any],
                   created_by: Optional[str] = None,
                   starter: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
        """Persist a job and (unless synchronous) start a worker thread.

        ``starter(job_record)`` executes the operation; the built-in starters
        cover the known job types (see ``_run_job``).
        """
        job_id = uuid.uuid4().hex[:12].upper()
        record = self.repo.create({
            "job_id": job_id,
            "job_type": job_type,
            "status": job_state.QUEUED,
            "progress": 0,
            "source": (source or "")[:512],
            "options": options or {},
            "created_by": created_by,
        })
        self.repo.add_event(job_id, "JOB_CREATED", {"job_type": job_type})
        self._publish(job_id, "JOB_CREATED", {"status": record["status"]})

        if self.synchronous:
            self._execute(record, starter)
        else:
            t = threading.Thread(
                target=self._worker, args=(record, starter),
                name=f"job-{job_id}", daemon=True,
            )
            with self._active_lock:
                self._active[job_id] = {"thread": t, "cancel": threading.Event()}
            t.start()
        return self.repo.get(job_id)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def _worker(self, record: Dict[str, Any], starter) -> None:
        job_id = record["job_id"]
        try:
            self._wait_for_slot(job_id)
            if self.repo.get(job_id) is None:
                return
            current = self.repo.get(job_id)
            if current["status"] == job_state.CANCELLED:
                return
            self._execute(current, starter)
        except Exception:
            logger.exception("Job worker crashed: %s", job_id)
            try:
                self._finish(job_id, job_state.FAILED,
                             errors=["Job worker failed (see server logs)"])
            except Exception:
                pass
        finally:
            with self._active_lock:
                self._active.pop(job_id, None)

    def _wait_for_slot(self, job_id: str) -> None:
        max_jobs = JobsConfig.max_concurrent_jobs()
        while True:
            active = self.repo.count_by_status().get(job_state.RUNNING, 0)
            running_ids = {
                jid for jid, info in list(self._active.items())
                if info["thread"].is_alive()
            }
            mine_is_running = job_id in running_ids
            if mine_is_running and len(running_ids) > max_jobs:
                # Another job grabbed the last slot; yield until a slot frees.
                time.sleep(0.5)
                continue
            if active >= max_jobs and not mine_is_running:
                time.sleep(0.5)
                if self.repo.get(job_id) is None:
                    return
                if self.repo.get(job_id)["status"] == job_state.CANCELLED:
                    return
                continue
            return

    def _execute(self, record: Dict[str, Any], starter) -> None:
        job_id = record["job_id"]
        now = datetime.now(timezone.utc).isoformat()
        self.repo.update_fields(job_id, {
            "status": job_state.RUNNING, "started_at": now,
            "current_phase": "Starting",
        })
        self.repo.add_event(job_id, "JOB_STARTED", {})
        self._publish(job_id, "JOB_STARTED", {})

        throttle = EventThrottle(JobsConfig.event_sample_threshold())
        last_flush = {"t": time.time()}

        # PROGRESS: emission policy.
        #
        # The engine now calls back once per *file* (including nested children),
        # from several worker threads at once. Writing every one of those to the
        # database would swamp it, but the previous code had no throttle at all -
        # ``JobsConfig.progress_min_interval()`` was defined, documented and
        # never used. The rule below is bounded *and* real-time:
        #
        #   * any change in the displayed integer percent  -> emit (at most ~100
        #     emissions per job, and every one of them is a visible movement of
        #     the bar),
        #   * any phase change                             -> emit,
        #   * otherwise                                    -> emit at most once
        #     per JOBS_PROGRESS_INTERVAL as a heartbeat.
        #
        # The most recent snapshot is always kept so the final flush publishes
        # the true end state rather than a stale intermediate one.
        min_interval = JobsConfig.progress_min_interval()
        emit_state = {"t": 0.0, "percent": -1, "phase": None, "last": None}

        def _persist(fields: Dict[str, Any]) -> None:
            try:
                self.repo.update_fields(job_id, fields)
                self._publish(job_id, "PROGRESS", fields)
            except Exception:
                logger.debug("progress persist failed", exc_info=True)

        def _fields_for(snapshot: Dict[str, Any]) -> Dict[str, Any]:
            percent = int(snapshot.get("percent") or 0)
            return {
                "progress": min(100, max(0, percent)),
                "current_phase": snapshot.get("current_phase") or "Processing",
                "current_item": (snapshot.get("current_file") or "")[:512],
                "stats": snapshot,
            }

        def progress_cb(snapshot: Dict[str, Any]) -> None:
            percent = int(snapshot.get("percent") or 0)
            phase = snapshot.get("current_phase")
            now = time.time()
            emit_state["last"] = snapshot

            moved = percent != emit_state["percent"]
            phase_changed = phase != emit_state["phase"]
            heartbeat = (now - emit_state["t"]) >= min_interval
            if not (moved or phase_changed or heartbeat):
                return

            emit_state["t"] = now
            emit_state["percent"] = percent
            emit_state["phase"] = phase
            _persist(_fields_for(snapshot))

        def flush_progress(final_percent: Optional[int] = None,
                           final_stats: Optional[Dict[str, Any]] = None) -> None:
            """Publish the last known state (used at every job boundary).

            ``final_stats`` carries the engine's closing accounting (storage
            counters such as ``files_stored``/``files_duplicates`` and the
            byte totals).  The periodic progress callbacks only publish the
            reader's live snapshot, so without merging these here the job -
            and therefore the Jobs page and ``/api/jobs/<id>`` - reported
            ``duplicates: null`` even though the engine had detected the
            duplicates.  Live keys win where both exist (they describe what
            actually happened), and the final numbers fill in the rest.
            """
            snapshot = emit_state["last"]
            if snapshot is None and not final_stats:
                return
            merged: Dict[str, Any] = dict(snapshot or {})
            for key, value in (final_stats or {}).items():
                if value is None:
                    continue
                merged.setdefault(key, value)
            fields = _fields_for(merged)
            if final_percent is not None:
                fields["progress"] = min(100, max(0, int(final_percent)))
            _persist(fields)

        try:
            if starter is not None:
                result = starter(self._job_context(record, progress_cb, throttle))
            else:
                result = self._run_builtin(record, progress_cb, throttle)
            throttle.flush(self.repo, job_id)

            finished = datetime.now(timezone.utc).isoformat()
            if result is None:
                self._finish(job_id, job_state.FAILED,
                             errors=["Job produced no result"])
                return
            cancelled = bool(getattr(result, "cancelled", False))
            paused = bool(getattr(result, "paused", False))
            errors = list(getattr(result, "errors", []) or [])
            warnings = list(getattr(result, "warnings", []) or [])

            if paused:
                self.repo.update_fields(job_id, {
                    "status": job_state.PAUSED, "completed_at": finished,
                    "stats": getattr(result, "stats", {}),
                    "warnings": warnings,
                })
                # Keep the real percent: a paused job resumes from where it
                # stopped, and showing 0% would misrepresent the stored work.
                flush_progress(final_stats=getattr(result, "stats", {}) or {})
                self._publish(job_id, "JOB_PAUSED", {})
                return
            if cancelled:
                status = job_state.CANCELLED
                self.repo.add_event(job_id, "JOB_CANCELLED", {})
                self._publish(job_id, "JOB_CANCELLED", {})
            else:
                status = job_state.final_state_for(bool(errors), bool(warnings))
                self.repo.add_event(job_id, "JOB_COMPLETED", {"status": status})
                self._publish(job_id, "JOB_COMPLETED", {"status": status})
            # PROGRESS: a job that finished normally has settled every
            # discovered unit, so 100% is correct. A cancelled or failed job
            # previously had its progress *reset to 0*, throwing away the real
            # work already done - the final percent is now taken from the
            # engine's own accounting.
            final_stats = getattr(result, "stats", {}) or {}
            if status in (job_state.COMPLETED, job_state.COMPLETED_WITH_WARNINGS):
                final_percent = 100
            else:
                final_percent = None
                for key in ("percent", "files_percent"):
                    if final_stats.get(key) is not None:
                        try:
                            final_percent = int(final_stats[key])
                            break
                        except (TypeError, ValueError):
                            continue
                if final_percent is None:
                    done = final_stats.get("files_processed") or final_stats.get("files_done")
                    total = final_stats.get("files_total") or final_stats.get("files_discovered")
                    if done is not None and total:
                        final_percent = int((int(done) / int(total)) * 100)

            self.repo.update_fields(job_id, {
                "status": status, "completed_at": finished,
                "stats": final_stats,
                "errors": errors, "warnings": warnings,
                "result_summary": getattr(result, "to_dict", dict)().get("result_summary")
                if hasattr(result, "to_dict") else None,
            })
            flush_progress(
                final_percent if final_percent is not None else 100,
                final_stats=final_stats,
            )
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            from core.errors import client_safe_message

            flush_progress()
            self._finish(job_id, job_state.FAILED, errors=[
                client_safe_message(exc, subsystem="services.jobs")
            ])

    def _job_context(self, record, progress_cb, throttle) -> Dict[str, Any]:
        """Context handed to custom starters (import services)."""
        return {
            "job": record,
            "progress": progress_cb,
            "emit": lambda et, payload=None: (
                throttle.add(et, payload or {}),
                self._publish(record["job_id"], et, payload or {}),
            ),
        }

    def _finish(self, job_id: str, status: str, errors: List[str]) -> None:
        finished = datetime.now(timezone.utc).isoformat()
        self.repo.update_fields(job_id, {
            "status": status, "completed_at": finished, "errors": errors,
        })
        self._publish(job_id, status, {})

    # ------------------------------------------------------------------
    # Built-in starters for known job types
    # ------------------------------------------------------------------
    def _run_builtin(self, record, progress_cb, throttle):
        from services.ingesting.options import IngestionOptions

        options = record.get("options") or {}
        job_type = record["job_type"]
        if job_type == "ingestion":
            from services.ingesting.service import IngestionRequest, IngestionService

            job_id = record["job_id"]

            request = IngestionRequest(
                path=options.get("path"),
                file_paths=options.get("file_paths") or [],
                source=options.get("source", ""),
                side=options.get("side", ""),
                recursive=bool(options.get("recursive", True)),
                options=IngestionOptions(**(options.get("processing") or {})),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )

            def control_flag(column):
                def _check():
                    try:
                        current = self.repo.get(job_id)
                        return bool(current and current.get(column))
                    except Exception:
                        return False
                return _check

            mgr = self

            def engine_cb(reader):
                # Direct in-process signal path for instant cancel/pause.
                mgr.register_engine(job_id, reader)

            def wrapped_progress(snapshot):
                snapshot = dict(snapshot)
                if not snapshot.get("current_phase"):
                    snapshot["current_phase"] = "Processing files"
                throttle.add("FILE_COMPLETED", {
                    "files_done": snapshot.get("files_done"),
                    "percent": snapshot.get("percent"),
                })
                progress_cb(snapshot)

            try:
                return IngestionService().run(
                    request, progress_cb=wrapped_progress,
                    cancel_cb=control_flag("cancellation_requested"),
                    pause_cb=control_flag("pause_requested"),
                    engine_cb=engine_cb,
                )
            finally:
                mgr.unregister_engine(job_id)
        if job_type == "domain_import":
            from services.importing.domain_import_service import (
                DomainImportRequest, DomainImportService,
            )

            request = DomainImportRequest(
                data_file=options.get("data_file"),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )
            return DomainImportService().run(request, progress_cb=progress_cb)
        if job_type == "backup_import":
            from services.importing.backup_import_service import (
                BackupImportRequest, BackupImportService,
            )

            request = BackupImportRequest(
                backup_path=options.get("backup_path"),
                backup_name=options.get("backup_name"),
                dry_run=bool(options.get("dry_run", False)),
                created_by=record.get("created_by"),
            )
            return BackupImportService().run(request, progress_cb=progress_cb)
        if job_type == "batch_import":
            return self._run_batch_import(record, progress_cb)
        raise ValueError(f"Unknown job type: {job_type}")

    def _run_batch_import(self, record, progress_cb):
        from services.ingesting.service import IngestionRequest, IngestionService
        from services.ingesting.options import IngestionOptions
        from services.importing.backup_import_service import BatchImportService

        options = record.get("options") or {}
        validated = BatchImportService().validate(
            options.get("file_paths") or [],
            # JOB-02: API clients may send numeric source/side (JSON ints);
            # downstream validation calls .strip(), so normalize to str here.
            str(options.get("source", "") or ""), str(options.get("side", "") or ""),
        )
        request = IngestionRequest(
            file_paths=validated["validated_paths"],
            source=validated["source"], side=validated["side"],
            options=IngestionOptions(**(options.get("processing") or {})),
        )

        def wrapped(snapshot):
            snapshot = dict(snapshot)
            # Only default the phase when the engine has not named one, so the
            # real "Expanding archive: ..." activity stays visible.
            if not snapshot.get("current_phase"):
                snapshot["current_phase"] = "Batch import"
            progress_cb(snapshot)

        return IngestionService().run(request, progress_cb=wrapped)

    # ------------------------------------------------------------------
    # Control operations (spec sections 11/12/21)
    # ------------------------------------------------------------------
    def cancel(self, job_id: str) -> Dict[str, Any]:
        job = self._require(job_id)
        if job_state.is_terminal(job["status"]):
            return job
        if job["status"] == job_state.QUEUED:
            self.repo.update_fields(job_id, {
                "status": job_state.CANCELLED,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "cancellation_requested": True,
            })
            self.repo.add_event(job_id, "JOB_CANCELLED", {"queued": True})
            self._publish(job_id, "JOB_CANCELLED", {"queued": True})
            return self.repo.get(job_id)
        self.repo.update_fields(job_id, {
            "status": job_state.CANCELLING, "cancellation_requested": True,
        })
        self.repo.add_event(job_id, "WARNING", {"message": "Cancellation requested"})
        with self._active_lock:
            info = self._active.get(job_id)
        if info:
            info["cancel"].set()
        # Ask the in-process engine (if this process owns the job) to stop.
        self._signal_engine(job_id, "cancel")
        # Cancellation lands within one file boundary; finalize if the worker
        # already exited (single-process deployments).
        deadline = time.time() + 30
        while time.time() < deadline:
            job = self.repo.get(job_id)
            if job and job_state.is_terminal(job["status"]):
                return job
            time.sleep(0.2)
        return self.repo.get(job_id)

    def pause(self, job_id: str) -> Dict[str, Any]:
        job = self._require(job_id)
        if job["status"] != job_state.RUNNING:
            raise ValueError("Only RUNNING jobs can be paused")
        self.repo.update_fields(job_id, {"pause_requested": True})
        self._signal_engine(job_id, "pause")
        deadline = time.time() + 30
        while time.time() < deadline:
            job = self.repo.get(job_id)
            if job and job["status"] in (job_state.PAUSED, job_state.CANCELLED,
                                         job_state.FAILED):
                return job
            time.sleep(0.2)
        return self.repo.get(job_id)

    def resume(self, job_id: str, created_by: Optional[str] = None) -> Dict[str, Any]:
        job = self._require(job_id)
        if job["status"] != job_state.PAUSED:
            raise ValueError("Only PAUSED jobs can be resumed")
        options = dict(job.get("options") or {})
        # A fresh execution with the same checkpoint policy continues where
        # the pause stopped (checkpoint skips processed files; dedup guards).
        return self.create_job(
            job["job_type"], job.get("source") or "", options,
            created_by=created_by or job.get("created_by"),
        )

    def retry(self, job_id: str, created_by: Optional[str] = None) -> Dict[str, Any]:
        """Retry a failed/cancelled job as a new job with identical options.

        Safe by design: deduplication prevents duplicate database records,
        and checkpointed ingestions skip already-processed files.
        """
        job = self._require(job_id)
        if job["status"] not in (job_state.FAILED, job_state.CANCELLED,
                                 job_state.COMPLETED_WITH_WARNINGS):
            raise ValueError("Only FAILED, CANCELLED or COMPLETED_WITH_WARNINGS jobs can be retried")
        return self.create_job(
            job["job_type"], job.get("source") or "",
            dict(job.get("options") or {}),
            created_by=created_by or job.get("created_by"),
        )

    def _signal_engine(self, job_id: str, kind: str) -> None:
        """Best-effort direct signal to an engine running in this process."""
        engine = getattr(self, "_engines", {}).get(job_id)
        if engine is None:
            return
        try:
            if kind == "cancel":
                engine.request_cancel()
            else:
                engine.request_pause()
        except Exception:
            logger.debug("engine signal failed", exc_info=True)

    def register_engine(self, job_id: str, engine) -> None:
        if not hasattr(self, "_engines"):
            self._engines = {}
        self._engines[job_id] = engine

    def unregister_engine(self, job_id: str) -> None:
        if hasattr(self, "_engines"):
            self._engines.pop(job_id, None)

    # ------------------------------------------------------------------
    # Crash recovery (spec section 34)
    # ------------------------------------------------------------------
    def recover_stale_jobs(self, stale_seconds: Optional[int] = None) -> Dict[str, Any]:
        """Mark interrupted jobs FAILED (identifiable); nothing is deleted.

        Already-stored files stay stored (each file commits its own
        transaction); checkpointed ingestions resume without duplicating
        work; deduplication protects everything else.
        """
        stale = self.repo.stale_running(stale_seconds or JobsConfig.stale_seconds())
        recovered = []
        for job in stale:
            message = (
                "Application restarted while the job was running; "
                "the job was interrupted. Retry to continue (processed "
                "files are not duplicated)."
            )
            self._finish(job["job_id"], job_state.FAILED, errors=[message])
            # Persist a terminal event so the audit trail reflects the
            # recovery (the happy path persists JOB_COMPLETED the same way).
            try:
                self.repo.add_event(job["job_id"], "FAILED", {
                    "reason": "crash_recovery", "errors": [message],
                })
            except Exception:
                logger.exception("Failed to persist crash-recovery event for %s",
                                 job["job_id"])
            recovered.append(job["job_id"])
        if recovered:
            logger.warning("Recovered %d stale jobs: %s", len(recovered), recovered)
        return {"recovered": recovered, "count": len(recovered)}

    # ------------------------------------------------------------------
    def _require(self, job_id: str) -> Dict[str, Any]:
        job = self.repo.get(job_id)
        if job is None:
            raise KeyError(f"Job not found: {job_id}")
        return job

    def get(self, job_id: str):
        return self.repo.get(job_id)

    def list(self, **kwargs):
        return self.repo.list_jobs(**kwargs)

    def events(self, job_id: str, limit: int = 500, after_id: int = 0):
        self._require(job_id)
        return self.repo.get_events(job_id, limit=limit, after_id=after_id)

    def delete(self, job_id: str) -> bool:
        job = self._require(job_id)
        if not job_state.is_terminal(job["status"]):
            raise ValueError("Only completed/failed/cancelled jobs can be deleted")
        with self._active_lock:
            self._active.pop(job_id, None)
        return self.repo.delete(job_id)
