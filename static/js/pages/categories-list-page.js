/**
 * Categories List Page JavaScript
 * Manages categories with extensive filtering, sorting, and display options
 * Matches the style of words/keywords management pages
 */

// Use window.translations to avoid conflicts with other scripts
if (typeof window.translations === 'undefined') {
    window.translations = {};
}

// Local translations object (merged with window.translations)
let translations = window.translations;

// Global state
let allCategories = [];
let filteredCategories = [];
let currentPage = 1;
let itemsPerPage = 10;
let currentSort = 'name-asc';
let currentFormat = 'grid';
let sortColumn = 'name';
let sortDirection = 'asc';

// Load translations from JSON script tag
document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('categories-page-data');
    if (pageDataEl) {
        try {
            const jsonText = pageDataEl.textContent.trim();
            if (jsonText) {
                const data = JSON.parse(jsonText);
                const pageTranslations = data.translations || {};
                // Merge with window.translations
                Object.assign(window.translations, pageTranslations);
                translations = window.translations;
            }
        } catch (e) {
            console.error('Error parsing categories page data:', e);
            translations = window.translations || {};
        }
    }
    
    // Initialize categories data from DOM
    initializeCategoriesData();
    
    // Initialize event listeners
    initializeEventListeners();
    
    // Set initial display format
    const formatSelect = document.getElementById('displayFormat');
    if (formatSelect) {
        currentFormat = formatSelect.value || 'grid';
    }
    
    // Initial render
    applyFiltersAndRender();
    
    console.log('Categories list page loaded');
});

// Initialize categories data from DOM
function initializeCategoriesData() {
    const categoryItems = document.querySelectorAll('.category-item');
    const categoriesMap = new Map();
    
    // Process all category items and deduplicate by ID
    categoryItems.forEach((item, index) => {
        const id = parseInt(item.getAttribute('data-category-id')) || 0;
        if (id > 0 && !categoriesMap.has(id)) {
            // Try multiple ways to get the category name
            let name = item.getAttribute('data-name') || '';
            
            if (!name) {
                // Try .category-name class (used in list/table/compact views)
                const nameElement = item.querySelector('.category-name');
                if (nameElement) {
                    name = nameElement.textContent.trim();
                }
            }
            
            if (!name) {
                // Try h5 strong (used in grid view)
                const h5Strong = item.querySelector('h5 strong');
                if (h5Strong) {
                    name = h5Strong.textContent.trim();
                }
            }
            
            if (!name) {
                // Fallback: try any strong tag
                const strong = item.querySelector('strong');
                if (strong) {
                    name = strong.textContent.trim();
                }
            }
            
            categoriesMap.set(id, {
                id: id,
                name: name || `Category ${id}`,
                file_count: parseInt(item.getAttribute('data-file-count')) || 0,
                word_count: parseInt(item.getAttribute('data-word-count')) || 0,
                element: item,
                index: categoriesMap.size + 1
            });
        }
    });
    
    // Convert map to array and sort by ID
    allCategories = Array.from(categoriesMap.values()).sort((a, b) => a.id - b.id);
    filteredCategories = [...allCategories];
}

// Initialize event listeners
function initializeEventListeners() {
    // Search input
    const searchInput = document.getElementById('categorySearch');
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
    
    // Event delegation for add word buttons
    document.addEventListener('click', function(e) {
        const addWordBtn = e.target.closest('.add-word-btn');
        if (addWordBtn) {
            e.preventDefault();
            const categoryId = parseInt(addWordBtn.getAttribute('data-category-id'));
            let categoryName = addWordBtn.getAttribute('data-category-name');
            
            // Parse JSON string if needed
            try {
                if (categoryName && (categoryName.startsWith('"') || categoryName.startsWith("'"))) {
                    categoryName = JSON.parse(categoryName);
                }
            } catch (e) {
                // Use as-is if parsing fails
            }
            
            addWordToCategory(categoryId, categoryName);
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
    const searchTerm = (document.getElementById('categorySearch')?.value || '').toLowerCase().trim();
    
    // Filter categories
    filteredCategories = allCategories.filter(category => {
        const categoryName = category.name.toLowerCase();
        return categoryName.includes(searchTerm);
    });
    
    // Sort categories
    sortCategories(filteredCategories);
    
    // Update counts
    updateCounts();
    
    // Render categories
    renderCategories();
    
    // Render pagination
    renderPagination();
}

// Sort categories
function sortCategories(categories) {
    categories.sort((a, b) => {
        let aVal, bVal;
        
        if (sortColumn === 'name') {
            aVal = a.name.toLowerCase();
            bVal = b.name.toLowerCase();
        } else if (sortColumn === 'id') {
            aVal = a.id;
            bVal = b.id;
        } else if (sortColumn === 'files') {
            aVal = a.file_count;
            bVal = b.file_count;
        } else if (sortColumn === 'words') {
            aVal = a.word_count;
            bVal = b.word_count;
        }
        
        if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
        return 0;
    });
}

// Update counts
function updateCounts() {
    const totalCount = allCategories.length;
    const filteredCount = filteredCategories.length;
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = Math.min(startIndex + itemsPerPage, filteredCount);
    const visibleCount = Math.max(0, endIndex - startIndex);
    const totalPages = Math.ceil(filteredCount / itemsPerPage);
    
    const totalEl = document.getElementById('totalCategoriesCount');
    const filteredEl = document.getElementById('filteredCategoriesCount');
    const visibleEl = document.getElementById('visibleCategoriesCount');
    const pageEl = document.getElementById('currentPageInfo');
    const resultsInfo = document.getElementById('searchResultsInfo');
    
    if (totalEl) totalEl.textContent = totalCount.toLocaleString();
    if (filteredEl) filteredEl.textContent = filteredCount.toLocaleString();
    if (visibleEl) visibleEl.textContent = visibleCount.toLocaleString();
    if (pageEl) pageEl.textContent = `${currentPage} / ${totalPages || 1}`;
    
    if (resultsInfo) {
        if (filteredCount === totalCount) {
            resultsInfo.textContent = `${translations.showing || 'Showing'} ${startIndex + 1}-${endIndex} ${translations.of || 'of'} ${totalCount.toLocaleString()} ${translations.categories || 'categories'}`;
        } else {
            resultsInfo.textContent = `${translations.showing || 'Showing'} ${startIndex + 1}-${endIndex} ${translations.of || 'of'} ${filteredCount.toLocaleString()} ${translations.filtered || 'filtered'} ${translations.categories || 'categories'} (${totalCount.toLocaleString()} ${translations.total || 'total'})`;
        }
    }
}

// Render categories based on current format
function renderCategories() {
    const container = document.getElementById('categoriesDisplayContainer');
    if (!container) return;
    
    // Calculate pagination
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const categoriesToShow = filteredCategories.slice(startIndex, endIndex);
    
    // Show/hide empty state
    const emptyState = document.getElementById('emptyState');
    if (emptyState) {
        emptyState.style.display = categoriesToShow.length === 0 ? 'block' : 'none';
    }
    
    if (categoriesToShow.length === 0) {
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
            renderTableView(categoriesToShow, startIndex);
            break;
        case 'list':
            renderListView(categoriesToShow);
            break;
        case 'grid':
            renderGridView(categoriesToShow);
            break;
        case 'compact':
            renderCompactView(categoriesToShow);
            break;
        default:
            renderGridView(categoriesToShow);
    }
}

// Render table view
function renderTableView(categories, startIndex) {
    const tbody = document.getElementById('categoriesTableBody');
    if (!tbody) return;
    
    tbody.innerHTML = '';
    
    categories.forEach((category, idx) => {
        const tr = document.createElement('tr');
        tr.className = 'category-item';
        tr.setAttribute('data-name', category.name.toLowerCase());
        tr.setAttribute('data-category-id', category.id);
        tr.setAttribute('data-file-count', category.file_count);
        tr.setAttribute('data-word-count', category.word_count);
        
        // Create table cells properly
        const td1 = document.createElement('td');
        td1.innerHTML = `<span class="badge bg-secondary">${category.id}</span>`;
        
        const td2 = document.createElement('td');
        td2.innerHTML = `<div class="d-flex align-items-center"><span class="category-name"><strong>${escapeHtml(category.name)}</strong></span></div>`;
        
        const td3 = document.createElement('td');
        td3.innerHTML = `<span class="badge bg-primary">${category.file_count}</span>`;
        
        const td4 = document.createElement('td');
        td4.innerHTML = `<span class="badge bg-info">${category.word_count}</span>`;
        
        const td5 = document.createElement('td');
        td5.innerHTML = `
            <div class="btn-group btn-group-sm">
                <a href="/categories/${category.id}/words" class="btn btn-outline-primary" title="${translations.view || 'View Words'}">
                    <i class="bi bi-eye"></i> ${translations.view || 'View'}
                </a>
                <button class="btn btn-outline-success add-word-btn" 
                        data-category-id="${category.id}" 
                        data-category-name='${JSON.stringify(category.name)}' 
                        title="${translations.addWord || 'Add Word'}">
                    <i class="bi bi-plus"></i> ${translations.addWord || 'Add Word'}
                </button>
                <button class="btn btn-outline-danger" onclick="deleteCategory(${category.id})" title="${translations.deleteCategory || 'Delete'}">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `;
        
        tr.appendChild(td1);
        tr.appendChild(td2);
        tr.appendChild(td3);
        tr.appendChild(td4);
        tr.appendChild(td5);
        
        tbody.appendChild(tr);
    });
}

// Render list view
function renderListView(categories) {
    const listContainer = document.getElementById('categoriesList');
    if (!listContainer) return;
    
    listContainer.innerHTML = '';
    
    categories.forEach(category => {
        const item = document.createElement('div');
        item.className = 'list-group-item d-flex justify-content-between align-items-center category-item';
        item.setAttribute('data-name', category.name.toLowerCase());
        item.setAttribute('data-category-id', category.id);
        item.setAttribute('data-file-count', category.file_count);
        item.setAttribute('data-word-count', category.word_count);
        
        item.innerHTML = `
            <div class="d-flex align-items-center gap-3">
                <span class="badge bg-secondary">#${category.id}</span>
                <div>
                    <strong class="category-name">${escapeHtml(category.name)}</strong>
                    <div class="d-flex gap-2 mt-1">
                        <span class="badge bg-primary">${category.file_count} ${translations.files || 'Files'}</span>
                        <span class="badge bg-info">${category.word_count} ${translations.words || 'Words'}</span>
                    </div>
                </div>
            </div>
            <div class="btn-group btn-group-sm">
                <a href="/categories/${category.id}/words" class="btn btn-outline-primary">
                    <i class="bi bi-eye"></i> ${translations.view || 'View'}
                </a>
                <button class="btn btn-outline-success add-word-btn" 
                        data-category-id="${category.id}" 
                        data-category-name='${JSON.stringify(category.name)}'>
                    <i class="bi bi-plus"></i> ${translations.addWord || 'Add Word'}
                </button>
            </div>
        `;
        
        listContainer.appendChild(item);
    });
}

// Render grid view
function renderGridView(categories) {
    const gridContainer = document.getElementById('categoriesGrid');
    if (!gridContainer) return;
    
    gridContainer.innerHTML = '';
    
    categories.forEach(category => {
        const card = document.createElement('div');
        card.className = 'category-card category-item';
        card.setAttribute('data-name', category.name.toLowerCase());
        card.setAttribute('data-category-id', category.id);
        card.setAttribute('data-file-count', category.file_count);
        card.setAttribute('data-word-count', category.word_count);
        
        card.innerHTML = `
            <div class="d-flex justify-content-between align-items-start mb-2">
                <span class="badge bg-secondary">#${category.id}</span>
            </div>
            <h5 class="mb-3"><strong>${escapeHtml(category.name)}</strong></h5>
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <small class="text-muted d-block">${translations.files || 'Files'}</small>
                    <span class="badge bg-primary">${category.file_count}</span>
                </div>
                <div>
                    <small class="text-muted d-block">${translations.words || 'Words'}</small>
                    <span class="badge bg-info">${category.word_count}</span>
                </div>
            </div>
            <div class="btn-group w-100" role="group">
                <a href="/categories/${category.id}/words" class="btn btn-sm btn-outline-primary">
                    <i class="bi bi-eye"></i> ${translations.view || 'View'}
                </a>
                <button class="btn btn-sm btn-outline-success add-word-btn" 
                        data-category-id="${category.id}" 
                        data-category-name='${JSON.stringify(category.name)}'>
                    <i class="bi bi-plus"></i> ${translations.addWord || 'Add Word'}
                </button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteCategory(${category.id})" title="Delete">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        `;
        
        gridContainer.appendChild(card);
    });
}

// Render compact view
function renderCompactView(categories) {
    const compactContainer = document.getElementById('categoriesCompact');
    if (!compactContainer) return;
    
    compactContainer.innerHTML = '';
    
    categories.forEach(category => {
        const badge = document.createElement('span');
        badge.className = 'category-badge category-item';
        badge.setAttribute('data-name', category.name.toLowerCase());
        badge.setAttribute('data-category-id', category.id);
        badge.setAttribute('data-file-count', category.file_count);
        badge.setAttribute('data-word-count', category.word_count);
        
        badge.innerHTML = `
            <span class="badge bg-secondary">#${category.id}</span>
            <span class="category-name">${escapeHtml(category.name)}</span>
            <span class="badge bg-primary">${category.file_count}F</span>
            <span class="badge bg-info">${category.word_count}W</span>
            <a href="/categories/${category.id}/words" class="btn btn-sm btn-link p-0" title="${translations.view || 'View Words'}">
                <i class="bi bi-eye"></i>
            </a>
        `;
        
        compactContainer.appendChild(badge);
    });
}

// Change display format
function changeDisplayFormat() {
    const container = document.getElementById('categoriesDisplayContainer');
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
    const paginationContainer = document.getElementById('pagination');
    if (!paginationContainer) return;
    
    // Ensure container has an ID
    if (!paginationContainer.id) {
        paginationContainer.id = 'pagination';
    }
    
    const totalPages = Math.ceil(filteredCategories.length / itemsPerPage);
    const totalItems = filteredCategories.length;
    
    if (totalPages <= 1 && totalItems === 0) {
        paginationContainer.innerHTML = '';
        return;
    }
    
    // Use unified pagination
    import('../modules/rendering/unified-pagination.js').then(module => {
        module.renderUnifiedPagination({
            currentPage: currentPage,
            totalPages: totalPages,
            containerId: paginationContainer.id,
            onPageChange: (targetPage) => {
                changePage(targetPage);
            },
            urlParams: {},
            showInfo: true,
            showJump: totalPages > 5,
            baseUrl: window.location.pathname
        });
    }).catch(err => {
        console.error('Error loading unified pagination:', err);
        // Fallback to old pagination
        renderOldPagination();
    });
}

// Fallback old pagination renderer
function renderOldPagination() {
    const paginationContainer = document.getElementById('pagination');
    if (!paginationContainer) return;
    
    const totalPages = Math.ceil(filteredCategories.length / itemsPerPage);
    const totalItems = filteredCategories.length;
    const startItem = totalItems > 0 ? (currentPage - 1) * itemsPerPage + 1 : 0;
    const endItem = Math.min(currentPage * itemsPerPage, totalItems);
    
    if (totalPages <= 1 && totalItems === 0) {
        paginationContainer.innerHTML = '';
        return;
    }
    
    let html = '<div class="d-flex justify-content-between align-items-center w-100 flex-wrap gap-2">';
    html += `<div class="small text-muted pagination-info">${translations.showing || 'Showing'} ${startItem}-${endItem} ${translations.of || 'of'} ${totalItems}</div>`;
    html += '<div class="pagination-page-numbers d-flex align-items-center gap-1">';
    html += `<button class="pagination-btn" ${currentPage === 1 ? 'disabled' : ''} onclick="changePage(${currentPage - 1})"><i class="bi bi-chevron-left"></i></button>`;
    
    const maxVisible = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisible / 2));
    let endPage = Math.min(totalPages, startPage + maxVisible - 1);
    if (endPage - startPage < maxVisible - 1) {
        startPage = Math.max(1, endPage - maxVisible + 1);
    }
    
    if (startPage > 1) {
        html += `<button class="pagination-btn" onclick="changePage(1)">1</button>`;
        if (startPage > 2) html += `<span class="pagination-ellipsis">...</span>`;
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `<button class="pagination-btn ${i === currentPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
    }
    
    if (endPage < totalPages) {
        if (endPage < totalPages - 1) html += `<span class="pagination-ellipsis">...</span>`;
        html += `<button class="pagination-btn" onclick="changePage(${totalPages})">${totalPages}</button>`;
    }
    
    html += `<button class="pagination-btn" ${currentPage === totalPages || totalPages === 0 ? 'disabled' : ''} onclick="changePage(${currentPage + 1})"><i class="bi bi-chevron-right"></i></button>`;
    html += '</div></div>';
    paginationContainer.innerHTML = html;
}

// Change page
function changePage(page) {
    const totalPages = Math.ceil(filteredCategories.length / itemsPerPage);
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    renderCategories();
    renderPagination();
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
    const searchInput = document.getElementById('categorySearch');
    if (searchInput) {
        searchInput.value = '';
        applyFiltersAndRender();
    }
}

// Clear all filters
function clearAllFilters() {
    document.getElementById('categorySearch').value = '';
    document.getElementById('sortBy').value = 'name-asc';
    document.getElementById('displayFormat').value = 'grid';
    document.getElementById('itemsPerPage').value = '10';
    
    currentPage = 1;
    itemsPerPage = 10;
    currentSort = 'name-asc';
    currentFormat = 'grid';
    sortColumn = 'name';
    sortDirection = 'asc';
    
    changeDisplayFormat();
}

// Export categories
function exportCategories() {
    const headers = ['ID', 'Name', 'Files', 'Words'];
    const rows = filteredCategories.map(cat => [cat.id, cat.name, cat.file_count, cat.word_count]);
    
    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
    ].join('\n');
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    
    link.setAttribute('href', url);
    link.setAttribute('download', `categories-${Date.now()}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
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

// Open category modal
function openCategoryModal(categoryId = null) {
    if (typeof bootstrap === 'undefined') {
        console.error('Bootstrap not loaded yet');
        setTimeout(() => openCategoryModal(categoryId), 100);
        return;
    }
    const modalElement = document.getElementById('categoryModal');
    if (!modalElement) {
        console.error('Category modal not found');
        return;
    }
    const modal = new bootstrap.Modal(modalElement);
    const form = document.getElementById('categoryForm');
    const title = document.getElementById('categoryModalLabel');
    const categoryIdInput = document.getElementById('categoryId');
    const categoryNameInput = document.getElementById('categoryName');
    
    if (categoryId) {
        title.textContent = translations.editCategory || 'Edit Category';
        categoryIdInput.value = categoryId;
        // Load category data
        fetch(`/api/categories/${categoryId}`)
            .then(response => response.json())
            .then(data => {
                if (data && data.name) {
                    categoryNameInput.value = data.name;
                }
            })
            .catch(error => {
                console.error('Error loading category:', error);
            });
    } else {
        title.textContent = translations.addCategory || 'Add Category';
        categoryIdInput.value = '';
        categoryNameInput.value = '';
    }
    
    form.reset();
    modal.show();
}

// Save category
async function saveCategory() {
    const form = document.getElementById('categoryForm');
    const formData = new FormData(form);
    const categoryId = formData.get('category_id');
    const categoryName = formData.get('category_name').trim();
    
    if (!categoryName) {
        showToast('Category name is required', 'error');
        return;
    }
    
    // Check for duplicate before submitting
    try {
        let csrfToken = getCSRFToken();
        if (!csrfToken) {
            csrfToken = await getCSRFTokenAsync();
        }
        
        const checkResponse = await fetch('/api/category/check', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                category_name: categoryName
            })
        });
        
        const checkData = await checkResponse.json();
        if (checkData.exists) {
            showToast(checkData.message || `Category "${categoryName}" already exists`, 'error');
            return;
        }
    } catch (error) {
        console.warn('Error checking category duplicate:', error);
        // Continue with submission if check fails (backend will catch it)
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
    
    const url = '/category/add';
    const method = 'POST';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            category_name: categoryName,
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.categoryAdded || 'Category added successfully', 'success');
            const modalElement = document.getElementById('categoryModal');
            if (modalElement && typeof bootstrap !== 'undefined') {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) modal.hide();
            }
            // Add new category to the list immediately
            if (data.category_id) {
                allCategories.push({
                    id: data.category_id,
                    name: categoryName,
                    file_count: 0,
                    word_count: 0
                });
                applyFiltersAndRender();
            } else {
                // Fallback to page reload if no ID returned
                setTimeout(() => {
                    window.location.reload();
                }, 500);
            }
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error saving category:', error);
        showToast('Error saving category: ' + error.message, 'error');
    });
}

// View words in category
function viewCategoryWords(categoryId, categoryName) {
    window.location.href = `/categories/${categoryId}/words`;
}

// Add word to category
function addWordToCategory(categoryId, categoryName) {
    const modalElement = document.getElementById('addWordToCategoryModal');
    if (!modalElement) {
        console.error('Add word modal not found');
        return;
    }
    
    const modal = new bootstrap.Modal(modalElement);
    const title = document.getElementById('addWordToCategoryModalLabel');
    const categoryIdInput = document.getElementById('targetCategoryId');
    const wordSelect = document.getElementById('wordSelect');
    
    if (!title || !categoryIdInput || !wordSelect) {
        console.error('Required modal elements not found');
        return;
    }
    
    // Handle JSON-encoded category name
    let displayName = categoryName;
    try {
        if (typeof categoryName === 'string' && (categoryName.startsWith('"') || categoryName.startsWith("'"))) {
            displayName = JSON.parse(categoryName);
        }
    } catch (e) {
        displayName = categoryName;
    }
    
    title.textContent = `${translations.addWordToCategory || 'Add Word to Category'}: ${escapeHtml(displayName)}`;
    categoryIdInput.value = categoryId;
    
    // Show modal - Bootstrap will handle aria-hidden automatically
    modal.show();
    
    // Initialize Select2 for word selection - wait for libraries to be ready
    function initializeSelect2() {
        if (typeof jQuery === 'undefined' || typeof jQuery.fn === 'undefined') {
            // jQuery not ready yet, try again
            setTimeout(initializeSelect2, 50);
            return;
        }
        
        if (typeof jQuery.fn.select2 === 'undefined') {
            // Select2 not loaded yet, try again
            setTimeout(initializeSelect2, 50);
            return;
        }
        
        // Destroy existing Select2 if it exists
        if ($(wordSelect).hasClass('select2-hidden-accessible')) {
            $(wordSelect).select2('destroy');
        }
        
        // Wait a bit for modal to be fully visible
        setTimeout(function() {
            $(wordSelect).select2({
                placeholder: translations.searchWord || 'Search for a word or type to create new...',
                allowClear: true,
                width: '100%',
                minimumInputLength: 0,
                dropdownParent: $(modalElement),
                tags: true, // Allow creating new tags/words
                createTag: function (params) {
                    const term = params.term.trim();
                    if (term === '') {
                        return null;
                    }
                    // Don't create tag if it matches an existing option
                    if (params.term.match(/^\d+$/)) {
                        return null; // Don't allow pure numbers as new words
                    }
                    return {
                        id: 'new:' + term,
                        text: term + ' (new)',
                        isNew: true
                    };
                },
                ajax: {
                    url: '/api/words/search',
                    dataType: 'json',
                    delay: 300,
                    data: function (params) {
                        const categoryIdInput = document.getElementById('targetCategoryId');
                        const categoryId = categoryIdInput ? parseInt(categoryIdInput.value) : null;
                        const requestData = {
                            q: params.term || '',
                            page: params.page || 1,
                            per_page: 20
                        };
                        // Exclude words already in this category
                        if (categoryId) {
                            requestData.exclude_category_id = categoryId;
                        }
                        return requestData;
                    },
                    processResults: function (data, params) {
                        params.page = params.page || 1;
                        
                        const results = (data.results || []).map(function(item) {
                            const wordId = item.id || item.word_id;
                            const wordText = item.text || item.word || String(wordId || '');
                            
                            return {
                                id: wordId,
                                text: wordText,
                                usage_count: item.usage_count || 0
                            };
                        });
                        
                        return {
                            results: results,
                            pagination: {
                                more: (params.page * 20) < (data.pagination?.total || 0)
                            }
                        };
                    },
                    cache: true
                },
                templateResult: function (data) {
                    if (data.loading) {
                        return data.text || 'Searching...';
                    }
                    
                    if (data.isNew) {
                        const $result = $('<span><i class="bi bi-plus-circle me-1"></i>' + escapeHtml(data.text.replace(' (new)', '')) + ' <small class="text-muted">(create new)</small></span>');
                        return $result;
                    }
                    
                    const $result = $('<span>' + escapeHtml(data.text) + '</span>');
                    if (data.usage_count && data.usage_count > 0) {
                        $result.append(' <small class="text-muted">(' + data.usage_count + ' files)</small>');
                    }
                    return $result;
                },
                templateSelection: function (data) {
                    if (typeof data === 'string') {
                        return data;
                    }
                    if (data && data.text) {
                        return data.text.replace(' (new)', '');
                    }
                    if (data && data.id) {
                        return data.id.toString().startsWith('new:') ? data.id.replace('new:', '') : data.id;
                    }
                    return data || '';
                },
                escapeMarkup: function (markup) {
                    return markup;
                }
            });
            
            // Clear selection
            $(wordSelect).val(null).trigger('change');
        }, 200);
    }
    
    // Start initialization
    initializeSelect2();
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
    const wordSelect = document.getElementById('wordSelect');
    
    if (!categoryIdInput || !wordSelect) {
        showToast('Form elements not found', 'error');
        return;
    }
    
    const categoryId = categoryIdInput.value;
    
    // Get word ID or new word text - handle both Select2 and regular select
    let wordIdOrText;
    if (typeof jQuery !== 'undefined' && $(wordSelect).hasClass('select2-hidden-accessible')) {
        wordIdOrText = $(wordSelect).val();
    } else {
        wordIdOrText = wordSelect.value;
    }
    
    if (!wordIdOrText) {
        showToast('Please select or enter a word', 'warning');
        return;
    }
    
    // Check if this is a new word (starts with "new:")
    let wordId;
    if (typeof wordIdOrText === 'string' && wordIdOrText.startsWith('new:')) {
        // This is a new word, create it first
        const wordText = wordIdOrText.replace('new:', '');
        try {
            showToast('Creating new word...', 'info');
            wordId = await createNewWord(wordText);
            showToast('Word created successfully', 'success');
        } catch (error) {
            console.error('Error creating word:', error);
            showToast('Error creating word: ' + error.message, 'error');
            return;
        }
    } else {
        // Existing word, use the ID
        wordId = parseInt(wordIdOrText);
        if (isNaN(wordId)) {
            showToast('Invalid word selection', 'error');
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
            word_id: parseInt(wordId),
            category_id: parseInt(categoryId),
            csrf_token: csrfToken
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.wordAdded || 'Word added to category successfully', 'success');
            const modalElement = document.getElementById('addWordToCategoryModal');
            if (modalElement) {
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) {
                    // Clear Select2 before closing
                    const wordSelect = document.getElementById('wordSelect');
                    if (wordSelect && typeof jQuery !== 'undefined' && $(wordSelect).hasClass('select2-hidden-accessible')) {
                        $(wordSelect).val(null).trigger('change');
                    }
                    modal.hide();
                }
            }
            // Refresh the page to update counts
            setTimeout(() => {
                window.location.reload();
            }, 500);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error adding word to category:', error);
        showToast('Error adding word to category: ' + error.message, 'error');
    });
}

// Delete category
async function deleteCategory(categoryId, categoryName) {
    // CAT-UI-01: resolve the display name from the row/card when the caller
    // (inline onclick) can only pass the id — avoids quoting category names
    // into HTML attributes.
    if (!categoryName) {
        categoryName = document.querySelector(`[data-category-id="${categoryId}"]`)?.dataset.name || '';
    }
    if (!confirm(translations.confirmDelete || `Are you sure you want to delete the category "${categoryName}"?`)) {
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
    
    fetch(`/api/categories/${categoryId}`, {
        method: 'DELETE',
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(data.message || translations.categoryDeleted || 'Category deleted successfully', 'success');
            setTimeout(() => {
                window.location.reload();
            }, 500);
        } else {
            showToast(data.error || translations.error || 'Error', 'error');
        }
    })
    .catch(error => {
        console.error('Error deleting category:', error);
        showToast('Error deleting category: ' + error.message, 'error');
    });
}

// Helper function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Find duplicate categories
async function findDuplicates() {
    const modal = new bootstrap.Modal(document.getElementById('duplicatesModal'));
    const loadingEl = document.getElementById('duplicatesLoading');
    const contentEl = document.getElementById('duplicatesContent');
    const emptyEl = document.getElementById('duplicatesEmpty');
    const removeBtn = document.getElementById('removeDuplicatesBtn');
    const summaryEl = document.getElementById('duplicatesSummary');
    const listEl = document.getElementById('duplicatesList');
    
    // Show modal and loading state
    modal.show();
    loadingEl.style.display = 'block';
    contentEl.style.display = 'none';
    emptyEl.style.display = 'none';
    removeBtn.style.display = 'none';
    
    try {
        const response = await fetch('/api/categories/find-duplicates');
        const data = await response.json();
        
        loadingEl.style.display = 'none';
        
        if (!data.success) {
            showToast(data.error || translations.errorFindingDuplicates || 'Error finding duplicates', 'error');
            return;
        }
        
        if (data.total_duplicates === 0) {
            emptyEl.style.display = 'block';
            removeBtn.style.display = 'none';
            return;
        }
        
        // Show duplicates
        contentEl.style.display = 'block';
        removeBtn.style.display = 'block';
        
        // Update summary
        summaryEl.innerHTML = `
            <strong>${translations.totalDuplicates || 'Total Duplicates'}:</strong> ${data.total_duplicates} ${translations.duplicateGroup || 'groups'}<br>
            <strong>${translations.totalToRemove || 'Total to Remove'}:</strong> ${data.total_to_remove} ${translations.categories || 'categories'}
        `;
        
        // Build duplicates list
        listEl.innerHTML = '';
        data.duplicates.forEach((dup, index) => {
            const item = document.createElement('div');
            item.className = 'list-group-item';
            item.innerHTML = `
                <div class="d-flex justify-content-between align-items-start">
                    <div class="flex-grow-1">
                        <h6 class="mb-2">
                            <span class="badge bg-warning me-2">${index + 1}</span>
                            <strong>${escapeHtml(dup.name)}</strong>
                            <span class="badge bg-secondary ms-2">${dup.count} ${translations.categories || 'categories'}</span>
                        </h6>
                        <div class="mt-2">
                            <small class="text-muted">
                                <strong>${translations.keep || 'Keep'}:</strong> ID ${dup.keep_id}<br>
                                <strong>${translations.remove || 'Remove'}:</strong> IDs ${dup.remove_ids.join(', ')}
                            </small>
                        </div>
                    </div>
                </div>
            `;
            listEl.appendChild(item);
        });
        
    } catch (error) {
        console.error('Error finding duplicates:', error);
        loadingEl.style.display = 'none';
        showToast(translations.errorFindingDuplicates || 'Error finding duplicates', 'error');
    }
}

// Remove duplicate categories
async function removeDuplicates() {
    if (!confirm(translations.confirmRemoveDuplicates || 'Are you sure you want to remove all duplicate categories? This action cannot be undone.')) {
        return;
    }
    
    const removeBtn = document.getElementById('removeDuplicatesBtn');
    const originalText = removeBtn.innerHTML;
    removeBtn.disabled = true;
    removeBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2"></span>${translations.removing || 'Removing...'}`;
    
    try {
        const response = await fetch('/api/categories/remove-duplicates', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        });
        
        const data = await response.json();
        
        removeBtn.disabled = false;
        removeBtn.innerHTML = originalText;
        
        if (!data.success) {
            showToast(data.error || translations.errorRemovingDuplicates || 'Error removing duplicates', 'error');
            return;
        }
        
        let message = data.message || `${translations.duplicatesRemoved || 'Duplicates removed successfully'}: ${data.removed} ${translations.categories || 'categories'}`;
        if (data.words_merged && data.words_merged > 0) {
            message += ` (${data.words_merged} ${translations.words || 'words'} ${translations.merged || 'merged'})`;
        }
        showToast(message, 'success');
        
        // Close modal and reload page
        const modal = bootstrap.Modal.getInstance(document.getElementById('duplicatesModal'));
        if (modal) {
            modal.hide();
        }
        
        setTimeout(() => {
            window.location.reload();
        }, 1000);
        
    } catch (error) {
        console.error('Error removing duplicates:', error);
        removeBtn.disabled = false;
        removeBtn.innerHTML = originalText;
        showToast(translations.errorRemovingDuplicates || 'Error removing duplicates', 'error');
    }
}

// Make functions available globally
window.openCategoryModal = openCategoryModal;
window.viewCategoryWords = viewCategoryWords;
window.addWordToCategory = addWordToCategory;
window.saveCategory = saveCategory;
window.saveWordToCategory = saveWordToCategory;
window.deleteCategory = deleteCategory;
window.changePage = changePage;
window.changePageSize = changePageSize;
window.sortByColumn = sortByColumn;
window.clearSearch = clearSearch;
window.clearAllFilters = clearAllFilters;
window.exportCategories = exportCategories;
window.applyFilters = applyFiltersAndRender;
window.changeDisplayFormat = changeDisplayFormat;
window.findDuplicates = findDuplicates;
window.removeDuplicates = removeDuplicates;
