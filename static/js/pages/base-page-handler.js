/**
 * Base Page Handler
 * Handles common functionality for all pages (sidebar, mobile menu, etc.)
 */

export function initBasePage() {
    // Mobile menu toggle
    const menuToggle = document.getElementById('menuToggle');
    const sidebar = document.getElementById('sidebar');
    if (menuToggle && sidebar) {
        menuToggle.addEventListener('click', function() {
            sidebar.classList.toggle('show');
        });
    }
    
    // Sidebar active state management
    updateSidebarActiveState();
    
    // Convert flash messages to notifications
    convertFlashMessages();
    
    // Update notification badge
    updateNotificationBadge();
    setInterval(updateNotificationBadge, 30000);
}

/**
 * Update sidebar active state
 */
function updateSidebarActiveState() {
    const currentPath = window.location.pathname;
    const currentEndpoint = document.body.dataset.currentEndpoint || window.currentEndpoint || '';
    const sidebarLinks = document.querySelectorAll('.sidebar-nav-link:not(.language-switcher .sidebar-nav-link)');
    
    // Check if server already set an active state correctly
    const serverActiveLink = document.querySelector('.sidebar-nav-link.active:not(.language-switcher .sidebar-nav-link)');
    
    // If server set an active state and it matches current endpoint, trust it
    if (serverActiveLink && currentEndpoint) {
        const activeEndpoint = serverActiveLink.getAttribute('data-endpoint');
        if (activeEndpoint === currentEndpoint) {
            // Server state is correct, don't override
            return;
        }
    }
    
    // Otherwise, find and set the correct active state
    let foundActive = false;
    
    // Priority 1: Match by endpoint name (most reliable)
    if (currentEndpoint) {
        sidebarLinks.forEach(link => {
            const endpoint = link.getAttribute('data-endpoint');
            if (endpoint && endpoint === currentEndpoint) {
                // Remove active from all other links
                sidebarLinks.forEach(l => l.classList.remove('active'));
                link.classList.add('active');
                foundActive = true;
            }
        });
    }
    
    // Priority 2: Match by URL path (fallback)
    if (!foundActive) {
        sidebarLinks.forEach(link => {
            const href = link.getAttribute('href');
            if (href && currentPath.startsWith(href)) {
                sidebarLinks.forEach(l => l.classList.remove('active'));
                link.classList.add('active');
                foundActive = true;
            }
        });
    }
}

/**
 * Convert Flask flash messages to notification system
 */
function convertFlashMessages() {
    // FLASH-01: only top-level flash messages are converted to toasts.
    // The previous selector grabbed every `.alert` on the page — including
    // hidden (`.d-none`) error boxes *inside modals* (e.g. form validation
    // feedback) — and deleted them ~100ms after load, so those elements could
    // never show an error. Also keep alerts that carry their own interactive
    // controls (e.g. the must-change-password banner with its action button):
    // only true flash messages (plain text + Bootstrap dismiss button) are
    // safe to convert and remove.
    const flashMessages = Array.from(document.querySelectorAll('.alert')).filter(function(alert) {
        if (alert.closest('.modal') || alert.classList.contains('d-none')) return false;
        const hasInteractive = Array.from(alert.querySelectorAll('button, a, input, select, form'))
            .some(function(el) { return !el.classList.contains('btn-close'); });
        return !hasInteractive;
    });
    flashMessages.forEach(function(alert) {
        // Determine message type from Bootstrap alert class
        let type = 'info';
        if (alert.classList.contains('alert-success')) {
            type = 'success';
        } else if (alert.classList.contains('alert-danger') || alert.classList.contains('alert-error')) {
            type = 'error';
        } else if (alert.classList.contains('alert-warning')) {
            type = 'warning';
        } else if (alert.classList.contains('alert-info')) {
            type = 'info';
        }
        
        // Extract message text (remove icon and close button text)
        const messageText = alert.textContent.trim();
        
        // Show notification using message system
        if (window.MessageSystem && messageText) {
            window.MessageSystem.show(messageText, type, {
                skipTranslation: false
            });
        }
        
        // Remove the original alert after a short delay
        setTimeout(function() {
            if (alert.parentNode) {
                alert.style.transition = 'opacity 0.3s';
                alert.style.opacity = '0';
                setTimeout(function() {
                    alert.remove();
                }, 300);
            }
        }, 100);
    });
}

/**
 * Update notification badge
 */
async function updateNotificationBadge() {
    try {
        const response = await fetch('/api/notifications/stats');
        const data = await response.json();
        
        if (data.success && data.stats) {
            const badge = document.getElementById('notificationsBadge');
            if (badge) {
                const unreadCount = data.stats.unread || 0;
                if (unreadCount > 0) {
                    badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
                    badge.style.display = 'block';
                } else {
                    badge.style.display = 'none';
                }
            }
        }
    } catch (error) {
        console.error('Error updating notification badge:', error);
    }
}

