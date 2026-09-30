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
    // The retired `window.fms` global never existed - the
    // file-management-system module exports a default and self-initialises
    // on a full load. The old 2-second poll therefore failed into this
    // same fallback on EVERY load, logging errors each time (the owner's
    // console trace). Call the navigation module directly instead:
    // initNavigation guards itself per content view, so a full load
    // (module self-run + this) and a swap revisit both end with exactly
    // one root load and a clean console.
    import('../modules/navigation/navigator.js').then((nav) => {
        if (nav && typeof nav.initNavigation === 'function') {
            nav.initNavigation();
        }
    }).catch(() => { /* the server-rendered content stands; console stays clean */ });
}

