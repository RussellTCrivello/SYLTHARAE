"""
Database module - Centralized exports for all database functionality.

This module provides a unified interface for all database operations,
maintaining backward compatibility while using the modern database architecture.
"""

# Core database classes
import logging
from database.database.database import Database
from database.database.config import DatabaseConfig
from database.exceptions import QueryError, TransactionError

# Database service
from database.services.contents_db_service import ContentDBService

# Repository classes (for advanced usage)
from database.database.repository.sources_repo import SourcesRepository
from database.database.repository.contents_repo import ContentsRepository
from database.database.repository.words_repo import WordsRepository
from database.database.repository.paths_repo import PathsRepository
from database.database.repository.keywords_repo import KeywordsRepository
from database.database.repository.categorys_repo import CategorysRepository
from database.database.repository.sides_repo import SidesRepository
from database.database.repository.words_hashs_repo import WordsHashsRepository
from database.database.repository.keywords_hashs_repo import KeywordsHashsRepository
from database.database.repository.words_categorys_repo import WordsCategorysRepository
from database.database.repository.titles_content_repo import TitlesContentRepository
from database.database.repository.punctuation_repo import PunctuationRepository
from database.database.repository.alerts_repo import AlertsRepository
from database.database.repository.best_repo import BaseRepository

# Query helper functions
from database.queries import (
    list_sources,
    search_sources,
    get_source_by_name,
    get_source_by_id,
    create_source,
    insert_source,
    list_sides,
    search_sides,
    get_side_by_id,
    get_side_by_name,
    create_side,
    insert_side,
)

logger = logging.getLogger(__name__)

# Database initialization — use database.bootstrap.bootstrap_database()
# (database/init_database.py was removed; bootstrap.py is the single
# authoritative database creation path)

# Backward compatibility: DatabaseHub (wrapper around Database)
class DatabaseHub:
    """
    Backward compatibility wrapper for DatabaseHub.
    Provides the same interface as the old DatabaseHub class.
    """
    def __init__(self, db_name=None):
        """
        Initialize DatabaseHub.
        
        Args:
            db_name: Database name (optional, uses config if not provided)
        """
        try:
            self.db = Database()
            # Verify pool was initialized
            if not self.db._pool:
                raise ConnectionError("Database connection pool not initialized after Database creation")
            self.db_name = db_name
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to initialize DatabaseHub: {e}")
            raise
    
    def reconnect(self):
        """Reconnect to database (rebuild the shared pool only if it is broken).

        Reconnection is for lost connections, not for load.  The check happens
        in :meth:`Database.rebuild_pool`, which keeps an already-usable pool
        and rate-limits rebuilds so concurrent workers cannot each create a
        new pool - that loop was what multiplied connections until PostgreSQL
        answered "too many clients already".
        """
        try:
            if not self.db.rebuild_pool():
                # Last resort: drop this handle and build a new one.  The pool
                # itself is process-wide, so this only replaces the wrapper.
                try:
                    self.db.close_all()
                except Exception:
                    pass
                self.db = Database()
            # Verify pool was initialized
            if not self.db._pool:
                raise ConnectionError("Database connection pool not initialized after reconnection")
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to reconnect DatabaseHub: {e}")
            raise
    
    def _reconnect(self):
        """
        Private method for reconnection (used by error recovery).
        Returns True if reconnection succeeded, False otherwise.
        """
        try:
            self.reconnect()
            # Verify reconnection by checking health
            return self._check_connection_health()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Reconnection failed: {e}")
            return False
    
    def _check_connection_health(self):
        """
        Check if database connection is healthy.
        Returns True if healthy, False otherwise.
        """
        try:
            return self.db.health_check()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.debug(f"Health check failed: {e}")
            return False
    
    def close(self):
        """
        Close database connections (alias for close_all for backward compatibility).
        """
        self.db.close_all()
    
    def get_connection(self):
        """Get a database connection"""
        return self.db.get_connection()
    
    def _get_connection(self):
        """Get a database connection (alias for get_connection)"""
        return self.get_connection()
    
    def execute_query(self, query, params=None):
        """Execute a query"""
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            result = cur.fetchall()
            conn.commit()
            cur.close()
            return result

# Backward compatibility: EnhancedQueryCache
class EnhancedQueryCache:
    """
    Enhanced query cache with TTL support.
    Provides caching for database queries with expiration.
    """
    def __init__(self, max_size=1000, default_ttl=300, max_memory_mb=None):
        """
        Initialize query cache.
        
        Args:
            max_size: Maximum number of cached entries
            default_ttl: Default time-to-live in seconds
            max_memory_mb: Maximum memory usage in MB (optional)
        """
        import time
        import threading
        self._cache = {}
        self._lock = threading.Lock()
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.max_memory_mb = max_memory_mb
        self._time = time
    
    def get(self, key):
        """Get cached value if not expired"""
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if self._time.time() < expiry:
                    return value
                else:
                    del self._cache[key]
            return None
    
    def set(self, key, value, ttl=None):
        """Set cached value with TTL"""
        with self._lock:
            if len(self._cache) >= self.max_size:
                # Remove oldest entry (simple FIFO)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            
            ttl = ttl or self.default_ttl
            expiry = self._time.time() + ttl
            self._cache[key] = (value, expiry)
    
    def clear(self):
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()
    
    def get_stats(self):
        """Get cache statistics"""
        with self._lock:
            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'keys': list(self._cache.keys())[:10]
            }

# Backward compatibility: ConnectionPool (wrapper around Database)
class ConnectionPool:
    """
    Backward compatibility wrapper for ConnectionPool.
    Uses the Database class's connection pooling internally.
    """
    def __init__(self, db_name=None, **kwargs):
        """
        Initialize connection pool.
        
        Args:
            db_name: Database name (optional)
            **kwargs: Additional connection parameters
        """
        self.db = Database()
    
    def get_connection(self):
        """Get a connection from the pool"""
        return self.db.get_connection()
    
    def close_all(self):
        """Close all connections"""
        self.db.close_all()

# Backward compatibility: TransactionManager
from contextlib import contextmanager

class TransactionManager:
    """
    Backward compatibility wrapper for TransactionManager.
    Uses the Database class's transaction support.
    """
    def __init__(self, connection_pool=None):
        """
        Initialize transaction manager.
        
        Args:
            connection_pool: ConnectionPool instance (optional)
        """
        if connection_pool:
            self.db = connection_pool.db if hasattr(connection_pool, 'db') else Database()
        else:
            self.db = Database()
    
    @contextmanager
    def transaction(self):
        """Transaction context manager"""
        conn = None
        try:
            conn = self.db.connect()
            yield conn
            conn.commit()
        except Exception:
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                self.db.putconn(conn)

# Helper functions for backward compatibility
def get_postgres_connection(**kwargs):
    """Get a PostgreSQL connection"""
    db = Database()
    return db.connect()

def get_db_connection(**kwargs):
    """Get a database connection (alias for get_postgres_connection)"""
    return get_postgres_connection(**kwargs)

def get_db_config():
    """Get database configuration as dictionary"""
    try:
        from settings import get_database_config
        db_config = get_database_config()
        return {
            'database': db_config.database,
            'user': db_config.user,
            'password': db_config.password or '',
            'host': db_config.host,
            'port': db_config.port,
        }
    except Exception:
        import os
        return {
            'database': os.getenv('DB_NAME', 'analysis'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', ''),
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', '5432')),
        }

# Performance analysis tools
from database.performance import (
    DatabasePerformanceAnalyzer,
    IndexAnalyzer,
    QueryProfiler,
    IndexInfo,
    IndexRecommendation,
    SlowQuery
)

# Additional helper functions that may be imported
def get_source(name_or_id):
    """Get source by name or ID (backward compatibility - prefers ID if integer)"""
    if isinstance(name_or_id, int):
        return get_source_by_id(name_or_id)
    else:
        return get_source_by_name(name_or_id)

def get_side(name_or_id):
    """Get side by name or ID"""
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(f"get_side called with: {name_or_id} (type: {type(name_or_id)})")
    if isinstance(name_or_id, int):
        logger.debug(f"Calling get_side_by_id with {name_or_id}")
        return get_side_by_id(name_or_id)
    else:
        logger.debug(f"Calling get_side_by_name with {name_or_id}")
        return get_side_by_name(name_or_id)

def update_source(source_id, **kwargs):
    """Update source (placeholder - implement based on repository)"""
    # This should be implemented using SourcesRepository
    pass

def update_side(side_id, **kwargs):
    """Update side (placeholder - implement based on repository)"""
    # This should be implemented using SidesRepository
    pass

def get_file(path_id):
    """Get file by path ID"""
    db_service = ContentDBService()
    return db_service.paths_repo.get_file_by_id(path_id)

def get_file_content(path_id):
    """Get file content as text"""
    db_service = ContentDBService()
    return db_service.get_content_as_text(path_id)

def search_files(**kwargs):
    """Search files with filters"""
    db_service = ContentDBService()
    return db_service.paths_repo.search_files(**kwargs)

def get_recent_files(limit=10):
    """Get recent files"""
    db_service = ContentDBService()
    return db_service.paths_repo.get_recent_files(limit)

def search_categories(search_query=None, page=1, per_page=20):
    """
    Search categories with pagination
    
    Args:
        search_query: Optional search term
        page: Page number (1-based)
        per_page: Results per page
    
    Returns:
        Tuple of (results_list, total_count)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        db_service = ContentDBService()
        offset = (page - 1) * per_page
        
        # Get results
        rows = db_service.categorys_repo.search_categories(search_query, per_page, offset)
        
        # Format results as dictionaries
        results = []
        for row in (rows or []):
            if row and len(row) >= 3:
                results.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'file_count': row[2] or 0
                })
        
        # Get total count
        if search_query:
            count_query = """
                SELECT COUNT(DISTINCT c.id)
                FROM categorys c
                JOIN words w ON c.word_id = w.id
                WHERE w.word ILIKE %s
            """
            search_param = f"%{search_query}%"
            count_result = db_service.categorys_repo.execute(
                count_query,
                (search_param,),
                fetchone=True
            )
        else:
            count_query = """
                SELECT COUNT(DISTINCT c.id)
                FROM categorys c
            """
            count_result = db_service.categorys_repo.execute(
                count_query,
                None,
                fetchone=True
            )
        total = count_result[0] if count_result else 0
        
        return results, total
    except Exception as e:
        logger.error(f"Error in search_categories: {e}", exc_info=True)
        # Return empty results on error
        return [], 0

def list_categories(limit=50):
    """List categories.

    AUDIT (API-02): this delegated to ``categorys_repo.list_categories()``,
    which does not exist - every caller (``/api/categories/all-words`` and
    ``get_categorys_word_id()``) raised AttributeError and returned 500.
    The repository exposes ``search_categories`` / ``get_categories_with_stats``
    but neither returns ``word_id``, which callers require, so reuse the
    existing ``list_all_join`` query and return dicts (callers index by name).
    """
    from database.database.queries.category_queries import CategoryQueries

    db_service = ContentDBService()
    rows = db_service.categorys_repo.execute(
        CategoryQueries.list_all_join(), (limit,), fetchall=True
    )
    categories = []
    for row in rows or []:
        if isinstance(row, dict):
            categories.append(dict(row))
            continue
        category_id, name, word_id = row[0], row[1], (row[2] if len(row) > 2 else None)
        categories.append(
            {
                "id": category_id,
                "name": name,
                "category_name": name,
                "word_id": word_id,
            }
        )
    return categories

def get_categories_for_dropdown():
    """Get categories for dropdown in format (id, name) tuples"""
    try:
        from database.database.queries.category_queries import CategoryQueries
        db_service = ContentDBService()
        # Use list_all_join query to get id and name
        results = db_service.categorys_repo.execute(
            CategoryQueries.list_all_join(),
            (1000,),  # Limit to 1000 categories
            fetchall=True
        )
        
        if results:
            # Return list of tuples (id, name) for dropdown compatibility
            return [(row[0], row[1]) for row in results if row and len(row) >= 2]
        return []
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting categories for dropdown: {e}", exc_info=True)
        return []

def get_statistics_query():
    """Get general statistics including file counts, word counts, and status breakdown"""
    try:
        import logging
        logger = logging.getLogger(__name__)
        
        # Use DatabaseHub directly to avoid circular imports
        db_hub = DatabaseHub()
        
        with db_hub.get_connection() as conn:
            cur = conn.cursor()
            
            # Get total files count
            cur.execute("SELECT COUNT(*) FROM paths")
            total_files = cur.fetchone()[0] or 0
            
            # Get total words count
            cur.execute("SELECT COUNT(DISTINCT id) FROM words")
            total_words = cur.fetchone()[0] or 0
            
            # Get file status breakdown
            cur.execute("""
                SELECT file_status, COUNT(*) 
                FROM paths 
                GROUP BY file_status
            """)
            status_results = cur.fetchall()
            status_dict = {row[0]: row[1] for row in status_results} if status_results else {}
            
            # Get unique file types count
            cur.execute("SELECT COUNT(DISTINCT file_type) FROM paths WHERE file_type IS NOT NULL")
            unique_file_types = cur.fetchone()[0] or 0
            
            # Get total storage bytes
            cur.execute("SELECT COALESCE(SUM(file_size), 0) FROM paths")
            total_storage_bytes = cur.fetchone()[0] or 0
            
            # Get total categories count
            # DB-AUDIT: the dashboard template and /api/dashboard/stats both read
            # 'total_categories'/'total_keywords', but this dict never provided
            # them, so those KPI cards were hard-wired to 0.
            cur.execute("SELECT COUNT(*) FROM categorys")
            total_categories = cur.fetchone()[0] or 0

            # Get total keywords count
            cur.execute("SELECT COUNT(*) FROM keywords")
            total_keywords = cur.fetchone()[0] or 0

            # Get database size (PostgreSQL)
            try:
                cur.execute("SELECT pg_database_size(current_database())")
                database_size_bytes = cur.fetchone()[0] or 0
            except Exception:
                database_size_bytes = 0
            
            cur.close()
        
        return {
            'total_files': total_files or 0,
            'total_words': total_words or 0,
            'total_categories': total_categories or 0,
            'total_keywords': total_keywords or 0,
            'unique_file_types': unique_file_types or 0,
            'total_storage_bytes': total_storage_bytes or 0,
            'database_size_bytes': database_size_bytes or 0,
            'status': status_dict
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting statistics: {e}", exc_info=True)
        return {
            'total_files': 0,
            'total_words': 0,
            'total_categories': 0,
            'total_keywords': 0,
            'unique_file_types': 0,
            'total_storage_bytes': 0,
            'database_size_bytes': 0,
            'status': {}
        }

def get_processing_statistics_query():
    """Get processing statistics by file type"""
    try:
        import logging
        logger = logging.getLogger(__name__)
        
        # Use DatabaseHub directly to avoid circular imports
        db_hub = DatabaseHub()
        
        query = """
            SELECT 
                COALESCE(file_type, 'Unknown') as type,
                COUNT(*) as total,
                COUNT(CASE WHEN file_status = 'Read' THEN 1 END) as processed,
                COUNT(CASE WHEN file_status = 'Unread' THEN 1 END) as unprocessed,
                COALESCE(SUM(file_size), 0) as total_size,
                CASE 
                    WHEN COUNT(*) > 0 
                    THEN ROUND((COUNT(CASE WHEN file_status = 'Read' THEN 1 END)::numeric / COUNT(*)::numeric) * 100, 2)
                    ELSE 0 
                END as success_rate
            FROM paths
            GROUP BY file_type
            ORDER BY total DESC
        """
        
        with db_hub.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            results = cur.fetchall()
            
            by_type = []
            if results:
                for row in results:
                    by_type.append({
                        'type': row[0] or 'Unknown',
                        'total': row[1] or 0,
                        'processed': row[2] or 0,
                        'unprocessed': row[3] or 0,
                        'total_size': row[4] or 0,
                        'success_rate': float(row[5] or 0)
                    })
            
            # Get daily processing speed (last 30 days)
            daily_speed_query = """
                SELECT 
                    DATE(date_creation) as date,
                    COUNT(*) as files
                FROM paths
                WHERE date_creation >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY DATE(date_creation)
                ORDER BY date ASC
            """
            
            cur.execute(daily_speed_query)
            daily_results = cur.fetchall()
            cur.close()
        
        daily_speed = []
        if daily_results:
            for row in daily_results:
                daily_speed.append({
                    'date': str(row[0]),
                    'files': row[1] or 0
                })
        
        # DB-AUDIT: the dashboard reads 'processed_count' to drive the
        # "Processed Files" card; derive it from the per-type roll-up so no
        # extra query is needed.
        processed_count = sum(entry['processed'] for entry in by_type)
        unprocessed_count = sum(entry['unprocessed'] for entry in by_type)

        return {
            'by_type': by_type,
            'daily_speed': daily_speed,
            'processed_count': processed_count,
            'unprocessed_count': unprocessed_count
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting processing statistics: {e}", exc_info=True)
        return {
            'by_type': [],
            'daily_speed': [],
            'processed_count': 0,
            'unprocessed_count': 0
        }

def get_category_statistics_detailed_query():
    """Get detailed category statistics with file counts"""
    try:
        import logging
        logger = logging.getLogger(__name__)
        
        # Use DatabaseHub to avoid circular imports
        db_hub = DatabaseHub()
        
        query = """
            SELECT 
                c.id,
                w.word as name,
                COUNT(DISTINCT p.id) as file_count,
                COUNT(DISTINCT wc.word_id) as word_count
            FROM categorys c
            JOIN words w ON c.word_id = w.id
            LEFT JOIN words_categorys wc ON wc.category_id = c.id
            LEFT JOIN words_hashs wp ON wp.word_id = wc.word_id
            LEFT JOIN hash_contexts hc ON hc.hash_id = wp.hash_id
            LEFT JOIN paths p ON p.context_id = hc.id
            GROUP BY c.id, w.word
            ORDER BY file_count DESC, w.word ASC
        """
        
        with db_hub.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            results = cur.fetchall()
            cur.close()
        
        categories = []
        if results:
            for row in results:
                categories.append({
                    'id': row[0],
                    'name': row[1] or 'Unnamed Category',
                    'file_count': row[2] or 0,
                    'word_count': row[3] or 0
                })
        
        return {
            'categories': categories,
            'total_categories': len(categories)
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting category statistics: {e}", exc_info=True)
        return {
            'categories': [],
            'total_categories': 0
        }

def get_period_comparison_query():
    """Get period comparison statistics (this week vs last week)"""
    try:
        import logging
        logger = logging.getLogger(__name__)
        
        # Use DatabaseHub directly to avoid circular imports
        db_hub = DatabaseHub()
        
        query = """
            SELECT 
                COUNT(CASE WHEN date_creation >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as files_this_week,
                COUNT(CASE WHEN date_creation >= CURRENT_DATE - INTERVAL '14 days' 
                          AND date_creation < CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as files_last_week,
                COALESCE(SUM(CASE WHEN date_creation >= CURRENT_DATE - INTERVAL '7 days' THEN file_size END), 0) as size_this_week,
                COALESCE(SUM(CASE WHEN date_creation >= CURRENT_DATE - INTERVAL '14 days' 
                             AND date_creation < CURRENT_DATE - INTERVAL '7 days' THEN file_size END), 0) as size_last_week
            FROM paths
        """
        
        with db_hub.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query)
            row = cur.fetchone()
            cur.close()
        
        if row:
            files_this_week = row[0] or 0
            files_last_week = row[1] or 0
            size_this_week = row[2] or 0
            size_last_week = row[3] or 0
            
            files_change = (files_this_week or 0) - (files_last_week or 0)
            size_change = (size_this_week or 0) - (size_last_week or 0)
            
            return {
                'files_change': files_change,
                'size_change': size_change,
                'files_this_week': files_this_week or 0,
                'files_last_week': files_last_week or 0,
                'size_this_week': size_this_week or 0,
                'size_last_week': size_last_week or 0
            }
        
        return {
            'files_change': 0,
            'size_change': 0,
            'files_this_week': 0,
            'files_last_week': 0,
            'size_this_week': 0,
            'size_last_week': 0
        }
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error getting period comparison: {e}", exc_info=True)
        return {
            'files_change': 0,
            'size_change': 0,
            'files_this_week': 0,
            'files_last_week': 0,
            'size_this_week': 0,
            'size_last_week': 0
        }

def get_email_words_db(
    page: int = 1,
    per_page: int = 50,
    search_term: str = None,
    domain: str = None,
    domain_mode: str = 'exact',
    source_filter: int = None,
    side_filter: int = None,
    date_from: str = None,
    date_to: str = None,
    sort_by: str = 'usage_count',
    sort_order: str = 'desc'
):
    """
    Get email words with pagination and filters.
    
    Args:
        page: Page number (1-based)
        per_page: Results per page (max 200)
        search_term: Optional search term to filter email addresses (max 500 chars)
        domain: Optional domain filter (sanitized)
        domain_mode: Domain filter mode ('exact', 'contains', 'endswith')
        source_filter: Filter by source ID (not yet implemented in query)
        side_filter: Filter by side ID (not yet implemented in query)
        date_from: Filter from date (not yet implemented in query)
        date_to: Filter to date (not yet implemented in query)
        sort_by: Sort column ('word' or 'usage_count')
        sort_order: Sort direction ('asc' or 'desc')
    
    Returns:
        Tuple of (results_list, total_count)
    """
    from database.services.contents_db_service import ContentDBService
    import logging
    import re
    
    logger = logging.getLogger(__name__)
    
    try:
        # Input validation
        if page < 1:
            page = 1
            logger.warning(f"Invalid page number, defaulting to 1")
        
        if per_page < 1:
            per_page = 50
            logger.warning(f"Invalid per_page, defaulting to 50")
        elif per_page > 200:
            per_page = 200
            logger.warning(f"per_page exceeds maximum (200), capping to 200")
        
        # Validate and sanitize search_term
        if search_term:
            search_term = str(search_term).strip()
            if len(search_term) > 500:
                search_term = search_term[:500]
                logger.warning(f"search_term truncated to 500 characters")
            # Remove potentially dangerous characters (basic sanitization)
            search_term = re.sub(r'[;\'"\\]', '', search_term)
            if not search_term:
                search_term = None
        
        # Validate and sanitize domain
        if domain:
            domain = str(domain).strip()
            if len(domain) > 255:
                domain = domain[:255]
                logger.warning(f"domain truncated to 255 characters")
            # Remove potentially dangerous characters
            domain = re.sub(r'[;\'"\\]', '', domain)
            if not domain:
                domain = None
        
        # Validate domain_mode
        domain_mode = str(domain_mode).lower().strip() if domain_mode else 'exact'
        if domain_mode not in ('exact', 'contains', 'endswith'):
            logger.warning(f"Invalid domain_mode '{domain_mode}', defaulting to 'exact'")
            domain_mode = 'exact'
        
        # Validate sort_by
        sort_by = str(sort_by).lower().strip() if sort_by else 'usage_count'
        if sort_by not in ('word', 'usage_count'):
            logger.warning(f"Invalid sort_by '{sort_by}', defaulting to 'usage_count'")
            sort_by = 'usage_count'
        
        # Validate sort_order
        sort_order = str(sort_order).lower().strip() if sort_order else 'desc'
        if sort_order not in ('asc', 'desc'):
            logger.warning(f"Invalid sort_order '{sort_order}', defaulting to 'desc'")
            sort_order = 'desc'
        
        service = ContentDBService()
        
        # Convert page/per_page to limit/offset
        limit = per_page
        offset = (page - 1) * per_page
        
        if offset < 0:
            offset = 0
        
        # Email pattern: must contain @ and be longer than 5 chars
        like_pattern = '%@%'  # Must contain @
        not_like_pattern = '%@%@%'  # Must not contain multiple @ (simple validation)
        
        # Call repository method with all parameters
        logger.debug(f"Calling get_email_words with: search_term={search_term}, domain={domain}, domain_mode={domain_mode}, sort_by={sort_by}, sort_order={sort_order}, limit={limit}, offset={offset}")
        
        results = service.words_repo.get_email_words(
            like_pattern=like_pattern,
            not_like_pattern=not_like_pattern,
            search=search_term,
            domain=domain,  # Now supports all domain modes
            domain_mode=domain_mode,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset
        )
        
        logger.debug(f"get_email_words returned {len(results) if results else 0} results")
        
        if not results:
            logger.warning(f"No email words found. Query parameters: search_term={search_term}, domain={domain}, domain_mode={domain_mode}")
            return [], 0
        
        # Extract total count from first row (COUNT(*) OVER() window function)
        total_count = 0
        email_words = []
        
        for row in results:
            if isinstance(row, tuple) and len(row) >= 3:
                word = row[0]
                usage_count = row[1] if row[1] else 0
                # Extract total_count from window function (same for all rows)
                if total_count == 0:
                    total_count = row[2] if row[2] else 0
                
                email_words.append({
                    'word': word,
                    'usage_count': usage_count
                })
        
        # If total_count is still 0 but we have results, try to get accurate count
        if total_count == 0 and email_words:
            logger.warning("Window function COUNT(*) OVER() returned 0, attempting separate count query")
            try:
                # Fallback: execute separate count query (matches main query structure)
                count_query = """
                    SELECT COUNT(DISTINCT w.id)
                    FROM words w
                    WHERE w.word LIKE %s
                      AND w.word NOT LIKE %s
                      AND LENGTH(w.word) > 5
                      AND (%s IS NULL OR w.word ILIKE %s)
                      AND (
                          %s IS NULL OR
                          (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) = %s) OR
                          (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) LIKE %s) OR
                          (%s IS NOT NULL AND LOWER(split_part(w.word, '@', 2)) LIKE %s)
                      )
                """
                
                # Build domain filter patterns for count query (match repository logic)
                domain_exact_param = None
                domain_contains_param = None
                domain_endswith_param = None
                
                if domain:
                    domain_lower = domain.lower()
                    if domain_mode == 'exact':
                        domain_exact_param = domain_lower
                    elif domain_mode == 'contains':
                        domain_contains_param = f"%{domain_lower}%"
                    elif domain_mode == 'endswith':
                        domain_endswith_param = f"%{domain_lower}"
                
                count_result = service.words_repo.execute(
                    count_query,
                    (
                        like_pattern,
                        not_like_pattern,
                        search_term,
                        f"%{search_term}%" if search_term else None,
                        domain,
                        domain_exact_param,  # IS NOT NULL check
                        domain_exact_param,  # Value
                        domain_contains_param,  # IS NOT NULL check
                        domain_contains_param,  # Value
                        domain_endswith_param,  # IS NOT NULL check
                        domain_endswith_param,  # Value
                    ),
                    fetch_one=True
                )
                
                if count_result and len(count_result) > 0:
                    total_count = count_result[0] if count_result[0] else 0
                    logger.info(f"Separate count query returned: {total_count}")
                else:
                    # Last resort: estimate from current results
                    if len(email_words) == limit:
                        total_count = offset + limit + 1  # At least this many
                    else:
                        total_count = offset + len(email_words)
                    logger.warning(f"Count query failed, using estimate: {total_count}")
            except Exception as count_error:
                logger.error(f"Error executing count query: {count_error}", exc_info=True)
                # Last resort: estimate from current results
                if len(email_words) == limit:
                    total_count = offset + limit + 1
                else:
                    total_count = offset + len(email_words)
        
        return email_words, total_count
        
    except Exception as e:
        logger.error(f"Error getting email words from database: {e}", exc_info=True)
        return [], 0


def get_email_words():
    """Get email words (alias for get_email_words_db for backward compatibility)"""
    return get_email_words_db()

def get_keyword_stats(keyword_id):
    """
    Get keyword statistics for a specific keyword
    
    Args:
        keyword_id: Keyword ID
    
    Returns:
        Dictionary with keys: usage_count, file_types, last_used
    """
    from database.services.contents_db_service import ContentDBService
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        db_service = ContentDBService()
        
        # Get usage count (number of files using this keyword)
        usage_query = """
            SELECT COUNT(DISTINCT p.id)
            FROM keywords_hashs kp
            JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
            JOIN paths p ON p.context_id = hc.id
            WHERE kp.keyword_id = %s
        """
        usage_result = db_service.keywords_repo.execute(
            usage_query,
            (keyword_id,),
            fetchone=True
        )
        usage_count = usage_result[0] if usage_result else 0
        
        # Get file types count (distinct file extensions)
        file_types_query = """
            SELECT COUNT(DISTINCT LOWER(SUBSTRING(p.file_name FROM '\.([^.]+)$')))
            FROM keywords_hashs kp
            JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
            JOIN paths p ON p.context_id = hc.id
            WHERE kp.keyword_id = %s
        """
        file_types_result = db_service.keywords_repo.execute(
            file_types_query,
            (keyword_id,),
            fetchone=True
        )
        file_types = file_types_result[0] if file_types_result else 0
        
        # Get last used date (most recent file date)
        last_used_query = """
            SELECT MAX(p.file_date)
            FROM keywords_hashs kp
            JOIN hash_contexts hc ON hc.hash_id = kp.hash_id
            JOIN paths p ON p.context_id = hc.id
            WHERE kp.keyword_id = %s
        """
        last_used_result = db_service.keywords_repo.execute(
            last_used_query,
            (keyword_id,),
            fetchone=True
        )
        last_used = last_used_result[0] if last_used_result else None
        
        return {
            'usage_count': usage_count,
            'file_types': file_types,
            'last_used': last_used
        }
    except Exception as e:
        logger.error(f"Error getting keyword stats for {keyword_id}: {e}", exc_info=True)
        return {
            'usage_count': 0,
            'file_types': 0,
            'last_used': None
        }

def get_category_statistics():
    """Get category statistics (placeholder)"""
    # Implement based on queries
    pass

# Export all public classes and functions
__all__ = [
    # Core classes
    'Database',
    'DatabaseConfig',
    'DatabaseHub',
    'ContentDBService',
    'QueryError',
    'TransactionError',
    
    # Repository classes
    'BaseRepository',
    'SourcesRepository',
    'ContentsRepository',
    'WordsRepository',
    'PathsRepository',
    'KeywordsRepository',
    'CategorysRepository',
    'SidesRepository',
    'WordsHashsRepository',
    'KeywordsHashsRepository',
    'WordsCategorysRepository',
    'TitlesContentRepository',
    'PunctuationRepository',
    'AlertsRepository',
    
    # Backward compatibility classes
    'ConnectionPool',
    'TransactionManager',
    'EnhancedQueryCache',
    
    # Helper functions
    'get_postgres_connection',
    'get_db_connection',
    'get_db_config',
    
    # Query functions
    'list_sources',
    'search_sources',
    'get_source_by_name',
    'get_source',
    'create_source',
    'insert_source',
    'update_source',
    'list_sides',
    'search_sides',
    'get_side_by_name',
    'get_side',
    'create_side',
    'insert_side',
    'update_side',
    'get_file',
    'get_file_content',
    'search_files',
    'get_recent_files',
    'search_categories',
    'list_categories',
    'get_categories_for_dropdown',
    'get_statistics_query',
    'get_processing_statistics_query',
    'get_category_statistics_detailed_query',
    'get_period_comparison_query',
    'get_email_words',
    'get_email_words_db',  # The actual function that accepts parameters
    'get_keyword_stats',
    'get_category_statistics',
    
    # Performance analyzers
    'DatabasePerformanceAnalyzer',
    'IndexAnalyzer',
    'QueryProfiler',
    'IndexInfo',
    'IndexRecommendation',
    'SlowQuery',
]

