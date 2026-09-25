"""
Monitoring Module
Performance monitoring and metrics collection
"""

try:
    from .monitor import (
        PerformanceMonitor,
        PerformanceMetrics,
        start_monitoring,
        stop_monitoring,
        get_monitor,
        print_dashboard
    )
except ImportError as e:
    # Fallback if monitor module is not available
    logger = __import__('logging').getLogger(__name__)
    logger.warning(f"Could not import monitor module: {e}")
    PerformanceMonitor = None
    PerformanceMetrics = None
    def start_monitoring():
        return None
    def stop_monitoring():
        pass
    def get_monitor():
        """Get the global performance monitor instance (fallback returns None)"""
        return None
    def print_dashboard():
        pass

__all__ = [
    'PerformanceMonitor',
    'PerformanceMetrics',
    'start_monitoring',
    'stop_monitoring',
    'get_monitor',
    'print_dashboard'
]

