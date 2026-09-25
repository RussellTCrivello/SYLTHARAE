/**
 * Sides List Page JavaScript
 * Extracted from Side/sides_list.html
 */

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('sides-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Also make available on window for backward compatibility
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing sides list page data:', e);
        }
    }
    
    console.log('Sides list page loaded');
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
let allSides = [];
let filteredSides = [];
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

// Initialize sides data from DOM
function initializeSidesData() {
    const sideItems = document.querySelectorAll('.side-card-item');
    allSides = [];
    
    sideItems.forEach(item => {
        allSides.push({
            id: item.getAttribute('data-id'),
            name: item.getAttribute('data-name'),
            importance: parseFloat(item.getAttribute('data-importance')) || 0,
            docCount: parseInt(item.getAttribute('data-doc-count')) || 0,
            sourceCount: parseInt(item.getAttribute('data-source-count')) || 0,
            element: item
        });
    });
    
    filteredSides = [...allSides];
    updateStats();
}

// Update statistics
function updateStats() {
    const totalCount = allSides.length;
    const filteredCount = filteredSides.length;
    const visibleCount = document.querySelectorAll('.side-card-item:not([style*="display: none"])').length;
    
    const totalEl = document.getElementById('totalSidesCount');
    const filteredEl = document.getElementById('filteredSidesCount');
    const visibleEl = document.getElementById('visibleSidesCount');
    
    if (totalEl) totalEl.textContent = totalCount;
    if (filteredEl) filteredEl.textContent = filteredCount;
    if (visibleEl) visibleEl.textContent = visibleCount;
}

// Apply filters and sorting
function applyFilters() {
    const searchInput = document.getElementById('sideSearch');
    const sortBy = document.getElementById('sortBy');
    
    const searchTerm = (searchInput?.value || '').toLowerCase();
    currentSort = sortBy?.value || 'importance-desc';
    
    // Filter
    filteredSides = allSides.filter(side => {
        if (searchTerm && !side.name.includes(searchTerm)) {
            return false;
        }
        return true;
    });
    
    // Sort
    const [sortField, sortDirection] = currentSort.split('-');
    filteredSides.sort((a, b) => {
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
            case 'source_count':
                aVal = a.sourceCount || 0;
                bVal = b.sourceCount || 0;
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
    filteredSides.forEach((side, index) => {
        if (side.element) {
            side.element.style.display = '';
            side.element.style.order = index;
        }
    });
    
    // Hide non-matching cards
    allSides.forEach(side => {
        if (!filteredSides.includes(side) && side.element) {
            side.element.style.display = 'none';
        }
    });
    
    updateStats();
}

// Change display format
function changeDisplayFormat() {
    const formatSelect = document.getElementById('displayFormat');
    if (!formatSelect) return;
    
    currentFormat = formatSelect.value;
    const container = document.getElementById('sidesCardContainer');
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
    const checkboxes = document.querySelectorAll('.side-checkbox');
    checkboxes.forEach(cb => {
        if (cb.closest('.side-card-item') && !cb.closest('.side-card-item').style.display.includes('none')) {
            cb.checked = true;
        }
    });
    updateBulkButtons();
}

function selectNone() {
    const checkboxes = document.querySelectorAll('.side-checkbox');
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
    window.ActionToolbar.sync('sidesActionBar', {
        selected: document.querySelectorAll('.side-checkbox:checked').length,
        total: document.querySelectorAll('.side-checkbox').length
    });
}

// Bulk operations
function bulkExport() {
    const checkboxes = document.querySelectorAll('.side-checkbox:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedIds.length === 0) {
        showToast('Please select sides to export', 'warning');
        return;
    }
    
    // Export functionality - would need backend endpoint
    showToast(`Exporting ${selectedIds.length} side(s)...`, 'info');
    console.log('Bulk export:', selectedIds);
}

function bulkUpdate() {
    const checkboxes = document.querySelectorAll('.side-checkbox:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.value));
    
    if (selectedIds.length === 0) {
        showToast('Please select sides to update', 'warning');
        return;
    }
    
    // Bulk update functionality - would need backend endpoint
    showToast(`Updating ${selectedIds.length} side(s)...`, 'info');
    console.log('Bulk update:', selectedIds);
}

// Export single side
function exportSide(sideId) {
    // Export functionality - would need backend endpoint
    showToast('Exporting side data...', 'info');
    console.log('Export side:', sideId);
}

// Clear search
function clearSearch() {
    const searchInput = document.getElementById('sideSearch');
    if (searchInput) {
        searchInput.value = '';
        applyFilters();
    }
}

// View side categories and keywords
function viewSideCategoriesKeywords(sideId) {
    window.location.href = `/sides/${sideId}/categories-keywords`;
}

// Initialize event listeners
function initializeEventListeners() {
    const searchInput = document.getElementById('sideSearch');
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
    initializeSidesData();
    initializeEventListeners();
    applyFilters();
});

// Make functions globally accessible
window.selectAll = selectAll;
window.selectNone = selectNone;
window.updateBulkButtons = updateBulkButtons;
window.bulkExport = bulkExport;
window.bulkUpdate = bulkUpdate;
window.clearSearch = clearSearch;
window.applyFilters = applyFilters;
window.changeDisplayFormat = changeDisplayFormat;
window.changePageSize = changePageSize;
window.viewSideCategoriesKeywords = viewSideCategoriesKeywords;
window.exportSide = exportSide;

function viewSide(sideId) {
    window.location.href = `/sides/${sideId}`;
}

function editSide(sideId) {
    openSideModal(sideId);
}



function duplicateSide(sideId) {
    if (confirm(translations.createCopyOfSide)) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/sides/${sideId}/duplicate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showToast(translations.sideDuplicatedSuccessfully || 'Side duplicated successfully!', 'success');
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 500);
            } else {
                showToast((translations.errorDuplicatingSide || 'Error duplicating side') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showToast(translations.errorDuplicatingSide || 'Error duplicating side', 'error');
        });
    }
}

function toggleSideStatus(sideId) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    fetch(`/api/sides/${sideId}/toggle-status`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(translations.sideStatusUpdated || 'Side status updated!', 'success');
            setTimeout(() => {
                window.location.href = window.location.pathname + '?t=' + Date.now();
            }, 500);
        } else {
            showToast((translations.errorUpdatingStatus || 'Error updating status') + ': ' + (data.message || (translations.unknownError || 'Unknown error')), 'error');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        showToast(translations.errorUpdatingSideStatus || 'Error updating side status', 'error');
    });
}

function deleteSide(sideId, sideName = '') {
    const confirmMessage = sideName 
        ? `${translations.areYouSureDeleteSide || 'Are you sure you want to delete the side'} "${sideName}"?\n\n${translations.actionCannotBeUndone || 'This action cannot be undone.'}`
        : translations.deleteSideConfirm;
    
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
            window.MessageFormatter.showNotification('delete', 'processing', { item: sideName || 'Side' });
        }
        
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch(`/api/sides/${sideId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Show formatted success notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteSuccess(sideName || 'Side', {
                        title: translations.sideDeleted || 'Side Deleted',
                        duration: 4000
                    });
                } else {
                    showToast(translations.sideDeletedSuccessfully || 'Side deleted successfully!', 'success');
                }
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 500);
            } else {
                // Show formatted error notification
                if (window.MessageFormatter) {
                    window.MessageFormatter.showDeleteError(sideName || 'Side', {
                        title: translations.deleteFailed || 'Delete Failed',
                        duration: 6000
                    });
                } else {
                    showToast((translations.errorDeletingSide || 'Error deleting side') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                }
            }
        })
        .catch(error => {
            console.error('Error:', error);
            if (window.MessageFormatter) {
                window.MessageFormatter.showDeleteError(sideName || 'Side', {
                    title: translations.deleteError || 'Delete Error',
                    duration: 6000
                });
            } else {
                showToast(translations.errorDeletingSide || 'Error deleting side', 'error');
            }
        });
    });
    
    modal.show();
}

// Modal functions
function openSideModal(sideId = null) {
    const modal = new bootstrap.Modal(document.getElementById('sideModal'));
    const form = document.getElementById('sideForm');
    const modalTitle = document.getElementById('modalTitle');
    const submitButtonText = document.getElementById('submitButtonText');
    
    // Reset form
    form.reset();
    document.getElementById('sideId').value = '';
    document.getElementById('importanceSlider').value = '0.5';
    document.getElementById('importanceValue').textContent = '0.50';
    
    if (sideId) {
        // Edit mode
        modalTitle.textContent = translations.editSide || 'Edit Side';
        submitButtonText.textContent = translations.updateSide || 'Update Side';
        document.getElementById('modalIcon').className = 'bi bi-pencil me-2';
        document.getElementById('sideId').value = sideId;
        
        // Load side data
        fetch(`/api/sides/${sideId}`)
            .then(response => {
                if (!response.ok) {
                    return response.json().then(err => {
                        throw new Error(err.error || `HTTP error! status: ${response.status}`);
                    });
                }
                return response.json();
            })
            .then(data => {
                if (data.success && data.side) {
                    const side = data.side;
                    document.getElementById('sideName').value = side.name || '';
                    
                    const importance = side.importance || 0.5;
                    document.getElementById('importanceSlider').value = importance;
                    document.getElementById('importanceValue').textContent = parseFloat(importance).toFixed(2);
                } else {
                    showToast((translations.errorLoadingSide || 'Error loading side') + ': ' + (data.error || (translations.unknownError || 'Unknown error')), 'error');
                    modal.hide();
                }
            })
            .catch(error => {
                console.error('Error loading side data:', error);
                showToast((translations.errorLoadingSideData || 'Error loading side data') + ': ' + error.message, 'error');
                modal.hide();
            });
    } else {
        // Add mode
        modalTitle.textContent = translations.addNewSide || 'Add New Side';
        submitButtonText.textContent = translations.createSide || 'Create Side';
        document.getElementById('modalIcon').className = 'bi bi-plus-circle me-2';
    }
    
    modal.show();
}

function submitSideForm() {
    const form = document.getElementById('sideForm');
    const sideId = document.getElementById('sideId').value;
    const formData = new FormData(form);
    
    // Validate required fields
    if (!formData.get('name')) {
        showToast(translations.pleaseFillInSideName || 'Please fill in the side name', 'warning');
        return;
    }
    
    // Convert FormData to JSON
    const data = {
        name: formData.get('name'),
        importance: parseFloat(formData.get('importance')) || 0.5
    };
    
    const url = sideId ? `/api/sides/${sideId}` : '/api/sides';
    const method = sideId ? 'PUT' : 'POST';
    
    // ✅ SECURITY: Get CSRF token
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
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
        // Get side name from form
        const sideName = document.getElementById('sideName')?.value || 'Side';
        
        // Check if result has success field (POST/PUT responses)
        if (result && result.success !== undefined) {
            if (result.success) {
                // Show formatted success notification
                const isEdit = sideId ? true : false;
                if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                    if (isEdit) {
                        window.MessageFormatter.showUpdateSuccess(sideName, {
                            title: 'Side Updated',
                            duration: 4000
                        });
                    } else {
                        window.MessageFormatter.showCreateSuccess(sideName, {
                            title: 'Side Created',
                            duration: 4000
                        });
                    }
                } else {
                    showToast(
                        isEdit ? (translations.sideUpdatedSuccessfully || 'Side updated successfully!') : (translations.sideCreatedSuccessfully || 'Side created successfully!'),
                        'success'
                    );
                }
                
                // Close modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('sideModal'));
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
                    const isEdit = sideId ? true : false;
                    if (isEdit) {
                        window.MessageFormatter.showUpdateError(sideName, {
                            title: 'Update Failed',
                            duration: 6000
                        });
                    } else {
                        window.MessageFormatter.showCreateError(sideName, {
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
            const isEdit = sideId ? true : false;
            if (window.MessageFormatter && typeof window.MessageFormatter.showCreateSuccess === 'function') {
                if (isEdit) {
                    window.MessageFormatter.showUpdateSuccess(sideName, {
                        title: 'Side Updated',
                        duration: 4000
                    });
                } else {
                    window.MessageFormatter.showCreateSuccess(sideName, {
                        title: 'Side Created',
                        duration: 4000
                    });
                }
            } else {
                showToast(
                    isEdit ? (translations.sideUpdatedSuccessfully || 'Side updated successfully!') : (translations.sideCreatedSuccessfully || 'Side created successfully!'),
                    'success'
                );
            }
            const modal = bootstrap.Modal.getInstance(document.getElementById('sideModal'));
            if (modal) {
                modal.hide();
            }
            setTimeout(() => {
                window.location.href = window.location.pathname + '?t=' + Date.now();
            }, 500);
        }
    })
    .catch(error => {
        console.error('Error saving side:', error);
        const sideName = document.getElementById('sideName')?.value || 'Side';
        const isEdit = sideId ? true : false;
        if (window.MessageFormatter && typeof window.MessageFormatter.showCreateError === 'function') {
            if (isEdit) {
                window.MessageFormatter.showUpdateError(sideName, {
                    title: 'Update Error',
                    duration: 6000
                });
            } else {
                window.MessageFormatter.showCreateError(sideName, {
                    title: 'Create Error',
                    duration: 6000
                });
            }
        } else {
            showToast((translations.errorSavingSide || 'Error saving side') + ': ' + error.message, 'error');
        }
    });
}

// Make functions globally accessible for onclick handlers
window.viewSide = viewSide;
window.editSide = editSide;
window.duplicateSide = duplicateSide;
window.toggleSideStatus = toggleSideStatus;
window.deleteSide = deleteSide;
window.openSideModal = openSideModal;
window.submitSideForm = submitSideForm;

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
        openSideModal(parseInt(editId));
    }
});

// Export default init function for universal-initializer
export default function init() {
    // The initialization is already handled in DOMContentLoaded above
    // This is just for compatibility with universal-initializer
    return Promise.resolve();
}