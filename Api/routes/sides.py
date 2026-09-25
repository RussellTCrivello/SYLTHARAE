"""
Sides routes
"""

from flask import render_template, redirect, url_for, request, flash
from Api.utils import (
    execute_query, select_info_sides
)
import logging

logger = logging.getLogger(__name__)


def register_sides_routes(app):
    """Register sides routes with the Flask app"""
    
    @app.route('/sides')
    def sides_list():
        """Side Management with CURSOR-BASED PAGINATION for billion+ records"""
        from Api.cursor_pagination import get_cursor_paginator, SortDirection
        
        cursor = request.args.get('cursor', type=int)
        limit = request.args.get('limit', 50, type=int)
        limit = max(1, min(1000, limit))
        search = request.args.get('search', '')
        
        try:
            # Build filters
            filters = {}
            joins = [
                'LEFT JOIN hash_contexts hc ON si.id = hc.side_id',
                'LEFT JOIN paths p ON p.context_id = hc.id'
            ]
            
            if search:
                filters['si.name'] = {'op': 'ILIKE', 'value': f'%{search}%'}
            
            # Use cursor-based pagination
            paginator = get_cursor_paginator('sides')
            
            # Note: For GROUP BY queries with aggregates, cursor pagination groups by primary key
            select_columns = [
                'si.id', 'si.name', 'si.importance', 'si.date_creation',
                'COUNT(DISTINCT p.id) as doc_count',
                'COUNT(DISTINCT hc.source_id) as source_count'
            ]
            
            result = paginator.get_page(
                cursor=cursor,
                limit=limit,
                sort_column='si.importance',
                sort_direction=SortDirection.DESC,
                filters=filters if filters else None,
                joins=joins,
                select_columns=select_columns,
                table_alias='si'
            )
            
            # Format results
            formatted_sides = []
            for row in result['data']:
                formatted_sides.append({
                    'id': row.get('id'),
                    'name': row.get('name'),
                    'importance': row.get('importance'),
                    'date_creation': row.get('date_creation'),
                    'doc_count': row.get('doc_count', 0),
                    'source_count': row.get('source_count', 0)
                })
            
            # Calculate approximate page number for display (cursor pagination doesn't use real pages)
            estimated_page = 1
            if cursor:
                # Rough estimate: assume each page has 'limit' items
                estimated_page = max(1, (cursor // limit) + 1)
            
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
            
            return render_template('Side/sides_list.html',
                                 sides=formatted_sides,
                                 search=search,
                                 cursor_pagination=True,
                                 next_cursor=result.get('next_cursor'),
                                 prev_cursor=result.get('prev_cursor'),
                                 has_next=has_next,
                                 has_prev=has_prev,
                                 total_estimated=total_estimated,
                                 total_sides=total_estimated,
                                 page=estimated_page,
                                 total_pages=total_pages,
                                 query_time_ms=result.get('query_time_ms', 0))
        
        except Exception as e:
            logger.error(f"Error in sides_list: {e}", exc_info=True)
            flash('Error loading sides. Please check database connection.', 'error')
            return render_template('Side/sides_list.html',
                                 sides=[],
                                 search=search,
                                 cursor_pagination=True,
                                 total_sides=0,
                                 total_estimated=0,
                                 page=1,
                                 total_pages=1,
                                 next_cursor=None,
                                 prev_cursor=None,
                                 has_next=False,
                                 has_prev=False,
                                 error=str(e))
    
    @app.route('/side/add', methods=['GET', 'POST'])
    def side_add():
        """Add new side - redirects to sides list with modal"""
        # Redirect to sides list page (modal will be opened via JavaScript)
        return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>')
    def side_detail(side_id):
        """View side details page - OPTIMIZED with aggregated stats"""
        try:
            # 🚀 OPTIMIZED: Get side data with document count in single query
            side_data = execute_query("""
                SELECT si.id, si.name, si.importance, si.date_creation,
                       COUNT(DISTINCT p.id) as doc_count,
                       COUNT(DISTINCT hc.source_id) as source_count,
                       COALESCE(SUM(p.file_size), 0) as total_size
                FROM sides si
                LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                LEFT JOIN paths p ON p.context_id = hc.id
                WHERE si.id = %s
                GROUP BY si.id, si.name, si.importance, si.date_creation
            """, (side_id,), fetch="one")
            
            if not side_data:
                flash('Side not found', 'error')
                return redirect(url_for('sides_list'))
            
            return render_template('Side/side_detail.html', 
                                 side={
                                     'id': side_data[0], 'name': side_data[1], 
                                     'importance': side_data[2], 'date_creation': side_data[3],
                                     'doc_count': side_data[4] or 0,
                                     'source_count': side_data[5] or 0,
                                     'total_size': side_data[6] or 0
                                 })
        except Exception as e:
            logger.error(f"Error loading side {side_id}: {e}")
            flash('Error loading side', 'error')
            return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>/categories-keywords')
    def side_categories_keywords(side_id):
        """View categories and keywords for a specific side"""
        try:
            side_data = execute_query("""
                SELECT si.id, si.name, si.importance, si.date_creation,
                       COUNT(DISTINCT p.id) as doc_count,
                       COUNT(DISTINCT hc.source_id) as source_count,
                       COALESCE(SUM(p.file_size), 0) as total_size
                FROM sides si
                LEFT JOIN hash_contexts hc ON si.id = hc.side_id
                LEFT JOIN paths p ON p.context_id = hc.id
                WHERE si.id = %s
                GROUP BY si.id, si.name, si.importance, si.date_creation
            """, (side_id,), fetch="one")
            
            if not side_data:
                flash('Side not found', 'error')
                return redirect(url_for('sides_list'))
            
            return render_template('Side/side_categories_keywords.html', 
                                 side={
                                     'id': side_data[0], 'name': side_data[1], 
                                     'importance': side_data[2], 'date_creation': side_data[3],
                                     'doc_count': side_data[4] or 0,
                                     'source_count': side_data[5] or 0,
                                     'total_size': side_data[6] or 0
                                 })
        except Exception as e:
            logger.error(f"Error loading side categories/keywords {side_id}: {e}")
            flash('Error loading side categories/keywords', 'error')
            return redirect(url_for('sides_list'))
    
    @app.route('/sides/<int:side_id>/edit', methods=['GET', 'POST'])
    def side_edit(side_id):
        """Edit side - redirects to sides list with modal"""
        # Redirect to sides list page with edit parameter (modal will be opened via JavaScript)
        return redirect(url_for('sides_list', edit=side_id))
