"""
Notifications Page Route
Displays notifications in a table view with pagination, search, and filtering
"""

from flask import render_template, request, jsonify
from flask_babel import gettext as _
from core.monitoring.notification_service import get_notification_service, NotificationType
from Api.utils import select_info_sources, select_info_sides, execute_query, get_file
import logging
from core.errors import client_error

logger = logging.getLogger(__name__)


def register_notification_page_routes(app):
    """Register notification page routes"""
    
    @app.route('/notifications')
    def notifications_page():
        """Notifications page with table view"""
        try:
            # Get sources and sides for filtering
            sources = select_info_sources()
            sides = select_info_sides()
            
            # Get statistics
            notification_service = get_notification_service()
            notification_service.refresh_notifications()
            
            # Get all notifications for stats
            all_notifications = notification_service.get_notifications(limit=10000)
            unread_notifications = [n for n in all_notifications if not n.read]
            
            # Count by type for duplicates and future files
            duplicates_count = len([n for n in all_notifications if n.type.value == 'similar_files'])
            future_count = len([n for n in all_notifications if n.type.value in ['future_date', 'future_event']])
            
            stats = {
                'total': len(all_notifications),
                'unread': len(unread_notifications),
                'by_type': {},
                'by_priority': {}
            }
            
            for n in all_notifications:
                stats['by_type'][n.type.value] = stats['by_type'].get(n.type.value, 0) + 1
                stats['by_priority'][n.priority.value] = stats['by_priority'].get(n.priority.value, 0) + 1
            
            return render_template(
                'Notifications/notifications.html',
                sources=sources,
                sides=sides,
                stats=stats
            )
        except Exception as e:
            logger.error(f"Error loading notifications page: {e}")
            return render_template(
                'Notifications/notifications.html',
                sources={},
                sides={},
                stats={'total': 0, 'unread': 0, 'by_type': {}, 'by_priority': {}}
            )
    
    @app.route('/api/notifications/paginated', methods=['GET'])
    def get_paginated_notifications():
        """Get paginated notifications with search and filtering"""
        try:
            notification_service = get_notification_service()
            notification_service.refresh_notifications()
            
            # Get query parameters
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            search = request.args.get('search', '').strip()
            source_id = request.args.get('source_id', type=int)
            side_id = request.args.get('side_id', type=int)
            notification_type = request.args.get('type', '')
            show_read = request.args.get('show_read', 'false').lower() == 'true'
            sort_by = request.args.get('sort_by', 'created_at')  # created_at, priority, title
            sort_order = request.args.get('sort_order', 'desc')  # asc, desc
            
            # Get all notifications directly from database to include dismissed ones
            # We need to query the database directly to get all notifications including dismissed
            
            # Build query to get all notifications
            query = """
                SELECT id, type, priority, title, message, file_id, file_name, file_path, 
                       event_date, metadata, created_at, read, dismissed
                FROM alerts
                WHERE 1=1
            """
            params = []
            
            # Filter by type if specified
            if notification_type:
                type_list = [t.strip() for t in notification_type.split(',')]
                placeholders = ','.join(['%s'] * len(type_list))
                query += f" AND type IN ({placeholders})"
                params.extend(type_list)
            
            # Filter by read/unread status
            # When show_read is True, we get all notifications (both read and unread)
            # When show_read is False, we only get unread notifications
            if not show_read:
                query += " AND read = FALSE"
            
            # Filter out dismissed notifications - we don't want to show dismissed ones
            query += " AND dismissed = FALSE"
            
            query += " ORDER BY created_at DESC LIMIT 10000"
            
            rows = execute_query(query, tuple(params), fetch="all")
            
            logger.info(f"Found {len(rows) if rows else 0} notification rows from database (type filter: {notification_type}, show_read: {show_read})")
            
            # Convert rows to Notification objects
            notifications = []
            for row in rows:
                try:
                    from core.monitoring.notification_service import Notification, NotificationType, NotificationPriority
                    import json
                    from datetime import datetime, date
                    
                    # Handle metadata
                    metadata = {}
                    if row[9]:
                        if isinstance(row[9], dict):
                            metadata = row[9]
                        elif isinstance(row[9], str):
                            try:
                                metadata = json.loads(row[9])
                            except (json.JSONDecodeError, TypeError):
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
                    notifications.append(notification)
                except Exception as e:
                    logger.warning(f"Error converting notification row: {e}")
                    continue
            
            # Apply search filter
            if search:
                search_lower = search.lower()
                notifications = [
                    n for n in notifications
                    if search_lower in n.title.lower() or search_lower in n.message.lower() or
                    (n.file_name and search_lower in n.file_name.lower())
                ]
            
            # Filter by source/side from metadata or file_id
            if source_id or side_id:
                filtered = []
                # Get file source/side info from database if needed
                
                for n in notifications:
                    file_source_id = None
                    file_side_id = None
                    
                    # First try metadata
                    if n.metadata:
                        file_source_id = n.metadata.get('source_id')
                        file_side_id = n.metadata.get('side_id')
                    
                    # If not in metadata and we have file_id, query database
                    if (not file_source_id or not file_side_id) and n.file_id:
                        try:

                            file_info = get_file(n.file_id)
                            if file_info:
                                # Get source/side from hash record
                                if file_info.get('hash_id'):
                                    hash_info = execute_query(
                                        "SELECT source_id, side_id FROM hashs WHERE id = %s",
                                        (file_info['hash_id'],),
                                        fetch="one"
                                    )
                                    if hash_info:
                                        file_source_id = hash_info[0][0] if not file_source_id else file_source_id
                                        file_side_id = hash_info[0][1] if not file_side_id else file_side_id
                        except Exception:
                            pass  # If query fails, use metadata values or skip filter
                    
                    # Apply filters
                    if source_id and file_source_id and file_source_id != source_id:
                        continue
                    if side_id and file_side_id and file_side_id != side_id:
                        continue
                    
                    # If we have filters but no source/side info, exclude (strict filtering)
                    if (source_id and not file_source_id) or (side_id and not file_side_id):
                        continue
                    
                    filtered.append(n)
                notifications = filtered
            
            # Sort notifications
            reverse_order = (sort_order == 'desc')
            if sort_by == 'priority':
                priority_order = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
                notifications.sort(key=lambda x: priority_order.get(x.priority.value, 0), reverse=reverse_order)
            elif sort_by == 'title':
                notifications.sort(key=lambda x: x.title.lower(), reverse=reverse_order)
            else:  # created_at (default)
                notifications.sort(key=lambda x: x.created_at, reverse=reverse_order)
            
            # Paginate
            total = len(notifications)
            start = (page - 1) * per_page
            end = start + per_page
            paginated_notifications = notifications[start:end]
            
            # Format for JSON with translation
            notifications_data = []
            for n in paginated_notifications:
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
                    'created_at': n.created_at.isoformat(),
                    'read': n.read,
                    'dismissed': n.dismissed,
                    'metadata': n.metadata
                })
            
            logger.info(f"Returning {len(notifications_data)} notifications (page {page}, per_page {per_page}, total {total})")
            
            return jsonify({
                'success': True,
                'notifications': notifications_data,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page if total > 0 else 0
                }
            })
            
        except Exception as e:
            logger.error(f"Error getting paginated notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications_page', success_key='success', status=500)

