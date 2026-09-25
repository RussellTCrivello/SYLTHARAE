"""SQL queries for alerts table"""
from .base_queries import BaseQueries


class AlertQueries(BaseQueries):
    """SQL queries related to alerts"""
    
    @staticmethod
    def get_by_id() -> str:
        """Get alert by ID"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, metadata, created_at, read, dismissed
            FROM alerts
            WHERE id = %s
        """
    
    @staticmethod
    def get_all() -> str:
        """Get all alerts"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read, dismissed
            FROM alerts
            ORDER BY created_at DESC
        """
    
    @staticmethod
    def insert_alert() -> str:
        """Insert new alert"""
        return """
            INSERT INTO alerts (type, priority, title, message, file_id, file_name, 
                              file_path, event_date, metadata, read, dismissed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
    
    @staticmethod
    def update_alert() -> str:
        """Update alert"""
        return """
            UPDATE alerts 
            SET type = %s, priority = %s, title = %s, message = %s, 
                file_id = %s, file_name = %s, file_path = %s, event_date = %s, 
                metadata = %s, read = %s, dismissed = %s
            WHERE id = %s
        """
    
    @staticmethod
    def delete_alert() -> str:
        """Delete alert"""
        return "DELETE FROM alerts WHERE id = %s"
    
    @staticmethod
    def mark_as_read() -> str:
        """Mark alert as read"""
        return "UPDATE alerts SET read = TRUE WHERE id = %s"
    
    @staticmethod
    def mark_as_dismissed() -> str:
        """Mark alert as dismissed"""
        return "UPDATE alerts SET dismissed = TRUE WHERE id = %s"
    
    @staticmethod
    def get_unread_alerts() -> str:
        """Get unread alerts"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at
            FROM alerts
            WHERE read = FALSE
            ORDER BY created_at DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_active_alerts() -> str:
        """Get active (not dismissed) alerts"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read
            FROM alerts
            WHERE dismissed = FALSE
            ORDER BY created_at DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_by_file_id() -> str:
        """Get alerts by file ID"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read, dismissed
            FROM alerts
            WHERE file_id = %s
            ORDER BY created_at DESC
        """
    
    @staticmethod
    def get_by_type() -> str:
        """Get alerts by type"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read, dismissed
            FROM alerts
            WHERE type = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
    
    @staticmethod
    def get_by_priority() -> str:
        """Get alerts by priority"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read, dismissed
            FROM alerts
            WHERE priority = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
    
    @staticmethod
    def search_alerts() -> str:
        """Search alerts with multiple filters"""
        return """
            SELECT id, type, priority, title, message, file_id, file_name, 
                   file_path, event_date, created_at, read, dismissed
            FROM alerts
            WHERE (%s IS NULL OR type = %s)
              AND (%s IS NULL OR priority = %s)
              AND (%s IS NULL OR read = %s)
              AND (%s IS NULL OR dismissed = %s)
              AND (%s IS NULL OR title ILIKE %s OR message ILIKE %s)
            ORDER BY created_at DESC
            LIMIT %s
        """
    
    @staticmethod
    def count_unread() -> str:
        """Count unread alerts"""
        return "SELECT COUNT(*) FROM alerts WHERE read = FALSE"
    
    @staticmethod
    def count_active() -> str:
        """Count active (not dismissed) alerts"""
        return "SELECT COUNT(*) FROM alerts WHERE dismissed = FALSE"
