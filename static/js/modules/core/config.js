/**
 * Core Configuration
 * Constants, translations, and configuration values
 */

/**
 * Get translations from window.appData or use defaults
 */
export const translations = window.appData?.translations || {
    pageNavigation: 'Page Navigation',
    previousPage: 'Previous Page',
    previous: 'Previous',
    goToPage: 'Go to page',
    home: 'Home',
    items: 'items',
    loading: 'Loading...',
    loadingFiles: 'Loading files...',
    noItemsFound: 'No items found in this section',
    errorLoadingFiles: 'Error loading files',
    files: 'Files',
    searchFiles: 'Search files...',
    searchDisplayedFiles: 'Search displayed files',
    selectAllFiles: 'Select All Files',
    selectAll: 'Select All',
    deselectAllFiles: 'Deselect All Files',
    deselectAll: 'Deselect All',
    exportSelectedFiles: 'Export Selected Files',
    exportSelected: 'Export Selected',
    selected: 'selected',
    perPage: 'Per Page:',
    itemsPerPage: 'Items Per Page',
    noFilesFound: 'No files found',
    showing: 'Showing',
    of: 'of',
    next: 'Next',
    nextPage: 'Next Page',
    currentPage: 'Current page',
    category: 'Category',
    keywords: 'Keywords',
    titles: 'Titles',
    sources: 'Sources',
    sides: 'Sides',
    hash: 'Relations',
    invalidNavigation: 'Invalid navigation parameters',
    fileCount: 'File Count',
    topCategories: 'Top Categories by File Count',
    topSources: 'Top Sources Distribution',
    dataDistribution: 'Data Distribution',
    categories: 'Categories',
    keywords: 'Keywords',
    sources: 'Sources',
    sides: 'Sides',
    hashes: 'Hashes',
    selectFile: 'Select file',
    viewDetails: 'View Details',
    viewDetailsFor: 'View Details for',
    openFullView: 'Open Full View in New Tab',
    openFullViewFor: 'Open Full View in New Tab for',
    fullView: 'Full View',
    exportFile: 'Export File',
    export: 'Export',
    errorLoadingItems: 'Error loading items',
    listView: 'List View',
    gridView: 'Grid View',
    switchToListView: 'Switch to List View',
    switchToGridView: 'Switch to Grid View',
    searchResults: 'Search Results',
    resultsFound: 'results found',
    noResultsFound: 'No results found',
    errorPerformingSearch: 'Error performing search',
    words: 'Words',
    results: 'results',
    searchWorksOnDisplayedData: 'Search works on displayed data. Please navigate to a section or item to search.'
};

// Expose translations on window for backward compatibility
window.translations = translations;

/**
 * Section labels - loaded from window.appData
 */
export const sectionLabels = window.appData?.sectionLabels || {
    category: translations.category || 'Category',
    keywords: translations.keywords || 'Keywords',
    titles: translations.titles || 'Titles',
    sources: translations.sources || 'Sources',
    sides: translations.sides || 'Sides',
    hash: translations.hash || 'Relations',
    geolocation: translations.geolocation || 'Geolocation'
};

/**
 * Section data cache - optional, used for filters and initial display
 */
export const sectionDataCache = window.appData?.data || {
    category: [],
    keywords: [],
    titles: [],
    sources: [],
    sides: [],
    hash: []
};

/**
 * Check if cursor pagination is enabled
 */
export const cursorPaginationEnabled = window.appData?.cursorPaginationEnabled === true;

/**
 * Modal configuration
 */
export const MODAL_ENABLED = true; // Change to false to disable modal pop-up

/**
 * Default pagination settings
 */
export const DEFAULT_PAGINATION = {
    sectionPerPage: 10,
    filePerPage: 50,  // Default files per page
    paginationWindowSize: 2
};

/**
 * Default sort settings
 */
export const DEFAULT_SORT = {
    section: 'file_count_desc',
    files: 'date_desc'
};

