/**
 * Page Initializer System
 * Centralized system for initializing page-specific JavaScript
 * Pages declare their needs via data attributes, and this system loads the appropriate modules
 */

// Page type registry
const pageHandlers = {
    'files-list': () => import('./files-list-page.js'),
    'file-types': () => import('./file-types-page.js'),
    'file-detail': () => import('./file-detail-page.js'),
    'keywords-list': () => import('./keywords-list-page.js'),
    'keyword-detail': () => import('./keyword-detail-page.js'),
    'words-list': () => import('./words-list-page.js'),
    'word-detail': () => import('./word-detail-page.js'),
    'sources-list': () => import('./sources-list-page.js'),
    'source-detail': () => import('./source-detail-page.js'),
    'sides-list': () => import('./sides-list-page.js'),
    'side-detail': () => import('./side-detail-page.js'),
    'search': () => import('./search-page.js'),
    'search-enhanced': () => import('./search-enhanced-page.js'),
    'search-advanced': () => import('./search-advanced-page.js'),
    'dashboard': () => import('./dashboard-page.js'),
    'notifications': () => import('./notifications-page.js'),
    'import-export': () => import('./import-export-page.js'),
    'analysis-batch': () => import('./analysis-batch-page.js'),
    'email-words': () => import('./email-words-page.js')
};

/**
 * Initialize page based on data attributes
 */
export async function initializePage() {
    // Get page type from body data attribute
    const pageType = document.body.dataset.pageType;
    if (!pageType) {
        console.warn('No page type specified. Add data-page-type to <body> tag.');
        return;
    }

    // Get page handler
    const handler = pageHandlers[pageType];
    if (!handler) {
        console.warn(`No handler found for page type: ${pageType}`);
        return;
    }

    try {
        // Load and initialize page-specific module
        const pageModule = await handler();
        if (pageModule && pageModule.default && typeof pageModule.default === 'function') {
            await pageModule.default();
        } else if (pageModule && typeof pageModule.init === 'function') {
            await pageModule.init();
        } else {
            console.warn(`Page module for ${pageType} does not export a default init function`);
        }
    } catch (error) {
        console.error(`Error initializing page ${pageType}:`, error);
    }
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializePage);
} else {
    initializePage();
}

// Export for manual initialization if needed
export default { initializePage };

