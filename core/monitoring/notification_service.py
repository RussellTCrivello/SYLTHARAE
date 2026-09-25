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

from .future_events import FutureEvent, FutureEventsAnalyzer

logger = logging.getLogger(__name__)


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
        self.future_analyzer = FutureEventsAnalyzer()
        self._notifications: List[Notification] = []
        #  OPTIMIZATION: Pending notifications queue for batch writes (no DB queries during processing)
        self._pending_notifications: List[Notification] = []
        self._pending_lock = threading.Lock()
        self._next_temp_id = -1  # Temporary IDs for pending notifications (negative to avoid conflicts)
        self._load_notifications()
    
    def _load_notifications(self):
        """Load notifications from database"""
        try:
            # Use execute_query from Api.utils (best practice - single source of truth)
            from Api.utils import execute_query
            
            # Load notifications from database (table should be created by schema.py)
            notifications = execute_query(
                """
                SELECT id, type, priority, title, message, file_id, file_name, 
                       file_path, event_date, metadata, created_at, read, dismissed
                FROM alerts
                WHERE dismissed = FALSE
                ORDER BY created_at DESC
                LIMIT 1000
                """,
                fetch="all"
            )
            
            if notifications:
                for row in notifications:
                    try:
                        # Handle metadata - it might already be a dict (from JSONB column) or a JSON string
                        metadata = {}
                        if row[9]:
                            if isinstance(row[9], dict):
                                metadata = row[9]
                            elif isinstance(row[9], str):
                                try:
                                    metadata = json.loads(row[9])
                                except (json.JSONDecodeError, TypeError):
                                    metadata = {}
                            else:
                                metadata = {}
                        
                        notification = Notification(
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
                            read=row[11] or False,
                            dismissed=row[12] or False
                        )
                        self._notifications.append(notification)
                    except Exception as e:
                        self.logger.warning(f"Error loading notification: {e}")
        except Exception as e:
            self.logger.error(f"Error loading notifications: {e}")
    
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
    
    def create_future_date_notification(
        self,
        future_event: FutureEvent
    ) -> Notification:
        """Create notification for future date detected"""
        days_until = (future_event.event_date - date.today()).days
        
        priority = NotificationPriority.HIGH
        if days_until <= 7:
            priority = NotificationPriority.CRITICAL
        elif days_until <= 30:
            priority = NotificationPriority.HIGH
        elif days_until <= 90:
            priority = NotificationPriority.MEDIUM
        
        notification = Notification(
            id=None,
            type=NotificationType.FUTURE_DATE,
            priority=priority,
            title=f"Future Date Detected: {future_event.event_date.strftime('%Y-%m-%d')}",
            message=f"Future date found in {future_event.file_name} ({days_until} days away)",
            file_id=future_event.file_id,
            file_name=future_event.file_name,
            file_path=future_event.file_path,
            event_date=future_event.event_date,
            metadata={
                'event_text': future_event.event_text,
                'context': future_event.context,
                'confidence': future_event.confidence,
                'event_type': future_event.event_type,
                'days_until': days_until
            },
            created_at=datetime.now()
        )
        
        return self._save_notification(notification)
    
    def create_future_event_notification(
        self,
        future_event: FutureEvent
    ) -> Notification:
        """Create notification for future event (verb tense analysis)"""
        days_until = (future_event.event_date - date.today()).days
        
        notification = Notification(
            id=None,
            type=NotificationType.FUTURE_EVENT,
            priority=NotificationPriority.MEDIUM,
            title=f"Future Event Detected: {future_event.event_date.strftime('%Y-%m-%d')}",
            message=f"Future-focused content found in {future_event.file_name}",
            file_id=future_event.file_id,
            file_name=future_event.file_name,
            file_path=future_event.file_path,
            event_date=future_event.event_date,
            metadata={
                'event_text': future_event.event_text,
                'context': future_event.context,
                'confidence': future_event.confidence,
                'event_type': future_event.event_type,
                'days_until': days_until
            },
            created_at=datetime.now()
        )
        
        return self._save_notification(notification)
    
    def analyze_file_for_future_events(
        self,
        file_id: int,
        file_name: str,
        file_path: str,
        content: str
    ) -> List[Notification]:
        """Analyze file content and create notifications for future events"""
        notifications = []
        
        try:
            # Analyze content for future events
            future_events = self.future_analyzer.analyze_content_for_future_events(
                content, file_id, file_name, file_path
            )
            
            # Create notifications for each future event
            for event in future_events:
                if event.event_type == 'explicit_date':
                    notification = self.create_future_date_notification(event)
                else:
                    notification = self.create_future_event_notification(event)
                
                notifications.append(notification)
        
        except Exception as e:
            self.logger.error(f"Error analyzing file {file_id} for future events: {e}")
        
        return notifications
    
    def _save_notification(self, notification: Notification) -> Notification:
        """
        Save notification to memory queue (no database query during processing).
        Notifications are batched and written to database later via flush_pending().
        """
        # Assign temporary ID for pending notification
        with self._pending_lock:
            notification.id = self._next_temp_id
            self._next_temp_id -= 1
            
            # Add to pending queue (will be written to DB in batch)
            self._pending_notifications.append(notification)
            
            # Also add to in-memory list immediately (for immediate access)
            self._notifications.insert(0, notification)
            
            self.logger.debug(f"Queued notification: {notification.title} (temp_id: {notification.id})")
        
        return notification
    
    def flush_pending_notifications(self, batch_size: int = 100) -> int:
        """
        Flush pending notifications to database in batches.
        This should be called periodically or after processing completes.
        
        Args:
            batch_size: Number of notifications to write per batch
            
        Returns:
            Number of notifications successfully written
        """
        if not self._pending_notifications:
            return 0
        
        written_count = 0
        
        with self._pending_lock:
            # Process in batches to avoid large transactions
            while self._pending_notifications:
                batch = self._pending_notifications[:batch_size]
                self._pending_notifications = self._pending_notifications[batch_size:]
                
                try:
                    # Use execute_query from Api.utils or database.queries
                    try:
                        from Api.utils import execute_query
                    except ImportError:
                        from database import DatabaseHub
                        db_hub = DatabaseHub()
                        def execute_query(query, params=None, fetch="all"):
                            # Use context manager for connection
                            with db_hub._get_connection() as conn:
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
                    
                    # Bulk insert using COPY or multiple VALUES for better performance
                    insert_query = """
                        INSERT INTO alerts (type, priority, title, message, file_id, file_name, 
                                           file_path, event_date, metadata, created_at, read, dismissed)
                        VALUES %s
                        RETURNING id, created_at
                    """
                    
                    # Prepare batch data
                    values_list = []
                    for notif in batch:
                        values_list.append((
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
                            notif.dismissed
                        ))
                    
                    # Execute batch insert
                    # Note: psycopg2.extras.execute_values is more efficient, but we'll use simple approach
                    # For now, insert one by one but in a transaction
                    for i, notif in enumerate(batch):
                        try:
                            result = execute_query(
                                """
                                INSERT INTO alerts (type, priority, title, message, file_id, file_name, 
                                                   file_path, event_date, metadata, created_at, read, dismissed)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                                """,
                                (
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
                                    notif.dismissed
                                ),
                                fetch="one"
                            )
                            
                            if result:
                                # Update notification with real ID
                                real_id = result[0]
                                notif.id = real_id
                                
                                # Update in main notifications list (match by temp_id and created_at)
                                for n in self._notifications:
                                    if n.id == notif.id and n.created_at == notif.created_at:
                                        n.id = real_id
                                        break
                                
                                written_count += 1
                                self.logger.debug(f"Flushed notification: {notif.title} (id: {real_id})")
                        
                        except Exception as e:
                            self.logger.error(f"Error flushing notification {notif.title}: {e}")
                            # Keep notification in pending queue for retry
                            continue
                
                except Exception as e:
                    self.logger.error(f"Error flushing notification batch: {e}")
                    # Put batch back in queue for retry
                    self._pending_notifications = batch + self._pending_notifications
                    break
        
        if written_count > 0:
            self.logger.info(f"✅ Flushed {written_count} notification(s) to database")
        
        return written_count
    
    def get_pending_count(self) -> int:
        """Get count of pending notifications waiting to be written to database"""
        with self._pending_lock:
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
        all_notifications = self._notifications.copy()
        
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
            
            execute_query(
                "UPDATE alerts SET read = TRUE WHERE id = %s",
                (notification_id,),
                fetch=None
            )
            
            # Update in memory
            for notification in self._notifications:
                if notification.id == notification_id:
                    notification.read = True
                    self.logger.debug(f"Marked notification {notification_id} as read")
                    return True
            
            # If not found in memory, refresh from database
            self.logger.debug(f"Notification {notification_id} not in memory, refreshing...")
            self.refresh_notifications()
            
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
            
            execute_query(
                "UPDATE alerts SET dismissed = TRUE WHERE id = %s",
                (notification_id,),
                fetch=None
            )
            
            # Update in memory
            for notification in self._notifications:
                if notification.id == notification_id:
                    notification.dismissed = True
                    self.logger.debug(f"Dismissed notification {notification_id}")
                    return True
            
            # If not found in memory, refresh from database
            self.logger.debug(f"Notification {notification_id} not in memory, refreshing...")
            self.refresh_notifications()
            
        except Exception as e:
            self.logger.error(f"Error dismissing notification: {e}")
            return False
        
        return False
    
    def refresh_notifications(self):
        """Reload notifications from database (useful after external changes)"""
        self._notifications.clear()
        self._load_notifications()
        self.logger.info(f"Refreshed notifications: {len(self._notifications)} loaded")
    
    def get_upcoming_events(self, days_ahead: int = 30) -> List[Notification]:
        """Get upcoming events within specified days"""
        today = date.today()
        end_date = today + timedelta(days=days_ahead)
        
        notifications = self.get_notifications(
            notification_type=NotificationType.FUTURE_DATE,
            unread_only=False
        )
        
        upcoming = [
            n for n in notifications
            if n.event_date and today <= n.event_date <= end_date
        ]
        
        return sorted(upcoming, key=lambda x: x.event_date or date.max)


def get_notification_service() -> NotificationService:
    """Get singleton instance of NotificationService"""
    if not hasattr(get_notification_service, '_instance'):
        get_notification_service._instance = NotificationService()
    return get_notification_service._instance

