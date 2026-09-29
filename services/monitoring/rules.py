"""Monitoring-rule storage: create, version, lifecycle, suppression.

Who may do what (enforced here, not only in the routes):

* **create** - an active analyst or administrator, for themselves.
* **view** - the owner; administrators see every rule (to supervise them),
  but never another user's rule *notifications* (those are addressed to the
  owner alone).
* **edit** (name / definition) - the owner only: the rule runs with the
  owner's access, so nobody else may change what it reads.
* **pause / resume / archive / suppress** - the owner, or an administrator.
* **resume** re-checks the owner: a rule disabled because its owner lost the
  right to run rules cannot be switched back on until that right returns.

Lifecycle: ``active`` (evaluated) / ``paused`` (not evaluated; matches that
appear meanwhile are picked up on resume) / ``disabled`` (set by the system,
with a reason) / ``archived`` (kept for history, never evaluated again; its
name may be reused). Suppression is different from pausing: a suppressed
rule is still evaluated, and what it matches is recorded as ``suppressed``
and never notified, even after the suppression ends.

A definition change that alters the fingerprint creates a new version (the
old one stays in ``monitoring_rule_versions``) and re-baselines the rule at
its next evaluation. Renaming does not.

Every function takes a connection and commits or rolls back itself.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras

from core.criteria.model import Criteria, CriteriaError, from_dict
from services.monitoring.rule_model import (
    RuleDefinitionError, definition_from_stored, parse_definition, validate_name)

#: Roles that may own monitoring rules.
RULE_ROLES = ("analyst", "admin")

STATUSES = ("active", "paused", "disabled", "archived")
DISABLED_REASONS = ("owner_inactive", "owner_role")
MAX_SUPPRESS_MINUTES = 60 * 24 * 90
MAX_LIST = 500

RULE_COLUMNS = (
    "r.id, r.owner_user_id, u.username AS owner_username, r.name, r.version, r.definition,"
    " r.definition_fingerprint, r.saved_search_id, r.status, r.disabled_reason,"
    " r.suppressed_until, r.baselined_version, r.last_evaluated_at, r.last_notified_at,"
    " r.last_digest_at, r.created_at, r.updated_at"
)
_FROM = "monitoring_rules r JOIN users u ON u.id = r.owner_user_id"


class RuleError(Exception):
    """A request refused for a stated reason. ``status`` is the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _not_found(what: str = "rule") -> RuleError:
    # Absent and not permitted are the same answer.
    return RuleError("NOT_FOUND", f"{what} not found", 404)


def owner_eligibility(cur, user_id: int) -> Tuple[bool, Optional[str], Optional[str]]:
    """``(eligible, reason, role)`` for a rule owner, read now."""
    cur.execute("SELECT role, is_active FROM users WHERE id = %s", (user_id,))
    row = _as_dict(cur, cur.fetchone())
    if row is None:
        return False, "owner_inactive", None
    role, active = row["role"], row["is_active"]
    if not active:
        return False, "owner_inactive", role
    if role not in RULE_ROLES:
        return False, "owner_role", role
    return True, None, role


def _as_dict(cur, row) -> Optional[Dict[str, Any]]:
    """A row as a dict, from a plain or a dict cursor."""
    if row is None or isinstance(row, dict):
        return row
    return {d[0]: v for d, v in zip(cur.description, row)}


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def rule_to_api(row: Dict[str, Any], *, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    suppressed_until = row["suppressed_until"]
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "owner_username": row.get("owner_username"),
        "name": row["name"],
        "version": row["version"],
        "definition": row["definition"],
        "definition_fingerprint": row["definition_fingerprint"],
        "saved_search_id": row["saved_search_id"],
        "status": row["status"],
        "disabled_reason": row["disabled_reason"],
        "suppressed_until": _iso(suppressed_until),
        "suppressed": bool(suppressed_until and suppressed_until > now),
        # False until the first evaluation of the current version has
        # recorded its baseline.
        "baselined": row["baselined_version"] == row["version"],
        "last_evaluated_at": _iso(row["last_evaluated_at"]),
        "last_notified_at": _iso(row["last_notified_at"]),
        "last_digest_at": _iso(row["last_digest_at"]),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _dict_cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def fetch_rule(cur, rule_id: int, *, for_update: bool = False) -> Optional[Dict[str, Any]]:
    cur.execute(f"SELECT {RULE_COLUMNS} FROM {_FROM} WHERE r.id = %s"  # nosec B608 # RULE_COLUMNS and _FROM are constants; filters are fixed fragments with bound parameters
                + (" FOR UPDATE OF r" if for_update else ""), (rule_id,))
    row = cur.fetchone()
    return dict(row) if row is not None else None


def can_view(rule: Dict[str, Any], user_id: Optional[int], is_admin: bool) -> bool:
    return user_id is not None and (rule["owner_user_id"] == user_id or is_admin)


def sync_monitor_flag(cur, saved_search_id: Optional[int]) -> None:
    """``saved_searches.monitor_enabled`` is derived: true exactly when an
    active or paused rule was built from the search. Updated in the same
    transaction as the rule change, so the two never disagree."""
    if saved_search_id is None:
        return
    cur.execute(
        "UPDATE saved_searches SET monitor_enabled = EXISTS (SELECT 1 FROM monitoring_rules"
        " WHERE saved_search_id = %s AND status IN ('active', 'paused')) WHERE id = %s",
        (saved_search_id, saved_search_id))


def _saved_search_criteria(cur, search_id: int, user_id: int, is_admin: bool) -> Criteria:
    from Api.services.saved_searches_repository import can_read

    cur.execute("SELECT id, owner_user_id, criteria FROM saved_searches WHERE id = %s",
                (search_id,))
    row = _as_dict(cur, cur.fetchone())
    if not can_read(row, user_id, is_admin):
        raise _not_found("saved search")
    try:
        return from_dict(row["criteria"] or {})
    except CriteriaError as exc:
        raise RuleError("VALIDATION_FAILED", f"saved search criteria are invalid: {exc}")


def _unique_name_error(exc) -> bool:
    return (isinstance(exc, psycopg2.errors.UniqueViolation)
            and "uq_monitoring_rules_owner_name" in str(exc))


def _record_version(cur, rule_id: int, version: int, definition, actor_id: Optional[int]):
    cur.execute(
        "INSERT INTO monitoring_rule_versions (rule_id, version, definition,"
        " definition_fingerprint, created_by_user_id) VALUES (%s, %s, %s, %s, %s)",
        (rule_id, version, psycopg2.extras.Json(definition.canonical()),
         definition.fingerprint(), actor_id))


def create_rule(conn, *, owner_id: int, is_admin: bool, name: Any, definition: Any,
                saved_search_id: Any = None) -> Dict[str, Any]:
    try:
        name = validate_name(name)
        with _dict_cur(conn) as cur:
            eligible, reason, _role = owner_eligibility(cur, owner_id)
            if not eligible:
                raise RuleError("FORBIDDEN", "monitoring rules need an active analyst or"
                                " administrator account", 403)
            override = None
            if saved_search_id is not None:
                if isinstance(saved_search_id, bool) or not isinstance(saved_search_id, int) \
                        or saved_search_id <= 0:
                    raise RuleError("VALIDATION_FAILED",
                                    "saved_search_id must be a positive integer")
                override = _saved_search_criteria(cur, saved_search_id, owner_id, is_admin)
            parsed = parse_definition(definition, criteria_override=override)
            cur.execute(
                "INSERT INTO monitoring_rules (owner_user_id, name, version, definition,"
                " definition_fingerprint, saved_search_id) VALUES (%s, %s, 1, %s, %s, %s)"
                " RETURNING id",
                (owner_id, name, psycopg2.extras.Json(parsed.canonical()),
                 parsed.fingerprint(), saved_search_id))
            rule_id = cur.fetchone()["id"]
            _record_version(cur, rule_id, 1, parsed, owner_id)
            sync_monitor_flag(cur, saved_search_id)
            rule = fetch_rule(cur, rule_id)
        conn.commit()
        return rule
    except RuleDefinitionError as exc:
        conn.rollback()
        raise RuleError("VALIDATION_FAILED", str(exc))
    except psycopg2.Error as exc:
        conn.rollback()
        if _unique_name_error(exc):
            raise RuleError("CONFLICT", "you already have a rule with this name", 409)
        raise
    except BaseException:
        conn.rollback()
        raise


def get_rule(conn, rule_id: int, *, user_id: Optional[int], is_admin: bool) -> Dict[str, Any]:
    try:
        with _dict_cur(conn) as cur:
            rule = fetch_rule(cur, rule_id)
    finally:
        conn.rollback()
    if rule is None or not can_view(rule, user_id, is_admin):
        raise _not_found()
    return rule


def list_rules(conn, *, user_id: int, is_admin: bool, all_users: bool = False,
               include_archived: bool = False) -> List[Dict[str, Any]]:
    """The caller's rules; an administrator may ask for every user's."""
    where, params = [], []
    if not (all_users and is_admin):
        where.append("r.owner_user_id = %s")
        params.append(user_id)
    if not include_archived:
        where.append("r.status <> 'archived'")
    sql = (f"SELECT {RULE_COLUMNS} FROM {_FROM}"  # nosec B608 # RULE_COLUMNS and _FROM are constants; filters are fixed fragments with bound parameters
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY lower(r.name), r.id LIMIT %s")
    try:
        with _dict_cur(conn) as cur:
            cur.execute(sql, params + [MAX_LIST + 1])
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()


def update_rule(conn, rule_id: int, *, user_id: int, is_admin: bool, name: Any = None,
                definition: Any = None) -> Dict[str, Any]:
    """Rename and/or replace the definition (owner only).

    ``definition`` replaces the whole definition. When it has no ``criteria``
    key the current criteria are kept - including a saved-search snapshot and
    its provenance; new criteria end that provenance (``saved_search_id``
    becomes NULL), because the rule no longer watches what the search said.
    """
    try:
        with _dict_cur(conn) as cur:
            rule = fetch_rule(cur, rule_id, for_update=True)
            if rule is None or not can_view(rule, user_id, is_admin):
                raise _not_found()
            if rule["owner_user_id"] != user_id:
                raise RuleError("FORBIDDEN", "only the owner may change a rule", 403)
            if rule["status"] == "archived":
                raise RuleError("CONFLICT", "an archived rule cannot be changed", 409)
            if name is None and definition is None:
                raise RuleError("VALIDATION_FAILED", "nothing to change")
            new_name = validate_name(name) if name is not None else rule["name"]
            version = rule["version"]
            saved_search_id = rule["saved_search_id"]
            if definition is not None:
                current = definition_from_stored(rule["definition"])
                keep_criteria = not isinstance(definition, dict) or "criteria" not in definition
                parsed = parse_definition(
                    definition, criteria_override=current.criteria if keep_criteria else None)
                if parsed.fingerprint() != rule["definition_fingerprint"]:
                    version += 1
                    _record_version(cur, rule_id, version, parsed, user_id)
                    if not keep_criteria and parsed.criteria != current.criteria:
                        saved_search_id = None
                    cur.execute(
                        "UPDATE monitoring_rules SET version = %s, definition = %s,"
                        " definition_fingerprint = %s, saved_search_id = %s WHERE id = %s",
                        (version, psycopg2.extras.Json(parsed.canonical()),
                         parsed.fingerprint(), saved_search_id, rule_id))
            cur.execute("UPDATE monitoring_rules SET name = %s, updated_at = NOW()"
                        " WHERE id = %s", (new_name, rule_id))
            if saved_search_id != rule["saved_search_id"]:
                sync_monitor_flag(cur, rule["saved_search_id"])
            rule = fetch_rule(cur, rule_id)
        conn.commit()
        return rule
    except RuleDefinitionError as exc:
        conn.rollback()
        raise RuleError("VALIDATION_FAILED", str(exc))
    except psycopg2.Error as exc:
        conn.rollback()
        if _unique_name_error(exc):
            raise RuleError("CONFLICT", "you already have a rule with this name", 409)
        raise
    except BaseException:
        conn.rollback()
        raise


#: action -> (statuses it applies to, resulting status)
_TRANSITIONS = {
    "pause": (("active",), "paused"),
    "resume": (("paused", "disabled"), "active"),
    "archive": (("active", "paused", "disabled"), "archived"),
}


def set_status(conn, rule_id: int, action: str, *, user_id: int, is_admin: bool
               ) -> Dict[str, Any]:
    if action not in _TRANSITIONS:
        raise RuleError("VALIDATION_FAILED", f"unknown action {action!r}")
    allowed, target = _TRANSITIONS[action]
    try:
        with _dict_cur(conn) as cur:
            rule = fetch_rule(cur, rule_id, for_update=True)
            if rule is None or not can_view(rule, user_id, is_admin):
                raise _not_found()
            if rule["status"] not in allowed:
                verb = {"pause": "paused", "resume": "resumed", "archive": "archived"}[action]
                raise RuleError("CONFLICT", f"a {rule['status']} rule cannot be {verb}", 409)
            if target == "active":
                eligible, reason, _ = owner_eligibility(cur, rule["owner_user_id"])
                if not eligible:
                    raise RuleError("CONFLICT", "the rule's owner may not run monitoring rules"
                                    f" ({reason}); it cannot be resumed", 409)
            cur.execute("UPDATE monitoring_rules SET status = %s, disabled_reason = NULL,"
                        " updated_at = NOW() WHERE id = %s", (target, rule_id))
            sync_monitor_flag(cur, rule["saved_search_id"])
            rule = fetch_rule(cur, rule_id)
        conn.commit()
        return rule
    except BaseException:
        conn.rollback()
        raise


def suppress(conn, rule_id: int, *, minutes: Any, user_id: int, is_admin: bool,
             now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Suppress for ``minutes`` from now, or lift the suppression (``0``)."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) \
            or not 0 <= minutes <= MAX_SUPPRESS_MINUTES:
        raise RuleError("VALIDATION_FAILED",
                        f"minutes must be an integer between 0 and {MAX_SUPPRESS_MINUTES}")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    until = now + datetime.timedelta(minutes=minutes) if minutes else None
    try:
        with _dict_cur(conn) as cur:
            rule = fetch_rule(cur, rule_id, for_update=True)
            if rule is None or not can_view(rule, user_id, is_admin):
                raise _not_found()
            if rule["status"] == "archived":
                raise RuleError("CONFLICT", "an archived rule cannot be suppressed", 409)
            cur.execute("UPDATE monitoring_rules SET suppressed_until = %s, updated_at = NOW()"
                        " WHERE id = %s", (until, rule_id))
            rule = fetch_rule(cur, rule_id)
        conn.commit()
        return rule
    except BaseException:
        conn.rollback()
        raise


def list_evaluations(conn, rule_id: int, *, limit: int = 50, offset: int = 0
                     ) -> Tuple[List[Dict[str, Any]], int]:
    try:
        with _dict_cur(conn) as cur:
            cur.execute("SELECT count(*) AS n FROM rule_evaluations WHERE rule_id = %s",
                        (rule_id,))
            total = cur.fetchone()["n"]
            cur.execute(
                "SELECT id, rule_version, trigger, job_id, status, owner_role, access_scope,"
                " criteria_fingerprint, definition_fingerprint, reference_date, evaluated_at,"
                " counts, error, started_at, finished_at FROM rule_evaluations"
                " WHERE rule_id = %s ORDER BY id DESC LIMIT %s OFFSET %s",
                (rule_id, limit, offset))
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
    for r in rows:
        for k in ("reference_date", "evaluated_at", "started_at", "finished_at"):
            r[k] = _iso(r[k])
    return rows, total


def list_versions(conn, rule_id: int) -> List[Dict[str, Any]]:
    try:
        with _dict_cur(conn) as cur:
            cur.execute("SELECT version, definition, definition_fingerprint, created_by_user_id,"
                        " created_at FROM monitoring_rule_versions WHERE rule_id = %s"
                        " ORDER BY version", (rule_id,))
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        conn.rollback()
    for r in rows:
        r["created_at"] = _iso(r["created_at"])
    return rows
