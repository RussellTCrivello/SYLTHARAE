/**
 * File Selection and Filtering
 * Handles file selection, filtering, and bulk operations
 */

import { exportSelectedFiles as exportFiles } from './file-export.js';

/**
 * Select all files
 */
export function selectAllFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const checkboxes = document.querySelectorAll('.file-checkbox, .file-select-checkbox, .file-row-item input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        checkbox.checked = true;
    });
    updateSelectedCount();
}

/**
 * Deselect all files
 */
export function deselectAllFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const checkboxes = document.querySelectorAll('.file-checkbox, .file-select-checkbox, .file-row-item input[type="checkbox"]');
    checkboxes.forEach(checkbox => {
        checkbox.checked = false;
    });
    updateSelectedCount();
}

/**
 * Export selected files — ONE zip download of the extracted text of every
 * selected file. (This used to fire one download per file with a 100 ms
 * stagger; with a 50-file selection that meant fifty downloads.)
 */
export async function exportSelectedFiles() {
    // Support both .file-checkbox and .file-select-checkbox for compatibility
    const selectedCheckboxes = document.querySelectorAll('.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked');
    const fileIds = Array.from(selectedCheckboxes).map(cb => parseInt(cb.value));

    if (fileIds.length === 0) {
        const notificationSystem = await import('../ui/notifications.js');
        const { translations } = await import('../core/config.js');
        notificationSystem.default.warning(translations.pleaseSelectAtLeastOneFileToExport || 'Please select at least one file to export');
        return;
    }

    const { exportSelectedFiles: exportBulkZip } = await import('./bulk-analyst-actions.js');
    exportBulkZip();
}

/**
 * Export the ORIGINAL source files of the selection as one zip download
 * (see bulk-analyst-actions.js).
 */
export async function exportSelectedOriginals() {
    const { exportSelectedOriginals: exportOriginalsZip } = await import('./bulk-analyst-actions.js');
    exportOriginalsZip();
}

/**
 * Filter displayed files by search query
 */
export function filterDisplayedFiles(searchQuery) {
    const query = (searchQuery || '').toLowerCase().trim();
    
    // Support .file-card (list view), .file-grid-item (grid view), and .file-row-item for compatibility
    // Also support any element with data-file-id attribute
    const fileElements = document.querySelectorAll('.file-card[data-file-id], .file-grid-item[data-file-id], .file-row-item[data-file-id], [data-file-id]');
    
    if (!query) {
        // Show all if no query - restore original numbering
        fileElements.forEach((el) => {
            el.style.display = '';
            // Restore original number from data attribute if it exists
            const originalNumber = el.getAttribute('data-original-number');
            if (originalNumber) {
                const numberBadge = el.querySelector('.file-card-number');
                if (numberBadge) {
                    numberBadge.textContent = originalNumber;
                }
            }
        });
        updateSelectedCount();
        return;
    }
    
    // Split query into keywords for better matching
    const keywords = query.split(/\s+/).filter(k => k.length > 0);
    const lowerQuery = query.toLowerCase();
    
    // Store original numbers before filtering
    fileElements.forEach(element => {
        const numberBadge = element.querySelector('.file-card-number');
        if (numberBadge && !element.hasAttribute('data-original-number')) {
            element.setAttribute('data-original-number', numberBadge.textContent);
        }
    });
    
    let visibleIndex = 0;
    fileElements.forEach(element => {
        const fileName = (element.getAttribute('data-file-name') || '').toLowerCase();
        const fileType = (element.getAttribute('data-file-type') || '').toLowerCase();
        const fileSource = (element.getAttribute('data-file-source') || element.getAttribute('data-file-source-id') || element.getAttribute('data-file-source-name') || '').toLowerCase();
        const fileSide = (element.getAttribute('data-file-side') || element.getAttribute('data-file-side-id') || element.getAttribute('data-file-side-name') || '').toLowerCase();
        const fileDate = (element.getAttribute('data-file-date') || '').toLowerCase();
        const fileMeta = element.textContent.toLowerCase();
        
        // Check if all keywords match (AND logic) or any part matches
        let matches = false;
        if (keywords.length > 1) {
            // All keywords must be found (AND logic)
            matches = keywords.every(keyword => 
                fileName.includes(keyword) || 
                fileType.includes(keyword) || 
                fileSource.includes(keyword) || 
                fileSide.includes(keyword) ||
                fileDate.includes(keyword) ||
                fileMeta.includes(keyword)
            );
        } else {
            // Single keyword or phrase match
            matches = fileName.includes(lowerQuery) || 
                     fileType.includes(lowerQuery) || 
                     fileSource.includes(lowerQuery) || 
                     fileSide.includes(lowerQuery) ||
                     fileDate.includes(lowerQuery) ||
                     fileMeta.includes(lowerQuery);
        }
        
        if (matches) {
            element.style.display = '';
            visibleIndex++;
            // Update number badge to reflect position in filtered list
            const numberBadge = element.querySelector('.file-card-number');
            if (numberBadge) {
                numberBadge.textContent = visibleIndex;
            }
        } else {
            element.style.display = 'none';
        }
    });
    
    updateSelectedCount();
}

/**
 * Update selected files count display
 */
function updateSelectedCount() {
    const selectedCount = document.querySelectorAll('.file-checkbox:checked, .file-select-checkbox:checked, .file-row-item input[type="checkbox"]:checked').length;
    const totalCount = document.querySelectorAll('.file-checkbox, .file-select-checkbox, .file-row-item input[type="checkbox"]').length;
    
    // Update any count display elements if they exist
    const countElements = document.querySelectorAll('[data-selected-count], #selectedCount');
    countElements.forEach(el => {
        el.textContent = `${selectedCount} / ${totalCount}`;
    });
}

/**
 * Initialize file selection handlers
 */
export function initializeFileSelection() {
    // Add change handlers to checkboxes
    document.addEventListener('change', (e) => {
        if (e.target.classList.contains('file-checkbox')) {
            updateSelectedCount();
        }
    });
}

