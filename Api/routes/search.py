"""
Search routes with enhanced full-text search, sorting, history, and saved searches.

This module provides:
- Full-text search using PostgreSQL tsvector/tsquery
- Sortable search results
- Search history tracking
- Saved searches management
- Export functionality
"""

from flask import render_template, request, jsonify, session, send_file
from Api.utils import (
    get_optimized_search_results, select_info_sources, select_info_sides,
    select_info_categories, search_files_by_word
)
from settings import get_settings
from Api.services.search_service import SearchService
from Api.services.search_history import SearchHistoryService, SavedSearchesService
from Api.services.export_service import ExportService
from Api.services.analyst_categories import AnalystCategoryService
from Api.routes.analyst_categories import resolve_request_scope
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from core.errors import client_error

logger = logging.getLogger(__name__)


from core.security.rate_limit import limiter


def _request_bool(data: Dict[str, Any], key: str, default: bool) -> bool:
    """Parse JSON booleans and query-string booleans consistently."""
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_search_statuses(data: Dict[str, Any]) -> Optional[list]:
    """Return a validated Read/Unread filter, None when it was not supplied."""
    if 'status' not in data:
        return None
    raw = data.get('status')
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    values = [str(value).strip() for value in values if value is not None and str(value).strip()]
    if len(values) == 1 and values[0].lower() == 'none':
        return []
    normalized = []
    for value in values:
        status = value.title()
        if status not in {'Read', 'Unread'}:
            raise ValueError("status must contain only 'Read', 'Unread', or 'none'")
        if status not in normalized:
            normalized.append(status)
    return normalized


def _current_user_id():
    """AUDIT (API-04): the auth middleware stores the user id under
    ``session['auth_user_id']`` (core/security/flask_ext.py::_SESSION_USER_KEY);
    ``session['user_id']`` is never set, so search history and saved searches
    were always recorded with ``user_id = None`` and could not be scoped to,
    or cleaned up for, a user.
    """
    return session.get('auth_user_id')


def _advanced_search_run_url(query: str, filters: Optional[dict]) -> str:
    """Build a /search/advanced URL that restores a full search definition.

    The Advanced Search page persists its complete state (query, analyst
    scope, filters, match options, sort, page) in the URL and re-runs the
    search on load (static/js/pages/search-advanced-page.js —
    readDefinitionFromUrl). Saved searches store exactly that definition
    (the page saves ``query`` plus ``filters`` including ``scope``,
    ``sort_by`` and ``options``), so encoding it here makes "Run" on the
    Saved Searches management page land on the advanced page with every
    filter restored — not just the query string.

    Keep the parameter names in sync with SEARCH_URL_PARAMS /
    serializeDefinitionToParams in the page script.
    """
    f = filters if isinstance(filters, dict) else {}
    params: Dict[str, Any] = {}

    if query:
        params['q'] = query
    if f.get('scope') and f['scope'] != 'uncategorized':
        params['scope'] = f['scope']
    if f.get('sort_by') and f['sort_by'] != 'relevance':
        params['sort'] = f['sort_by']
    try:
        similarity_threshold = float(f.get('similarity_threshold', 0.32))
    except (TypeError, ValueError):
        similarity_threshold = 0.32
    if 0.05 <= similarity_threshold <= 0.95 and similarity_threshold != 0.32:
        params['sim'] = str(similarity_threshold)
    options = f.get('options') if isinstance(f.get('options'), dict) else {}
    if options.get('case_sensitive'):
        params['cs'] = '1'
    if options.get('whole_word'):
        params['ww'] = '1'
    if options.get('use_fuzzy') is False:
        params['fz'] = '0'
    if f.get('hide_duplicates'):
        params['hd'] = '1'

    def _ids(key: str) -> list:
        values = f.get(key) or []
        if not isinstance(values, list):
            values = [values]
        return [str(v) for v in values if v is not None and str(v) != '']

    for key, param in (('file_type', 'ft'), ('category_id', 'cat'),
                       ('analyst_category_id', 'acat'), ('source_id', 'src'),
                       ('side_id', 'side')):
        if _ids(key):
            params[param] = _ids(key)

    if f.get('date_from'):
        params['df'] = f['date_from']
    if f.get('date_to'):
        params['dt'] = f['date_to']

    status = f.get('status') if isinstance(f.get('status'), list) else ['Read']
    read = 'Read' in status
    unread = 'Unread' in status
    if read and unread:
        params['st'] = 'read,unread'
    elif unread:
        params['st'] = 'unread'
    elif not read:
        params['st'] = 'none'

    query_string = urlencode(params, doseq=True)
    return '/search/advanced' + (f'?{query_string}' if query_string else '')


def register_search_routes(app):
    """Register search routes with the Flask app"""
    
    @app.route('/search/enhanced')
    @limiter.limit("30 per minute")
    def search_enhanced_page():
        """Enhanced search page with full-text search, sorting, and filters"""
        from Api.utils import select_info_sources, select_info_sides, select_info_categories
        
        # Ensure all values are lists (handle None case)
        sources = select_info_sources() or []
        sides = select_info_sides() or []
        categories = select_info_categories() or []
        
        # Ensure they're iterable lists
        if not isinstance(sources, list):
            sources = list(sources) if sources else []
        if not isinstance(sides, list):
            sides = list(sides) if sides else []
        if not isinstance(categories, list):
            categories = list(categories) if categories else []
        
        return render_template('Search/search_enhanced.html',
                             sources=sources,
                             sides=sides,
                             categories=categories,
                             analyst_scope=resolve_request_scope())
    
    @app.route('/search')
    @limiter.limit("30 per minute")
    def search_page():
        """Enhanced Search with Content Filtering - Uses Google-like search algorithm"""
        settings = get_settings()
        search_config = settings.get_search_config()
        display_config = settings.get_display_config()
        
        query = request.args.get('q', '')
        page = request.args.get('page', 1, type=int)
        per_page = display_config.get('results_per_page', 10)
        max_results = search_config.get('max_results', 1000)
        results = []
        total_results = 0

        # Analyst-categorization search scope (FR-2.x): explicit parameter
        # wins and is persisted in the session (FR-2.3); otherwise the last
        # selection or the default "uncategorized only" (FR-2.1) applies.
        analyst_scope = resolve_request_scope()

        # Use Google-like search: supports multiple words, partial words, numbers
        # Minimum 2 chars (reduced from 3 to match enhanced search)
        if query and len(query.strip()) >= 2:
            # Use the enhanced search_files_by_word which now supports multiple words
            results, total_results = search_files_by_word(query, page, per_page, analyst_scope=analyst_scope)
            # Decorate results with analyst categories (own, separate
            # namespace - never merged with smart categories, FR-1.4)
            results = AnalystCategoryService.attach_categories_to_results(results)

        total_pages = (total_results + per_page - 1) // per_page if total_results > 0 else 1

        return render_template('Search/search.html',
                             query=query,
                             results=results,
                             page=page,
                             total_pages=total_pages,
                             total_results=total_results,
                             analyst_scope=analyst_scope)
    
    @app.route('/search/saved')
    def saved_searches_page():
        """Saved Searches management page.

        FUNC-02: this template and its page script existed without a route.
        The saved-search CRUD endpoints (``/api/search/saved``) have always
        been live; this renders the management view over the same service.
        """
        searches = SavedSearchesService.get_saved_searches(user_id=_current_user_id()) or []
        # "Run" restores the FULL definition on the Advanced Search page
        # (query + scope + filters + options), not just the query string.
        for entry in searches:
            if isinstance(entry, dict):
                entry['run_url'] = _advanced_search_run_url(
                    entry.get('query') or '', entry.get('filters'))
        return render_template('Search/saved_searches.html', saved_searches=searches)

    @app.route('/search/advanced')
    @limiter.limit("30 per minute")
    def search_advanced():
        """Advanced Search with Multiple Filters - Uses Google-like search algorithm"""
        # Get filter parameters
        file_type = request.args.get('file_type', '')
        source_id = request.args.get('source_id', '')
        side_id = request.args.get('side_id', '')
        date_from = request.args.get('date_from', '')
        date_to = request.args.get('date_to', '')
        query = request.args.get('q', '')

        # Analyst-categorization search scope (FR-2.x) - first-class input on
        # the Advanced Search interface (FR-3.2)
        analyst_scope = resolve_request_scope()
        
        results = []
        sources = select_info_sources() or []
        sides = select_info_sides() or []
        
        categories = select_info_categories() or []
        
        # Ensure they're iterable lists
        if not isinstance(sources, list):
            sources = list(sources) if sources else []
        if not isinstance(sides, list):
            sides = list(sides) if sides else []
        if not isinstance(categories, list):
            categories = list(categories) if categories else []
        
        settings = get_settings()
        search_config = settings.get_search_config()
        max_results = search_config.get('max_results', 1000)
        
        # Use Google-like search: supports multiple words, partial words, numbers
        # Minimum 2 chars to match enhanced search behavior
        if query or file_type or source_id or side_id or date_from or date_to:
            # Use optimized search function which now supports multiple words
            results = get_optimized_search_results(
                query=query if query and len(query.strip()) >= 2 else None,
                file_type=file_type,
                source_id=int(source_id) if source_id else None,
                side_id=int(side_id) if side_id else None,
                date_from=date_from,
                date_to=date_to,
                limit=min(100, max_results),  # Use settings max_results
                analyst_scope=analyst_scope
            )

        # Analyst categories for the manual-categorization control - a
        # dedicated namespace, never the smart category list (FR-1.4)
        analyst_categories = AnalystCategoryService.list_categories()

        return render_template('Search/search_advanced.html',
                             query=query,
                             file_type=file_type,
                             source_id=source_id,
                             side_id=side_id,
                             date_from=date_from,
                             date_to=date_to,
                             results=results,
                             sources=sources,
                             sides=sides,
                             categories=categories,
                             analyst_scope=analyst_scope,
                             analyst_categories=analyst_categories)
    
    @app.route('/search/advanced', methods=['POST'])
    @limiter.limit("30 per minute")
    def search_advanced_api():
        """Advanced Search API endpoint for JSON responses"""
        try:
            # Get search criteria from JSON request
            criteria = request.get_json()
            if not criteria:
                return jsonify({'error': 'No search criteria provided'}), 400
            
            # Extract search parameters
            query = criteria.get('query', '')
            file_type = criteria.get('file_type', '')
            source_id = criteria.get('source_id', '')
            side_id = criteria.get('side_id', '')
            date_from = criteria.get('date_from', '')
            date_to = criteria.get('date_to', '')
            category_id = criteria.get('category_id', '')

            # Analyst-categorization search scope (FR-2.x)
            analyst_scope = resolve_request_scope()

            settings = get_settings()
            search_config = settings.get_search_config()
            max_results = search_config.get('max_results', 1000)

            # Perform search using optimized search function (now supports multiple words)
            results = get_optimized_search_results(
                query=query if query and len(query.strip()) >= 2 else None,
                file_type=file_type,
                source_id=int(source_id) if source_id else None,
                side_id=int(side_id) if side_id else None,
                date_from=date_from,
                date_to=date_to,
                category_id=int(category_id) if category_id else None,
                limit=min(100, max_results),  # Use settings max_results
                analyst_scope=analyst_scope
            )
            
            # Format results for JSON response
            formatted_results = []
            for result in results:
                formatted_results.append({
                    'id': result[0],
                    'file_name': result[1],
                    'file_type': result[2],
                    'file_date': result[3].isoformat() if result[3] else None,
                    'source_name': result[4],
                    'side_name': result[5],
                    'file_status': result[6] if len(result) > 6 else 'Unknown'
                })
            
            return jsonify(formatted_results)
            
        except Exception as e:
            logger.error(f"Search API error: {e}")
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    # ==================== ENHANCED SEARCH API ====================
    
    @app.route('/api/search', methods=['GET', 'POST'])
    @limiter.limit("30 per minute")
    def api_search():
        """
        Enhanced search API with full-text search, sorting, and filtering.
        
        Query Parameters (GET) or JSON Body (POST):
        - query: Search query string
        - file_type: Filter by file type
        - source_id: Filter by source ID
        - side_id: Filter by side ID
        - date_from: Start date (YYYY-MM-DD)
        - date_to: End date (YYYY-MM-DD)
        - category_id: Filter by category ID
        - sort_by: Sort field ('relevance', 'date', 'name', 'type', 'size')
        - sort_order: Sort order ('asc' or 'desc')
        - page: Page number (default: 1)
        - per_page: Results per page (default: 50)
        - use_fulltext: Use PostgreSQL full-text search (default: true)
        """
        try:
            # Get parameters from GET or POST
            if request.method == 'POST':
                data = request.get_json() or {}
                # Handle multiple values from JSON (arrays)
                source_ids = data.get('source_id') or data.get('source_ids', [])
                side_ids = data.get('side_id') or data.get('side_ids', [])
                category_ids = data.get('category_id') or data.get('category_ids', [])
                analyst_category_ids = data.get('analyst_category_id') or data.get('analyst_category_ids', [])
                file_type = data.get('file_type')
            else:
                # Handle GET parameters - use getlist for multiple values
                data = {}
                for key in request.args:
                    values = request.args.getlist(key)
                    if len(values) == 1:
                        data[key] = values[0]
                    else:
                        data[key] = values
                
                # Extract multiple values from query params
                source_ids = request.args.getlist('source_id')
                side_ids = request.args.getlist('side_id')
                category_ids = request.args.getlist('category_id')
                analyst_category_ids = request.args.getlist('analyst_category_id')
                file_type = request.args.getlist('file_type') if request.args.getlist('file_type') else data.get('file_type')
            
            raw_query = data.get('query', '')
            if not isinstance(raw_query, str):
                return jsonify({'error': 'query must be a string'}), 400
            query = raw_query.strip()
            try:
                file_statuses = _parse_search_statuses(data)
            except ValueError as status_error:
                return jsonify({'error': str(status_error)}), 400
            
            # Convert to lists of integers, handle both single and multiple values
            if source_ids:
                if isinstance(source_ids, (list, tuple)):
                    source_ids = [int(sid) for sid in source_ids if sid and str(sid).isdigit()]
                else:
                    try:
                        source_ids = [int(source_ids)]
                    except (ValueError, TypeError):
                        source_ids = []
            else:
                source_ids = []
            
            if side_ids:
                if isinstance(side_ids, (list, tuple)):
                    side_ids = [int(sid) for sid in side_ids if sid and str(sid).isdigit()]
                else:
                    try:
                        side_ids = [int(side_ids)]
                    except (ValueError, TypeError):
                        side_ids = []
            else:
                side_ids = []
            
            if category_ids:
                if isinstance(category_ids, (list, tuple)):
                    category_ids = [int(cid) for cid in category_ids if cid and str(cid).isdigit()]
                else:
                    try:
                        category_ids = [int(category_ids)]
                    except (ValueError, TypeError):
                        category_ids = []
            else:
                category_ids = []

            # Analyst (manual) category filter - parsed independently from
            # the smart category_ids above (FR-1.4 separation)
            if analyst_category_ids:
                if isinstance(analyst_category_ids, (list, tuple)):
                    analyst_category_ids = [int(cid) for cid in analyst_category_ids if cid and str(cid).isdigit()]
                else:
                    try:
                        analyst_category_ids = [int(analyst_category_ids)]
                    except (ValueError, TypeError):
                        analyst_category_ids = []
            else:
                analyst_category_ids = []
            
            # For backward compatibility, use first value if single selection
            source_id = source_ids[0] if source_ids else None
            side_id = side_ids[0] if side_ids else None
            category_id = category_ids[0] if category_ids else None
            
            date_from = data.get('date_from')
            date_to = data.get('date_to')
            sort_by = str(data.get('sort_by', 'relevance')).strip().lower()
            sort_order = str(data.get('sort_order', 'desc')).strip().lower()
            if sort_by not in {'relevance', 'date', 'name', 'type', 'size'}:
                return jsonify({'error': 'sort_by must be relevance, date, name, type, or size'}), 400
            if sort_order not in {'asc', 'desc'}:
                return jsonify({'error': "sort_order must be 'asc' or 'desc'"}), 400
            try:
                page = max(1, int(data.get('page', 1) or 1))
                per_page = min(max(1, int(data.get('per_page', 50) or 50)), 200)
            except (TypeError, ValueError):
                return jsonify({'error': 'page and per_page must be whole numbers'}), 400
            use_fulltext = _request_bool(data, 'use_fulltext', True)
            use_advanced = _request_bool(data, 'use_advanced', True)
            use_bm25 = _request_bool(data, 'use_bm25', True)
            use_expansion = _request_bool(data, 'use_expansion', True)
            use_fuzzy = _request_bool(data, 'use_fuzzy', True)
            case_sensitive = _request_bool(data, 'case_sensitive', False)
            whole_word = _request_bool(data, 'whole_word', False)
            hide_duplicates = _request_bool(data, 'hide_duplicates', False)
            if hide_duplicates or case_sensitive or whole_word:
                use_advanced = True

            offset = (page - 1) * per_page

            # Analyst-categorization search scope (FR-2.x): explicit
            # parameter wins and is session-persisted (FR-2.3); default is
            # "uncategorized files only" (FR-2.1). Applies to every search
            # path below and filters strictly on analyst categorization
            # status - smart categorization is never consulted (FR-2.4).
            analyst_scope = resolve_request_scope()

            has_advanced_filters = any((
                file_type, source_ids, side_ids, category_ids, analyst_category_ids,
                date_from, date_to, file_statuses is not None
            ))

            # Filter-only searches are valid when an explicit filter is
            # supplied. With neither query nor filters, preserve the empty
            # response instead of accidentally scanning the whole corpus.
            if use_advanced and (query or has_advanced_filters):
                results, total_count = SearchService.advanced_search(
                    query=query,
                    file_type=file_type,
                    source_id=source_id,
                    side_id=side_id,
                    date_from=date_from,
                    date_to=date_to,
                    category_id=category_id,
                    source_ids=source_ids if source_ids else None,
                    side_ids=side_ids if side_ids else None,
                    category_ids=category_ids if category_ids else None,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    limit=per_page,
                    offset=offset,
                    use_bm25=use_bm25,
                    use_expansion=use_expansion,
                    use_fuzzy=use_fuzzy,
                    analyst_scope=analyst_scope,
                    analyst_category_ids=analyst_category_ids if analyst_category_ids else None,
                    file_statuses=file_statuses,
                    hide_duplicates=hide_duplicates,
                    case_sensitive=case_sensitive,
                    whole_word=whole_word,
                )
            elif use_fulltext and query:
                results, total_count = SearchService.full_text_search(
                    query=query,
                    file_type=file_type,
                    source_id=source_id,
                    side_id=side_id,
                    date_from=date_from,
                    date_to=date_to,
                    category_id=category_id,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    limit=per_page,
                    offset=offset,
                    analyst_scope=analyst_scope,
                    hide_duplicates=hide_duplicates,
                )
            else:
                # Fallback to simple search
                if query:
                    results, total_count = SearchService.simple_search(
                        query=query,
                        limit=per_page,
                        offset=offset,
                        analyst_scope=analyst_scope
                    )
                else:
                    results, total_count = [], 0

            # Decorate results with analyst categories in their own field -
            # visually and structurally separate from any smart-category
            # field on the same result (FR-1.4).
            results = AnalystCategoryService.attach_categories_to_results(results)
            
            # Save to search history
            if query:
                user_id = _current_user_id()
                SearchHistoryService.add_search(
                    query=query,
                    filters={
                        'file_type': file_type,
                        'source_id': source_id,
                        'side_id': side_id,
                        'date_from': date_from,
                        'date_to': date_to,
                        'category_id': category_id,
                        'status': file_statuses,
                        'analyst_scope': analyst_scope,
                        'hide_duplicates': hide_duplicates,
                    },
                    result_count=total_count,
                    user_id=user_id
                )
            
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
            
            return jsonify({
                'results': results,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total_count,
                    'total_pages': total_pages,
                    'has_prev': page > 1,
                    'has_next': page < total_pages
                },
                'query': query,
                'filters': {
                    'file_type': file_type,
                    'source_id': source_id,
                    'side_id': side_id,
                    'date_from': date_from,
                    'date_to': date_to,
                    'category_id': category_id,
                    'status': file_statuses,
                    # Analyst-categorization scope actually applied (FR-2.x)
                    'analyst_scope': analyst_scope
                },
                'sort': {
                    'by': sort_by,
                    'order': sort_order
                }
            })
            
        except Exception as e:
            logger.error(f"Enhanced search API error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    # ==================== AUTOCOMPLETE / SUGGESTIONS ====================
    
    @app.route('/api/search/autocomplete', methods=['GET'])
    def api_autocomplete():
        """
        Get autocomplete suggestions for a search query.
        
        Query Parameters:
        - query: Partial query string (required)
        - limit: Maximum number of suggestions (default: 10)
        """
        try:
            query = request.args.get('query', '').strip()
            limit = int(request.args.get('limit', 10))
            
            if not query or len(query) < 2:
                return jsonify({
                    'suggestions': [],
                    'count': 0
                })
            
            suggestions = SearchService.autocomplete(query, limit=limit)
            
            return jsonify({
                'suggestions': suggestions,
                'count': len(suggestions),
                'query': query
            })
            
        except Exception as e:
            logger.error(f"Autocomplete API error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/suggestions', methods=['GET'])
    def api_suggestions():
        """
        Get simple search suggestions (just text strings).
        
        Query Parameters:
        - query: Partial query string (required)
        - limit: Maximum number of suggestions (default: 5)
        """
        try:
            query = request.args.get('query', '').strip()
            limit = int(request.args.get('limit', 5))
            
            if not query or len(query) < 2:
                return jsonify({
                    'suggestions': []
                })
            
            suggestions = SearchService.get_search_suggestions(query, limit=limit)
            
            return jsonify({
                'suggestions': suggestions
            })
            
        except Exception as e:
            logger.error(f"Suggestions API error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    # ==================== SEARCH HISTORY ====================
    
    @app.route('/api/search/history', methods=['GET', 'POST'])
    def api_search_history():
        """
        Get or add search history.
        
        GET:
        - limit: Maximum number of entries (default: 20)
        
        POST:
        - query: Search query string
        - filters: Optional search filters dictionary
        - result_count: Optional number of results
        """
        try:
            if request.method == 'POST':
                # Add search to history
                data = request.get_json() or {}
                query = data.get('query', '').strip()
                filters = data.get('filters', {})
                result_count = data.get('result_count', 0)
                user_id = _current_user_id()
                
                if query:
                    SearchHistoryService.add_search(
                        query=query,
                        filters=filters,
                        result_count=result_count,
                        user_id=user_id
                    )
                    return jsonify({
                        'success': True,
                        'message': 'Search added to history'
                    }), 201
                else:
                    return jsonify({
                        'success': False,
                        'error': 'Query is required'
                    }), 400
            else:
                # GET - retrieve search history
                limit = int(request.args.get('limit', 20))
                user_id = _current_user_id()
                
                history = SearchHistoryService.get_history(limit=limit, user_id=user_id)
                
                return jsonify({
                    'history': history,
                    'count': len(history)
                })
            
        except Exception as e:
            logger.error(f"Search history API error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/history', methods=['DELETE'])
    def api_clear_search_history():
        """Clear search history."""
        try:
            user_id = _current_user_id()
            SearchHistoryService.clear_history(user_id=user_id)
            
            return jsonify({'success': True, 'message': 'Search history cleared'})
            
        except Exception as e:
            logger.error(f"Clear search history error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    # ==================== SAVED SEARCHES ====================
    
    @app.route('/api/search/saved', methods=['GET'])
    def api_get_saved_searches():
        """Get all saved searches."""
        try:
            user_id = _current_user_id()
            searches = SavedSearchesService.get_saved_searches(user_id=user_id)
            
            return jsonify({
                'searches': searches,
                'count': len(searches)
            })
            
        except Exception as e:
            logger.error(f"Get saved searches error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/saved', methods=['POST'])
    def api_save_search():
        """
        Save a search.
        
        JSON Body:
        - name: Name for the saved search
        - query: Search query string
        - filters: Optional search filters dictionary
        """
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            name = data.get('name', '').strip()
            query = data.get('query', '').strip()
            filters = data.get('filters', {})
            
            if not name:
                return jsonify({'error': 'Name is required'}), 400
            
            user_id = _current_user_id()
            search_id = SavedSearchesService.save_search(
                name=name,
                query=query,
                filters=filters,
                user_id=user_id
            )
            
            return jsonify({
                'success': True,
                'search_id': search_id,
                'message': 'Search saved successfully'
            }), 201
            
        except Exception as e:
            logger.error(f"Save search error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/saved/<int:search_id>', methods=['GET'])
    def api_get_saved_search(search_id):
        """Get a specific saved search."""
        try:
            search = SavedSearchesService.get_saved_search(search_id)
            
            if not search:
                return jsonify({'error': 'Saved search not found'}), 404
            
            # Mark as used
            SavedSearchesService.mark_used(search_id)
            
            return jsonify({'search': search})
            
        except Exception as e:
            logger.error(f"Get saved search error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/saved/<int:search_id>', methods=['PUT'])
    def api_update_saved_search(search_id):
        """
        Update a saved search.
        
        JSON Body (all optional):
        - name: New name
        - query: New query
        - filters: New filters
        """
        try:
            data = request.get_json() or {}
            
            success = SavedSearchesService.update_saved_search(
                search_id=search_id,
                name=data.get('name'),
                query=data.get('query'),
                filters=data.get('filters')
            )
            
            if not success:
                return jsonify({'error': 'Saved search not found'}), 404
            
            return jsonify({
                'success': True,
                'message': 'Search updated successfully'
            })
            
        except Exception as e:
            logger.error(f"Update saved search error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    @app.route('/api/search/saved/<int:search_id>', methods=['DELETE'])
    def api_delete_saved_search(search_id):
        """Delete a saved search."""
        try:
            success = SavedSearchesService.delete_saved_search(search_id)
            
            if not success:
                return jsonify({'error': 'Saved search not found'}), 404
            
            return jsonify({
                'success': True,
                'message': 'Search deleted successfully'
            })
            
        except Exception as e:
            logger.error(f"Delete saved search error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)
    
    # ==================== EXPORT SEARCH RESULTS ====================
    
    @app.route('/api/search/export', methods=['POST'])
    @limiter.limit("6 per minute")
    def api_export_search_results():
        """Export the authoritative result set of a query.

        The client sends the *query definition* - what was searched for, under
        which filters, in which order, over which scope - and this endpoint
        re-runs it. The rows in the file come from the database, never from the
        browser, so an export cannot contain a stale page, a truncated list or
        rows that were never there.

        JSON Body:
        - query, file_type(s), source_id(s), side_id(s), category_id(s),
          analyst_category_id(s), status, date_from, date_to, sort_by, sort_order
        - export_scope: 'page' (the page being read), 'filtered' (the whole
          result set), 'dataset' (everything the filters allow, query dropped)
        - analyst_scope: the independent analyst-categorization scope
        - page, per_page: required only for scope='page'
        - format: 'csv', 'excel' or 'json'
        - filename: optional stem

        The response says what it contains: `X-Export-Scope`, `X-Export-Rows`,
        `X-Export-Total` and `X-Export-Truncated`.
        """
        from Api.services import search_export

        try:
            definition = search_export.parse(request.get_json(silent=True))
        except search_export.ExportRequestError as refused:
            return jsonify({'success': False, 'error': str(refused),
                            'code': 'invalid_export_request'}), 400

        try:
            result = search_export.resolve(definition, resolve_request_scope())
            if not result.rows:
                return jsonify({
                    'success': False,
                    'error': 'Nothing to export: the query and filters produced '
                             'no results.',
                    'scope': definition.scope,
                    'total': 0,
                }), 400
            data, mimetype, extension = search_export.export_bytes(result)
            response = send_file(
                data, mimetype=mimetype, as_attachment=True,
                download_name=(f"{search_export.suggested_filename(definition)}"
                               f".{extension}"))
            for header, value in result.headers.items():
                response.headers[header] = value
            return response

        except Exception as e:
            logger.error(f"Export search results error: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)

    @app.route('/api/search/export-filenames', methods=['POST'])
    @limiter.limit("6 per minute")
    def api_export_search_filenames():
        """Export only the filenames from the authoritative current search."""
        import csv
        from io import BytesIO, StringIO
        from flask import Response
        from Api.services import search_export
        from Api.services.document_intelligence import spreadsheet_safe_text

        try:
            definition = search_export.parse(request.get_json(silent=True))
        except search_export.ExportRequestError as refused:
            return jsonify({'success': False, 'error': str(refused),
                            'code': 'invalid_export_request'}), 400

        try:
            result = search_export.resolve(definition, resolve_request_scope())
            if not result.rows:
                return jsonify({
                    'success': False,
                    'error': 'Nothing to export: the query and filters produced no results.',
                    'scope': definition.scope,
                    'total': 0,
                }), 400

            values = [
                (spreadsheet_safe_text(row.get('file_name', '')),
                 spreadsheet_safe_text(row.get('file_type', 'Unknown')))
                for row in result.rows
            ]
            base = f"{search_export.suggested_filename(definition)}_filenames"
            if definition.format == 'csv':
                output = StringIO(newline='')
                writer = csv.writer(output)
                writer.writerow(['File name', 'File type'])
                writer.writerows(values)
                response = Response(
                    output.getvalue().encode('utf-8-sig'),
                    mimetype='text/csv; charset=utf-8')
                response.headers['Content-Disposition'] = f'attachment; filename="{base}.csv"'
            elif definition.format == 'excel':
                try:
                    from openpyxl import Workbook
                except ImportError:
                    return jsonify({'success': False, 'error': 'XLSX export is unavailable on this server'}), 503
                workbook = Workbook(write_only=True)
                sheet = workbook.create_sheet('Matching filenames')
                sheet.append(['File name', 'File type'])
                for row in values:
                    sheet.append(list(row))
                buffer = BytesIO()
                workbook.save(buffer)
                buffer.seek(0)
                response = send_file(
                    buffer,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True,
                    download_name=f'{base}.xlsx',
                )
            else:
                return jsonify({'success': False, 'error': 'format must be csv or excel'}), 400

            for header, value in result.headers.items():
                response.headers[header] = value
            return response
        except Exception as e:
            logger.error(f"Filename search export failed: {e}", exc_info=True)
            return client_error(e, subsystem='Api.routes.search', status=500)

