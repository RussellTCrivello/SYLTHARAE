"""
System Initialization Module
Handles first-time setup and system initialization on startup
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from settings.config import get_config
from settings import get_settings, get_settings_manager

logger = logging.getLogger(__name__)

# Marker file to track if system has been initialized
INIT_MARKER_FILE = Path('.system_initialized')


def is_system_initialized() -> bool:
    """Check if system has been initialized"""
    return INIT_MARKER_FILE.exists()


def mark_system_initialized():
    """Mark system as initialized"""
    try:
        INIT_MARKER_FILE.touch()
        logger.info("System initialization marker created")
    except Exception as e:
        logger.warning(f"Could not create initialization marker: {e}")


def load_config_from_json(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from config.json file"""
    if config_path is None:
        # Try to find config.json in project root
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent
        config_path = project_root / 'config.json'
    
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"Configuration loaded from {config_path}")
        return config
    except Exception as e:
        logger.error(f"Error loading config from {config_path}: {e}")
        return {}


def initialize_from_config(config: Dict[str, Any]):
    """Initialize system settings from config.json"""
    try:
        # Initialize settings manager
        settings_manager = get_settings()
        
        # Update processing settings
        if 'processing' in config:
            processing_config = config['processing']
            settings_manager.set_setting('processing.max_workers', processing_config.get('max_workers', 4))
            settings_manager.set_setting('processing.chunk_size', processing_config.get('chunk_size', 1048576))
            settings_manager.set_setting('processing.parallel_processing', processing_config.get('parallel_processing', True))
            settings_manager.set_setting('processing.extract_archives', processing_config.get('extract_archives', True))
            settings_manager.set_setting('processing.extract_attachments', processing_config.get('extract_attachments', True))
        
        # Update display settings
        if 'display' in config:
            display_config = config['display']
            settings_manager.set_setting('display.results_per_page', display_config.get('results_per_page', 10))
            settings_manager.set_setting('display.theme', display_config.get('theme', 'light'))
            settings_manager.set_setting('display.language', display_config.get('language', 'en'))
        
        # Update search settings
        if 'search' in config:
            search_config = config['search']
            settings_manager.set_setting('search.max_results', search_config.get('max_results', 1000))
            settings_manager.set_setting('search.enable_history', search_config.get('enable_history', True))
            settings_manager.set_setting('search.enable_saved_searches', search_config.get('enable_saved_searches', True))
        
        # Update notification settings
        if 'notifications' in config:
            notification_config = config['notifications']
            settings_manager.set_setting('notifications.enabled', notification_config.get('enabled', True))
            settings_manager.set_setting('notifications.email_notifications', notification_config.get('email_notifications', False))
            settings_manager.set_setting('notifications.browser_notifications', notification_config.get('browser_notifications', True))
        
        # Update system settings
        if 'system' in config:
            system_config = config['system']
            settings_manager.set_setting('system.language', system_config.get('language', 'en'))
            settings_manager.set_setting('system.timezone', system_config.get('timezone', 'UTC'))
            settings_manager.set_setting('system.debug_mode', system_config.get('debug_mode', False))
            if 'app_logo' in system_config:
                settings_manager.set_setting('system.app_logo', system_config.get('app_logo', ''))
            if 'app_icon' in system_config:
                settings_manager.set_setting('system.app_icon', system_config.get('app_icon', 'bi-file-earmark-text'))
        
        # Update theme settings
        if 'theme' in config:
            theme_config = config['theme']
            for key, value in theme_config.items():
                settings_manager.set_setting(f'theme.{key}', value)
        
        # Update environment variables
        if 'environment_variables' in config:
            env_vars = config['environment_variables']
            for key, value in env_vars.items():
                if value:  # Only set if value is not empty
                    os.environ[key] = str(value)
        
        logger.info("System settings initialized from config.json")
        
    except Exception as e:
        logger.error(f"Error initializing from config: {e}")


def initialize_paths(config: Dict[str, Any]):
    """Initialize directory paths from config"""
    try:
        app_config = get_config()
        settings = get_settings()
        
        if 'paths' in config:
            config['paths']  # validate presence
            
            # Set project root
            if app_config.project_root is None:
                current_file = Path(__file__).resolve()
                project_root = current_file.parent.parent
                app_config.set_project_root(project_root)
                # Also set in unified settings
                settings.set_project_root(project_root)
            elif settings.project_root is None:
                # Sync from app_config to settings
                settings.set_project_root(app_config.project_root)
            
            # Create necessary directories
            if app_config.uploads_dir:
                app_config.uploads_dir.mkdir(parents=True, exist_ok=True)
            if app_config.logs_dir:
                app_config.logs_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info("Directory paths initialized")
        
    except Exception as e:
        logger.error(f"Error initializing paths: {e}")


def initialize_database_config(config: Dict[str, Any], skip_connection_test: bool = True):
    """Apply the database configuration from config.json.

    OPS-07: this used to seed ``database.*`` one key at a time with
    ``set_setting()``. ``database.*`` has no entry in ``SETTING_DEFINITIONS``,
    so ``SettingsManager.set(..., validate=True)`` validated nothing, and
    ``settings.json`` was rewritten on *every* startup with an unvalidated and
    untested target. An operator ``config.json`` pointing at a host that no
    longer exists therefore silently destroyed the working configuration the
    administrator had saved through the validated settings endpoint.

    It now goes through ``SettingsManager.apply_database_config()`` - the same
    boundary every other database mutation uses:

    * fields config.json omits fall back to the current values (never to
      ``localhost:5432`` defaults);
    * the result is structurally validated;
    * unless this is the very first startup, the connection is proven before
      anything is written;
    * a rejected configuration is **not** persisted. The application keeps the
      last-known-good configuration it loaded from ``settings.json``.

    Args:
        config: Configuration dictionary
        skip_connection_test: If True, don't test database connection (for first startup)
    """
    try:
        if 'database' not in config:
            return

        db_config_data = config['database']
        if not isinstance(db_config_data, dict):
            logger.error(
                "Ignoring config.json database block: expected an object, got %s.",
                type(db_config_data).__name__,
            )
            return

        manager = get_settings_manager()

        # Drop nulls so an explicitly-null field falls back to the current
        # value instead of being written as the string "None".
        proposed = {k: v for k, v in db_config_data.items() if v is not None}

        applied, error = manager.apply_database_config(
            proposed, require_test=not skip_connection_test
        )

        if applied:
            logger.info("Database configuration applied from config.json")
            return

        # Rejected. Nothing was written and the environment is untouched, so the
        # process keeps using the last-known-good configuration it loaded from
        # settings.json.
        logger.error(
            "Database configuration in config.json was rejected and has NOT been "
            "applied: %s The application continues with its existing configuration.",
            error,
        )

    except Exception as e:
        logger.error(f"Error initializing database config: {e}")


def upgrade_database_schema():
    """Apply pending schema migrations to the configured database.

    Upgrade path for EXISTING installations. The setup wizard runs
    migrations only at first install, so a deployment that pulls new code
    containing a new migration would otherwise start against a stale
    schema - exactly what happened with PR #11: the analyst tables were
    missing, every categorization call failed with
    ``relation "analyst_categories" does not exist`` and the assign
    endpoint surfaced it as an opaque 400.

    Uses the single authoritative migration runner: idempotent,
    pending-only, each migration in its own transaction with rollback on
    failure. An unreachable or unconfigured database only logs a warning -
    startup continues so the setup gate and health checks can report the
    problem (same tolerance the rest of startup has).

    Returns the list of migration versions applied (empty when current).
    """
    try:
        from settings.config import get_database_config
        from database.bootstrap import bootstrap_database

        db = get_database_config()
        cfg = {
            "host": db.host,
            "port": int(db.port),
            "user": db.user,
            "password": db.password,
            "database": db.database,
        }
        report = bootstrap_database(cfg)
        applied = report.get("applied_migrations") or []
        if applied:
            logger.info(
                "✅ Applied pending schema migrations: %s (schema now at version %s)",
                ", ".join(applied), report.get("current_version"),
            )
        else:
            logger.info(
                "✅ Database schema is up to date (version %s)",
                report.get("current_version"),
            )
        return applied
    except Exception as e:
        logger.warning(
            "⚠️ Schema upgrade check skipped - database unreachable or not "
            "configured yet: %s",
            e,
        )
        return []


def initialize_system(first_startup: bool = False):
    """
    Initialize the entire system
    
    Args:
        first_startup: If True, perform first-time initialization
    """
    try:
        logger.info("Starting system initialization...")
        
        # Load configuration from config.json
        config = load_config_from_json()
        
        if not config:
            logger.warning("No configuration found, using defaults")
            return
        
        # Initialize paths first
        initialize_paths(config)
        
        # Initialize settings from config
        initialize_from_config(config)
        
        # Initialize database config (skip connection test on first startup)
        # Connection will be tested during setup wizard
        initialize_database_config(config, skip_connection_test=first_startup)
        
        # Mark as initialized if first startup
        if first_startup:
            mark_system_initialized()
            logger.info("✅ System initialization completed (first startup)")
        else:
            logger.info("✅ System initialization completed")
        
    except Exception as e:
        logger.error(f"Error during system initialization: {e}")
        raise


def ensure_system_initialized():
    """Ensure system is initialized, run initialization if needed"""
    if not is_system_initialized():
        logger.info("First startup detected, initializing system...")
        initialize_system(first_startup=True)
    else:
        # Still load config on every startup to apply any changes
        logger.info("System already initialized, loading configuration...")
        initialize_system(first_startup=False)
        # Upgrade path for existing installations: the setup wizard runs
        # migrations only at first install, so apply anything pending now.
        # Deliberately outside initialize_system(): that function returns
        # early when config.json is absent, but database configuration can
        # equally come from .env (what the installer writes) - pending
        # migrations must be applied either way.
        upgrade_database_schema()

