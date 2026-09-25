/**
 * Loading Manager
 * Provides visual feedback during settings operations
 */

class LoadingManager {
    /**
     * Show loading overlay on element
     * @param {HTMLElement} element - Element to show loading on
     * @param {string} message - Loading message
     * @returns {HTMLElement} Loading overlay element
     */
    static show(element, message = 'Loading...') {
        if (!element) {
            return null;
        }

        // Remove existing overlay if present
        this.hide(element);

        // Create overlay
        const overlay = document.createElement('div');
        overlay.className = 'loading-overlay';
        overlay.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(255, 255, 255, 0.9);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            border-radius: inherit;
        `;

        // Create spinner
        const spinner = document.createElement('div');
        spinner.className = 'spinner-border text-primary';
        spinner.setAttribute('role', 'status');
        spinner.innerHTML = '<span class="visually-hidden">Loading...</span>';

        // Create message
        const messageEl = document.createElement('p');
        messageEl.className = 'mt-2 mb-0 text-muted';
        messageEl.textContent = message;
        messageEl.style.cssText = 'font-size: 0.875rem;';

        overlay.appendChild(spinner);
        overlay.appendChild(messageEl);

        // Ensure parent has relative positioning
        const originalPosition = element.style.position;
        if (!['relative', 'absolute', 'fixed'].includes(getComputedStyle(element).position)) {
            element.style.position = 'relative';
        }

        element.appendChild(overlay);

        // Store original position for cleanup
        overlay.dataset.originalPosition = originalPosition;

        return overlay;
    }

    /**
     * Hide loading overlay
     * @param {HTMLElement} element - Element to hide loading on
     */
    static hide(element) {
        if (!element) {
            return;
        }

        const overlay = element.querySelector('.loading-overlay');
        if (overlay) {
            overlay.remove();
        }
    }

    /**
     * Show loading on button
     * @param {HTMLElement} button - Button element
     * @param {string} loadingText - Loading text
     * @returns {string} Original button HTML
     */
    static showButton(button, loadingText = 'Loading...') {
        if (!button) {
            return null;
        }

        const originalHtml = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${loadingText}`;
        
        return originalHtml;
    }

    /**
     * Hide loading on button
     * @param {HTMLElement} button - Button element
     * @param {string} originalHtml - Original button HTML
     */
    static hideButton(button, originalHtml) {
        if (!button) {
            return;
        }

        button.disabled = false;
        if (originalHtml) {
            button.innerHTML = originalHtml;
        }
    }

    /**
     * Show loading on form
     * @param {HTMLElement} form - Form element
     * @param {string} message - Loading message
     */
    static showForm(form, message = 'Saving...') {
        if (!form) {
            return;
        }

        // Disable all inputs
        const inputs = form.querySelectorAll('input, select, textarea, button');
        inputs.forEach(input => {
            input.disabled = true;
        });

        // Show overlay
        this.show(form, message);
    }

    /**
     * Hide loading on form
     * @param {HTMLElement} form - Form element
     */
    static hideForm(form) {
        if (!form) {
            return;
        }

        // Enable all inputs
        const inputs = form.querySelectorAll('input, select, textarea, button');
        inputs.forEach(input => {
            input.disabled = false;
        });

        // Hide overlay
        this.hide(form);
    }
}

// Export for global access
if (typeof window !== 'undefined') {
    window.LoadingManager = LoadingManager;
}

export default LoadingManager;

