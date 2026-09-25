"""
Routes module - registers all routes with the Flask app
"""

def register_all_routes(app, babel_instance=None):
    """Register all routes with the Flask app"""
    # Import and register route modules
    from . import common, dashboard, categories, search
    from . import analyst_categories
    from . import analytics, analysis, archives, keywords, words, sources, sides, api, performance, notifications, notifications_page
    from . import preview, import_export, setup
    from . import operations_api, operations_pages
    # Import new settings routes (replaces old settings.py) - optional
    try:
        from settings.routes import register_settings_routes
        has_settings_routes = True
    except ImportError:
        # Settings routes module doesn't exist - create a no-op function
        def register_settings_routes(app):
            """No-op function when settings routes module is not available"""
            pass
        has_settings_routes = False
    
    # Register routes
    if babel_instance:
        common.register_common_routes(app, babel_instance)
    else:
        common.register_common_routes(app, None)
    dashboard.register_dashboard_routes(app)
    categories.register_categories_routes(app)
    search.register_search_routes(app)
    # Analyst-driven manual categorization (fully separate from the smart
    # classification taxonomy - see Api/routes/analyst_categories.py)
    analyst_categories.register_analyst_category_routes(app)
    # Register new settings routes (includes both API and page routes)
    register_settings_routes(app)
    analytics.register_analytics_routes(app)
    analysis.register_analysis_routes(app)
    archives.register_archives_routes(app)
    keywords.register_keywords_routes(app)
    words.register_words_routes(app)
    sources.register_sources_routes(app)
    sides.register_sides_routes(app)
    api.register_api_routes(app)
    performance.register_performance_routes(app)
    notifications.register_notification_routes(app)
    notifications_page.register_notification_page_routes(app)
    preview.register_preview_routes(app)
    import_export.register_import_export_routes(app)
    # Unified operations API + pages (Input / Import Center / Job Center)
    app.register_blueprint(operations_api.operations_bp)
    operations_pages.register_operations_pages(app)
    # Setup routes are registered separately in app.py BEFORE other routes
    # setup.register_setup_routes(app)  # Moved to app.py to register first


