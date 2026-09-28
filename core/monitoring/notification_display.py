"""
Shared notification display formatting.

Every API route that returns notifications to the UI must derive the
displayed title/message from the SAME rules, otherwise copies drift and the
UI shows values that do not match the database rows (e.g. duplicate
notifications rendered as "Found 0 similar file(s) with similarity ≥ 80%").

Design rules (accuracy first):

* Duplicate notifications (``metadata.duplicate_count``/``metadata.hash``,
  created by ``POST /api/notifications/scan``) are rendered as duplicates
  with their REAL count and file list - never as similarity-threshold
  messages, which do not apply to them.
* Similarity notifications (``metadata.similar_count``) keep the threshold
  message.
* Future-date messages recompute ``days_until`` LIVE from ``event_date``;
  the value frozen into ``metadata.days_until`` at creation time goes stale
  the day after the scan.
* Rule and scenario notifications (``rule_*``, ``scenario_*``) are rendered from
  their structured metadata (rule name, subject counts, overflow, the reason a
  rule was disabled), so they are translated at display time.
* Types without special rendering pass through the stored title/message
  unchanged (no fabricated defaults).

``translate`` is the gettext function (``flask_babel.gettext``); it is
injected so this module stays importable (and testable) outside a request
context.
"""

from datetime import date, datetime
from typing import Tuple

from .notification_service import Notification, NotificationType


def format_title_message(notification: Notification, translate) -> Tuple[str, str]:
    """Return the accurate ``(title, message)`` pair for a notification."""
    n = notification
    metadata = n.metadata or {}
    title = n.title
    message = n.message

    if n.type == NotificationType.SIMILAR_FILES:
        if "duplicate_count" in metadata or "hash" in metadata:
            # Storage-duplicate notification created by the scan endpoint.
            count = metadata.get("duplicate_count", 0)
            file_names = metadata.get("file_names") or []
            if n.file_name:
                title = translate(
                    "Duplicate Files Detected: %(file_name)s",
                    file_name=n.file_name,
                )
            else:
                title = translate("Duplicate Files Detected")
            if count:
                if file_names:
                    display_names = list(file_names[:5])
                    if len(file_names) > 5:
                        display_names.append(f"... and {len(file_names) - 5} more")
                    message = translate(
                        "Found %(count)s duplicate file(s) with the same hash. Files: %(files)s",
                        count=count,
                        files=", ".join(display_names),
                    )
                else:
                    message = translate(
                        "Found %(count)s duplicate file(s) with the same hash",
                        count=count,
                    )
            # else: keep the stored message verbatim (most precise fallback)
        else:
            # Similarity-threshold notification.
            similar_count = metadata.get("similar_count", 0)
            similarity_threshold = metadata.get("similarity_threshold", 0.8)
            threshold_percent = int(similarity_threshold * 100)
            if n.file_name:
                title = translate(
                    "Similar Files Detected: %(file_name)s",
                    file_name=n.file_name,
                )
            else:
                title = translate("Similar Files Detected")
            message = translate(
                "Found %(count)s similar file(s) with similarity ≥ %(threshold)s%%",
                count=similar_count,
                threshold=threshold_percent,
            )

    elif n.type == NotificationType.FUTURE_EVENT:
        if n.event_date:
            date_str = n.event_date.strftime("%Y-%m-%d")
            title = translate("Future Event Detected: %(date)s", date=date_str)
        else:
            title = translate("Future Event Detected")
        if n.file_name:
            message = translate(
                "Future-focused content found in %(file_name)s",
                file_name=n.file_name,
            )
        else:
            message = translate("Future-focused content found")

    elif n.type == NotificationType.FUTURE_DATE:
        if n.event_date:
            date_str = n.event_date.strftime("%Y-%m-%d")
            title = translate("Future Date Detected: %(date)s", date=date_str)
            # LIVE countdown: metadata.days_until was frozen at creation time.
            event_day = n.event_date.date() if isinstance(n.event_date, datetime) else n.event_date
            days_until = (event_day - date.today()).days
            if n.file_name:
                if days_until > 0:
                    message = translate(
                        "Future date found in %(file_name)s (%(days)s days away)",
                        file_name=n.file_name,
                        days=days_until,
                    )
                else:
                    message = translate(
                        "Future date found in %(file_name)s",
                        file_name=n.file_name,
                    )
            else:
                if days_until > 0:
                    message = translate(
                        "Future date found (%(days)s days away)",
                        days=days_until,
                    )
                else:
                    message = translate("Future date found")
        else:
            title = translate("Future Date Detected")
            message = translate("Future date found")

    elif n.type == NotificationType.PROCESSING_COMPLETE:
        title = translate("Processing Complete")
        if n.file_name:
            message = translate(
                "File processing completed: %(file_name)s",
                file_name=n.file_name,
            )
        else:
            message = translate("File processing completed")

    elif n.type == NotificationType.BATCH_COMPLETE:
        title = translate("Batch Processing Complete")
        batch_count = metadata.get("batch_count", 0)
        if batch_count:
            message = translate(
                "Batch processing completed: %(count)s file(s) processed",
                count=batch_count,
            )
        else:
            message = translate("Batch processing completed")

    elif n.type == NotificationType.ERROR:
        if not title or title == n.message:
            title = translate("Error")
        if n.file_name:
            message = translate(
                "Error in %(file_name)s: %(error)s",
                file_name=n.file_name,
                error=n.message,
            )
        else:
            message = n.message

    elif n.type == NotificationType.WARNING:
        if not title or title == n.message:
            title = translate("Warning")
        message = n.message

    elif n.type == NotificationType.INFO:
        if not title or title == n.message:
            title = translate("Information")
        message = n.message

    elif n.type == NotificationType.RULE_MATCH:
        name = metadata.get("rule_name") or n.title
        count = metadata.get("subject_count", 0)
        delivery = metadata.get("delivery")
        if delivery == "digest":
            title = translate("Rule digest: %(name)s", name=name)
            message = translate("%(count)s new matches since the last digest", count=count)
        elif delivery == "overflow":
            title = translate("Rule: %(name)s", name=name)
            message = translate(
                "%(count)s further matches in %(groups)s groups (notification limit"
                " per evaluation reached)", count=count, groups=metadata.get("group_count", 0))
        else:
            title = translate("Rule: %(name)s", name=name)
            label = metadata.get("group_label")
            if label:
                message = translate("%(count)s new matches: %(label)s", count=count, label=label)
            else:
                message = translate("%(count)s new matches", count=count)

    elif n.type == NotificationType.RULE_STATUS:
        name = metadata.get("rule_name") or n.title
        title = translate("Rule disabled: %(name)s", name=name)
        reason = metadata.get("disabled_reason")
        if reason == "owner_inactive":
            message = translate("The rule was disabled because its owner's account is inactive.")
        elif reason == "owner_role":
            message = translate("The rule was disabled because its owner's role no longer"
                                " allows monitoring rules.")
        else:
            message = n.message

    elif n.type == NotificationType.SCENARIO_OUTCOME:
        name = metadata.get("scenario_name") or n.title
        title = translate("Scenario: %(name)s", name=name)
        if metadata.get("delivery") == "overflow":
            message = translate(
                "%(count)s further contents entered notified outcomes (notification limit"
                " per evaluation reached)", count=metadata.get("content_count", 0))
        else:
            labels = metadata.get("outcome_labels") or {}
            entered = [labels.get(o, o) for o in metadata.get("entered_outcomes") or []]
            target = n.file_name or translate("Content %(id)s", id=metadata.get("hash_id"))
            message = translate("Outcome %(outcome)s: %(target)s",
                                outcome=", ".join(entered) or n.message, target=target)

    elif n.type == NotificationType.SCENARIO_STATUS:
        name = metadata.get("scenario_name") or n.title
        title = translate("Scenario disabled: %(name)s", name=name)
        reason = metadata.get("disabled_reason")
        if reason == "owner_inactive":
            message = translate("The scenario was disabled because its owner's account is"
                                " inactive.")
        elif reason == "owner_role":
            message = translate("The scenario was disabled because its owner's role no longer"
                                " allows scenarios.")
        else:
            message = n.message

    return title, message


def display_payload(notification: Notification, translate) -> dict:
    """Full JSON payload (used by list/single/paginated routes)."""
    n = notification
    title, message = format_title_message(n, translate)
    return {
        "id": n.id,
        "type": n.type.value,
        "priority": n.priority.value,
        "title": title,
        "message": message,
        "file_id": n.file_id,
        "file_name": n.file_name,
        "file_path": n.file_path,
        "event_date": n.event_date.isoformat() if n.event_date else None,
        "metadata": n.metadata,
        "created_at": n.created_at.isoformat(),
        "read": n.read,
        "dismissed": n.dismissed,
        # True when the notification is addressed to the viewer alone.
        "addressed": n.recipient_user_id is not None,
        "rule_id": n.rule_id,
        "scenario_id": n.scenario_id,
    }
