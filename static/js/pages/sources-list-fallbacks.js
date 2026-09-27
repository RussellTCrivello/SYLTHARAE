/**
 * Sources list: retrying stand-ins for handlers until sources-list-page.js (a module) loads.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Fallback for onclick handlers before module loads
// These functions retry multiple times to ensure the module has loaded
function createRetryFunction(fnName, maxRetries = 20, delay = 50) {
    const retryWrapper = function(...args) {
        let retries = 0;
        const tryCall = () => {
            const fn = window[fnName];
            // Check if function exists and is not the retry wrapper itself
            if (typeof fn === 'function' && fn !== retryWrapper && !fn.toString().includes('createRetryFunction')) {
                // Real function is loaded, call it
                try {
                    return fn(...args);
                } catch (error) {
                    console.error(`Error calling ${fnName}:`, error);
                }
            } else if (retries < maxRetries) {
                // Function not loaded yet, retry
                retries++;
                setTimeout(tryCall, delay);
            } else {
                console.error(`Function ${fnName} not available after ${maxRetries} retries`);
                alert(`Error: ${fnName} function is not available. Please refresh the page.`);
            }
        };
        tryCall();
    };
    // Mark this as a retry wrapper so we can detect it
    retryWrapper._isRetryWrapper = true;
    return retryWrapper;
}

// Set up fallback functions only if they don't exist or are retry wrappers
(function() {
    const functionsToSetup = [
        'openSourceModal',
        'viewSource',
        'editSource',
        'duplicateSource',
        'exportSource',
        'viewSourceCategoriesKeywords',
        'toggleSourceStatus',
        'deleteSource',
        'submitSourceForm'
    ];

    functionsToSetup.forEach(fnName => {
        const existing = window[fnName];
        // Only set up retry if function doesn't exist or is a retry wrapper
        if (typeof existing !== 'function' || existing._isRetryWrapper) {
            window[fnName] = createRetryFunction(fnName);
        }
    });
})();
