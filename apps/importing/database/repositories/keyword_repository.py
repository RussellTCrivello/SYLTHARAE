"""
Keyword Repository
Handles all database operations for the keywords table
"""

from typing import Optional, List

from apps.importing.database.transaction_manager import TransactionManager
from apps.importing.utils.logger import get_logger
from apps.importing.utils.exceptions import RepositoryException
from apps.importing.utils.constants import TABLE_KEYWORDS
from core.serialization import pack_int_list, unpack_int_list

logger = get_logger(__name__)


class KeywordRepository:
    """Repository for keyword operations"""
    
    def __init__(self, transaction_manager: TransactionManager):
        """
        Initialize keyword repository
        
        Args:
            transaction_manager: Transaction manager instance
        """
        self.tx_manager = transaction_manager
        self.logger = logger
    
    def create_keyword(
        self,
        word_ids: List[int],
        category_id: int
    ) -> Optional[int]:
        """
        Create a keyword from a list of word IDs
        
        Args:
            word_ids: List of word IDs representing the phrase
            category_id: Category to associate with this keyword
            
        Returns:
            Keyword ID if successful, None otherwise
        """
        if not word_ids or len(word_ids) < 2:
            self.logger.warning("Keywords must have at least 2 words")
            return None
        
        try:
            # Pickle the word ID sequence
            keyword_blob = pack_int_list(word_ids)
            
            # Check if keyword already exists with this category_id
            existing = self.tx_manager.execute_query(
                f"SELECT id FROM {TABLE_KEYWORDS} "
                f"WHERE keyword = %s AND category_id = %s",
                (keyword_blob, category_id),
                fetch=True
            )
            
            if existing:
                keyword_id = existing[0][0]
                self.logger.debug(f"Keyword already exists (ID: {keyword_id})")
                return keyword_id
            
            # Insert new keyword
            result = self.tx_manager.execute_query(
                f"INSERT INTO {TABLE_KEYWORDS} (keyword, category_id) "
                f"VALUES (%s, %s) RETURNING id",
                (keyword_blob, category_id),
                fetch=True
            )
            
            if result:
                keyword_id = result[0][0]
                self.logger.debug(
                    f"Created keyword with {len(word_ids)} words (ID: {keyword_id})"
                )
                return keyword_id
            
            return None
            
        except Exception as e:
            # Check if this is a duplicate key error
            # The unique constraint is on 'keyword' column only, not (keyword, category_id)
            # So the same phrase can exist in different categories, but only once globally
            error_msg = str(e).lower()
            
            # Check both the exception message and the underlying cause
            is_duplicate_error = (
                "duplicate key" in error_msg or 
                "unique constraint" in error_msg or
                "keywords_keyword_key" in error_msg
            )
            
            # Also check the underlying exception if it's wrapped
            if hasattr(e, '__cause__') and e.__cause__:
                cause_msg = str(e.__cause__).lower()
                is_duplicate_error = is_duplicate_error or (
                    "duplicate key" in cause_msg or 
                    "unique constraint" in cause_msg or
                    "keywords_keyword_key" in cause_msg
                )
            
            if is_duplicate_error:
                # Keyword already exists with a different category_id (or same one in race condition)
                # Query for the existing keyword ID (ignoring category_id)
                try:
                    existing = self.tx_manager.execute_query(
                        f"SELECT id FROM {TABLE_KEYWORDS} "
                        f"WHERE keyword = %s",
                        (keyword_blob,),
                        fetch=True
                    )
                    if existing:
                        keyword_id = existing[0][0]
                        self.logger.debug(
                            f"Keyword already exists globally (ID: {keyword_id}), "
                            f"skipping duplicate for category {category_id}"
                        )
                        return keyword_id
                except Exception as lookup_error:
                    self.logger.warning(
                        f"Failed to lookup existing keyword after duplicate error: {lookup_error}"
                    )
                # If lookup failed, return None (keyword exists but we couldn't get its ID)
                # This will be treated as a skip by the orchestrator
                return None
            
            # For other errors, log and raise
            self.logger.error(f"Failed to create keyword: {e}")
            raise RepositoryException(f"Keyword creation failed: {e}") from e
    
