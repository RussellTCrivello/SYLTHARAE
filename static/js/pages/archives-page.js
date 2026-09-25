/**
 * Archives / File Management Analysis System Page Handler
 * Handles the main archives page with SYLTHARAE system
 */

import { getPageData } from './data-helper.js';

export default async function initArchivesPage() {
    console.log('Archives page: Initializing...');
    
    // Get page data from JSON script tag
    const data = getPageData('page-data');
    
    // Fallback to window.appData for backward compatibility
    const appData = data || window.appData || {};
    console.log('Archives page: Page data loaded', appData);
    
    // Load Select2 dynamically if needed
    loadSelect2();
    
    // Initialize any page-specific functionality
    initializePageFeatures();
    
    // Ensure navigation is initialized - don't wait for file-management-system
    // The file-management-system.js should handle this, but we'll ensure it happens
    ensureNavigationInitialized();
}

/**
 * Load Select2 library dynamically
 */
function loadSelect2() {
    if (typeof jQuery !== 'undefined' && typeof jQuery.fn.select2 === 'undefined') {
        const script = document.createElement('script');
        script.src = document.querySelector('link[href*="select2.min.css"]')?.href.replace('css/select2.min.css', 'js/select2.min.js') || '/static/dist/js/select2.min.js';
        script.onerror = function() {
            console.error('Failed to load Select2 library');
        };
        document.head.appendChild(script);
    } else if (typeof jQuery === 'undefined') {
        // jQuery not ready yet, try again
        setTimeout(loadSelect2, 50);
    }
}

/**
 * Initialize page-specific features
 */
function initializePageFeatures() {
    console.log('Initializing archives page features...');
    // Any archives-specific initialization
    // The file-management-system.js module handles most functionality
}

/**
 * Ensure navigation is initialized
 */
function ensureNavigationInitialized() {
    console.log('Archives page: Ensuring navigation is initialized...');
    
    // Wait for file-management-system to be ready, then ensure navigation is initialized
    let attempts = 0;
    const maxAttempts = 20; // Try for 2 seconds (20 * 100ms)
    
    const checkAndInit = () => {
        attempts++;
        console.log(`Archives page: Checking for navigation (attempt ${attempts}/${maxAttempts})...`);
        
        if (window.fms && window.fms.navigation && window.fms.navigation.initNavigation) {
            console.log('Archives page: File management system ready, initializing navigation...');
            try {
                window.fms.navigation.initNavigation();
                console.log('Archives page: Navigation initialized successfully');
            } catch (error) {
                console.error('Archives page: Error initializing navigation:', error);
            }
        } else if (attempts < maxAttempts) {
            console.log('Archives page: File management system not ready yet, retrying...');
            setTimeout(checkAndInit, 100);
        } else {
            console.error('Archives page: File management system not available after max attempts!');
            console.error('Archives page: window.fms =', window.fms);
            // Try to manually load root view as fallback
            const contentView = document.getElementById('unifiedContentView');
            if (contentView && contentView.innerHTML.includes('Loading...')) {
                console.warn('Archives page: Attempting fallback root view load...');
                // Import and call loadRootView directly as fallback
                import('../modules/views/root-view.js').then(module => {
                    if (module.loadRootView) {
                        console.log('Archives page: Loading root view directly...');
                        module.loadRootView();
                    }
                }).catch(err => {
                    console.error('Archives page: Failed to load root view module:', err);
                });
            }
        }
    };
    
    // Start checking after a short delay to allow modules to load
    setTimeout(checkAndInit, 300);
}

