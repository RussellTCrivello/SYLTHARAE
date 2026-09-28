"""
Notification Service
System-wide notification management for alerts, similar files, and future events
"""

import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import json
import threading


logger = logging.getLogger(__name__)

# Signal types that can carry a resolved date (a relative expression only
# when resolved against the document's own date). From the detector, so the
# notification layer never hard-codes its vocabulary.
from core.detection.temporal_intel import SIGNAL_DATE, SIGNAL_RELATIVE  # noqa: E402

_DATED_SIGNAL_TYPES = (SIGNAL_DATE, SIGNAL_RELATIVE)


class NotificationType(Enum):
    """Types of notifications"""
    SIMILAR_FILES = "similar_files"
    FUTURE_DATE = "future_date"
    FUTURE_EVENT = "future_event"
    PROCESSING_COMPLETE = "processing_complete"
    BATCH_COMPLETE = "batch_complete"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class NotificationPriority(Enum):
    """Notification priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Notification:
    """Represents a system notification"""
    id: Optional[int]
    type: NotificationType
    priority: NotificationPriority
    title: str
    message: str
    file_id: Optional[int]
    file_name: Optional[str]
    file_path: Optional[str]
    event_date: Optional[date]
    metadata: Dict[str, Any]
    created_at: datetime
    read: bool = False
    dismissed: bool = False


#: Column order shared by every ``SELECT ... FROM alerts`` in this service.
ALERT_COLUMNS = (
    "id, type, priority, title, message, file_id, file_name, "
    "file_path, event_date, metadata, created_at, read, dismissed"
)


def notification_from_row(row) -> Notification:
    """Convert an ``alerts`` row (``ALERT_COLUMNS`` order) to a Notification."""
    metadata: Dict[str, Any] = {}
    raw_metadata = row[9]
    if isinstance(raw_metadata, dict):
        metadata = dict(raw_metadata)
    elif isinstance(raw_metadata, str):
        try:
            metadata = json.loads(raw_metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    return Notification(
        id=row[0],
        type=NotificationType(row[1]),
        priority=NotificationPriority(row[2]),
        title=row[3],
        message=row[4],
        file_id=row[5],
        file_name=row[6],
        file_path=row[7],
        event_date=row[8] if row[8] else None,
        metadata=metadata,
        created_at=row[10],
        read=bool(row[11]),
        dismissed=bool(row[12]),
    )


class NotificationService:
    """
    Centralized notification service for system-wide alerts.
    
    Features:
    - Similar files notifications
    - Future date alerts
    - Future event alerts
    - Notification persistence
    - Notification filtering and management
    """
    
    def __init__(self):
        self.logger = logger
        self._notifications: List[Notification] = []
        #  OPTIMIZATION: Pending notifications queue for batch writes (no DB queries during processing)
        self._pending_notifications: List[Notification] = []
        # Guards BOTH the in-memory list and the pending queue.  The list is
        # only ever swapped as a whole under this lock (never clear()+append),
        # so concurrent refreshes can no longer interleave into duplicate
        # entries ("10 loaded" for a table with 5 rows).
        self._lock = threading.RLock()
        self._next_temp_id = -1  # Temporary IDs for pending notifications (negative to avoid conflicts)
        self._load_notifications()

    def _fetch_notifications(self, limit: int = 1000) -> List[Notification]:
        """Read notifications from the database into a fresh list."""
        # Use execute_query from Api.utils (best practice - single source of truth)
        from Api.utils import execute_query

        rows = execute_query(
            f"""
            SELECT {ALERT_COLUMNS}
            FROM alerts
            WHERE dismissed = FALSE
            ORDER BY created_at DESC
            LIMIT {int(limit)}
            """,
            fetch="all"
        )

        fetched: List[Notification] = []
        for row in rows or []:
            try:
                fetched.append(notification_from_row(row))
            except Exception as e:
                self.logger.warning(f"Error loading notification: {e}")
        return fetched

    def _load_notifications(self):
        """Load notifications from database (atomic swap, pending preserved)."""
        try:
            fetched = self._fetch_notifications()
        except Exception as e:
            self.logger.error(f"Error loading notifications: {e}")
            fetched = []
        with self._lock:
            self._notifications = fetched + list(self._pending_notifications)

    def create_similar_files_notification(
        self,
        file_id: int,
        file_name: str,
        file_path: str,
        similar_files: List[Dict],
        similarity_threshold: float = 0.8
    ) -> Notification:
        """Create notification for similar files"""
        similar_count = len(similar_files)
        
        notification = Notification(
            id=None,
            type=NotificationType.SIMILAR_FILES,
            priority=NotificationPriority.MEDIUM,
            title=f"Similar Files Detected: {file_name}",
            message=f"Found {similar_count} similar file(s) with similarity ≥ {int(similarity_threshold * 100)}%",
            file_id=file_id,
            file_name=file_name,
            file_path=file_path,
            event_date=None,
            metadata={
                'similar_files': similar_files,
                'similarity_threshold': similarity_threshold,
                'similar_count': similar_count
            },
            created_at=datetime.now()
        )
        
        return self._save_notification(notification)
    
    # Legacy priority bands (days until the date). Product defaults carried
    # over unchanged from the previous implementation; they are not derived
    # from a published source. Phase 3 replaces them with computed priority.
    _FUTURE_DATE_BANDS = ((7, NotificationPriority.CRITICAL), (30, NotificationPriority.HIGH),
                          (90, NotificationPriority.MEDIUM))

    def create_future_date_notification(self, *, file_id: int, file_name: str, file_path: str,
                                        signal: Dict[str, Any],
                                        reference_date: date) -> Notification:
        """Queue a FUTURE_DATE notification for one stored temporal signal.

        ``signal`` is a row from ``signal_store.signals_for`` (content_signals);
        ``reference_date`` is the explicit clock the orientation was computed
        against. Approximate dates (Hijri) keep their whole range.
        """
        event_date = date.fromisoformat(signal["date_from"])
        days_until = (event_date - reference_date).days
        priority = NotificationPriority.HIGH
        for limit, band in self._FUTURE_DATE_BANDS:
            if days_until <= limit:
                priority = band
                break
        notification = Notification(
            id=None,
            type=NotificationType.FUTURE_DATE,
            priority=priority,
            title=f"Future Date Detected: {event_date.isoformat()}",
            message=f"Future date found in {file_name} ({days_until} days away)",
            file_id=file_id,
            file_name=file_name,
            file_path=file_path,
            event_date=event_date,
            metadata={
                "event_text": signal["surface"],
                "value": signal["value"],
                "language": signal.get("language"),
                "calendar": signal.get("calendar"),
                "resolution": signal["resolution"],
                "date_to": signal["date_to"],
                "char_start": signal["char_start"],
                "char_end": signal["char_end"],
                "text_orientation": signal.get("text_orientation"),
                "context": (signal.get("evidence") or {}).get("context"),
                "detector_ver": signal["detector_ver"],
                # Why: what matched, how sure the detector is, and the sentence.
                "method": signal.get("method"),
                "confidence": signal.get("confidence"),
                "confidence_basis": signal.get("confidence_basis"),
                "evidence_sentence": signal.get("sentence"),
                "reference_date": reference_date.isoformat(),
                "days_until": days_until,
                "event_type": "explicit_date",
            },
            created_at=datetime.now(),
        )
        return self._save_notification(notification)

    @staticmethod
    def future_date_signals(signals: List[Dict[str, Any]],
                            reference_date: date) -> List[Dict[str, Any]]:
        """The earliest-first, one-per-date subset of ``signals`` that are
        resolved dates lying wholly after ``reference_date``.

        Ambiguous and unresolved signals are excluded - their date is unknown,
        and unknown is never treated as "in the future".
        """
        chosen: Dict[str, Dict[str, Any]] = {}
        for sig in signals:
            if sig["signal_type"] not in _DATED_SIGNAL_TYPES or not sig.get("date_from"):
                continue
            # Same rule as TemporalSignal.clock_orientation: the whole range
            # must lie after the reference date.
            if date.fromisoformat(sig["date_from"]) <= reference_date:
                continue
            chosen.setdefault(sig["date_from"], sig)
        return [chosen[k] for k in sorted(chosen)]

    def analyze_file_for_future_events(self, *, file_id: int, file_name: str, file_path: str,
                                       signals: List[Dict[str, Any]], reference_date: date,
                                       skip_dates: Optional[set] = None) -> List[Notification]:
        """Queue one FUTURE_DATE notification per future date in ``signals``.

        Detection is not performed here: signals come from ``content_signals``
        (core.detection.temporal_intel, stored at ingestion/re-detection).
        ``skip_dates`` holds event dates already notified for this file.
        Errors propagate to the caller - they are never swallowed.
        """
        skip = skip_dates or set()
        created = []
        for sig in self.future_date_signals(signals, reference_date):
            if date.fromisoformat(sig["date_from"]) in skip:
                continue
            created.append(self.create_future_date_notification(
                file_id=file_id, file_name=file_name, file_path=file_path,
                signal=sig, reference_date=reference_date))
        return created

    def _save_notification(self, notification: Notification) -> Notification:
        """
        Save notification to memory queue (no database query during processing).
        Notifications are batched and written to database later via flush_pending().
        """
        # Assign temporary ID for pending notification
        with self._lock:
            notification.id = self._next_temp_id
            self._next_temp_id -= 1
            
            # Add to pending queue (will be written to DB in batch)
            self._pending_notifications.append(notification)
            
            # Also add to in-memory list immediately (for immediate access)
            self._notifications.insert(0, notification)
            
            self.logger.debug(f"Queued notification: {notification.title} (temp_id: {notification.id})")
        
        return notification
    
    def _flush_batch(self, batch: List[Notification]) -> int:
        """
        Write one batch with a SINGLE multi-row INSERT (one round trip, one
        transaction).  Returns the number written; raises on failure so the
        caller can re-queue the whole batch (no silent per-row drops).
        """
        from Api.utils import execute_query

        # created_at doubles as the RETURNING correlation key - make sure it
        # is unique inside the batch.
        for notif in batch:
            while any(o is not notif and o.created_at == notif.created_at for o in batch):
                notif.created_at += timedelta(microseconds=1)

        values_sql = ", ".join(
            ["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(batch)
        )
        query = (
            "INSERT INTO alerts (type, priority, title, message, file_id, file_name, "
            "file_path, event_date, metadata, created_at, read, dismissed) "
            f"VALUES {values_sql} "
            "RETURNING id, created_at"
        )
        params: list = []
        for notif in batch:
            params.extend([
                notif.type.value,
                notif.priority.value,
                notif.title,
                notif.message,
                notif.file_id,
                notif.file_name,
                notif.file_path,
                notif.event_date,
                json.dumps(notif.metadata),
                notif.created_at,
                notif.read,
                notif.dismissed,
            ])

        result = execute_query(query, tuple(params), fetch="all") or []
        if len(result) != len(batch):
            raise RuntimeError(
                f"Batch insert returned {len(result)} rows for {len(batch)} notifications"
            )

        by_created_at = {row[1]: row[0] for row in result}
        if len(by_created_at) != len(batch):
            raise RuntimeError("Batch insert returned duplicate created_at correlation keys")

        with self._lock:
            for notif in batch:
                real_id = by_created_at.get(notif.created_at)
                if real_id is None:
                    raise RuntimeError("Batch insert did not return a row for a notification")
                # The SAME object lives in _notifications, so one assignment
                # updates every view of it.
                notif.id = real_id
        return len(batch)

    def flush_pending_notifications(self, batch_size: int = 100) -> int:
        """
        Flush pending notifications to database in batches.
        This should be called periodically or after processing completes.

        Guarantees:
        * one multi-row INSERT per batch (bounded round trips at scan volume)
        * a failed batch is re-queued whole and never silently dropped
        * ids are written back onto the in-memory objects under the lock

        Args:
            batch_size: Number of notifications to write per batch

        Returns:
            Number of notifications successfully written
        """
        with self._lock:
            if not self._pending_notifications:
                return 0

        written_count = 0
        while True:
            with self._lock:
                if not self._pending_notifications:
                    break
                batch = self._pending_notifications[:batch_size]
                del self._pending_notifications[:batch_size]

            try:
                written_count += self._flush_batch(batch)
                for notif in batch:
                    self.logger.debug(
                        f"Flushed notification: {notif.title} (id: {notif.id})"
                    )
            except Exception as e:
                # Put the batch back so nothing is lost; stop so a persistent
                # failure cannot loop forever.
                self.logger.error(f"Error flushing notification batch ({len(batch)} items): {e}")
                with self._lock:
                    self._pending_notifications = batch + self._pending_notifications
                break

        if written_count > 0:
            self.logger.info(f"✅ Flushed {written_count} notification(s) to database")

        return written_count

    def get_pending_count(self) -> int:
        """Get count of pending notifications waiting to be written to database"""
        with self._lock:
            return len(self._pending_notifications)
    
    def get_notifications(
        self,
        notification_type: Optional[NotificationType] = None,
        priority: Optional[NotificationPriority] = None,
        unread_only: bool = False,
        limit: int = 100
    ) -> List[Notification]:
        """
        Get notifications with filters.
        Uses in-memory list only (no database query).
        Includes both persisted and pending notifications.
        """
        # Combine persisted and pending notifications (pending may have temp IDs)
        with self._lock:
            all_notifications = list(self._notifications)
        
        if notification_type:
            all_notifications = [n for n in all_notifications if n.type == notification_type]
        
        if priority:
            all_notifications = [n for n in all_notifications if n.priority == priority]
        
        if unread_only:
            all_notifications = [n for n in all_notifications if not n.read]
        
        # Filter out dismissed
        all_notifications = [n for n in all_notifications if not n.dismissed]
        
        # Sort by created_at descending
        all_notifications.sort(key=lambda x: x.created_at, reverse=True)
        
        return all_notifications[:limit]
    
    def mark_as_read(self, notification_id: int) -> bool:
        """Mark notification as read"""
        try:
            # Use execute_query from Api.utils or database.queries
            try:
                from Api.utils import execute_query
            except ImportError:
                from database import DatabaseHub
                db_hub = DatabaseHub()
                def execute_query(query, params=None, fetch="all"):
                    conn = db_hub._get_connection()
                    cursor = conn.cursor()
                    try:
                        if params:
                            cursor.execute(query, params)
                        else:
                            cursor.execute(query)
                        if fetch == "all":
                            result = cursor.fetchall()
                        elif fetch == "one":
                            result = cursor.fetchone()
                        else:
                            conn.commit()
                            result = None
                        if fetch is not None:
                            conn.commit()
                        return result if result else ([] if fetch == "all" else None)
                    finally:
                        cursor.close()
            
            updated_row = execute_query(
                "UPDATE alerts SET read = TRUE WHERE id = %s RETURNING id",
                (notification_id,),
                fetch="one"
            )
            
            # Update in memory (covers pending objects that are not in the DB yet)
            with self._lock:
                for notification in self._notifications:
                    if notification.id == notification_id:
                        notification.read = True
                        self.logger.debug(f"Marked notification {notification_id} as read")
                        return True
            
            # Not in memory: True iff the database row existed and was updated.
            # (No full-table refresh - a refresh here raced concurrent readers.)
            if updated_row:
                self.logger.debug(f"Marked notification {notification_id} as read (database row)")
                return True
            
        except Exception as e:
            self.logger.error(f"Error marking notification as read: {e}")
            return False
        
        return False
    
    def dismiss_notification(self, notification_id: int) -> bool:
        """Dismiss a notification"""
        try:
            # Use execute_query from Api.utils or database.queries
            try:
                from Api.utils import execute_query
            except ImportError:
                from database import DatabaseHub
                db_hub = DatabaseHub()
                def execute_query(query, params=None, fetch="all"):
                    conn = db_hub._get_connection()
                    cursor = conn.cursor()
                    try:
                        if params:
                            cursor.execute(query, params)
                        else:
                            cursor.execute(query)
                        if fetch == "all":
                            result = cursor.fetchall()
                        elif fetch == "one":
                            result = cursor.fetchone()
                        else:
                            conn.commit()
                            result = None
                        if fetch is not None:
                            conn.commit()
                        return result if result else ([] if fetch == "all" else None)
                    finally:
                        cursor.close()
            
            updated_row = execute_query(
                "UPDATE alerts SET dismissed = TRUE WHERE id = %s RETURNING id",
                (notification_id,),
                fetch="one"
            )
            
            # Update in memory (covers pending objects that are not in the DB yet)
            with self._lock:
                for notification in self._notifications:
                    if notification.id == notification_id:
                        notification.dismissed = True
                        self.logger.debug(f"Dismissed notification {notification_id}")
                        return True
            
            # Not in memory: True iff the database row existed and was updated.
            # (No full-table refresh - a refresh here raced concurrent readers.)
            if updated_row:
                self.logger.debug(f"Dismissed notification {notification_id} (database row)")
                return True
            
        except Exception as e:
            self.logger.error(f"Error dismissing notification: {e}")
            return False
        
        return False
    
    def refresh_notifications(self) -> int:
        """
        Reload notifications from database (useful after external changes).

        Returns:
            Number of notifications now held in memory.

        The swap is atomic: the fresh list is built OUTSIDE the lock and
        swapped in as a whole, with still-pending (not yet flushed)
        notifications merged back in.  The old clear()+append() sequence let
        two concurrent refreshes interleave and double every row in memory
        ("10 loaded" while the table held 5).
        """
        try:
            fetched = self._fetch_notifications()
        except Exception as e:
            self.logger.error(f"Error refreshing notifications: {e}")
            fetched = None

        if fetched is None:
            return 0

        with self._lock:
            self._notifications = fetched + list(self._pending_notifications)
            loaded = len(self._notifications)
        self.logger.info(f"Refreshed notifications: {loaded} loaded")
        return loaded

    def get_stats(self) -> Dict[str, Any]:
        """
        Exact notification statistics.

        COUNT/GROUP BY queries run in SQL, so totals are correct for any
        table size and memory stays O(1) - unlike the previous implementation
        which forced a full refresh and counted an in-memory list capped at
        LIMIT 1000.  Pending (unflushed) notifications are overlaid so brand
        new alerts are counted immediately.
        """
        today = date.today()
        stats: Dict[str, Any] = {
            'total': 0,
            'unread': 0,
            'by_type': {},
            'by_priority': {},
            'upcoming_events': 0,
        }

        try:
            from Api.utils import execute_query

            row = execute_query(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE NOT read) AS unread
                FROM alerts
                WHERE dismissed = FALSE
                """,
                fetch="one"
            )
            if row:
                stats['total'] = int(row[0])
                stats['unread'] = int(row[1])

            for type_row in execute_query(
                """
                SELECT type, COUNT(*)
                FROM alerts
                WHERE dismissed = FALSE
                GROUP BY type
                """,
                fetch="all"
            ) or []:
                stats['by_type'][type_row[0]] = int(type_row[1])

            for priority_row in execute_query(
                """
                SELECT priority, COUNT(*)
                FROM alerts
                WHERE dismissed = FALSE
                GROUP BY priority
                """,
                fetch="all"
            ) or []:
                stats['by_priority'][priority_row[0]] = int(priority_row[1])

            upcoming_row = execute_query(
                """
                SELECT COUNT(*)
                FROM alerts
                WHERE type = %s
                  AND dismissed = FALSE
                  AND event_date BETWEEN %s AND %s
                """,
                (NotificationType.FUTURE_DATE.value, today, today + timedelta(days=30)),
                fetch="one"
            )
            if upcoming_row:
                stats['upcoming_events'] = int(upcoming_row[0])
        except Exception as e:
            self.logger.warning(
                f"SQL stats unavailable, falling back to in-memory counts "
                f"(list capped at {1000}): {e}"
            )
            for n in self.get_notifications(limit=1000):
                stats['total'] += 1
                if not n.read:
                    stats['unread'] += 1
                stats['by_type'][n.type.value] = stats['by_type'].get(n.type.value, 0) + 1
                stats['by_priority'][n.priority.value] = stats['by_priority'].get(n.priority.value, 0) + 1
            stats['upcoming_events'] = len(self.get_upcoming_events(days_ahead=30))
            return stats

        # Overlay notifications that are still queued (not in the DB yet).
        with self._lock:
            pending = [n for n in self._pending_notifications if not n.dismissed]
        for n in pending:
            stats['total'] += 1
            if not n.read:
                stats['unread'] += 1
            stats['by_type'][n.type.value] = stats['by_type'].get(n.type.value, 0) + 1
            stats['by_priority'][n.priority.value] = stats['by_priority'].get(n.priority.value, 0) + 1
            if (
                n.type == NotificationType.FUTURE_DATE
                and n.event_date
                and today <= n.event_date <= today + timedelta(days=30)
            ):
                stats['upcoming_events'] += 1

        return stats

    def get_upcoming_events(self, days_ahead: int = 30) -> List[Notification]:
        """Get upcoming events within specified days (SQL + pending overlay)."""
        today = date.today()
        end_date = today + timedelta(days=days_ahead)

        try:
            from Api.utils import execute_query
            rows = execute_query(
                f"""
                SELECT {ALERT_COLUMNS}
                FROM alerts
                WHERE type = %s
                  AND dismissed = FALSE
                  AND event_date BETWEEN %s AND %s
                ORDER BY event_date ASC
                LIMIT 1000
                """,
                (NotificationType.FUTURE_DATE.value, today, end_date),
                fetch="all"
            )
            upcoming = [notification_from_row(row) for row in rows or []]
        except Exception as e:
            self.logger.warning(f"SQL upcoming query unavailable, using in-memory list: {e}")
            notifications = self.get_notifications(
                notification_type=NotificationType.FUTURE_DATE,
                unread_only=False
            )
            upcoming = [
                n for n in notifications
                if n.event_date and today <= n.event_date <= end_date
            ]

        # Merge queued (unflushed) future-date notifications.
        with self._lock:
            pending = [
                n for n in self._pending_notifications
                if not n.dismissed
                and n.type == NotificationType.FUTURE_DATE
                and n.event_date
                and today <= n.event_date <= end_date
            ]
        known_ids = {n.id for n in upcoming}
        upcoming.extend(n for n in pending if n.id not in known_ids)

        return sorted(upcoming, key=lambda x: x.event_date or date.max)


def get_notification_service() -> NotificationService:
    """Get singleton instance of NotificationService"""
    if not hasattr(get_notification_service, '_instance'):
        get_notification_service._instance = NotificationService()
    return get_notification_service._instance

