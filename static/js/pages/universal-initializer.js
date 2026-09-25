/**
 * Universal Page Initializer
 * Automatically detects page type and initializes appropriate handlers
 * NO INLINE JAVASCRIPT NEEDED - Everything is external!
 */

import { initBasePage } from './base-page-handler.js';
import { getPageData } from './data-helper.js';

// Page type detection patterns
const pageDetectors = [
    // Specific page types (checked first)
    { selector: '[data-page-type]', getType: (el) => el.dataset.pageType },
    { selector: 'body[data-page-type]', getType: (el) => el.dataset.pageType },
    
    // Route-based detection (fallback)
    { selector: 'body', getType: (el) => {
        const path = window.location.pathname;
        const endpoint = el.dataset.currentEndpoint || '';
        
        // Map routes to page types
        if (path.includes('/files') && !path.match(/\/file\/\d+/)) return 'files-list';
        if (path.match(/\/file\/\d+/)) return 'file-detail';
        if (path.includes('/keywords') && !path.match(/\/keyword\/\d+/)) return 'keywords-list';
        if (path.match(/\/keyword\/\d+/)) return 'keyword-detail';
        if (path.match(/\/categories\/\d+\/words/)) return 'category-words';  // Must be before general /words check
        if (path.includes('/words') && !path.match(/\/word\/\d+/)) return 'words-list';
        if (path.match(/\/word\/\d+/)) return 'word-detail';
        if (path.includes('/sources') && !path.match(/\/source\/\d+/)) return 'sources-list';
        if (path.match(/\/source\/\d+/)) return 'source-detail';
        if (path.includes('/sides') && !path.match(/\/side\/\d+/)) return 'sides-list';
        if (path.match(/\/side\/\d+/)) return 'side-detail';
        if (path === '/search/enhanced') return 'search-enhanced';
        if (path === '/search/advanced') return 'search-advanced';
        if (path === '/search') return 'search';
        if (path === '/' || path === '/dashboard') return 'dashboard';
        if (path.includes('/dashboard/comprehensive')) return 'comprehensive-dashboard';
        if (path.includes('/notifications')) return 'notifications';
        if (path.includes('/import') || path.includes('/export')) return 'import-export';
        if (path.includes('/analysis/batch')) return 'analysis-batch';
        if (path.includes('/email-words')) return 'email-words';
        if (path.includes('/archives')) return 'archives';
        if (path.includes('/analysis/path')) return 'path-analysis';
        
        return 'default';
    }}
];

// Page handler registry
const pageHandlers = {
    'files-list': () => import('./files-list-page.js'),
    'file-types': () => import('./file-types-page.js'),
    'file-detail': () => import('./file-detail-page.js'),
    'keywords-list': () => import('./keywords-list-page.js'),
    'keyword-detail': () => import('./keyword-detail-page.js'),
    'category-words': () => import('./category-words-page.js'),
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
    'comprehensive-dashboard': () => import('./comprehensive-dashboard-page.js'),
    'notifications': () => import('./notifications-page.js'),
    'import-export': () => import('./import-export-page.js'),
    'analysis-batch': () => import('./analysis-batch-page.js'),
    'email-words': () => import('./email-words-page.js'),
    'archives': () => import('./archives-page.js'),
    'path-analysis': () => import('./path-analysis-page.js'),
    'translation-management': () => import('./translation-management-page.js'),
    'default': () => Promise.resolve({ default: () => {} }) // No-op for default
};

/**
 * Detect page type
 */
function detectPageType() {
    for (const detector of pageDetectors) {
        const element = document.querySelector(detector.selector);
        if (element) {
            const pageType = detector.getType(element);
            if (pageType && pageType !== 'default') {
                return pageType;
            }
        }
    }
    return 'default';
}

/**
 * Initialize page
 */
export async function initializePage() {
    // Always initialize base page functionality first
    initBasePage();
    
    // Detect page type
    const pageType = detectPageType();
    
    if (pageType === 'default') {
        // No specific handler needed
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
export default { initializePage, detectPageType };

