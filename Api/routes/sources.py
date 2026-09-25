"""
Sources routes
"""

from flask import render_template, redirect, url_for, request, flash
from Api.utils import (
    execute_query, select_info_sources, select_info_sides,
    get_source_with_stats, insert_info_sources
)
import logging

logger = logging.getLogger(__name__)


def register_sources_routes(app):
    """Register sources routes with the Flask app"""
    
    @app.route('/sources')
    def sources_list():
        """Source Management with CURSOR-BASED PAGINATION for billion+ records"""
        from Api.cursor_pagination import get_cursor_paginator, SortDirection
        
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        limit = max(1, min(1000, limit))
        search = request.args.get('search', '')
        
        # Calculate approximate page number for display (cursor pagination doesn't use real pages)
        estimated_page = 1
        if cursor:
            # Rough estimate: assume each page has 'limit' items
            estimated_page = max(1, (cursor // limit) + 1)
        
        try:
            # Build filters
            filters = {}
            joins = [
                'LEFT JOIN hash_contexts hc ON s.id = hc.source_id',
                'LEFT JOIN paths p ON p.context_id = hc.id',
                'LEFT JOIN categorys c ON s.category_id = c.id',
                'LEFT JOIN words w ON c.word_id = w.id'
            ]
            
            if search:
                filters['s.name'] = {'op': 'ILIKE', 'value': f'%{search}%'}
            
            # Use cursor-based pagination
            paginator = get_cursor_paginator('sources')
            
            select_columns = [
                's.id', 's.name', 's.job', 's.importance', 's.country', 's.city',
                's.description', 's.accounts', 's.note', 's.attachments', 's.date_creation',
                's.ownership', 's.access_status', 's.entry_date', 's.category_id',
                'w.word as category_name',
                'COUNT(DISTINCT p.id) as doc_count'
            ]
            
            result = paginator.get_page(
                cursor=cursor,
                limit=limit,
                sort_column='s.importance',
                sort_direction=SortDirection.DESC,
                filters=filters if filters else None,
                joins=joins,
                select_columns=select_columns,
                table_alias='s'
            )
            
            # Format results
            formatted_sources = []
            for row in result['data']:
                # Map entry_date to date_source_discovery for frontend compatibility
                entry_date = row.get('entry_date')
                date_source_discovery = None
                if entry_date:
                    if isinstance(entry_date, str):
                        date_source_discovery = entry_date
                    elif hasattr(entry_date, 'isoformat'):
                        date_source_discovery = entry_date.isoformat()
                    else:
                        date_source_discovery = str(entry_date)
                
                formatted_sources.append({
                    'id': row.get('id'),
                    'name': row.get('name'),
                    'job': row.get('job'),
                    'importance': row.get('importance'),
                    'country': row.get('country'),
                    'city': row.get('city'),
                    'description': row.get('description'),
                    'accounts': row.get('accounts'),
                    'note': row.get('note'),
                    'attachments': row.get('attachments'),
                    'date_creation': row.get('date_creation'),
                    'ownership': row.get('ownership'),
                    'access_status': row.get('access_status'),
                    'date_source_discovery': date_source_discovery,
                    'category_id': row.get('category_id'),
                    'category_name': row.get('category_name'),
                    'doc_count': row.get('doc_count', 0)
                })
            
            # Get total count for display (approximate)
            total_estimated = result.get('total_estimated') or 0
            has_next = result.get('has_next', False)
            has_prev = result.get('has_prev', False)
            
            # Calculate total_pages: if we have next/prev pages, ensure at least 2 pages
            if total_estimated > 0:
                total_pages = max(1, (total_estimated + limit - 1) // limit)
            elif has_next or has_prev:
                # If we have pagination but no estimate, set to at least 2
                total_pages = 2
            else:
                total_pages = 1
            
            return render_template('Sources/sources_list.html',
                                 sources=formatted_sources,
                                 search=search,
                                 # Required template variables
                                 total_sources=total_estimated,
                                 page=estimated_page,
                                 total_pages=total_pages,
                                 # Cursor pagination data
                                 cursor_pagination=True,
                                 next_cursor=result.get('next_cursor'),
                                 prev_cursor=result.get('prev_cursor'),
                                 has_next=has_next,
                                 has_prev=has_prev,
                                 total_estimated=total_estimated,
                                 query_time_ms=result.get('query_time_ms', 0))
        
        except Exception as e:
            # Log full error details for debugging
            import traceback
            error_details = traceback.format_exc()
            logger.error(f"Error in sources_list: {e}\n{error_details}")
            flash(f'Error loading sources: {str(e)}', 'error')
            # Provide all required template variables even on error
            return render_template('Sources/sources_list.html',
                                 sources=[],
                                 search=search or '',
                                 total_sources=0,
                                 page=1,
                                 total_pages=1,
                                 cursor_pagination=True,
                                 next_cursor=None,
                                 prev_cursor=None,
                                 has_next=False,
                                 has_prev=False,
                                 total_estimated=0,
                                 query_time_ms=0,
                                 error=str(e))
    
    @app.route('/source/add', methods=['GET', 'POST'])
    def source_add():
        """Add new source - redirects to sources list with modal"""
        # Redirect to sources list page (modal will be opened via JavaScript)
        return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>')
    def source_detail(source_id):
        """View source details page - OPTIMIZED with aggregated stats"""
        try:

            source_data = get_source_with_stats(source_id)
            
            if not source_data:
                flash('Source not found', 'error')
                return redirect(url_for('sources_list'))
            
            return render_template('Sources/source_detail.html', 
                                 source=source_data)
        except Exception as e:
            logger.error(f"Error loading source {source_id}: {e}")
            flash('Error loading source', 'error')
            return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>/categories-keywords')
    def source_categories_keywords(source_id):
        """View categories and keywords for a specific source"""
        try:
            source_data = get_source_with_stats(source_id)
            
            if not source_data:
                flash('Source not found', 'error')
                return redirect(url_for('sources_list'))
            
            return render_template('Sources/source_categories_keywords.html', 
                                 source=source_data)
        except Exception as e:
            logger.error(f"Error loading source categories/keywords {source_id}: {e}")
            flash('Error loading source categories/keywords', 'error')
            return redirect(url_for('sources_list'))
    
    @app.route('/sources/<int:source_id>/edit', methods=['GET', 'POST'])
    def source_edit(source_id):
        """Edit source - redirects to sources list with modal"""
        # Redirect to sources list page with edit parameter (modal will be opened via JavaScript)
        return redirect(url_for('sources_list', edit=source_id))
