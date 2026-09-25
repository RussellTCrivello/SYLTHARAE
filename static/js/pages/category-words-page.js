/**
 * Category Words Page JavaScript
 * Displays words in a specific category with extensive filtering, sorting, and display options
 * Matches the style of words/keywords management pages
 */

// Use window.translations to avoid conflicts with other scripts
if (typeof window.translations === 'undefined') {
    window.translations = {};
}

// Local translations object (merged with window.translations)
let translations = window.translations;

// Global state
let allWords = [];
let filteredWords = [];
let currentPage = 1;
let itemsPerPage = 10;
let currentSort = 'word-asc';
let currentFormat = 'table';
let sortColumn = 'word';
let sortDirection = 'asc';

// Initialization flag to prevent double initialization
let isInitialized = false;

// Main initialization function
function initializeCategoryWordsPage() {
    // Prevent double initialization
    if (isInitialized) {
        return;
    }
    isInitialized = true;
    
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl) {
        try {
            const jsonText = pageDataEl.textContent.trim();
            if (!jsonText) {
                console.warn('Category words page data is empty');
                return;
            }
            
            // Try to parse JSON
            const data = JSON.parse(jsonText);
            const pageTranslations = data.translations || {};
            // Merge with window.translations
            Object.assign(window.translations, pageTranslations);
            translations = window.translations;
        } catch (e) {
            console.error('Error parsing category words page data:', e);
            console.error('JSON content:', pageDataEl.textContent.substring(0, 500));
            // Continue with empty translations to prevent page break
            translations = window.translations || {};
        }
    }
    
    // Initialize words data from DOM
    initializeWordsData();
    
    // Initialize event listeners
    initializeEventListeners();
    
    // Set initial display format
    const formatSelect = document.getElementById('displayFormat');
    if (formatSelect) {
        currentFormat = formatSelect.value || 'table';
    }
    
    // Initial render
    applyFiltersAndRender();
    
    console.log('Category words page loaded');
}

// Load translations from JSON script tag
// Support both direct script loading and module import
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeCategoryWordsPage);
} else {
    // DOM already loaded, initialize immediately
    initializeCategoryWordsPage();
}

// Export default init function for universal-initializer
export default function init() {
    // Wait for DOM if needed, then initialize
    if (document.readyState === 'loading') {
        return new Promise((resolve) => {
            document.addEventListener('DOMContentLoaded', () => {
                initializeCategoryWordsPage();
                resolve();
            });
        });
    } else {
        initializeCategoryWordsPage();
        return Promise.resolve();
    }
}

// Initialize words data from DOM
function initializeWordsData() {
    // Only read from the table view to avoid duplicates from multiple view modes
    const tableBody = document.getElementById('categoryWordsTableBody');
    const wordItems = tableBody ? tableBody.querySelectorAll('.word-item') : [];
    
    // Use a Map to deduplicate by word ID
    const wordMap = new Map();
    Array.from(wordItems).forEach((item) => {
        const id = parseInt(item.getAttribute('data-word-id')) || 0;
        const word = item.getAttribute('data-word-text') || item.querySelector('.word-text')?.textContent?.trim() || '';
        // Only add if not already in map (deduplicates by ID)
        if (!wordMap.has(id)) {
            wordMap.set(id, { id, word, element: item });
        }
    });
    
    allWords = Array.from(wordMap.values()).map((item, index) => ({
        ...item,
        index: index + 1
    }));
    filteredWords = [...allWords];
}

// Initialize event listeners
function initializeEventListeners() {
    // Search input
    const searchInput = document.getElementById('wordSearch');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(function() {
            applyFiltersAndRender();
        }, 300));
    }
    
    // Sort dropdown
    const sortBy = document.getElementById('sortBy');
    if (sortBy) {
        sortBy.addEventListener('change', function() {
            currentSort = this.value;
            const [field, direction] = currentSort.split('-');
            sortColumn = field;
            sortDirection = direction;
            applyFiltersAndRender();
        });
    }
    
    // Display format dropdown
    const displayFormat = document.getElementById('displayFormat');
    if (displayFormat) {
        displayFormat.addEventListener('change', function() {
            currentFormat = this.value;
            changeDisplayFormat();
        });
        // Set initial format
        currentFormat = displayFormat.value || 'table';
    }
    
    // Items per page dropdown
    const itemsPerPageSelect = document.getElementById('itemsPerPage');
    if (itemsPerPageSelect) {
        itemsPerPageSelect.addEventListener('change', function() {
            itemsPerPage = this.value === 'all' ? Infinity : parseInt(this.value);
            currentPage = 1;
            applyFiltersAndRender();
        });
    }
    
    // Event delegation for remove word buttons
    document.addEventListener('click', function(e) {
        const removeBtn = e.target.closest('.remove-word-btn');
        if (removeBtn) {
            e.preventDefault();
            const categoryId = parseInt(removeBtn.getAttribute('data-category-id'));
            const wordId = parseInt(removeBtn.getAttribute('data-word-id'));
            let wordName = removeBtn.getAttribute('data-word-name');
            
            // Parse JSON string if needed
            try {
                if (wordName && (wordName.startsWith('"') || wordName.startsWith("'"))) {
                    wordName = JSON.parse(wordName);
                }
            } catch (e) {
                // Use as-is if parsing fails
            }
            
            removeWordFromCategory(categoryId, wordId, wordName);
        }
    });
}

// Debounce helper
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Apply filters and render
function applyFiltersAndRender() {
    const searchTerm = (document.getElementById('wordSearch')?.value || '').toLowerCase().trim();
    
    // Filter words
    filteredWords = allWords.filter(word => {
        const wordText = word.word.toLowerCase();
        return wordText.includes(searchTerm);
    });
    
    // Sort words
    sortWords(filteredWords);
    
    // Update counts
    updateCounts();
    
    // Render words
    renderWords();
    
    // Render pagination
    renderPagination();
}

// Sort words
function sortWords(words) {
    words.sort((a, b) => {
        let aVal, bVal;
        
        if (sortColumn === 'word') {
            aVal = a.word.toLowerCase();
            bVal = b.word.toLowerCase();
        } else if (sortColumn === 'id') {
            aVal = a.id;
            bVal = b.id;
        }
        
        if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
        return 0;
    });
}

// Update counts
function updateCounts() {
    const totalCount = allWords.length;
    const filteredCount = filteredWords.length;
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = Math.min(startIndex + itemsPerPage, filteredCount);
    const visibleCount = Math.max(0, endIndex - startIndex);
    const totalPages = Math.ceil(filteredCount / itemsPerPage);
    
    const totalEl = document.getElementById('totalWordsCount');
    const filteredEl = document.getElementById('filteredWordsCount');
    const visibleEl = document.getElementById('visibleWordsCount');
    const pageEl = document.getElementById('currentPageInfo');
    const resultsInfo = document.getElementById('searchResultsInfo');
    
    if (totalEl) totalEl.textContent = totalCount.toLocaleString();
    if (filteredEl) filteredEl.textContent = filteredCount.toLocaleString();
    if (visibleEl) visibleEl.textContent = visibleCount.toLocaleString();
    if (pageEl) pageEl.textContent = `${currentPage} / ${totalPages || 1}`;
    
    if (resultsInfo) {
        if (filteredCount === totalCount) {
            resultsInfo.textContent = `${translations.showing || 'Showing'} ${startIndex + 1}-${endIndex} ${translations.of || 'of'} ${totalCount.toLocaleString()} ${translations.words || 'words'}`;
        } else {
            resultsInfo.textContent = `${translations.showing || 'Showing'} ${startIndex + 1}-${endIndex} ${translations.of || 'of'} ${filteredCount.toLocaleString()} ${translations.filtered || 'filtered'} ${translations.words || 'words'} (${totalCount.toLocaleString()} ${translations.total || 'total'})`;
        }
    }
}

// Render words based on current format
function renderWords() {
    const container = document.getElementById('wordsDisplayContainer');
    if (!container) return;
    
    // Calculate pagination
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const wordsToShow = filteredWords.slice(startIndex, endIndex);
    
    // Show/hide empty state
    const emptyState = document.getElementById('emptyState');
    if (emptyState) {
        emptyState.style.display = wordsToShow.length === 0 ? 'block' : 'none';
    }
    
    if (wordsToShow.length === 0) {
        // Hide all views
        ['table', 'list', 'grid', 'compact'].forEach(view => {
            const viewEl = container.querySelector(`.view-${view}`);
            if (viewEl) viewEl.style.display = 'none';
        });
        return;
    }
    
    // Render based on format
    switch (currentFormat) {
        case 'table':
            renderTableView(wordsToShow, startIndex);
            break;
        case 'list':
            renderListView(wordsToShow);
            break;
        case 'grid':
            renderGridView(wordsToShow);
            break;
        case 'compact':
            renderCompactView(wordsToShow);
            break;
        default:
            renderTableView(wordsToShow, startIndex);
    }
}

// Render table view
function renderTableView(words, startIndex) {
    const tbody = document.getElementById('categoryWordsTableBody');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    
    words.forEach((word, idx) => {
        const globalIndex = startIndex + idx + 1;
        const tr = document.createElement('tr');
        tr.className = 'word-item';
        tr.setAttribute('data-word', word.word.toLowerCase());
        tr.setAttribute('data-word-id', word.id);
        tr.setAttribute('data-word-text', word.word);
        
        // Ensure proper table cell structure
        const td1 = document.createElement('td');
        td1.innerHTML = `<span class="badge bg-secondary">${word.id}</span>`;
        
        const td2 = document.createElement('td');
        td2.innerHTML = `<span class="word-text"><strong>${escapeHtml(word.word)}</strong></span>`;
        
        const td3 = document.createElement('td');
        td3.className = 'text-end';
        td3.innerHTML = `
            <button class="btn btn-outline-danger btn-sm remove-word-btn" 
                    data-category-id="${getCategoryId()}" 
                    data-word-id="${word.id}" 
                    data-word-name='${JSON.stringify(word.word)}' 
                    title="${translations.remove || 'Remove'}">
                <i class="bi bi-trash"></i> ${translations.remove || 'Remove'}
            </button>
        `;
        
        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        
        tbody.appendChild(tr);
    });
}

// Render list view
function renderListView(words) {
    const listContainer = document.getElementById('wordsList');
    if (!listContainer) return;
    
    listContainer.innerHTML = '';
    
    words.forEach(word => {
        const item = document.createElement('div');
        item.className = 'list-group-item d-flex justify-content-between align-items-center word-item';
        item.setAttribute('data-word', word.word.toLowerCase());
        item.setAttribute('data-word-id', word.id);
        item.setAttribute('data-word-text', word.word);
        
        item.innerHTML = `
            <div class="d-flex align-items-center gap-2">
                <span class="badge bg-secondary">#${word.id}</span>
                <span class="word-text"><strong>${escapeHtml(word.word)}</strong></span>
            </div>
            <button class="btn btn-sm btn-outline-danger remove-word-btn" 
                    data-category-id="${getCategoryId()}" 
                    data-word-id="${word.id}" 
                    data-word-name='${JSON.stringify(word.word)}'>
                <i class="bi bi-trash me-1"></i>${translations.remove || 'Remove'}
            </button>
        `;
        
        listContainer.appendChild(item);
    });
}

// Render grid view
function renderGridView(words) {
    const gridContainer = document.getElementById('wordsGrid');
    if (!gridContainer) return;
    
    gridContainer.innerHTML = '';
    
    words.forEach(word => {
        const card = document.createElement('div');
        card.className = 'word-card word-item';
        card.setAttribute('data-word', word.word.toLowerCase());
        card.setAttribute('data-word-id', word.id);
        card.setAttribute('data-word-text', word.word);
        
        card.innerHTML = `
            <div class="d-flex justify-content-between align-items-start mb-2">
                <span class="badge bg-secondary">#${word.id}</span>
            </div>
            <h6 class="word-text mb-3"><strong>${escapeHtml(word.word)}</strong></h6>
            <button class="btn btn-sm btn-outline-danger w-100 remove-word-btn" 
                    data-category-id="${getCategoryId()}" 
                    data-word-id="${word.id}" 
                    data-word-name='${JSON.stringify(word.word)}'>
                <i class="bi bi-trash"></i> ${translations.remove || 'Remove'}
            </button>
        `;
        
        gridContainer.appendChild(card);
    });
}

// Render compact view
function renderCompactView(words) {
    const compactContainer = document.getElementById('wordsCompact');
    if (!compactContainer) return;
    
    compactContainer.innerHTML = '';
    
    words.forEach(word => {
        const badge = document.createElement('span');
        badge.className = 'word-badge word-item';
        badge.setAttribute('data-word', word.word.toLowerCase());
        badge.setAttribute('data-word-id', word.id);
        badge.setAttribute('data-word-text', word.word);
        
        badge.innerHTML = `
            <span class="badge bg-secondary">#${word.id}</span>
            <span class="word-text">${escapeHtml(word.word)}</span>
            <button type="button" class="btn-close btn-close-sm remove-word-btn" 
                    data-category-id="${getCategoryId()}" 
                    data-word-id="${word.id}" 
                    data-word-name='${JSON.stringify(word.word)}' 
                    aria-label="${translations.remove || 'Remove'}"></button>
        `;
        
        compactContainer.appendChild(badge);
    });
}

// Change display format
function changeDisplayFormat() {
    const container = document.getElementById('wordsDisplayContainer');
    if (!container) return;
    
    // Get the selected format from dropdown if not already set
    const formatSelect = document.getElementById('displayFormat');
    if (formatSelect && formatSelect.value) {
        currentFormat = formatSelect.value;
    }
    
    // Remove all view mode classes
    container.classList.remove('view-mode-table', 'view-mode-list', 'view-mode-grid', 'view-mode-compact');
    
    // Add current view mode class
    container.classList.add(`view-mode-${currentFormat}`);
    
    // Re-render
    applyFiltersAndRender();
}

// Render pagination
function renderPagination() {
    // One renderer. This page used to carry its own fallback pager, so the
    // same list could be drawn two different ways depending on whether a
    // module had loaded - the second implementation is gone.
    const container = document.getElementById('pagination');
    if (!container) return;

    const totalPages = Math.ceil(filteredWords.length / itemsPerPage) || 1;
    const totalItems = filteredWords.length;

    if (totalPages <= 1 && totalItems === 0) {
        container.innerHTML = '';
        return;
    }

    import('../modules/rendering/unified-pagination.js').then(module => {
        module.renderUnifiedPagination({
            currentPage: currentPage,
            totalPages: totalPages,
            containerId: container.id,
            onPageChange: (targetPage) => changePage(targetPage),
            urlParams: {},
            showInfo: true,
            showJump: totalPages > 5,
            baseUrl: window.location.pathname
        });
    });
}

// Change page
function changePage(page) {
    const totalPages = Math.ceil(filteredWords.length / itemsPerPage);
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    applyFiltersAndRender();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Change page size
function changePageSize() {
    currentPage = 1;
    applyFiltersAndRender();
}

// Sort by column
function sortByColumn(column) {
    if (sortColumn === column) {
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = column;
        sortDirection = 'asc';
    }
    
    currentSort = `${sortColumn}-${sortDirection}`;
    const sortSelect = document.getElementById('sortBy');
    if (sortSelect) {
        sortSelect.value = currentSort;
    }
    
    applyFiltersAndRender();
}

// Clear search
function clearSearch() {
    const searchInput = document.getElementById('wordSearch');
    if (searchInput) {
        searchInput.value = '';
        applyFiltersAndRender();
    }
}

// Clear all filters
function clearAllFilters() {
    document.getElementById('wordSearch').value = '';
    document.getElementById('sortBy').value = 'word-asc';
    document.getElementById('displayFormat').value = 'table';
    document.getElementById('itemsPerPage').value = '10';
    
    currentPage = 1;
    itemsPerPage = 10;
    currentSort = 'word-asc';
    currentFormat = 'table';
    sortColumn = 'word';
    sortDirection = 'asc';
    
    changeDisplayFormat();
}

// Export words
function exportWordsToCSV() {
    const headers = ['ID', 'Word'];
    const rows = filteredWords.map(word => [word.id, word.word]);
    
    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    
    link.setAttribute('href', url);
    link.setAttribute('download', `category-words-${Date.now()}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Get category ID from page data
function getCategoryId() {
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            return data.category_id;
        } catch (e) {
            console.error('Error parsing category ID:', e);
        }
    }
    return 0;
}

// CSRF token helper functions
function getCSRFToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    return metaTag ? metaTag.getAttribute('content') : '';
}

async function getCSRFTokenAsync() {
    const metaToken = document.querySelector('meta[name="csrf-token"]');
    if (metaToken) {
        const token = metaToken.getAttribute('content');
        if (token) return token;
    }
    
    try {
        const response = await fetch('/api/csrf-token');
        if (!response.ok) {
            console.warn('Failed to fetch CSRF token from API');
            return '';
        }
        const data = await response.json();
        return data.csrf_token || '';
    } catch (error) {
        console.error('Error fetching CSRF token:', error);
        return '';
    }
}

// Toast notification helper
function showToast(message, type = 'info', duration = 4000) {
    let toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }
    
    const toastId = 'toast-' + Date.now();
    const icons = {
        success: 'check-circle-fill',
        error: 'exclamation-triangle-fill',
        warning: 'exclamation-triangle-fill',
        info: 'info-circle-fill'
    };
    
    const bgColors = {
        success: 'success',
        error: 'danger',
        warning: 'warning',
        info: 'info'
    };
    
    const toastHtml = `
        <div id="${toastId}" class="toast align-items-center text-white bg-${bgColors[type]} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi bi-${icons[type]} me-2"></i>
                    ${escapeHtml(message)}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: duration });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}

// Remove word from category
async function removeWordFromCategory(categoryId, wordId, wordName) {
    // Handle JSON-encoded word name
    let displayName = wordName;
    try {
        if (typeof wordName === 'string' && (wordName.startsWith('"') || wordName.startsWith("'"))) {
            displayName = JSON.parse(wordName);
        }
    } catch (e) {
        displayName = wordName;
    }
    
    if (!confirm(translations.confirmRemove || `Are you sure you want to remove "${displayName}" from this category?`)) {
        return;
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch(`/api/categories/${categoryId}/words/${wordId}`, {
        method: 'DELETE',
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.wordRemoved || 'Word removed from category successfully', 'success');
            // Remove word from allWords array
            allWords = allWords.filter(w => w.id !== wordId);
            // Reload page after a short delay
            setTimeout(() => {
                window.location.reload();
            }, 1000);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error removing word from category:', error);
        showToast('Error removing word from category: ' + error.message, 'error');
    });
}

// Word search state
let wordSearchTimeout = null;
let currentWordSearchResults = [];
let selectedWordId = null;
let selectedWordText = null;
let clickOutsideHandler = null;
let inputHandler = null;
let keydownHandler = null;

// Open add word modal
function openAddWordModal() {
    const modalElement = document.getElementById('addWordToCategoryModal');
    if (!modalElement) {
        console.error('Add word modal not found');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const modalTitle = document.getElementById('addWordToCategoryModalLabel');
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordInput = document.getElementById('wordInput');
    const wordResults = document.getElementById('wordSearchResults');
    const selectedWordIdInput = document.getElementById('selectedWordId');
    
    if (!categoryIdInput || !wordInput || !wordResults || !selectedWordIdInput) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Clean up any existing handlers first
    if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler, true);
        clickOutsideHandler = null;
    }
    if (inputHandler && wordInput) {
        wordInput.removeEventListener('input', inputHandler);
        inputHandler = null;
    }
    if (keydownHandler && wordInput) {
        wordInput.removeEventListener('keydown', keydownHandler);
        keydownHandler = null;
    }
    if (wordSearchTimeout) {
        clearTimeout(wordSearchTimeout);
        wordSearchTimeout = null;
    }
    
    // Reset state
    selectedWordId = null;
    selectedWordText = null;
    currentWordSearchResults = [];
    wordInput.value = '';
    selectedWordIdInput.value = '';
    wordResults.style.display = 'none';
    wordResults.innerHTML = '';
    
    // Update modal title with category name
    const pageDataEl = document.getElementById('category-words-page-data');
    if (pageDataEl && modalTitle) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            const categoryName = data.category_name || '';
            modalTitle.textContent = `${translations.addWordToCategory || 'Add Word to Category'}: ${categoryName}`;
        } catch (e) {
            console.error('Error parsing category name:', e);
        }
    }
    
    // Initialize word search functionality
    function initializeWordSearch() {
        // Clear any existing timeouts
        if (wordSearchTimeout) {
            clearTimeout(wordSearchTimeout);
            wordSearchTimeout = null;
        }
        
        // Remove existing listeners if any
        if (inputHandler) {
            wordInput.removeEventListener('input', inputHandler);
        }
        if (keydownHandler) {
            wordInput.removeEventListener('keydown', keydownHandler);
        }
        
        // Handle input changes
        inputHandler = function(e) {
            const searchTerm = e.target.value.trim();
            
            // Clear previous timeout
            if (wordSearchTimeout) {
                clearTimeout(wordSearchTimeout);
            }
            
            // Reset selection
            selectedWordId = null;
            selectedWordText = null;
            selectedWordIdInput.value = '';
            
            if (searchTerm.length === 0) {
                wordResults.style.display = 'none';
                wordResults.innerHTML = '';
                return;
            }
            
            // Debounce search
            wordSearchTimeout = setTimeout(() => {
                searchWords(searchTerm);
            }, 300);
        };
        wordInput.addEventListener('input', inputHandler);
        
        // Handle keyboard navigation
        keydownHandler = function(e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                const firstItem = wordResults.querySelector('.word-result-item');
                if (firstItem) {
                    firstItem.focus();
                    firstItem.classList.add('active');
                }
            } else if (e.key === 'Escape') {
                wordResults.style.display = 'none';
            } else if (e.key === 'Enter' && selectedWordId) {
                e.preventDefault();
                // Word already selected, will be handled by form submit
            }
        };
        wordInput.addEventListener('keydown', keydownHandler);
        
        // Focus input when modal opens
        setTimeout(() => {
            wordInput.focus();
        }, 300);
    }
    
    // Search words function
    async function searchWords(searchTerm) {
        if (!searchTerm || searchTerm.length === 0) {
            wordResults.style.display = 'none';
            return;
        }
        
        try {
            const categoryId = getCategoryId();
            const params = new URLSearchParams({
                q: searchTerm,
                page: 1,
                per_page: 20
            });
            
            if (categoryId) {
                params.append('exclude_category_id', categoryId);
            }
            
            const response = await fetch(`/api/words/search?${params.toString()}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                currentWordSearchResults = data.results;
                renderWordResults(data.results, searchTerm);
            } else {
                // Show option to create new word
                renderWordResults([], searchTerm);
            }
        } catch (error) {
            console.error('Error searching words:', error);
            wordResults.style.display = 'none';
        }
    }
    
    // Render search results
    function renderWordResults(results, searchTerm) {
        wordResults.innerHTML = '';
        
        if (results.length > 0) {
            results.forEach((item, index) => {
                const wordId = item.id || item.word_id;
                const wordText = item.text || item.word || '';
                const usageCount = item.usage_count || 0;
                
                const itemDiv = document.createElement('div');
                itemDiv.className = 'word-result-item';
                itemDiv.tabIndex = 0;
                itemDiv.setAttribute('data-word-id', wordId);
                itemDiv.setAttribute('data-word-text', wordText);
                
                itemDiv.innerHTML = `
                    <div class="d-flex align-items-center">
                        <i class="bi bi-check-circle me-2 text-primary"></i>
                        <span class="word-text">${escapeHtml(wordText)}</span>
                        ${usageCount > 0 ? `<small class="text-muted ms-2">(${usageCount} files)</small>` : ''}
                    </div>
                `;
                
                itemDiv.addEventListener('click', function() {
                    selectWord(wordId, wordText);
                });
                
                itemDiv.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        selectWord(wordId, wordText);
                    } else if (e.key === 'ArrowDown') {
                        e.preventDefault();
                        const next = wordResults.querySelectorAll('.word-result-item')[index + 1];
                        if (next) {
                            itemDiv.classList.remove('active');
                            next.focus();
                            next.classList.add('active');
                        }
                    } else if (e.key === 'ArrowUp') {
                        e.preventDefault();
                        if (index > 0) {
                            const prev = wordResults.querySelectorAll('.word-result-item')[index - 1];
                            itemDiv.classList.remove('active');
                            if (prev) {
                                prev.focus();
                                prev.classList.add('active');
                            } else {
                                wordInput.focus();
                            }
                        }
                    }
                });
                
                wordResults.appendChild(itemDiv);
            });
        }
        
        // Always show option to create new word if search term doesn't match exactly
        const exactMatch = results.some(item => {
            const wordText = (item.text || item.word || '').toLowerCase();
            return wordText === searchTerm.toLowerCase();
        });
        
        if (!exactMatch && searchTerm.length > 0 && !searchTerm.match(/^\d+$/)) {
            const createDiv = document.createElement('div');
            createDiv.className = 'word-result-item word-result-create';
            createDiv.tabIndex = 0;
            createDiv.setAttribute('data-word-id', 'new');
            createDiv.setAttribute('data-word-text', searchTerm);
            
            createDiv.innerHTML = `
                <div class="d-flex align-items-center">
                    <i class="bi bi-plus-circle me-2 text-success"></i>
                    <span class="word-text">${escapeHtml(searchTerm)}</span>
                    <small class="text-muted ms-2">(create new)</small>
                </div>
            `;
            
            createDiv.addEventListener('click', function() {
                selectWord('new', searchTerm);
            });
            
            createDiv.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    selectWord('new', searchTerm);
                }
            });
            
            wordResults.appendChild(createDiv);
        }
        
        wordResults.style.display = 'block';
    }
    
    // Select word function
    function selectWord(wordId, wordText) {
        selectedWordId = wordId;
        selectedWordText = wordText;
        
        if (wordId === 'new') {
            selectedWordIdInput.value = '';
            wordInput.value = wordText;
        } else {
            selectedWordIdInput.value = wordId;
            wordInput.value = wordText;
        }
        
        wordResults.style.display = 'none';
        wordInput.focus();
    }
    
    // Show modal and initialize
    modal.show();
    
    // Wait for modal to be fully shown
    modalElement.addEventListener('shown.bs.modal', function onShown() {
        modalElement.removeEventListener('shown.bs.modal', onShown);
        initializeWordSearch();
        
        // Add click outside handler after initialization
        const inputContainer = wordInput.closest('.position-relative') || wordInput.parentElement;
        clickOutsideHandler = function(e) {
            const target = e.target;
            if (inputContainer && wordResults && 
                !inputContainer.contains(target) && 
                !wordResults.contains(target)) {
                wordResults.style.display = 'none';
            }
        };
        // Use capture phase to catch clicks before they bubble
        setTimeout(() => {
            document.addEventListener('click', clickOutsideHandler, true);
        }, 100);
    }, { once: true });
    
    // Clean up when modal is hidden
    modalElement.addEventListener('hidden.bs.modal', function onHidden() {
        // Clean up event listeners
        if (clickOutsideHandler) {
            document.removeEventListener('click', clickOutsideHandler, true);
            clickOutsideHandler = null;
        }
        
        // Clear search timeout
        if (wordSearchTimeout) {
            clearTimeout(wordSearchTimeout);
            wordSearchTimeout = null;
        }
        
        // Remove input event listeners
        if (inputHandler && wordInput) {
            wordInput.removeEventListener('input', inputHandler);
            inputHandler = null;
        }
        if (keydownHandler && wordInput) {
            wordInput.removeEventListener('keydown', keydownHandler);
            keydownHandler = null;
        }
        
        // Reset form
        if (wordInput) wordInput.value = '';
        if (selectedWordIdInput) selectedWordIdInput.value = '';
        if (wordResults) {
            wordResults.style.display = 'none';
            wordResults.innerHTML = '';
        }
        
        // Reset state
        selectedWordId = null;
        selectedWordText = null;
        currentWordSearchResults = [];
    }, { once: true });
}

// Create a new word
async function createNewWord(wordText) {
    const csrfToken = getCSRFToken() || await getCSRFTokenAsync();
    if (!csrfToken) {
        throw new Error('Unable to obtain CSRF token');
    }
    
    const response = await fetch('/api/words', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            word: wordText.trim(),
            csrf_token: csrfToken
        })
    });
    
    const data = await response.json();
    if (data.success) {
        return data.id;
    } else {
        throw new Error(data.error || 'Failed to create word');
    }
}

// Save word to category
async function saveWordToCategory() {
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordInput = document.getElementById('wordInput');
    const selectedWordIdInput = document.getElementById('selectedWordId');
    
    if (!categoryIdInput || !wordInput || !selectedWordIdInput) {
        showToast('Form elements not found', 'error');
        return;
    }
    
    const categoryId = categoryIdInput.value;
    const wordText = wordInput.value.trim();
    
    if (!wordText) {
        showToast('Please enter or select a word', 'warning');
        return;
    }
    
    // Get word ID - either from selection or create new
    let wordId;
    const selectedId = selectedWordIdInput.value;
    
    if (selectedId === 'new' || (!selectedId && wordText)) {
        // This is a new word, create it first
        try {
            showToast('Creating new word...', 'info');
            wordId = await createNewWord(wordText);
            showToast('Word created successfully', 'success');
        } catch (error) {
            console.error('Error creating word:', error);
            showToast('Error creating word: ' + error.message, 'error');
            return;
        }
    } else if (selectedId) {
        // Existing word, use the ID
        wordId = parseInt(selectedId);
        if (isNaN(wordId)) {
            showToast('Invalid word selection', 'error');
            return;
        }
    } else {
        // Try to find word by text
        try {
            const response = await fetch(`/api/words/search?q=${encodeURIComponent(wordText)}&per_page=1`);
            const data = await response.json();
            if (data.results && data.results.length > 0 && data.results[0].word.toLowerCase() === wordText.toLowerCase()) {
                wordId = data.results[0].id;
            } else {
                // Create new word
                showToast('Creating new word...', 'info');
                wordId = await createNewWord(wordText);
                showToast('Word created successfully', 'success');
            }
        } catch (error) {
            console.error('Error finding/creating word:', error);
            showToast('Error processing word: ' + error.message, 'error');
            return;
        }
    }
    
    // Get CSRF token
    let csrfToken = getCSRFToken();
    if (!csrfToken) {
        csrfToken = await getCSRFTokenAsync();
    }
    
    if (!csrfToken) {
        showToast('Unable to obtain CSRF token. Please refresh the page.', 'error');
        return;
    }
    
    fetch('/api/words-categorys/add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            word_id: wordId,
            category_id: parseInt(categoryId),
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(async data => {
        if (data.success) {
            showToast(data.message || translations.wordAdded || 'Word added to category successfully', 'success');
            
            // Clean up modal and event listeners first
            const modalElement = document.getElementById('addWordToCategoryModal');
            if (modalElement) {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    // Clean up event listeners
                    if (clickOutsideHandler) {
                        document.removeEventListener('click', clickOutsideHandler, true);
                        clickOutsideHandler = null;
                    }
                    if (wordSearchTimeout) {
                        clearTimeout(wordSearchTimeout);
                        wordSearchTimeout = null;
                    }
                    
                    // Clear form
                    const wordInput = document.getElementById('wordInput');
                    const selectedWordIdInput = document.getElementById('selectedWordId');
                    const wordResults = document.getElementById('wordSearchResults');
                    if (wordInput) {
                        wordInput.value = '';
                        // Remove event listeners
                        const newInput = wordInput.cloneNode(true);
                        wordInput.parentNode.replaceChild(newInput, wordInput);
                    }
                    if (selectedWordIdInput) selectedWordIdInput.value = '';
                    if (wordResults) {
                        wordResults.style.display = 'none';
                        wordResults.innerHTML = '';
                    }
                    
                    // Hide modal
                    modal.hide();
                }
            }
            
            // Add word to list immediately without reload
            try {
                await addWordToList(wordId, wordText);
            } catch (error) {
                console.error('Error adding word to list:', error);
                // Fallback to reload if dynamic update fails
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            }
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error adding word to category:', error);
        showToast('Error adding word to category: ' + error.message, 'error');
    });
}

// Add word to list dynamically
async function addWordToList(wordId, wordText) {
    try {
        // Create new word object
        const newWord = {
            id: wordId,
            word: wordText,
            index: allWords.length + 1
        };
        
        // Add to allWords array
        allWords.push(newWord);
        
        // Update filtered words if they match current filter
        const searchTerm = (document.getElementById('wordSearch')?.value || '').toLowerCase().trim();
        if (!searchTerm || wordText.toLowerCase().includes(searchTerm)) {
            filteredWords.push(newWord);
            // Re-sort
            sortWords(filteredWords);
        }
        
        // Update counts
        updateCounts();
        
        // Re-render the display
        renderWords();
        
        // Update pagination
        renderPagination();
        
        console.log('Word added to list:', newWord);
    } catch (error) {
        console.error('Error in addWordToList:', error);
        throw error;
    }
}

// Helper function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Make functions available globally (must be done at module load time)
// This ensures onclick handlers in HTML can access them
if (typeof window !== 'undefined') {
    window.removeWordFromCategory = removeWordFromCategory;
    window.openAddWordModal = openAddWordModal;
    window.saveWordToCategory = saveWordToCategory;
    window.changePage = changePage;
    window.changePageSize = changePageSize;
    window.sortByColumn = sortByColumn;
    window.clearSearch = clearSearch;
    window.clearAllFilters = clearAllFilters;
    window.exportWords = exportWordsToCSV;
    window.applyFilters = applyFiltersAndRender;
    window.changeDisplayFormat = changeDisplayFormat;
}
