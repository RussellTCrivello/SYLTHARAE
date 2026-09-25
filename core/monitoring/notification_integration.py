"""
Notification Integration
Helper functions to integrate notifications with file processing and similar files detection
Uses unified config system for all settings access.
"""

import logging
from typing import Optional, List, Dict
from core.monitoring.notification_service import get_notification_service
from settings import get_settings

logger = logging.getLogger(__name__)


def should_create_notifications() -> bool:
    """Check if notifications should be created based on unified config"""
    try:
        settings = get_settings()
        return settings.get_setting('notifications.enabled', True)
    except Exception:
        return True


def should_analyze_future_events() -> bool:
    """Check if future events analysis should be performed (from unified config)"""
    try:
        settings = get_settings()
        return (
            settings.get_setting('notifications.enabled', True) and
            settings.get_setting('notifications.auto_analyze_files', True)
        )
    except Exception:
        return True


def analyze_file_after_processing(
    file_id: int,
    file_name: str,
    file_path: str,
    content: str
) -> List[Dict]:
    """
    Analyze file for future events after processing.
    Returns list of created notifications.
    """
    if not should_analyze_future_events():
        return []
    
    try:
        settings = get_settings()
        future_dates_enabled = settings.get_setting('notifications.future_dates_enabled', True)
        future_events_enabled = settings.get_setting('notifications.future_events_enabled', True)
        
        if not (future_dates_enabled or future_events_enabled):
            return []
        
        notification_service = get_notification_service()
        notifications = notification_service.analyze_file_for_future_events(
            file_id=file_id,
            file_name=file_name,
            file_path=file_path,
            content=content
        )
        
        return [
            {
                'id': n.id,
                'type': n.type.value,
                'title': n.title,
                'event_date': n.event_date.isoformat() if n.event_date else None
            }
            for n in notifications
        ]
    
    except Exception as e:
        logger.error(f"Error analyzing file {file_id} for future events: {e}")
        return []


def create_similar_files_notification_if_enabled(
    file_id: int,
    file_name: str,
    file_path: str,
    similar_files: List[Dict],
    similarity_threshold: float = 0.8
) -> Optional[Dict]:
    """
    Create similar files notification if enabled in settings.
    Returns notification dict or None.
    """
    if not should_create_notifications():
        return None
    
    try:
        settings = get_settings()
        similar_files_enabled = settings.get_setting('notifications.similar_files_enabled', True)
        
        if not similar_files_enabled or not similar_files:
            return None
        
        notification_service = get_notification_service()
        notification = notification_service.create_similar_files_notification(
            file_id=file_id,
            file_name=file_name,
            file_path=file_path,
            similar_files=similar_files,
            similarity_threshold=similarity_threshold
        )
        
        return {
            'id': notification.id,
            'type': notification.type.value,
            'title': notification.title,
            'message': notification.message
        }
    
    except Exception as e:
        logger.error(f"Error creating similar files notification: {e}")
        return None

