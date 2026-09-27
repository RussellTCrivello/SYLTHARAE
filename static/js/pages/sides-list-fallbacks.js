/**
 * Sides list: stand-ins for handlers until sides-list-page.js (a module) loads.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Fallback for onclick handlers before module loads
if (typeof window.openSideModal === 'undefined') {
    window.openSideModal = function() { setTimeout(() => window.openSideModal?.(), 100); };
    window.viewSide = function(id) { setTimeout(() => window.viewSide?.(id), 100); };
    window.editSide = function(id) { setTimeout(() => window.editSide?.(id), 100); };
    window.duplicateSide = function(id) { setTimeout(() => window.duplicateSide?.(id), 100); };
    window.toggleSideStatus = function(id) { setTimeout(() => window.toggleSideStatus?.(id), 100); };
    window.deleteSide = function(id) { setTimeout(() => window.deleteSide?.(id), 100); };
    window.submitSideForm = function() { setTimeout(() => window.submitSideForm?.(), 100); };
}
