"""Regression guards for the persistent-shell content-swap lifecycle."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_archives_navigation_exposes_legacy_controls_after_a_swap():
    navigator = (ROOT / "static/js/modules/navigation/navigator.js").read_text()
    assert "window.navigateToSection = navigateToSection" in navigator
    assert "fms.navigation" in navigator
    assert "navigateToItem" in navigator


def test_swap_syncs_route_data_and_skips_shell_and_json_scripts():
    swap = (ROOT / "static/js/modules/core/navigation-swap.js").read_text()
    assert "syncRouteJsonData(parsed, nextMain)" in swap
    assert "syltharae:before-page-swap" in swap
    assert "data-navigation-shell" in swap
    assert "application/json" in swap

    base = (ROOT / "templates/base.html").read_text()
    for asset in (
        "js/jquery.min.js", "js/bootstrap.bundle.min.js", "js/chart.umd.js",
        "js/jspdf.umd.min.js", "js/modules/charts/chart-customizer.js",
        "js/modules/ui/file-actions.js", "js/modules/ui/documents-panel.js",
        "js/modules/core/language-init.js", "js/user-menu.js",
    ):
        line = next(line for line in base.splitlines() if asset in line)
        assert "data-navigation-shell" in line, asset


def test_base_shell_notification_poll_is_initialized_once():
    handler = (ROOT / "static/js/pages/base-page-handler.js").read_text()
    assert "window.__notificationBadgeTimer" in handler
    assert "menuToggle.dataset.basePageToggleBound" in handler


def test_operations_widget_disposes_its_poll_on_navigation():
    widget = (ROOT / "static/js/modules/core/operations-widget.js").read_text()
    assert "syltharae:before-page-swap" in widget
    assert "window.clearInterval(state.timer)" in widget
    assert "state.controller.abort()" in widget
