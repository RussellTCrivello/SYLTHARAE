/**
 * File Management System
 * Handles all file listing, filtering, selection, and bulk operations
 * Consolidated from files-management.js
 */

import { apiPost, apiDelete } from '../api/api-client.js';
import { getCSRFToken } from '../core/utils.js';
import * as fileSelection from './file-selection.js';
import * as fileExport from './file-export.js';
import { renderUnifiedPagination } from '../rendering/unified-pagination.js';

// Current state
const FileManagement = {
    // Current state
    selectedFiles: new Set(),
    currentView: 'list',
    
    // Pagination data from server
    data: window.fileManagementData || {},
    
    // Initialize
    init() {
        this.restoreFilterValues();
        this.setupEventListeners();
        this.restoreViewPreference();
        this.updateBulkToolbar();
        this.renderPagination();
    },
    
    // Restore filter values from URL parameters
    restoreFilterValues() {
        const params = new URLSearchParams(window.location.search);
        
        // Restore search input
        const searchInput = document.getElementById('smartSearch');
        if (searchInput && params.has('search')) {
            searchInput.value = params.get('search');
        }
        
        // Restore filter dropdowns
        const filters = {
            'sourceFilter': 'source',
            'sideFilter': 'side',
            'fileTypeFilter': 'file_type',
            'statusFilter': 'status',
            'sortBy': 'sort'
        };
        
        Object.entries(filters).forEach(([elementId, paramName]) => {
            const element = document.getElementById(elementId);
            if (element && params.has(paramName)) {
                element.value = params.get(paramName);
            }
        });
        
        // Restore per page selector
        const perPageSelect = document.getElementById('perPageFiles');
        if (perPageSelect && params.has('limit')) {
            perPageSelect.value = params.get('limit');
        }
    },
    
    // Setup all event listeners
    setupEventListeners() {
        // View mode toggle
        document.querySelectorAll('.view-mode-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.switchView(e.target.closest('.view-mode-btn').dataset.view));
        });
        
        // Filter changes - search input
        const searchInput = document.getElementById('smartSearch');
        if (searchInput) {
            // Remove existing listeners to avoid duplicates
            const newSearchInput = searchInput.cloneNode(true);
            searchInput.parentNode.replaceChild(newSearchInput, searchInput);
            
            newSearchInput.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.applyFilters();
                }
            });
            
            // Also trigger on blur if value changed
            let lastSearchValue = newSearchInput.value;
            newSearchInput.addEventListener('blur', () => {
                if (newSearchInput.value !== lastSearchValue) {
                    lastSearchValue = newSearchInput.value;
                    this.applyFilters();
                }
            });
        }
        
        // Filter dropdowns - add change listeners
        ['sourceFilter', 'sideFilter', 'fileTypeFilter', 'statusFilter', 'sortBy'].forEach(id => {
            const element = document.getElementById(id);
            if (element) {
                // Remove existing listeners to avoid duplicates
                const newElement = element.cloneNode(true);
                element.parentNode.replaceChild(newElement, element);
                
                newElement.addEventListener('change', () => {
                    this.applyFilters();
                });
            }
        });
        
        // Checkbox changes
        document.querySelectorAll('.file-checkbox').forEach(cb => {
            cb.addEventListener('change', () => this.updateBulkToolbar());
        });
    },
    
    // ==================== VIEW MANAGEMENT ====================
    switchView(view) {
        this.currentView = view;
        
        // Update buttons
        document.querySelectorAll('.view-mode-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === view);
        });
        
        // Show/hide views
        document.querySelectorAll('.files-view').forEach(viewEl => {
            viewEl.style.display = viewEl.dataset.view === view ? 'block' : 'none';
        });
        
        // Save preference
        localStorage.setItem('filesViewMode', view);
    },
    
    restoreViewPreference() {
        const saved = localStorage.getItem('filesViewMode');
        if (saved) {
            this.switchView(saved);
        }
    },
    
    // ==================== SELECTION MANAGEMENT ====================
    selectAllFiles() {
        fileSelection.selectAllFiles();
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = true;
        }
        this.updateBulkToolbar();
    },
    
    deselectAllFiles() {
        fileSelection.deselectAllFiles();
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox) {
            selectAllCheckbox.checked = false;
        }
        this.updateBulkToolbar();
    },
    
    toggleSelectAll(checkbox) {
        if (checkbox.checked) {
            this.selectAllFiles();
        } else {
            this.deselectAllFiles();
        }
    },
    
    updateBulkToolbar() {
        const selected = document.querySelectorAll('.file-checkbox:checked').length;
        
        // Update count
        const countEl = document.getElementById('selectedCount');
        if (countEl) countEl.textContent = selected;
        
        // Enable/disable bulk action buttons
        const bulkBtns = [
            'bulkAnalyzeBtn', 'bulkExportBtn', 'bulkNamesCsvBtn', 'bulkNamesExcelBtn',
            'bulkDeleteBtn'
        ];
        bulkBtns.forEach(btnId => {
            const btn = document.getElementById(btnId);
            if (btn) btn.disabled = selected === 0;
        });
        
        // Update select all checkbox state
        const allCheckboxes = document.querySelectorAll('.file-checkbox');
        const selectAllCheckbox = document.getElementById('selectAllCheckbox');
        if (selectAllCheckbox && allCheckboxes.length > 0) {
            selectAllCheckbox.checked = selected === allCheckboxes.length;
            selectAllCheckbox.indeterminate = selected > 0 && selected < allCheckboxes.length;
        }
    },
    
    // ==================== FILTER MANAGEMENT ====================
    applyFilters() {
        const params = new URLSearchParams();
        
        // Get all filter values
        const search = document.getElementById('smartSearch')?.value?.trim();
        const source = document.getElementById('sourceFilter')?.value;
        const side = document.getElementById('sideFilter')?.value;
        const fileType = document.getElementById('fileTypeFilter')?.value;
        const status = document.getElementById('statusFilter')?.value;
        const sort = document.getElementById('sortBy')?.value || 'date_desc';
        const limit = document.getElementById('perPageFiles')?.value || '10';
        
        // Add to params if not empty
        if (search) params.set('search', search);
        if (source) params.set('source', source);
        if (side) params.set('side', side);
        if (fileType) params.set('file_type', fileType);
        if (status) params.set('status', status);
        if (sort) params.set('sort', sort);
        if (limit) params.set('limit', limit);
        
        // Reset to first page when filtering
        params.delete('cursor');
        
        // Navigate
        window.location.href = window.location.pathname + '?' + params.toString();
    },
    
    clearFileSearch() {
        const searchInput = document.getElementById('smartSearch');
        if (searchInput) {
            searchInput.value = '';
            this.applyFilters();
        }
    },
    
    // ==================== PAGINATION ====================
    changeFilesPageSize() {
        const perPageSelect = document.getElementById('perPageFiles');
        if (perPageSelect) {
            const params = new URLSearchParams(window.location.search);
            params.set('limit', perPageSelect.value);
            params.delete('cursor'); // Reset to first page
            window.location.href = window.location.pathname + '?' + params.toString();
        }
    },
    
    // Render numbered pagination buttons (unified style)
    renderPagination() {
        // Try to get data from this.data first, then window.fileManagementData
        const data = this.data && this.data.totalPages ? this.data : (window.fileManagementData || {});
        
        if (!data || !data.totalPages || data.totalPages <= 1) {
            const container = document.getElementById('paginationContainer');
            if (container) {
                container.innerHTML = '';
            }
            return;
        }
        
        const currentPage = parseInt(data.currentPage) || 1;
        const totalPages = parseInt(data.totalPages) || 1;
        
        // Get URL parameters to preserve
        const urlParams = {};
        const currentParams = new URLSearchParams(window.location.search);
        currentParams.forEach((value, key) => {
            if (key !== 'page') {
                urlParams[key] = value;
            }
        });
        
        // Use unified pagination
        renderUnifiedPagination({
            currentPage: currentPage,
            totalPages: totalPages,
            containerId: 'paginationContainer',
            onPageChange: (page) => {
                this.navigateToPage(page);
            },
            urlParams: urlParams,
            showInfo: true,
            showJump: totalPages > 5,
            baseUrl: window.location.pathname
        });
    },
    
    // Navigate to a specific page using offset-based pagination
    navigateToPage(targetPage) {
        // Get current URL parameters to preserve all filters
        const currentParams = new URLSearchParams(window.location.search);
        
        // Get filter values from form inputs (preferred) or URL params (fallback)
        const search = document.getElementById('smartSearch')?.value?.trim() || currentParams.get('search') || '';
        const source = document.getElementById('sourceFilter')?.value || currentParams.get('source') || '';
        const side = document.getElementById('sideFilter')?.value || currentParams.get('side') || '';
        const fileType = document.getElementById('fileTypeFilter')?.value || currentParams.get('file_type') || '';
        const status = document.getElementById('statusFilter')?.value || currentParams.get('status') || '';
        const sort = document.getElementById('sortBy')?.value || currentParams.get('sort') || 'date_desc';
        const limit = document.getElementById('perPageFiles')?.value || currentParams.get('limit') || '10';
        
        // Build URL with filters
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        if (source) params.set('source', source);
        if (side) params.set('side', side);
        if (fileType) params.set('file_type', fileType);
        if (status) params.set('status', status);
        if (sort) params.set('sort', sort);
        params.set('limit', limit);
        params.set('page', targetPage);
        
        window.location.href = window.location.pathname + '?' + params.toString();
    },
    
    // ==================== BULK OPERATIONS ====================
    async bulkAnalyze() {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
        
        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToAnalyze || 'Please select files to analyze';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }
        
        try {
            const data = await apiPost('/analysis/batch/process', { file_ids: selected });
            
            if (data.success) {
                const message = `${window.translations?.analysisStartedFor || 'Analysis started for'} ${selected.length} ${window.translations?.files || 'files'}`;
                if (window.showSuccess) {
                    window.showSuccess(message);
                } else if (window.alert) {
                    window.alert(message);
                }
                setTimeout(() => {
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 2000);
            } else {
                const errorMsg = `${window.translations?.errorStartingAnalysis || 'Error starting analysis'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in bulkAnalyze:', error);
            const errorMsg = window.translations?.errorStartingAnalysis || 'Error starting analysis';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    },
    
    async bulkExport() {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
        
        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToExport || 'Please select files to export';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }
        
        // Use file-export module
        await fileExport.exportSelectedFiles(selected);
    },
    
    async bulkDelete() {
        const selected = Array.from(document.querySelectorAll('.file-checkbox:checked')).map(cb => cb.value);
        
        if (selected.length === 0) {
            const msg = window.translations?.pleaseSelectFilesToDelete || 'Please select files to delete';
            if (window.showWarning) {
                window.showWarning(msg);
            } else if (window.alert) {
                window.alert(msg);
            }
            return;
        }
        
        const confirmMsg = `${selected.length} ${window.translations?.deleteFilesConfirm || 'files will be deleted. Are you sure?'}`;
        if (!confirm(confirmMsg)) {
            return;
        }
        
        try {
            const data = await apiPost('/files/bulk-delete', { file_ids: selected });
            
            if (data.success) {
                const message = `${window.translations?.deletedFiles || 'Deleted'} ${selected.length} ${window.translations?.files || 'files'}`;
                if (window.showSuccess) {
                    window.showSuccess(message);
                } else if (window.alert) {
                    window.alert(message);
                }
                window.location.href = window.location.pathname + '?t=' + Date.now();
            } else {
                const errorMsg = `${window.translations?.errorDeletingFiles || 'Error deleting files'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in bulkDelete:', error);
            const errorMsg = window.translations?.errorDeletingFiles || 'Error deleting files';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    },
    
    // ==================== SINGLE FILE OPERATIONS ====================
    async deleteFile(fileId) {
        const confirmMsg = window.translations?.deleteFileConfirm || 'Are you sure you want to delete this file?';
        if (!confirm(confirmMsg)) {
            return;
        }
        
        try {
            const data = await apiDelete(`/file/${fileId}/delete`);
            
            if (data.success) {
                if (window.showSuccess) {
                    window.showSuccess(window.translations?.fileDeleted || 'File deleted');
                } else if (window.alert) {
                    window.alert(window.translations?.fileDeleted || 'File deleted');
                }
                window.location.href = window.location.pathname + '?t=' + Date.now();
            } else {
                const errorMsg = `${window.translations?.errorDeletingFile || 'Error deleting file'}: ${data.error || 'Unknown error'}`;
                if (window.showError) {
                    window.showError(errorMsg);
                } else if (window.alert) {
                    window.alert(errorMsg);
                }
            }
        } catch (error) {
            console.error('Error in deleteFile:', error);
            const errorMsg = window.translations?.errorDeletingFile || 'Error deleting file';
            if (window.showError) {
                window.showError(errorMsg);
            } else if (window.alert) {
                window.alert(errorMsg);
            }
        }
    },
    
    // ==================== PREVIEW ====================
    quickPreview(fileId) {
        // Placeholder for quick preview functionality
        console.log('Quick preview for file:', fileId);
        // You can implement a modal preview here
    }
};

// FILE-UI-01: files_list.html references bulkDelete()/deleteFile(id) from
// inline onclick handlers; expose the implementations globally (this module
// is loaded as an ES module, so its top-level names are module-scoped).
window.bulkDelete = (...args) => FileManagement.bulkDelete(...args);
window.deleteFile = (...args) => FileManagement.deleteFile(...args);

// Export for use in other modules
export default FileManagement;

