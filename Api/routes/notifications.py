"""
Notification API Routes
System-wide notification management endpoints
"""

from flask import request, jsonify
from flask_babel import gettext as _
from datetime import date, datetime, timedelta
from core.monitoring.notification_service import (
    get_notification_service,
    NotificationType,
    NotificationPriority
)
from Api.utils import execute_query
import logging
from core.errors import client_error, client_safe_message

logger = logging.getLogger(__name__)


def register_notification_routes(app):
    """Register notification routes with the Flask app"""
    
    @app.route('/api/notifications', methods=['GET'])
    def get_notifications():
        """Get notifications with optional filters"""
        try:
            notification_service = get_notification_service()
            
            # 🔔 OPTIMIZATION: Flush pending notifications when accessed (ensures they're persisted)
            pending_count = notification_service.get_pending_count()
            if pending_count > 0:
                notification_service.flush_pending_notifications()
            
            # Get query parameters
            notification_type = request.args.get('type')
            priority = request.args.get('priority')
            unread_only = request.args.get('unread_only', 'false').lower() == 'true'
            limit = request.args.get('limit', 100, type=int)
            
            # Convert string to enum if provided
            type_enum = None
            if notification_type:
                try:
                    type_enum = NotificationType(notification_type)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid notification type: {notification_type}'
                    }), 400
            
            priority_enum = None
            if priority:
                try:
                    priority_enum = NotificationPriority(priority)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': f'Invalid priority: {priority}'
                    }), 400
            
            # Get notifications
            notifications = notification_service.get_notifications(
                notification_type=type_enum,
                priority=priority_enum,
                unread_only=unread_only,
                limit=limit
            )
            
            # Convert to JSON-serializable format with translation
            notifications_data = []
            for n in notifications:
                # Translate title and message for all notification types
                title = n.title
                message = n.message
                
                if n.type == NotificationType.SIMILAR_FILES:
                    # Translate similar files notifications
                    if n.file_name:
                        title = _('Similar Files Detected: %(file_name)s', file_name=n.file_name)
                    else:
                        title = _('Similar Files Detected')
                    similar_count = n.metadata.get('similar_count', 0) if n.metadata else 0
                    similarity_threshold = n.metadata.get('similarity_threshold', 0.8) if n.metadata else 0.8
                    threshold_percent = int(similarity_threshold * 100)
                    message = _('Found %(count)s similar file(s) with similarity ≥ %(threshold)s%%', 
                               count=similar_count, threshold=threshold_percent)
                
                elif n.type == NotificationType.FUTURE_EVENT:
                    # Translate future event notifications
                    if n.event_date:
                        date_str = n.event_date.strftime('%Y-%m-%d')
                        title = _('Future Event Detected: %(date)s', date=date_str)
                    else:
                        title = _('Future Event Detected')
                    if n.file_name:
                        message = _('Future-focused content found in %(file_name)s', file_name=n.file_name)
                    else:
                        message = _('Future-focused content found')
                
                elif n.type == NotificationType.FUTURE_DATE:
                    # Translate future date notifications
                    if n.event_date:
                        date_str = n.event_date.strftime('%Y-%m-%d')
                        title = _('Future Date Detected: %(date)s', date=date_str)
                        days_until = n.metadata.get('days_until', 0) if n.metadata else 0
                        if n.file_name:
                            if days_until > 0:
                                message = _('Future date found in %(file_name)s (%(days)s days away)', 
                                           file_name=n.file_name, days=days_until)
                            else:
                                message = _('Future date found in %(file_name)s', file_name=n.file_name)
                        else:
                            if days_until > 0:
                                message = _('Future date found (%(days)s days away)', days=days_until)
                            else:
                                message = _('Future date found')
                    else:
                        title = _('Future Date Detected')
                        message = _('Future date found')
                
                elif n.type == NotificationType.PROCESSING_COMPLETE:
                    # Translate processing complete notifications
                    title = _('Processing Complete')
                    if n.file_name:
                        message = _('File processing completed: %(file_name)s', file_name=n.file_name)
                    else:
                        message = _('File processing completed')
                
                elif n.type == NotificationType.BATCH_COMPLETE:
                    # Translate batch complete notifications
                    title = _('Batch Processing Complete')
                    batch_count = n.metadata.get('batch_count', 0) if n.metadata else 0
                    if batch_count > 0:
                        message = _('Batch processing completed: %(count)s file(s) processed', count=batch_count)
                    else:
                        message = _('Batch processing completed')
                
                elif n.type == NotificationType.ERROR:
                    # Translate error notifications
                    if not title or title == n.message:
                        title = _('Error')
                    if n.file_name:
                        message = _('Error in %(file_name)s: %(error)s', 
                                   file_name=n.file_name, error=n.message)
                    else:
                        message = n.message  # Keep original error message
                
                elif n.type == NotificationType.WARNING:
                    # Translate warning notifications
                    if not title or title == n.message:
                        title = _('Warning')
                    message = n.message  # Keep original warning message
                
                elif n.type == NotificationType.INFO:
                    # Translate info notifications
                    if not title or title == n.message:
                        title = _('Information')
                    message = n.message  # Keep original info message
                
                notifications_data.append({
                    'id': n.id,
                    'type': n.type.value,
                    'priority': n.priority.value,
                    'title': title,
                    'message': message,
                    'file_id': n.file_id,
                    'file_name': n.file_name,
                    'file_path': n.file_path,
                    'event_date': n.event_date.isoformat() if n.event_date else None,
                    'metadata': n.metadata,
                    'created_at': n.created_at.isoformat(),
                    'read': n.read,
                    'dismissed': n.dismissed
                })
            
            return jsonify({
                'success': True,
                'notifications': notifications_data,
                'count': len(notifications_data)
            })
        
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/upcoming', methods=['GET'])
    def get_upcoming_events():
        """Get upcoming events within specified days"""
        try:
            notification_service = get_notification_service()
            days_ahead = request.args.get('days', 30, type=int)
            
            upcoming = notification_service.get_upcoming_events(days_ahead=days_ahead)
            
            events_data = []
            for n in upcoming:
                # Translate title and message for all notification types
                title = n.title
                message = n.message
                
                if n.type == NotificationType.SIMILAR_FILES:
                    if n.file_name:
                        title = _('Similar Files Detected: %(file_name)s', file_name=n.file_name)
                    else:
                        title = _('Similar Files Detected')
                    similar_count = n.metadata.get('similar_count', 0) if n.metadata else 0
                    similarity_threshold = n.metadata.get('similarity_threshold', 0.8) if n.metadata else 0.8
                    threshold_percent = int(similarity_threshold * 100)
                    message = _('Found %(count)s similar file(s) with similarity ≥ %(threshold)s%%', 
                               count=similar_count, threshold=threshold_percent)
                
                elif n.type == NotificationType.FUTURE_EVENT:
                    if n.event_date:
                        date_str = n.event_date.strftime('%Y-%m-%d')
                        title = _('Future Event Detected: %(date)s', date=date_str)
                    else:
                        title = _('Future Event Detected')
                    if n.file_name:
                        message = _('Future-focused content found in %(file_name)s', file_name=n.file_name)
                    else:
                        message = _('Future-focused content found')
                
                elif n.type == NotificationType.FUTURE_DATE:
                    if n.event_date:
                        date_str = n.event_date.strftime('%Y-%m-%d')
                        title = _('Future Date Detected: %(date)s', date=date_str)
                        days_until = n.metadata.get('days_until', 0) if n.metadata else 0
                        if n.file_name:
                            if days_until > 0:
                                message = _('Future date found in %(file_name)s (%(days)s days away)', 
                                           file_name=n.file_name, days=days_until)
                            else:
                                message = _('Future date found in %(file_name)s', file_name=n.file_name)
                        else:
                            if days_until > 0:
                                message = _('Future date found (%(days)s days away)', days=days_until)
                            else:
                                message = _('Future date found')
                    else:
                        title = _('Future Date Detected')
                        message = _('Future date found')
                
                elif n.type == NotificationType.PROCESSING_COMPLETE:
                    title = _('Processing Complete')
                    if n.file_name:
                        message = _('File processing completed: %(file_name)s', file_name=n.file_name)
                    else:
                        message = _('File processing completed')
                
                elif n.type == NotificationType.BATCH_COMPLETE:
                    title = _('Batch Processing Complete')
                    batch_count = n.metadata.get('batch_count', 0) if n.metadata else 0
                    if batch_count > 0:
                        message = _('Batch processing completed: %(count)s file(s) processed', count=batch_count)
                    else:
                        message = _('Batch processing completed')
                
                elif n.type == NotificationType.ERROR:
                    if not title or title == n.message:
                        title = _('Error')
                    if n.file_name:
                        message = _('Error in %(file_name)s: %(error)s', 
                                   file_name=n.file_name, error=n.message)
                    else:
                        message = n.message
                
                elif n.type == NotificationType.WARNING:
                    if not title or title == n.message:
                        title = _('Warning')
                    message = n.message
                
                elif n.type == NotificationType.INFO:
                    if not title or title == n.message:
                        title = _('Information')
                    message = n.message
                
                events_data.append({
                    'id': n.id,
                    'title': title,
                    'message': message,
                    'file_id': n.file_id,
                    'file_name': n.file_name,
                    'file_path': n.file_path,
                    'event_date': n.event_date.isoformat() if n.event_date else None,
                    'days_until': (n.event_date - date.today()).days if n.event_date else None,
                    'metadata': n.metadata,
                    'created_at': n.created_at.isoformat()
                })
            
            return jsonify({
                'success': True,
                'events': events_data,
                'count': len(events_data)
            })
        
        except Exception as e:
            logger.error(f"Error getting upcoming events: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>', methods=['GET'])
    def get_notification(notification_id):
        """Get a single notification by ID"""
        try:
            notification_service = get_notification_service()
            notification_service.refresh_notifications()
            
            # Get all notifications and find the one with matching ID
            all_notifications = notification_service.get_notifications(limit=10000)
            notification = next((n for n in all_notifications if n.id == notification_id), None)
            
            if not notification:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404
            
            # Translate title and message for all notification types
            title = notification.title
            message = notification.message
            
            if notification.type == NotificationType.SIMILAR_FILES:
                if notification.file_name:
                    title = _('Similar Files Detected: %(file_name)s', file_name=notification.file_name)
                else:
                    title = _('Similar Files Detected')
                similar_count = notification.metadata.get('similar_count', 0) if notification.metadata else 0
                similarity_threshold = notification.metadata.get('similarity_threshold', 0.8) if notification.metadata else 0.8
                threshold_percent = int(similarity_threshold * 100)
                message = _('Found %(count)s similar file(s) with similarity ≥ %(threshold)s%%', 
                           count=similar_count, threshold=threshold_percent)
            
            elif notification.type == NotificationType.FUTURE_EVENT:
                if notification.event_date:
                    date_str = notification.event_date.strftime('%Y-%m-%d')
                    title = _('Future Event Detected: %(date)s', date=date_str)
                else:
                    title = _('Future Event Detected')
                if notification.file_name:
                    message = _('Future-focused content found in %(file_name)s', file_name=notification.file_name)
                else:
                    message = _('Future-focused content found')
            
            elif notification.type == NotificationType.FUTURE_DATE:
                if notification.event_date:
                    date_str = notification.event_date.strftime('%Y-%m-%d')
                    title = _('Future Date Detected: %(date)s', date=date_str)
                    days_until = notification.metadata.get('days_until', 0) if notification.metadata else 0
                    if notification.file_name:
                        if days_until > 0:
                            message = _('Future date found in %(file_name)s (%(days)s days away)', 
                                       file_name=notification.file_name, days=days_until)
                        else:
                            message = _('Future date found in %(file_name)s', file_name=notification.file_name)
                    else:
                        if days_until > 0:
                            message = _('Future date found (%(days)s days away)', days=days_until)
                        else:
                            message = _('Future date found')
                else:
                    title = _('Future Date Detected')
                    message = _('Future date found')
            
            elif notification.type == NotificationType.PROCESSING_COMPLETE:
                title = _('Processing Complete')
                if notification.file_name:
                    message = _('File processing completed: %(file_name)s', file_name=notification.file_name)
                else:
                    message = _('File processing completed')
            
            elif notification.type == NotificationType.BATCH_COMPLETE:
                title = _('Batch Processing Complete')
                batch_count = notification.metadata.get('batch_count', 0) if notification.metadata else 0
                if batch_count > 0:
                    message = _('Batch processing completed: %(count)s file(s) processed', count=batch_count)
                else:
                    message = _('Batch processing completed')
            
            elif notification.type == NotificationType.ERROR:
                if not title or title == notification.message:
                    title = _('Error')
                if notification.file_name:
                    message = _('Error in %(file_name)s: %(error)s', 
                               file_name=notification.file_name, error=notification.message)
                else:
                    message = notification.message
            
            elif notification.type == NotificationType.WARNING:
                if not title or title == notification.message:
                    title = _('Warning')
                message = notification.message
            
            elif notification.type == NotificationType.INFO:
                if not title or title == notification.message:
                    title = _('Information')
                message = notification.message
            
            return jsonify({
                'success': True,
                'notification': {
                    'id': notification.id,
                    'type': notification.type.value,
                    'priority': notification.priority.value,
                    'title': title,
                    'message': message,
                    'file_id': notification.file_id,
                    'file_name': notification.file_name,
                    'file_path': notification.file_path,
                    'event_date': notification.event_date.isoformat() if notification.event_date else None,
                    'metadata': notification.metadata,
                    'created_at': notification.created_at.isoformat(),
                    'read': notification.read,
                    'dismissed': notification.dismissed
                }
            })
        
        except Exception as e:
            logger.error(f"Error getting notification: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
    def mark_notification_read(notification_id):
        """Mark notification as read"""
        try:
            notification_service = get_notification_service()
            success = notification_service.mark_as_read(notification_id)
            
            if success:
                return jsonify({
                    'success': True,
                    'message': 'Notification marked as read'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404
        
        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/<int:notification_id>/dismiss', methods=['POST'])
    def dismiss_notification(notification_id):
        """Dismiss a notification"""
        try:
            notification_service = get_notification_service()
            success = notification_service.dismiss_notification(notification_id)
            
            if success:
                return jsonify({
                    'success': True,
                    'message': 'Notification dismissed'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404
        
        except Exception as e:
            logger.error(f"Error dismissing notification: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/analyze-file/<int:file_id>', methods=['POST'])
    def analyze_file_for_events(file_id):
        """Analyze a file for future events and create notifications"""
        try:
            from Api.utils import load_text_content
            notification_service = get_notification_service()
            
            # Get file info
            file_info = execute_query(
                """
                SELECT id, file_name, file_path
                FROM paths
                WHERE id = %s
                """,
                (file_id,),
                fetch="one"
            )
            
            if not file_info:
                return jsonify({
                    'success': False,
                    'error': 'File not found'
                }), 404
            
            file_id_db, file_name, file_path = file_info
            
            # Load file content
            content = load_text_content(file_id)
            
            if not content:
                return jsonify({
                    'success': False,
                    'error': 'File content not available'
                }), 400
            
            # Analyze for future events
            notifications = notification_service.analyze_file_for_future_events(
                file_id=file_id_db,
                file_name=file_name,
                file_path=file_path,
                content=content
            )
            
            return jsonify({
                'success': True,
                'notifications_created': len(notifications),
                'notifications': [
                    {
                        'id': n.id,
                        'type': n.type.value,
                        'title': n.title,
                        'event_date': n.event_date.isoformat() if n.event_date else None
                    }
                    for n in notifications
                ]
            })
        
        except Exception as e:
            logger.error(f"Error analyzing file for events: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/refresh', methods=['POST'])
    def refresh_notifications():
        """Refresh notifications from database"""
        try:
            notification_service = get_notification_service()
            notification_service.refresh_notifications()
            
            return jsonify({
                'success': True,
                'message': 'Notifications refreshed successfully',
                'count': len(notification_service.get_notifications(limit=10000))
            })
        except Exception as e:
            logger.error(f"Error refreshing notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/stats', methods=['GET'])
    def get_notification_stats():
        """Get notification statistics"""
        try:
            notification_service = get_notification_service()
            
            # Refresh to ensure we have latest data
            notification_service.refresh_notifications()
            
            all_notifications = notification_service.get_notifications(limit=10000)
            
            stats = {
                'total': len(all_notifications),
                'unread': len([n for n in all_notifications if not n.read]),
                'by_type': {},
                'by_priority': {},
                'upcoming_events': len(notification_service.get_upcoming_events(days_ahead=30))
            }
            
            # Count by type
            for n in all_notifications:
                type_val = n.type.value
                stats['by_type'][type_val] = stats['by_type'].get(type_val, 0) + 1
            
            # Count by priority
            for n in all_notifications:
                priority_val = n.priority.value
                stats['by_priority'][priority_val] = stats['by_priority'].get(priority_val, 0) + 1
            
            return jsonify({
                'success': True,
                'stats': stats
            })
        
        except Exception as e:
            logger.error(f"Error getting notification stats: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    @app.route('/api/notifications/scan', methods=['POST'])
    def scan_for_notifications():
        """Scan for duplicate files and files with future dates, then create notifications"""
        try:
            from core.monitoring.notification_service import (
                get_notification_service,
                NotificationType,
                NotificationPriority
            )
            from datetime import date, datetime
            import json
            
            notification_service = get_notification_service()
            notifications_created = []
            
            # 1. Find duplicate files (same hash, different paths)
            logger.info("Scanning for duplicate files...")
            # First, find hashes with multiple files (group by hash only)
            duplicate_query = """
                SELECT 
                    h.hash,
                    COUNT(DISTINCT p.id) as file_count,
                    MIN(p.id) as primary_file_id
                FROM hashs h
                JOIN hash_contexts hc ON hc.hash_id = h.id
                JOIN paths p ON p.context_id = hc.id
                GROUP BY h.hash
                HAVING COUNT(DISTINCT p.id) > 1
                ORDER BY file_count DESC
                LIMIT 1000
            """
            
            duplicate_results = execute_query(duplicate_query, None, fetch="all")
            
            duplicate_count = 0
            for row in duplicate_results or []:
                hash_value, file_count, primary_file_id = row
                
                # Get all files with this hash
                files_query = """
                    SELECT p.id, p.file_name, p.file_path
                    FROM paths p
                    INNER JOIN hash_contexts hc ON p.context_id = hc.id JOIN hashs h ON hc.hash_id = h.id
                    WHERE h.hash = %s
                    ORDER BY p.id ASC
                """
                files_result = execute_query(files_query, (hash_value,), fetch="all")
                
                if not files_result or len(files_result) < 2:
                    continue
                
                # Extract file information
                file_ids = [f[0] for f in files_result]
                file_names = [f[1] or 'Unknown' for f in files_result]
                file_paths = [f[2] or '' for f in files_result]
                
                # Use the first file as the primary reference
                primary_file_name = file_names[0] if file_names else 'Unknown'
                primary_file_path = file_paths[0] if file_paths else ''
                
                # Check if notification already exists for this hash
                existing = execute_query(
                    """
                    SELECT id FROM alerts 
                    WHERE type = %s 
                    AND metadata->>'hash' = %s
                    AND dismissed = FALSE
                    LIMIT 1
                    """,
                    (NotificationType.SIMILAR_FILES.value, hash_value),
                    fetch="one"
                )
                
                if not existing:
                    from core.monitoring.notification_service import Notification
                    
                    # Create a readable message with file names
                    file_names_display = file_names[:5]
                    if len(file_names) > 5:
                        file_names_display.append(f'... and {len(file_names) - 5} more')
                    
                    notification = Notification(
                        id=None,
                        type=NotificationType.SIMILAR_FILES,
                        priority=NotificationPriority.MEDIUM,
                        title=f"Duplicate Files Detected: {primary_file_name}",
                        message=f"Found {file_count} duplicate file(s) with the same hash. Files: {', '.join(file_names_display)}",
                        file_id=primary_file_id,
                        file_name=primary_file_name,
                        file_path=primary_file_path,
                        event_date=None,
                        metadata={
                            'hash': hash_value,
                            'duplicate_count': file_count,
                            'file_ids': file_ids,
                            'file_names': file_names,
                            'file_paths': file_paths
                        },
                        created_at=datetime.now()
                    )
                    notification = notification_service._save_notification(notification)
                    notifications_created.append({
                        'type': 'duplicate',
                        'id': notification.id,
                        'title': notification.title
                    })
                    duplicate_count += 1
            
            # 2. Find files with future dates in content
            logger.info("Scanning for files with future dates...")
            from Api.utils import load_text_content
            from core.monitoring.future_events import FutureEventsAnalyzer
            
            future_analyzer = FutureEventsAnalyzer()
            today = date.today()
            
            # Get all files with content (m0011: contents is keyed by hash,
            # reached through the path's content identity context)
            files_query = """
                SELECT DISTINCT p.id, p.file_name, p.file_path, c.id as content_id
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                INNER JOIN contents c ON c.hash_id = hc.hash_id
                WHERE p.file_status = 'Read'
                ORDER BY p.id DESC
                LIMIT 5000
            """
            
            files_with_content = execute_query(files_query, None, fetch="all")
            
            future_date_count = 0
            processed_files = 0
            
            for row in files_with_content or []:
                file_id, file_name, file_path, content_id = row
                processed_files += 1
                
                # Load content
                try:
                    content = load_text_content(file_id)
                    if not content:
                        continue
                    
                    # Extract all dates from content
                    dates_found = future_analyzer.extract_all_dates(content)
                    
                    # Check for future dates
                    future_dates = [(d, ctx, pos) for d, ctx, pos in dates_found if d > today]
                    
                    if future_dates:
                        # Get the earliest future date
                        future_dates.sort(key=lambda x: x[0])
                        earliest_date, context, position = future_dates[0]
                        days_until = (earliest_date - today).days
                        
                        # Check if notification already exists for this file and date
                        existing = execute_query(
                            """
                            SELECT id FROM alerts 
                            WHERE type = %s 
                            AND file_id = %s
                            AND event_date = %s
                            AND dismissed = FALSE
                            LIMIT 1
                            """,
                            (NotificationType.FUTURE_DATE.value, file_id, earliest_date),
                            fetch="one"
                        )
                        
                        if not existing:
                            from core.monitoring.notification_service import Notification
                            notification = Notification(
                                id=None,
                                type=NotificationType.FUTURE_DATE,
                                priority=NotificationPriority.HIGH if days_until <= 30 else NotificationPriority.MEDIUM,
                                title=f"Future Date Detected: {earliest_date.strftime('%Y-%m-%d')}",
                                message=f"Future date found in {file_name} ({days_until} days away). Context: {context[:100]}...",
                                file_id=file_id,
                                file_name=file_name,
                                file_path=file_path,
                                event_date=earliest_date,
                                metadata={
                                    'days_until': days_until,
                                    'future_dates': [d.isoformat() for d, _, _ in future_dates],
                                    'context': context[:200],
                                    'position': position
                                },
                                created_at=datetime.now()
                            )
                            notification = notification_service._save_notification(notification)
                            notifications_created.append({
                                'type': 'future_date',
                                'id': notification.id,
                                'title': notification.title,
                                'date': earliest_date.isoformat()
                            })
                            future_date_count += 1
                
                except Exception as e:
                    logger.debug(f"Error processing file {file_id} for future dates: {e}")
                    continue
            
            # Flush pending notifications
            notification_service.flush_pending_notifications()
            
            # Refresh notifications cache to ensure new notifications are available
            notification_service.refresh_notifications()
            
            logger.info(f"Scan completed: {duplicate_count} duplicates, {future_date_count} future dates, {processed_files} files processed")
            
            return jsonify({
                'success': True,
                'message': f'Scan completed. Created {len(notifications_created)} new notification(s)',
                'duplicates_found': duplicate_count,
                'future_dates_found': future_date_count,
                'files_processed': processed_files,
                'notifications_created': notifications_created
            })
        
        except Exception as e:
            logger.error(f"Error scanning for notifications: {e}", exc_info=True)
            return jsonify({
                'success': False, 
                'error': client_safe_message(e, subsystem='Api.routes.notifications'),
                'message': f'Error during scan: {str(e)}'
            }), 500

