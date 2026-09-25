/**
 * Words List Page JavaScript
 * Extracted from Word/Word_list.html
 */

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('words-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing words list page data:', e);
        }
    }
    
    console.log('Words list page loaded');
});

    const tableBody = document.getElementById('wordsTableBody');
    const searchInput = document.getElementById('searchWords');
    const statusFilter = document.getElementById('statusFilter');
    const sortBySelect = document.getElementById('sortBy');
    const sortOrderSelect = document.getElementById('sortOrder');
    const perPageSelect = document.getElementById('perPage');
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    
    let currentPage = 1;
    let currentPerPage = 10;
    let currentSort = 'usage_count';
    let currentOrder = 'desc';
    let selectedWords = new Set();
    let wordModal = null;
    let editWordId = null;
    
    // Initialize Bootstrap modal
    function initializeModal() {
        const modalElement = document.getElementById('wordModal');
        if (modalElement) {
            if (typeof bootstrap !== 'undefined') {
                try {
                    wordModal = new bootstrap.Modal(modalElement);
                } catch (e) {
                    console.error('Error initializing Bootstrap modal:', e);
                }
            } else {
                // Bootstrap not loaded yet, try again after a short delay
                setTimeout(initializeModal, 100);
            }
        }
    }
    
    // Initialize
    function init() {
        const urlParams = new URLSearchParams(window.location.search);
        currentPage = parseInt(urlParams.get('page')) || 1;
        
        if (perPageSelect) {
            currentPerPage = parseInt(perPageSelect.value) || 10;
        }
        
        if (sortBySelect) {
            sortBySelect.value = currentSort;
        }
        if (sortOrderSelect) {
            sortOrderSelect.value = currentOrder;
        }
        
        updateSortIcons();
        createPaginationContainer();
        
        // Only fetch page if tableBody exists
        if (tableBody) {
            fetchPage(currentPage);
        }
        
        setupEventListeners();
        
        // Initialize Bootstrap modal - ensure it's available
        initializeModal();

        // AUDIT (UI-01): /words/add used to render a missing template (500).
        // It now redirects here with ?add=1, so open the existing modal.
        if (urlParams.get('add') === '1') {
            setTimeout(() => openAddWordModal(), 200);
        }
    }
    
    function updateSortIcons() {
        document.querySelectorAll('[id^="sortIcon-"]').forEach(icon => {
            icon.className = 'bi bi-arrow-down-up';
        });
        
        const activeIcon = document.getElementById(`sortIcon-${currentSort}`);
        if (activeIcon) {
            if (currentOrder === 'asc') {
                activeIcon.className = 'bi bi-arrow-up';
            } else {
                activeIcon.className = 'bi bi-arrow-down';
            }
        }
    }
    
    function setupEventListeners() {
        let searchTimeout;
        
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    currentPage = 1;
                    fetchPage(1);
                }, 300);
            });
        }
        
        if (perPageSelect) {
            perPageSelect.addEventListener('change', () => {
                currentPerPage = parseInt(perPageSelect.value);
                currentPage = 1;
                fetchPage(1);
            });
        }
    }
    
    function renderRows(words, page) {
        if (!tableBody) return;
        
        tableBody.innerHTML = '';
        
        if (!words || words.length === 0) {
            const query = searchInput?.value?.trim() || '';
            const message = query ? 
                `${translations.noWordsFound} "${query}".` : 
                translations.noWordsYet;
            
            tableBody.innerHTML = `
                <tr><td colspan="6" class="text-center py-5 text-muted">
                    <i class="bi bi-book display-4 mb-3"></i>
                    <div>${message}</div>
                </td></tr>`;
            return;
        }
        
        words.forEach((word, idx) => {
            if (!word || !word.id) return;
            
            const globalIndex = ((page - 1) * currentPerPage) + (idx + 1);
            const tr = document.createElement('tr');
            tr.dataset.wordId = word.id;
            
            tr.innerHTML = `
                <td>
                    <input type="checkbox" class="form-check-input word-checkbox" 
                           value="${word.id}" onchange="updateSelection()">
                </td>
                <td><span class="badge bg-secondary">${globalIndex}</span></td>
                <td>
                    <div class="d-flex align-items-center">
                        <span class="word-text" data-word-id="${word.id}">
                            <strong>${(word.word || '').replace(/</g,'&lt;')}</strong>
                        </span>
                        <button class="btn btn-sm btn-link p-0 ms-1" onclick="editWord(${word.id})" title="${translations.editWord}">
                            <i class="bi bi-pencil"></i>
                        </button>
                    </div>
                </td>
                <td><span class="badge bg-info">${word.usage_count || 0}</span></td>
                <td>${word.usage_count > 0 ? `<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>${translations.active}</span>` : `<span class="badge bg-secondary"><i class="bi bi-dash-circle me-1"></i>${translations.unused}</span>`}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary" onclick="viewWord(${word.id})" title="${translations.viewDetails || 'View Details'}">
                            <i class="bi bi-eye"></i>
                        </button>
                        <button class="btn btn-outline-danger" onclick="deleteWord(${word.id})" title="${translations.deleteWord || 'Delete'}">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>`;
            tableBody.appendChild(tr);
        });
        
        // Update stats
        const activeCount = words.filter(w => w.usage_count > 0).length;
        const unusedCount = words.filter(w => w.usage_count === 0).length;
        const activeEl = document.getElementById('activeCount');
        const unusedEl = document.getElementById('unusedCount');
        if (activeEl) activeEl.textContent = activeCount;
        if (unusedEl) unusedEl.textContent = unusedCount;
    }
    
    function renderPaginator(page, total_pages) {
        // Find or create pagination container
        let paginationContainer = document.querySelector('.pagination-container');
        if (!paginationContainer) {
            paginationContainer = createPaginationContainer();
        }
        
        if (!paginationContainer) {
            console.error('Could not create pagination container');
            return;
        }
        
        // Ensure container has an ID
        if (!paginationContainer.id) {
            paginationContainer.id = 'paginationContainer';
        }
        
        // Get URL parameters to preserve
        const urlParams = {};
        const currentParams = new URLSearchParams(window.location.search);
        currentParams.forEach((value, key) => {
            if (key !== 'page') {
                urlParams[key] = value;
            }
        });
        
        // Use unified pagination
        import('../modules/rendering/unified-pagination.js').then(module => {
            module.renderUnifiedPagination({
                currentPage: page,
                totalPages: total_pages,
                containerId: paginationContainer.id,
                onPageChange: (targetPage) => {
                    // Update URL without page reload
                    const url = new URL(window.location);
                    url.searchParams.set('page', targetPage);
                    window.history.pushState({}, '', url);
                    fetchPage(targetPage);
                },
                urlParams: urlParams,
                showInfo: true,
                showJump: total_pages > 5,
                baseUrl: window.location.pathname
            });
        }).catch(err => {
            console.error('Error loading unified pagination:', err);
            // Fallback to old pagination if module fails to load
            renderOldPaginator(page, total_pages, paginationContainer);
        });
    }
    
    // Fallback old pagination renderer
    function renderOldPaginator(page, total_pages, paginationContainer) {
        paginationContainer.innerHTML = '';
        
        let pageInfo = paginationContainer.querySelector('.pagination-info');
        if (!pageInfo) {
            pageInfo = document.createElement('div');
            pageInfo.className = 'small text-muted pagination-info';
            paginationContainer.insertBefore(pageInfo, paginationContainer.firstChild);
        }
        pageInfo.textContent = `${translations.page} ${page} ${translations.of} ${total_pages}`;
        
        let pagContainer = paginationContainer.querySelector('ul.pagination');
        if (!pagContainer) {
            pagContainer = createPaginationList();
            paginationContainer.appendChild(pagContainer);
        }
        pagContainer.innerHTML = '';
        
        const windowSize = 2;
        const start = Math.max(1, page - windowSize);
        const end = Math.min(total_pages, page + windowSize);
        
        function addItem(label, p, disabled=false, active=false) {
            const li = document.createElement('li');
            li.className = 'page-item' + (disabled ? ' disabled' : '') + (active ? ' active' : '');
            const a = document.createElement('a');
            a.className = 'page-link';
            a.href = '#';
            a.innerHTML = label;
            a.addEventListener('click', function(ev){
                ev.preventDefault();
                if (disabled || active) return;
                const url = new URL(window.location);
                url.searchParams.set('page', p);
                window.history.pushState({}, '', url);
                fetchPage(p);
            });
            li.appendChild(a);
            pagContainer.appendChild(li);
        }
        
        addItem('<i class="bi bi-chevron-double-left" aria-hidden="true"></i>', 1, page === 1);
        addItem('<i class="bi bi-chevron-left" aria-hidden="true"></i>', Math.max(1, page - 1), page === 1);
        
        if (start > 1) addItem('1', 1);
        if (start > 2) {
            const li = document.createElement('li'); 
            li.className='page-item disabled';
            li.innerHTML = '<span class="page-link">…</span>'; 
            pagContainer.appendChild(li);
        }
        
        for (let p = start; p <= end; p++) {
            addItem(String(p), p, false, p === page);
        }
        
        if (end < total_pages - 1) {
            const li = document.createElement('li'); 
            li.className='page-item disabled';
            li.innerHTML = '<span class="page-link">…</span>'; 
            pagContainer.appendChild(li);
        }
        if (end < total_pages) addItem(String(total_pages), total_pages);
        
        addItem('<i class="bi bi-chevron-right" aria-hidden="true"></i>', Math.min(total_pages, page + 1), page === total_pages);
        addItem('<i class="bi bi-chevron-double-right" aria-hidden="true"></i>', total_pages, page === total_pages);
    }
    
    function createPaginationContainer() {
        const wordsTable = document.getElementById('wordsTable');
        if (!wordsTable) return null;
        
        const statCard = wordsTable.closest('.stat-card');
        if (!statCard) return null;
        
        let container = statCard.querySelector('.pagination-container');
        if (container) {
            container.style.display = 'flex';
            return container;
        }
        
        container = document.createElement('div');
        container.className = 'd-flex justify-content-between align-items-center mt-3 mb-3 pagination-container';
        container.style.display = 'flex';
        container.style.width = '100%';
        container.style.clear = 'both';
        
        const tableWrapper = statCard.querySelector('.table-wrapper');
        if (tableWrapper && tableWrapper.parentNode) {
            tableWrapper.parentNode.insertBefore(container, tableWrapper.nextSibling);
        } else {
            statCard.appendChild(container);
        }
        
        return container;
    }
    
    function createPaginationList() {
        const ul = document.createElement('ul');
        ul.className = 'pagination pagination-sm mb-0';
        return ul;
    }
    
    let pendingFetch = null;
    function fetchPage(page=1) {
        currentPage = page;
        if (pendingFetch) pendingFetch.abort();
        const controller = new AbortController();
        pendingFetch = controller;
        
        // Show loading state
        if (tableBody) {
            tableBody.innerHTML = `
                <tr><td colspan="6" class="text-center py-5">
                    <div class="spinner-border text-primary" role="status">
                        <span class="visually-hidden">${translations.loading || 'Loading...'}</span>
                    </div>
                    <div class="mt-2 text-muted">${translations.loading || 'Loading...'}</div>
                </td></tr>`;
        }
        
        // Get API URL from page data or use default
        const pageDataEl = document.getElementById('words-list-page-data');
        let apiUrl = '/api/words';
        if (pageDataEl) {
            try {
                const data = JSON.parse(pageDataEl.textContent);
                apiUrl = data.api_url || '/api/words';
            } catch (e) {
                console.warn('Error parsing page data, using default API URL');
            }
        }
        
        const url = new URL(apiUrl, window.location.origin);
        url.searchParams.set('page', page);
        url.searchParams.set('per_page', currentPerPage);
        url.searchParams.set('sort', currentSort);
        url.searchParams.set('order', currentOrder);
        
        if (searchInput && searchInput.value.trim()) url.searchParams.set('q', searchInput.value.trim());
        if (statusFilter && statusFilter.value) url.searchParams.set('status', statusFilter.value);
        url.searchParams.set('_t', Date.now());
        
        fetch(url.toString(), { 
            signal: controller.signal,
            cache: 'no-cache',
            headers: {
                'Accept': 'application/json',
                'Cache-Control': 'no-cache'
            }
        })
            .then(r => {
                if (!r.ok) throw new Error(`HTTP error! status: ${r.status}`);
                return r.json();
            })
            .then(json => {
                if (!tableBody) return;
                
                if (!json || json.success === false) {
                    tableBody.innerHTML = `
                        <tr><td colspan="6" class="text-center py-5 text-danger">
                            <i class="bi bi-exclamation-triangle display-4 mb-3"></i>
                            <div>${translations.errorLoadingWords || 'Error loading words'}: ${json?.error || 'Invalid response'}</div>
                        </td></tr>`;
                    return;
                }
                
                if (json.per_page) {
                    currentPerPage = json.per_page;
                    if (perPageSelect) perPageSelect.value = json.per_page;
                }
                
                if (json.sort_by) {
                    currentSort = json.sort_by;
                    if (sortBySelect) sortBySelect.value = currentSort;
                }
                if (json.sort_order) {
                    currentOrder = json.sort_order;
                    if (sortOrderSelect) sortOrderSelect.value = currentOrder;
                }
                updateSortIcons();
                
                const words = json.words || [];
                const pageNum = json.page || 1;
                const totalPages = json.total_pages || 1;
                
                renderRows(words, pageNum);
                renderPaginator(pageNum, totalPages);
                updateSearchInfo(json);

                // STALE-01: keep the server-rendered "Total Words" stat card
                // in sync after client-side pagination/mutations.
                const totalStat = document.getElementById('totalWordsStat');
                if (totalStat && typeof json.total === 'number') {
                    totalStat.textContent = json.total;
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') return;
                console.error('Fetch error', err);
                if (tableBody) {
                    tableBody.innerHTML = `
                        <tr><td colspan="6" class="text-center py-5 text-danger">
                            <i class="bi bi-exclamation-triangle display-4 mb-3"></i>
                            <div>${translations.errorLoadingWords || 'Error loading words'}: ${err.message}</div>
                        </td></tr>`;
                }
            })
            .finally(() => { if (pendingFetch === controller) pendingFetch = null; });
    }
    
    function updateSearchInfo(json) {
        const infoEl = document.getElementById('searchResultsInfo');
        if (infoEl) {
            const start = ((json.page - 1) * json.per_page) + 1;
            const end = start + ((json.words || []).length) - 1;
            const total = json.total || 0;
            const query = searchInput ? searchInput.value.trim() : '';
            
            if (query) {
                infoEl.textContent = `Found ${total} result${total !== 1 ? 's' : ''} for "${query}" (${start}–${end})`;
            } else {
                infoEl.textContent = `${translations.showing} ${start}–${end} ${translations.of} ${total} ${translations.words}`;
            }
        }
    }
    
    // The page owns the selection (which rows, and the set the operations
    // send); the toolbar owns how that scope is drawn and announced. So this
    // reports the numbers and nothing else: it never enables or disables a
    // button, and it never names an action.
    function updateSelection() {
        const checkboxes = document.querySelectorAll('.word-checkbox:checked');
        selectedWords.clear();
        checkboxes.forEach(cb => selectedWords.add(parseInt(cb.value)));

        const allCheckboxes = document.querySelectorAll('.word-checkbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = allCheckboxes.length > 0 && checkboxes.length === allCheckboxes.length;
            selectAllCheckbox.indeterminate = checkboxes.length > 0 && checkboxes.length < allCheckboxes.length;
        }

        if (window.ActionToolbar) {
            window.ActionToolbar.sync('wordsActionBar', {
                selected: checkboxes.length,
                total: allCheckboxes.length
            });
        }
    }
    
    function toggleSelectAll() {
        const isChecked = selectAllCheckbox.checked;
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = isChecked;
        });
        updateSelection();
    }
    
    function selectAll() {
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = true;
        });
        updateSelection();
    }
    
    function selectNone() {
        document.querySelectorAll('.word-checkbox').forEach(cb => {
            cb.checked = false;
        });
        updateSelection();
    }
    
    function applyFilters() {
        currentPage = 1;
        fetchPage(1);
    }
    
    function sortBy(field) {
        if (currentSort === field) {
            currentOrder = currentOrder === 'asc' ? 'desc' : 'asc';
        } else {
            currentSort = field;
            currentOrder = 'asc';
        }
        sortBySelect.value = currentSort;
        sortOrderSelect.value = currentOrder;
        updateSortIcons();
        fetchPage(currentPage);
    }
    
    function applySorting() {
        currentSort = sortBySelect.value;
        currentOrder = sortOrderSelect.value;
        currentPage = 1;
        updateSortIcons();
        fetchPage(1);
    }
    
    function changePageSize() {
        currentPerPage = parseInt(perPageSelect.value);
        currentPage = 1;
        fetchPage(1);
    }
    
    function clearSearch() {
        searchInput.value = '';
        currentPage = 1;
        fetchPage(1);
    }
    
    function openAddWordModal() {
        // Get modal element - try both possible IDs
        let modalElement = document.getElementById('wordModal');
        if (!modalElement) {
            modalElement = document.getElementById('addWordToCategoryModal');
        }
        if (!modalElement) {
            // Modal doesn't exist on this page, silently return
            return;
        }
        
        // Check if Bootstrap is available
        if (typeof bootstrap === 'undefined') {
            console.error('Bootstrap is not loaded');
            return;
        }
        
        // Create modal instance (create new each time to avoid timing issues)
        let modal;
        try {
            modal = new bootstrap.Modal(modalElement);
        } catch (e) {
            console.error('Error creating Bootstrap modal:', e);
            return;
        }
        
        // Reset form and set title
        editWordId = null;
        const wordInput = document.getElementById('wordInput');
        const wordModalLabel = document.getElementById('wordModalLabel');
        
        if (wordInput) wordInput.value = '';
        if (wordModalLabel) wordModalLabel.innerHTML = '<i class="bi bi-book"></i> ' + translations.addWord;
        
        // Show modal
        try {
            modal.show();
            // Also update the stored reference for consistency
            wordModal = modal;
        } catch (e) {
            console.error('Error showing modal:', e);
        }
    }
    
    function editWord(wordId) {
        fetch(`/api/words/${wordId}`)
            .then(response => response.json())
            .then(data => {
                if (data.success && data.word) {
                    const modalElement = document.getElementById('wordModal');
                    if (!modalElement || typeof bootstrap === 'undefined') {
                        alert(translations.errorLoadingWords);
                        return;
                    }
                    
                    editWordId = wordId;
                    document.getElementById('wordInput').value = data.word.word;
                    document.getElementById('wordModalLabel').innerHTML = '<i class="bi bi-pencil"></i> ' + translations.editWord;
                    
                    // Create and show modal
                    try {
                        const modal = new bootstrap.Modal(modalElement);
                        modal.show();
                        wordModal = modal; // Store for consistency
                    } catch (e) {
                        console.error('Error showing edit modal:', e);
                        alert(translations.errorLoadingWords);
                    }
                } else {
                    alert(translations.errorLoadingWords);
                }
            })
            .catch(error => {
                console.error('Error loading word:', error);
                alert(translations.errorLoadingWords);
            });
    }
    
    function submitWord() {
        const wordText = document.getElementById('wordInput').value.trim();
        if (!wordText) {
            alert(translations.wordRequired);
            return;
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        const url = editWordId ? `/api/words/${editWordId}` : '/api/words';
        const method = editWordId ? 'PUT' : 'POST';
        
        fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ word: wordText })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Hide modal
                const modalElement = document.getElementById('wordModal');
                if (modalElement && typeof bootstrap !== 'undefined') {
                    try {
                        if (wordModal) {
                            wordModal.hide();
                        } else {
                            const modal = bootstrap.Modal.getInstance(modalElement);
                            if (modal) modal.hide();
                        }
                    } catch (e) {
                        console.error('Error hiding modal:', e);
                    }
                }
                alert(editWordId ? translations.wordUpdatedSuccessfully : translations.wordAddedSuccessfully);
                fetchPage(currentPage);
            } else {
                alert(data.error || translations.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert(translations.error);
        });
    }
    
    function bulkDelete() {
        if (selectedWords.size === 0) return;
        
        if (!confirm(`${selectedWords.size} ${translations.deleteSelectedWords}`)) return;
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/words/bulk-delete', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({ word_ids: Array.from(selectedWords) })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert(`${translations.successfullyDeleted} ${data.deleted_count} ${translations.words}`);
                window.location.href = window.location.pathname + '?t=' + Date.now();
            } else {
                alert(translations.errorDeletingWords + ': ' + (data.error || translations.error));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert(translations.errorDeletingWords);
        });
    }
    
    function bulkUpdate() {
        if (selectedWords.size === 0) return;
        
        if (selectedWords.size === 1) {
            const wordId = Array.from(selectedWords)[0];
            editWord(wordId);
            return;
        }
        
        if (confirm(`Multiple words selected. Edit the first word?`)) {
            const wordId = Array.from(selectedWords)[0];
            editWord(wordId);
        }
    }
    
    // Global functions
    window.viewWord = function(id) {
        window.location.href = `/words/${id}`;
    };
    
    window.deleteWord = function(id) {
        if (!confirm(translations.deleteWordConfirm + id + '?')) return;
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/words/${id}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(r => r.json())
        .then(j => {
            if (j.success) {
                alert(translations.wordDeletedSuccessfully);
                window.location.href = window.location.pathname + '?t=' + Date.now();
            } else {
                alert(j.error || translations.failedToDelete);
            }
        })
        .catch(e => alert(translations.error + ': ' + e.message));
    };
    
    window.updateSelection = updateSelection;
    window.toggleSelectAll = toggleSelectAll;
    window.selectAll = selectAll;
    window.selectNone = selectNone;
    window.applyFilters = applyFilters;
    window.applySorting = applySorting;
    window.sortBy = sortBy;
    window.changePageSize = changePageSize;
    window.clearSearch = clearSearch;
    window.bulkDelete = bulkDelete;
    window.bulkUpdate = bulkUpdate;
    window.openAddWordModal = openAddWordModal;
    window.editWord = editWord;
    window.submitWord = submitWord;
    
    // Wait for DOM and Bootstrap to be ready - optimized with better checks
    function waitForBootstrap(callback, maxAttempts = 10) {
        const modalElement = document.getElementById('wordModal');
        if (typeof bootstrap !== 'undefined' && (modalElement || maxAttempts <= 5)) {
            callback();
        } else if (maxAttempts > 0) {
            setTimeout(() => waitForBootstrap(callback, maxAttempts - 1), 50);
        } else {
            // Bootstrap might not be needed if modal doesn't exist
            console.warn('Bootstrap not available after waiting, initializing anyway');
            callback();
        }
    }
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            waitForBootstrap(() => {
                init();
            });
        });
    } else {
        // DOM already loaded
        waitForBootstrap(() => {
            init();
        });
    }