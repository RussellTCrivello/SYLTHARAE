"""
Search History Service
Manages search history and saved searches using JSON storage (not database)
"""

import json
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import threading

logger = logging.getLogger(__name__)

# Thread-safe storage
_history_lock = threading.Lock()
_saved_searches_lock = threading.Lock()

# Storage paths
STORAGE_DIR = Path(__file__).parent.parent.parent / 'data'
HISTORY_FILE = STORAGE_DIR / 'search_history.json'
SAVED_SEARCHES_FILE = STORAGE_DIR / 'saved_searches.json'

# Ensure storage directory exists
STORAGE_DIR.mkdir(exist_ok=True)


class SearchHistoryService:

    
    @staticmethod
    def add_search(
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        result_count: int = 0,
        user_id: Optional[str] = None
    ) -> None:
        """
        Add a search to history.
        
        Args:
            query: Search query string
            filters: Optional search filters
            result_count: Number of results found
            user_id: Optional user identifier
        """
        try:
            with _history_lock:
                history = SearchHistoryService._load_history()
                
                # Add new search entry
                entry = {
                    'id': len(history) + 1,
                    'query': query,
                    'filters': filters or {},
                    'result_count': result_count,
                    'user_id': user_id,
                    'timestamp': datetime.now().isoformat()
                }
                
                history.append(entry)
                
                # Keep only last 100 searches
                if len(history) > 100:
                    history = history[-100:]
                
                SearchHistoryService._save_history(history)
                
        except Exception as e:
            logger.error(f"Error adding search to history: {e}", exc_info=True)
    
    @staticmethod
    def get_history(limit: int = 20, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get search history.
        
        Args:
            limit: Maximum number of entries to return
            user_id: Optional user identifier to filter by
        
        Returns:
            List of search history entries
        """
        try:
            with _history_lock:
                history = SearchHistoryService._load_history()
                
                # Filter by user if provided
                if user_id:
                    history = [h for h in history if h.get('user_id') == user_id]
                
                # Return most recent entries
                return history[-limit:][::-1]  # Reverse to show newest first
                
        except Exception as e:
            logger.error(f"Error getting search history: {e}", exc_info=True)
            return []
    
    @staticmethod
    def clear_history(user_id: Optional[str] = None) -> None:
        """
        Clear search history.
        
        Args:
            user_id: Optional user identifier to clear only their history
        """
        try:
            with _history_lock:
                if user_id:
                    history = SearchHistoryService._load_history()
                    history = [h for h in history if h.get('user_id') != user_id]
                    SearchHistoryService._save_history(history)
                else:
                    SearchHistoryService._save_history([])
                    
        except Exception as e:
            logger.error(f"Error clearing search history: {e}", exc_info=True)
    
    @staticmethod
    def _load_history() -> List[Dict[str, Any]]:
        """Load search history from JSON file."""
        try:
            if HISTORY_FILE.exists():
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading search history: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _save_history(history: List[Dict[str, Any]]) -> None:
        """Save search history to JSON file."""
        try:
            with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving search history: {e}", exc_info=True)


class SavedSearchesService:
    """
    Service for managing saved searches.
    
    Stores saved searches in JSON file (not database) to avoid schema changes.
    """
    
    @staticmethod
    def save_search(
        name: str,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None
    ) -> int:
        """
        Save a search.
        
        Args:
            name: Name for the saved search
            query: Search query string
            filters: Optional search filters
            user_id: Optional user identifier
        
        Returns:
            ID of the saved search
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                
                # Generate new ID
                new_id = max([s.get('id', 0) for s in searches] + [0]) + 1
                
                # Add new saved search
                entry = {
                    'id': new_id,
                    'name': name,
                    'query': query,
                    'filters': filters or {},
                    'user_id': user_id,
                    'created_at': datetime.now().isoformat(),
                    'last_used': None
                }
                
                searches.append(entry)
                SavedSearchesService._save_searches(searches)
                
                return new_id
                
        except Exception as e:
            logger.error(f"Error saving search: {e}", exc_info=True)
            raise
    
    @staticmethod
    def get_saved_searches(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all saved searches.
        
        Args:
            user_id: Optional user identifier to filter by
        
        Returns:
            List of saved searches
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                
                # Filter by user if provided
                if user_id:
                    searches = [s for s in searches if s.get('user_id') == user_id]
                
                return searches
                
        except Exception as e:
            logger.error(f"Error getting saved searches: {e}", exc_info=True)
            return []
    
    @staticmethod
    def get_saved_search(search_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific saved search by ID.
        
        Args:
            search_id: ID of the saved search
        
        Returns:
            Saved search dictionary or None if not found
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                for search in searches:
                    if search.get('id') == search_id:
                        return search
                return None
                
        except Exception as e:
            logger.error(f"Error getting saved search: {e}", exc_info=True)
            return None
    
    @staticmethod
    def update_saved_search(
        search_id: int,
        name: Optional[str] = None,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update a saved search.
        
        Args:
            search_id: ID of the saved search
            name: New name (optional)
            query: New query (optional)
            filters: New filters (optional)
        
        Returns:
            True if updated, False if not found
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                
                for search in searches:
                    if search.get('id') == search_id:
                        if name is not None:
                            search['name'] = name
                        if query is not None:
                            search['query'] = query
                        if filters is not None:
                            search['filters'] = filters
                        
                        SavedSearchesService._save_searches(searches)
                        return True
                
                return False
                
        except Exception as e:
            logger.error(f"Error updating saved search: {e}", exc_info=True)
            return False
    
    @staticmethod
    def delete_saved_search(search_id: int) -> bool:
        """
        Delete a saved search.
        
        Args:
            search_id: ID of the saved search to delete
        
        Returns:
            True if deleted, False if not found
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                
                original_count = len(searches)
                searches = [s for s in searches if s.get('id') != search_id]
                
                if len(searches) < original_count:
                    SavedSearchesService._save_searches(searches)
                    return True
                
                return False
                
        except Exception as e:
            logger.error(f"Error deleting saved search: {e}", exc_info=True)
            return False
    
    @staticmethod
    def mark_used(search_id: int) -> None:
        """
        Mark a saved search as used (update last_used timestamp).
        
        Args:
            search_id: ID of the saved search
        """
        try:
            with _saved_searches_lock:
                searches = SavedSearchesService._load_searches()
                
                for search in searches:
                    if search.get('id') == search_id:
                        search['last_used'] = datetime.now().isoformat()
                        SavedSearchesService._save_searches(searches)
                        break
                        
        except Exception as e:
            logger.error(f"Error marking saved search as used: {e}", exc_info=True)
    
    @staticmethod
    def _load_searches() -> List[Dict[str, Any]]:
        """Load saved searches from JSON file."""
        try:
            if SAVED_SEARCHES_FILE.exists():
                with open(SAVED_SEARCHES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return []
        except Exception as e:
            logger.error(f"Error loading saved searches: {e}", exc_info=True)
            return []
    
    @staticmethod
    def _save_searches(searches: List[Dict[str, Any]]) -> None:
        """Save saved searches to JSON file."""
        try:
            with open(SAVED_SEARCHES_FILE, 'w', encoding='utf-8') as f:
                json.dump(searches, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving saved searches: {e}", exc_info=True)

