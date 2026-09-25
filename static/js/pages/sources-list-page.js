/**
 * Sources List Page JavaScript
 * Extracted from Sources/sources_list.html
 */

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('sources-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing sources list page data:', e);
        }
    }
    
    console.log('Sources list page loaded');
});


// Toast notification helper function
function showToast(message, type = 'info', duration = 4000) {
    const toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        const container = document.createElement('div');
        container.id = 'toastContainer';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        container.style.zIndex = '9999';
        document.body.appendChild(container);
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
        <div id="${toastId}" class="toast align-items-center text-white bg-${bgColors[type]} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="bi bi-${icons[type]} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    
    document.getElementById('toastContainer').insertAdjacentHTML('beforeend', toastHtml);
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement, { delay: duration });
    toast.show();
    
    toastElement.addEventListener('hidden.bs.toast', () => {
        toastElement.remove();
    });
}
// Global state for filtering and sorting
let allSources = [];
let filteredSources = [];
let currentPage = 1;
let itemsPerPage = 50;
let currentSort = 'importance-desc';
let currentFormat = 'grid';

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

// Initialize sources data from DOM
function initializeSourcesData() {
    const sourceItems = document.querySelectorAll('.source-card-item');
    allSources = [];
    
    sourceItems.forEach(item => {
        allSources.push({
            id: item.getAttribute('data-id'),
            name: item.getAttribute('data-name'),
            importance: parseFloat(item.getAttribute('data-importance')) || 0,
            docCount: parseInt(item.getAttribute('data-doc-count')) || 0,
            country: item.getAttribute('data-country') || '',
            city: item.getAttribute('data-city') || '',
            ownership: item.getAttribute('data-ownership') || '',
            accessStatus: item.getAttribute('data-access-status') || '',
            element: item
        });
    });
    
    filteredSources = [...allSources];
    updateStats();
}

// Update statistics
function updateStats() {
    const totalCount = allSources.length;
    const filteredCount = filteredSources.length;
    const visibleCount = document.querySelectorAll('.source-card-item:not([style*="display: none"])').length;
    
    const totalEl = document.getElementById('totalSourcesCount');
    const filteredEl = document.getElementById('filteredSourcesCount');
    const visibleEl = document.getElementById('visibleSourcesCount');
    
    if (totalEl) totalEl.textContent = totalCount;
    if (filteredEl) filteredEl.textContent = filteredCount;
    if (visibleEl) visibleEl.textContent = visibleCount;
}

// Apply filters and sorting
function applyFilters() {
    const searchInput = document.getElementById('sourceSearch');
    const sortBy = document.getElementById('sortBy');
    
    const searchTerm = (searchInput?.value || '').toLowerCase();
    currentSort = sortBy?.value || 'importance-desc';
    
    // Filter
    filteredSources = allSources.filter(source => {
        if (searchTerm && !source.name.includes(searchTerm)) {
            return false;
        }
        return true;
    });
    
    // Sort
    const [sortField, sortDirection] = currentSort.split('-');
    filteredSources.sort((a, b) => {
        let aVal, bVal;
        
        switch(sortField) {
            case 'name':
                aVal = a.name || '';
                bVal = b.name || '';
                break;
            case 'importance':
                aVal = a.importance || 0;
                bVal = b.importance || 0;
                break;
            case 'id':
                aVal = parseInt(a.id) || 0;
                bVal = parseInt(b.id) || 0;
                break;
            case 'doc_count':
                aVal = a.docCount || 0;
                bVal = b.docCount || 0;
                break;
            default:
                aVal = a.name || '';
                bVal = b.name || '';
        }
        
        if (sortDirection === 'asc') {
            return aVal > bVal ? 1 : aVal < bVal ? -1 : 0;
        } else {
            return aVal < bVal ? 1 : aVal > bVal ? -1 : 0;
        }
    });
    
    // Show/hide cards
    filteredSources.forEach((source, index) => {
        if (source.element) {
            source.element.style.display = '';
            source.element.style.order = index;
        }
    });
    
    // Hide non-matching cards
    allSources.forEach(source => {
        if (!filteredSources.includes(source) && source.element) {
            source.element.style.display = 'none';
        }
    });
    
    updateStats();
}

// Change display format
function changeDisplayFormat() {
    const formatSelect = document.getElementById('displayFormat');
    if (!formatSelect) return;
    
    currentFormat = formatSelect.value;
    const container = document.getElementById('sourcesCardContainer');
    if (!container) return;
    
    // For now, grid is the default - table/list views would require template changes
    // This is a placeholder for future implementation
    console.log('Display format changed to:', currentFormat);
}

// Change page size
function changePageSize() {
    const itemsPerPageSelect = document.getElementById('itemsPerPage');
    if (!itemsPerPageSelect) return;
    
    const newLimit = parseInt(itemsPerPageSelect.value) || 50;
    itemsPerPage = newLimit;
    
    // Update URL with new limit parameter and reset to first page
    const url = new URL(window.location.href);
    url.searchParams.set('limit', newLimit);
    url.searchParams.delete('cursor'); // Reset to first page
    url.searchParams.delete('page');
    window.location.href = url.toString();
}

// Selection management
function selectAll() {
    const checkboxes = document.querySelectorAll('.source-checkbox');
    checkboxes.forEach(cb => {
        if (cb.closest('.source-card-item') && !cb.closest('.source-card-item').style.display.includes('none')) {
            cb.checked = true;
        }
    });
    updateBulkButtons();
}

function selectNone() {
    const checkboxes = document.querySelectorAll('.source-checkbox');
    checkboxes.forEach(cb => cb.checked = false);
    updateBulkButtons();
}

// The toolbar owns how the scope is drawn and announced; this page owns the
// numbers, because they are what the reader can see: how many rows are on
// screen right now, and how many of them are chosen. The words come from the
// bar (the component renders them through the catalogs), so no English is
// assembled here. One call, and the toolbar decides the rest - no action is
// named, and nothing is bound.
function updateBulkButtons() {
    if (!window.ActionToolbar) return;
    window.ActionToolbar.sync('sourcesActionBar', {
        selected: document.querySelectorAll('.source-checkbox:checked').length,
        total: document.querySelectorAll('.source-checkbox').length
    });
}

// Bulk operations
function bulkExport() {
    const checkboxes = document.querySelectorAll('.source-checkbox:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedIds.length === 0) {
        showToast('Please select sources to export', 'warning');
        return;
    }
    
    // Export functionality - would need backend endpoint
    showToast(`Exporting ${selectedIds.length} source(s)...`, 'info');
    console.log('Bulk export:', selectedIds);
}

function bulkUpdate() {
    const checkboxes = document.querySelectorAll('.source-checkbox:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedIds.length === 0) {
        showToast('Please select sources to update', 'warning');
        return;
    }
    
    // Bulk update functionality - would need backend endpoint
    showToast(`Updating ${selectedIds.length} source(s)...`, 'info');
    console.log('Bulk update:', selectedIds);
}

// Clear search
function clearSearch() {
    const searchInput = document.getElementById('sourceSearch');
    if (searchInput) {
        searchInput.value = '';
        applyFilters();
    }
}

// View source categories and keywords
function viewSourceCategoriesKeywords(sourceId) {
    window.location.href = `/sources/${sourceId}/categories-keywords`;
}

// View source details
function viewSource(sourceId) {
    window.location.href = `/sources/${sourceId}`;
}

// Edit source
function editSource(sourceId) {
    openSourceModal(sourceId);
}

// Initialize event listeners
function initializeEventListeners() {
    const searchInput = document.getElementById('sourceSearch');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(() => {
            applyFilters();
        }, 300));
    }
    
    const sortBy = document.getElementById('sortBy');
    if (sortBy) {
        sortBy.addEventListener('change', () => {
            applyFilters();
        });
    }
    
    const displayFormat = document.getElementById('displayFormat');
    if (displayFormat) {
        displayFormat.addEventListener('change', () => {
            changeDisplayFormat();
        });
    }
    
    const itemsPerPageSelect = document.getElementById('itemsPerPage');
    if (itemsPerPageSelect) {
        itemsPerPageSelect.addEventListener('change', () => {
            changePageSize();
        });
    }
    
    // No checkbox listener is registered here. Each row's own `onchange`
    // calls `updateBulkButtons()`, and a second, document-wide listener for
    // the same event would update the toolbar twice per click for no gain.
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', function() {
    initializeSourcesData();
    initializeEventListeners();
    applyFilters();
});

// Make functions globally accessible IMMEDIATELY (before module loads)
// This ensures buttons work even if module hasn't finished loading
window.selectAll = selectAll;
window.selectNone = selectNone;
window.updateBulkButtons = updateBulkButtons;
window.bulkExport = bulkExport;
window.bulkUpdate = bulkUpdate;
window.clearSearch = clearSearch;
window.applyFilters = applyFilters;
window.changeDisplayFormat = changeDisplayFormat;
window.changePageSize = changePageSize;
window.viewSourceCategoriesKeywords = viewSourceCategoriesKeywords;
window.viewSource = viewSource;
window.editSource = editSource;



// ✅ SECURITY: Helper function to get CSRF token
function getCSRFToken() {
    const metaTag = document.querySelector('meta[name="csrf-token"]');
    return metaTag ? metaTag.getAttribute('content') : '';
}

function duplicateSource(sourceId) {
    if (confirm(translations.createCopyOfSource)) {
        fetch(`/api/sources/${sourceId}/duplicate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(translations.sourceDuplicatedSuccessfully || 'Source duplicated successfully!', 'success');
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 500);
            } else {
                showToast((translations.errorDuplicatingSource || 'Error duplicating source') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast(translations.errorDuplicatingSource || 'Error duplicating source', 'error');
        });
    }
}

function toggleSourceStatus(sourceId) {
    fetch(`/api/sources/${sourceId}/toggle-status`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(translations.sourceStatusUpdated || 'Source status updated!', 'success');
            setTimeout(() => {
                window.location.href = window.location.pathname + '?t=' + Date.now();
            }, 500);
        } else {
            showToast((translations.errorUpdatingStatus || 'Error updating status') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast(translations.errorUpdatingSourceStatus || 'Error updating source status', 'error');
    });
}

function exportSource(sourceId) {
    fetch(`/api/sources/${sourceId}/export`, {
        method: 'GET',
    })
    .then(response => {
        if (response.ok) {
            return response.blob();
        }
        throw new Error('Export failed');
    })
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `source_${sourceId}_export.json`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        showToast(translations.sourceDataExportedSuccessfully || 'Source data exported successfully!', 'success');
    })
    .catch(error => {
        console.error('Error:', error);
        showToast(translations.errorExportingSourceData || 'Error exporting source data', 'error');
    });
}

function deleteSource(sourceId, sourceName = '') {
    const confirmMessage = sourceName 
        ? `${translations.areYouSureDeleteSource || 'Are you sure you want to delete the source'} "${sourceName}"?\n\n${translations.actionCannotBeUndone || 'This action cannot be undone.'}`
        : translations.deleteSourceConfirm;
    
    // Show confirmation modal
    const modal = new bootstrap.Modal(document.getElementById('deleteConfirmModal'));
    const messageEl = document.getElementById('deleteConfirmMessage');
    const confirmBtn = document.getElementById('deleteConfirmButton');
    
    messageEl.textContent = confirmMessage;
    
    // Remove any existing event listeners
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
    
    // Add click handler for confirmation
    newConfirmBtn.addEventListener('click', function performDelete() {
        modal.hide();
        
        // Show processing notification
        if (window.MessageFormatter) {
            window.MessageFormatter.showNotification('delete', 'processing', { item: sourceName || 'Source' });
        }
        
        fetch(`/api/sources/${sourceId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken()
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Show formatted success notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteSuccess(sourceName || 'Source', {
                        title: translations.sourceDeleted || 'Source Deleted',
                        duration: 4000
                    });
                } else {
                    showToast(translations.sourceDeletedSuccessfully || 'Source deleted successfully!', 'success');
                }
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 500);
            } else {
                // Show formatted error notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteError(sourceName || 'Source', {
                        title: translations.deleteFailed || 'Delete Failed',
                        duration: 6000
                    });
                } else {
                    showToast((translations.errorDeletingSource || 'Error deleting source') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error:', error);
            if (window.MessageFormatter) {
                window.MessageFormatter.showDeleteError(sourceName || 'Source', {
                    title: translations.deleteError || 'Delete Error',
                    duration: 6000
                });
            } else {
                showToast(translations.errorDeletingSource || 'Error deleting source', 'error');
            }
        });
    });
    
    modal.show();
}

// Modal functions
let categories = [];

function openSourceModal(sourceId = null) {
    const modal = new bootstrap.Modal(document.getElementById('sourceModal'));
    const form = document.getElementById('sourceForm');
    const modalTitle = document.getElementById('modalTitle');
    const submitButtonText = document.getElementById('submitButtonText');
    
    // Reset form
    form.reset();
    document.getElementById('sourceId').value = '';
    document.getElementById('importanceSlider').value = '0.5';
    document.getElementById('importanceValue').textContent = '0.50';
    
    if (sourceId) {
        // Edit mode
        modalTitle.textContent = translations.editSource || 'Edit Source';
        submitButtonText.textContent = translations.updateSource || 'Update Source';
        document.getElementById('modalIcon').className = 'bi bi-pencil me-2';
        document.getElementById('sourceId').value = sourceId;
        
        // Load source data
        fetch(`/api/sources/${sourceId}`)
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => {
                        throw new Error(err.error || `HTTP error! status: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.success && data.source) {
                    const source = data.source;
                    document.getElementById('sourceName').value = source.name || '';
                    document.getElementById('sourceJob').value = source.job || '';
                    document.getElementById('sourceCountry').value = source.country || '';
                    document.getElementById('sourceCity').value = source.city || '';
                    document.getElementById('sourceDescription').value = source.description || '';
                    document.getElementById('sourceAccounts').value = source.accounts || '';
                    document.getElementById('sourceAttachments').value = source.attachments || '';
                    document.getElementById('sourceNote').value = source.note || '';
                    
                    // Set enum/optional fields - handle null values properly
                    const ownershipValue = source.ownership ? source.ownership : '';
                    document.getElementById('sourceOwnership').value = ownershipValue;
                    
                    const accessStatusValue = source.access_status ? source.access_status : '';
                    document.getElementById('sourceAccessStatus').value = accessStatusValue;
                    
                    // Category needs to be set after categories are loaded
                    const categoryId = source.category_id ? source.category_id.toString() : '';
                    // Store category ID to set after categories load
                    if (categories.length > 0) {
                        populateCategoryDropdown(categoryId);
                    } else {
                        // If categories not loaded yet, wait for them to load
                        const checkCategories = setInterval(() => {
                            if (categories.length > 0) {
                                clearInterval(checkCategories);
                                populateCategoryDropdown(categoryId);
                            }
                        }, 100);
                        // Timeout after 2 seconds
                        setTimeout(() => clearInterval(checkCategories), 2000);
                    }
                    
                    // Date field - handle null/empty values
                    if (source.date_source_discovery) {
                        try {
                            const date = new Date(source.date_source_discovery);
                            if (!isNaN(date.getTime())) {
                                document.getElementById('sourceDateDiscovery').value = date.toISOString().split('T')[0];
                            } else {
                                document.getElementById('sourceDateDiscovery').value = '';
                            }
                        } catch (e) {
                            console.warn('Error parsing date:', e);
                            document.getElementById('sourceDateDiscovery').value = '';
                        }
                    } else {
                        document.getElementById('sourceDateDiscovery').value = '';
                    }
                    
                    const importance = source.importance || 0.5;
                    document.getElementById('importanceSlider').value = importance;
                    document.getElementById('importanceValue').textContent = parseFloat(importance).toFixed(2);
                } else {
                    showToast((translations.errorLoadingSource || 'Error loading source') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                    modal.hide();
                }
            })
            .catch(error => {
                console.error('Error loading source data:', error);
                showToast(translations.errorLoadingSourceData || 'Error loading source data: ' + error.message, 'error');
                modal.hide();
            });
    } else {
        // Add mode
        modalTitle.textContent = translations.addNewSource || 'Add New Source';
        submitButtonText.textContent = translations.createSource || 'Create Source';
        document.getElementById('modalIcon').className = 'bi bi-plus-circle me-2';
    }
    
    // Load categories if not already loaded
    if (categories.length === 0) {
        loadCategories();
    } else {
        populateCategoryDropdown();
    }
    
    modal.show();
}

function loadCategories() {
    fetch('/api/categories')
        .then(response => response.json())
        .then(data => {
            categories = Array.isArray(data) ? data : [];
            populateCategoryDropdown();
        })
        .catch(error => {
            console.error('Error loading categories:', error);
            categories = [];
        });
}

function populateCategoryDropdown(preserveValue = null) {
    const categorySelect = document.getElementById('sourceCategory');
    const currentValue = preserveValue !== null ? preserveValue : categorySelect.value;
    
    // Clear existing options except the first one
    categorySelect.innerHTML = `<option value="">-- ${translations.selectCategory || 'Select Category'} --</option>`;
    
    categories.forEach(cat => {
        const option = document.createElement('option');
        option.value = cat.id;
        option.textContent = cat.name;
        categorySelect.appendChild(option);
    });
    
    // Restore previous selection if provided or if in edit mode
    if (currentValue) {
        categorySelect.value = currentValue;
    }
}

function submitSourceForm() {
    const form = document.getElementById('sourceForm');
    const sourceId = document.getElementById('sourceId').value;
    const formData = new FormData(form);
    
    // Validate required fields
    if (!formData.get('name') || !formData.get('job') || !formData.get('country')) {
        showToast(translations.pleaseFillInAllRequiredFields || 'Please fill in all required fields (Name, Job/Type, Country)', 'warning')
        return;
    }
    
    // Convert FormData to JSON - ensure ALL fields are included
    // Empty strings for text fields will clear them, null for enum/optional fields will clear them
    const data = {
        name: formData.get('name') || '',
        job: formData.get('job') || '',
        country: formData.get('country') || '',
        city: formData.get('city') || '',  // Empty string clears the field
        importance: parseFloat(formData.get('importance')) || 0.5,
        description: formData.get('description') || '',  // Empty string clears the field
        accounts: formData.get('accounts') || '',  // Empty string clears the field
        note: formData.get('note') || '',  // Empty string clears the field
        attachments: formData.get('attachments') || '',  // Empty string clears the field
        // Convert empty strings to null for optional/enum fields (NULL clears them)
        ownership: (() => {
            const val = formData.get('ownership');
            return (val && val.trim()) ? val : null;
        })(),
        access_status: (() => {
            const val = formData.get('access_status');
            return (val && val.trim()) ? val : null;
        })(),
        date_source_discovery: (() => {
            const val = formData.get('date_source_discovery');
            return (val && val.trim()) ? val : null;
        })(),
        category_id: (() => {
            const val = formData.get('category_id');
            if (val && val.trim()) {
                const num = parseInt(val);
                return isNaN(num) ? null : num;
            }
            return null;
        })()
    };
    
    const url = sourceId ? `/api/sources/${sourceId}` : '/api/sources';
    const method = sourceId ? 'PUT' : 'POST';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
        },
        body: JSON.stringify(data)
    })
    .then(response => {
        console.log('Response status:', response.status);
        // Check if response is ok (status 200-299)
        if (!response.ok) {
            return response.json().then(err => {
                console.error('Error response:', err);
                throw new Error(err.error || `HTTP error! status: ${response.status}`);
            });
        }
        return response.json();
    })
    .then(result => {
        console.log('Response result:', result);
        // Get source name from form
        const sourceName = document.getElementById('sourceName')?.value || 'Source';
        
        // Check if result has success field (POST/PUT responses)
        if (result && result.success !== undefined) {
            if (result.success) {
                // Show formatted success notification
                const isEdit = sourceId ? true : false;
                if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                    if (isEdit) {
                        window.MessageFormatter.showUpdateSuccess(sourceName, {
                            title: 'Source Updated',
                            duration: 4000
                        });
                    } else {
                        window.MessageFormatter.showCreateSuccess(sourceName, {
                            title: 'Source Created',
                            duration: 4000
                        });
                    }
                } else {
                    showToast(
                        isEdit ? (translations.sourceUpdatedSuccessfully || 'Source updated successfully!') : (translations.sourceCreatedSuccessfully || 'Source created successfully!'),
                        'success'
                    );
                }
                
                // Close modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('sourceModal'));
                if (modal) {
                    modal.hide();
                }
                
                // Small delay to ensure modal closes before reload
                // Add cache-busting parameter to ensure fresh data
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 500);
            } else {
                // Show formatted error notification
                if (window.MessageFormatter && typeof window.MessageFormatter.showCreateError === 'function') {
                    const isEdit = sourceId ? true : false;
                    if (isEdit) {
                        window.MessageFormatter.showUpdateError(sourceName, {
                            title: 'Update Failed',
                            duration: 6000
                        });
                    } else {
                        window.MessageFormatter.showCreateError(sourceName, {
                            title: 'Create Failed',
                            duration: 6000
                        });
                    }
                } else {
                    showToast((translations.error || 'Error') + ': ' + (result.error || (translations.unknownError || 'Unknown error')), 'error');
                }
            }
        } else {
            // If no success field but response was ok, assume it worked
            console.log('No success field, but response was ok - assuming success');
            const isEdit = sourceId ? true : false;
            if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                if (isEdit) {
                    window.MessageFormatter.showUpdateSuccess(sourceName, {
                        title: 'Source Updated',
                        duration: 4000
                    });
                } else {
                    window.MessageFormatter.showCreateSuccess(sourceName, {
                        title: 'Source Created',
                        duration: 4000
                    });
                }
            } else {
                showToast(
                    isEdit ? (translations.sourceUpdatedSuccessfully || 'Source updated successfully!') : (translations.sourceCreatedSuccessfully || 'Source created successfully!'),
                    'success'
                );
            }
            const modal = bootstrap.Modal.getInstance(document.getElementById('sourceModal'));
            if (modal) {
                modal.hide();
            }
            setTimeout(() => {
                window.location.href = window.location.pathname + '?t=' + Date.now();
            }, 500);
        }
    })
    .catch(error => {
        console.error('Error saving source:', error);
        const sourceName = document.getElementById('sourceName')?.value || 'Source';
        const isEdit = sourceId ? true : false;
        if (window.MessageFormatter && typeof window.MessageFormatter.showCreateError === 'function') {
            if (isEdit) {
                window.MessageFormatter.showUpdateError(sourceName, {
                    title: 'Update Error',
                    duration: 6000
                });
            } else {
                window.MessageFormatter.showCreateError(sourceName, {
                    title: 'Create Error',
                    duration: 6000
                });
            }
        } else {
            showToast((translations.errorSavingSource || 'Error saving source') + ': ' + error.message, 'error');
        }
    });
}

// Make functions globally accessible for onclick handlers
// Export immediately (not in DOMContentLoaded) so they're available when buttons are clicked
// This IIFE runs as soon as the module loads, ensuring functions are available immediately
(function() {
    // Export all button handler functions to window object
    // This ensures they're available even if the module hasn't fully initialized
    window.viewSource = viewSource;
    window.editSource = editSource;
    window.duplicateSource = duplicateSource;
    window.toggleSourceStatus = toggleSourceStatus;
    window.exportSource = exportSource;
    window.deleteSource = deleteSource;
    window.openSourceModal = openSourceModal;
    window.submitSourceForm = submitSourceForm;
    window.viewSourceCategoriesKeywords = viewSourceCategoriesKeywords;
    
    // Remove retry wrapper flags if they exist
    Object.keys(window).forEach(key => {
        if (window[key] && window[key]._isRetryWrapper) {
            delete window[key]._isRetryWrapper;
        }
    });
})();

// Initialize importance slider
document.addEventListener('DOMContentLoaded', function() {
    const slider = document.getElementById('importanceSlider');
    const valueDisplay = document.getElementById('importanceValue');
    
    if (slider && valueDisplay) {
        slider.addEventListener('input', (e) => {
            valueDisplay.textContent = parseFloat(e.target.value).toFixed(2);
        });
    }
    
    // Check if we should open the modal in edit mode from URL parameter
    const urlParams = new URLSearchParams(window.location.search);
    const editId = urlParams.get('edit');
    if (editId) {
        // Remove the edit parameter from URL
        urlParams.delete('edit');
        const newUrl = window.location.pathname + (urlParams.toString() ? '?' + urlParams.toString() : '');
        window.history.replaceState({}, '', newUrl);
        
        // Open modal in edit mode
        openSourceModal(parseInt(editId));
    }
});

// Export default init function for universal-initializer
export default function init() {
    // The initialization is already handled in DOMContentLoaded above
    // This is just for compatibility with universal-initializer
    return Promise.resolve();
}