"""
Notification API Routes
System-wide notification management endpoints
"""

from flask import request, jsonify
from flask_babel import gettext as _
from datetime import date, datetime
from core.monitoring.notification_service import (
    get_notification_service,
    NotificationType,
    NotificationPriority,
    notification_from_row,
)
from core.monitoring.notification_display import format_title_message, display_payload
from Api.utils import execute_query
import logging
from core.errors import client_error, client_safe_message
from core.security.rate_limit import INTERACTIVE_READ_LIMIT, limiter

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
            
            # Single shared formatter keeps displayed titles/messages
            # accurate and identical across every endpoint (see
            # core/monitoring/notification_display.py).
            notifications_data = [display_payload(n, _) for n in notifications]

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
                title, message = format_title_message(n, _)
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
            # Direct row lookup - no forced refresh, no linear scan of
            # an in-memory list capped at 10 000 entries.
            row = execute_query(
                """
                SELECT id, type, priority, title, message, file_id, file_name,
                       file_path, event_date, metadata, created_at, read, dismissed
                FROM alerts
                WHERE id = %s AND dismissed = FALSE
                """,
                (notification_id,),
                fetch="one"
            )
            notification = notification_from_row(row) if row else None

            if notification is None:
                # Fallback covers pending (temp-id) notifications in memory.
                notification = next(
                    (
                        n for n in notification_service.get_notifications(limit=5000)
                        if n.id == notification_id
                    ),
                    None,
                )

            if not notification:
                return jsonify({
                    'success': False,
                    'error': 'Notification not found'
                }), 404

            payload = display_payload(notification, _)

            return jsonify({
                'success': True,
                'notification': payload
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

            # Persist before responding so the payload carries real ids.
            notification_service.flush_pending_notifications()

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
            count = notification_service.refresh_notifications()

            return jsonify({
                'success': True,
                'message': 'Notifications refreshed successfully',
                'count': count
            })
        except Exception as e:
            logger.error(f"Error refreshing notifications: {e}")
            return client_error(e, subsystem='Api.routes.notifications', success_key='success', status=500)
    
    # The sidebar badge reads this on every page load and then every 30 s per
    # open tab (base-page-handler.js): five idle tabs alone reach the 600/hour
    # default, after which the badge - and each page boot - got 429 (live
    # browser smoke through nginx). An interactive read; see
    # INTERACTIVE_READ_LIMIT.
    @limiter.limit(INTERACTIVE_READ_LIMIT)
    @app.route('/api/notifications/stats', methods=['GET'])
    def get_notification_stats():
        """Get notification statistics"""
        try:
            notification_service = get_notification_service()
            
            # Exact SQL aggregates (COUNT/GROUP BY).  No forced refresh,
            # no in-memory scan: totals stay correct at any table size and
            # the 30-second stats poll stops racing concurrent refreshes.
            stats = notification_service.get_stats()

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

            notification_service = get_notification_service()
            # Collected as (kind, notification, extra); the JSON response is
            # built AFTER the flush so it reports the real database ids, not
            # the temporary negative queue ids.
            created_items = []
            
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

            # One query loads every hash key that already has a duplicate
            # notification - including DISMISSED ones, so a dismissed
            # duplicate is not resurrected on the next scan.  Set membership
            # replaces the previous per-hash existence probe (N+1 queries).
            existing_hash_rows = execute_query(
                """
                SELECT metadata->>'hash'
                FROM alerts
                WHERE type = %s
                  AND metadata->>'hash' IS NOT NULL
                """,
                (NotificationType.SIMILAR_FILES.value,),
                fetch="all"
            ) or []
            existing_hashes = {r[0] for r in existing_hash_rows}

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
                
                # Skip hashes that already produced a notification (in any
                # state - dismissed notifications stay dismissed)
                if hash_value not in existing_hashes:
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
                    created_items.append(('duplicate', notification, None))
                    existing_hashes.add(hash_value)
                    duplicate_count += 1
            
            # 2. Find files with future dates in content
            logger.info("Scanning for files with future dates...")
            from Api.utils import load_text_content
            from core.monitoring.future_events import FutureEventsAnalyzer
            
            future_analyzer = FutureEventsAnalyzer()
            today = date.today()
            
            # One row PER PATH: EXISTS instead of "DISTINCT p.id, ..., c.id".
            # Paths whose content has several chunks used to be scanned once
            # per chunk, which inflated "files processed" and wasted content
            # loads.  (m0011: contents is keyed by hash, reached through the
            # path's content identity context.)
            files_query = """
                SELECT p.id, p.file_name, p.file_path
                FROM paths p
                JOIN hash_contexts hc ON hc.id = p.context_id
                WHERE p.file_status = 'Read'
                  AND EXISTS (SELECT 1 FROM contents c WHERE c.hash_id = hc.hash_id)
                ORDER BY p.id DESC
                LIMIT 5000
            """

            files_with_content = execute_query(files_query, None, fetch="all")

            # Single preload of every (file_id, event_date) future-date alert
            # for the candidate files - replaces a per-file existence query of
            # up to 5 000 queries per scan.  Dismissed alerts are included so
            # they are not resurrected either.
            candidate_ids = [row[0] for row in files_with_content or []]
            existing_future_pairs = set()
            if candidate_ids:
                for pair_row in execute_query(
                    """
                    SELECT file_id, event_date
                    FROM alerts
                    WHERE type = %s
                      AND file_id = ANY(%s)
                    """,
                    (NotificationType.FUTURE_DATE.value, candidate_ids),
                    fetch="all"
                ) or []:
                    existing_future_pairs.add((pair_row[0], pair_row[1]))

            future_date_count = 0
            processed_files = 0
            
            for row in files_with_content or []:
                file_id, file_name, file_path = row
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
                        
                        # Skip (file, date) pairs that already produced a
                        # notification (in any state)
                        if (file_id, earliest_date) not in existing_future_pairs:
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
                            created_items.append(('future_date', notification, earliest_date.isoformat()))
                            existing_future_pairs.add((file_id, earliest_date))
                            future_date_count += 1
                
                except Exception as e:
                    logger.debug(f"Error processing file {file_id} for future dates: {e}")
                    continue
            
            # Persist, then build the response with real ids.
            notification_service.flush_pending_notifications()

            # Refresh notifications cache to ensure new notifications are available
            notification_service.refresh_notifications()

            notifications_created = []
            for kind, created_notification, extra in created_items:
                entry = {
                    'type': kind,
                    'id': created_notification.id,
                    'title': created_notification.title
                }
                if extra is not None:
                    entry['date'] = extra
                notifications_created.append(entry)

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

