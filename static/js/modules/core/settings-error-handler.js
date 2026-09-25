/**
 * Centralized Settings Error Handler
 * Provides consistent error handling across all settings operations
 */

class SettingsErrorHandler {
    /**
     * Handle settings-related errors
     * @param {Error} error - Error object
     * @param {string} context - Context where error occurred
     * @param {Object} options - Additional options
     */
    static handle(error, context, options = {}) {
        const {
            showNotification = true,
            logToBackend = true,
            silent = false
        } = options;

        console.error(`Settings error (${context}):`, error);

        if (silent) {
            return;
        }

        // Show user-friendly message
        if (showNotification) {
            this.showUserMessage(error, context);
        }

        // Log to backend for debugging
        if (logToBackend) {
            this.logError(error, context);
        }
    }

    /**
     * Show user-friendly error message
     * @param {Error} error - Error object
     * @param {string} context - Context
     */
    static showUserMessage(error, context) {
        const message = this.getUserFriendlyMessage(error, context);

        // Try different notification systems
        if (window.notifications && typeof window.notifications.error === 'function') {
            window.notifications.error(message, { duration: 5000 });
        } else if (window.notifications && typeof window.notifications.show === 'function') {
            window.notifications.show(message, 'error', { duration: 5000 });
        } else if (window.MessageSystem && typeof window.MessageSystem.show === 'function') {
            window.MessageSystem.show(message, 'error');
        } else if (typeof showSystemMessage === 'function') {
            showSystemMessage('danger', message);
        } else {
            // Fallback: create alert
            const alertDiv = document.createElement('div');
            alertDiv.className = 'alert alert-danger alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3';
            alertDiv.style.zIndex = '9999';
            alertDiv.innerHTML = `
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.body.appendChild(alertDiv);
            setTimeout(() => {
                if (alertDiv.parentNode) {
                    alertDiv.remove();
                }
            }, 5000);
        }
    }

    /**
     * Get user-friendly error message
     * @param {Error} error - Error object
     * @param {string} context - Context
     * @returns {string} User-friendly message
     */
    static getUserFriendlyMessage(error, context) {
        const errorMessage = error.message || String(error);

        // Map common error messages to user-friendly text
        const errorMap = {
            'Validation failed': 'Some settings have invalid values. Please check your input.',
            'Failed to save settings': 'Could not save settings. Please try again.',
            'Network error': 'Connection error. Please check your internet connection.',
            'CSRF token': 'Security token expired. Please refresh the page and try again.',
            'Permission denied': 'You do not have permission to change this setting.',
            'Invalid type': 'Invalid value type. Please check the setting format.',
            'must be at least': 'Value is too small. Please enter a larger value.',
            'must be at most': 'Value is too large. Please enter a smaller value.',
            'format is invalid': 'Invalid format. Please check the setting format.'
        };

        // Find matching error message
        for (const [key, friendlyMessage] of Object.entries(errorMap)) {
            if (errorMessage.toLowerCase().includes(key.toLowerCase())) {
                return friendlyMessage;
            }
        }

        // Default message
        return `Failed to ${context}: ${errorMessage}`;
    }

    /**
     * Log error to backend
     * @param {Error} error - Error object
     * @param {string} context - Context
     */
    static logError(error, context) {
        try {
            fetch('/api/logs/error', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                },
                body: JSON.stringify({
                    error: error.message || String(error),
                    context: context,
                    stack: error.stack,
                    url: window.location.href,
                    timestamp: new Date().toISOString()
                })
            }).catch(() => {
                // Silent fail - don't break the app if logging fails
            });
        } catch (e) {
            // Silent fail
        }
    }

    /**
     * Handle validation errors specifically
     * @param {Array<string>} errors - Array of error messages
     */
    static handleValidationErrors(errors) {
        if (!errors || errors.length === 0) {
            return;
        }

        const message = `Validation failed:\n${errors.map(e => `• ${e}`).join('\n')}`;

        if (window.notifications && typeof window.notifications.error === 'function') {
            window.notifications.error(message, { duration: 8000 });
        } else {
            alert(message);
        }
    }
}

// Export for global access
if (typeof window !== 'undefined') {
    window.SettingsErrorHandler = SettingsErrorHandler;
}

export default SettingsErrorHandler;

