/**
 * Files List Page JavaScript
 * Extracted from file/files_list.html
 */

import FileManagement from '../modules/file-operations/file-management.js';
import {
    chooseExportDestination,
    ensureExportExtension,
    saveExportBlob,
} from '../modules/core/export-download.js';

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('files-list-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
            // Make translations available globally for FileManagement
            window.translations = window.translations || {};
            Object.assign(window.translations, translations);
        } catch (e) {
            console.error('Error parsing files list page data:', e);
        }
    }
    
    // Load page data (pagination info) from JSON script tag
    const pageInfoEl = document.getElementById('page-data');
    if (pageInfoEl) {
        try {
            const pageData = JSON.parse(pageInfoEl.textContent);
            // Set data for FileManagement
            window.fileManagementData = pageData;
            FileManagement.data = pageData;
            console.log('Page data loaded:', pageData);
        } catch (e) {
            console.error('Error parsing page data:', e);
        }
    }
    
    // Initialize file management
    if (FileManagement && typeof FileManagement.init === 'function') {
        FileManagement.init();
    }
    
    console.log('Files list page loaded');
});

// Expose functions to window for onclick handlers
window.selectAllFiles = function() {
    if (FileManagement && typeof FileManagement.selectAllFiles === 'function') {
        FileManagement.selectAllFiles();
    }
};

window.deselectAllFiles = function() {
    if (FileManagement && typeof FileManagement.deselectAllFiles === 'function') {
        FileManagement.deselectAllFiles();
    }
};

window.toggleSelectAll = function(checkbox) {
    if (FileManagement && typeof FileManagement.toggleSelectAll === 'function') {
        FileManagement.toggleSelectAll(checkbox);
    }
};

window.updateBulkToolbar = function() {
    if (FileManagement && typeof FileManagement.updateBulkToolbar === 'function') {
        FileManagement.updateBulkToolbar();
    }
};

window.bulkAnalyze = function() {
    if (FileManagement && typeof FileManagement.bulkAnalyze === 'function') {
        FileManagement.bulkAnalyze();
    }
};

window.bulkExport = function() {
    if (FileManagement && typeof FileManagement.bulkExport === 'function') {
        FileManagement.bulkExport();
    }
};

window.exportSelectedFileNames = async function(format = 'csv') {
    const fileIds = Array.from(document.querySelectorAll('.file-checkbox:checked'))
        .map(checkbox => Number(checkbox.value))
        .filter(id => Number.isSafeInteger(id) && id > 0);
    const message = (key, fallback) => translations[key] || fallback;
    if (!fileIds.length) {
        window.showWarning?.(message('pleaseSelectFilesToExport', 'Please select files to export'));
        return;
    }

    const filename = window.prompt(
        message('filenameExportPrompt', 'Name this filename export (leave blank for an automatic name):'),
        `selected_filenames_${new Date().toISOString().slice(0, 10)}`);
    if (filename === null) return;

    const extension = format === 'excel' ? 'xlsx' : 'csv';
    const suggestedName = ensureExportExtension(
        filename.trim() || `selected_filenames_${new Date().toISOString().slice(0, 10)}`,
        extension,
        'selected_filenames');
    let destination = null;
    try {
        destination = await chooseExportDestination(suggestedName);
        if (destination === false) return;
    } catch (error) {
        // The browser download manager remains a safe fallback if its native
        // save picker is present but unavailable in this context.
        console.warn('Save location picker unavailable:', error);
    }

    try {
        const response = await fetch('/api/files/names/export', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
            body: JSON.stringify({
                scope: 'selected',
                file_ids: fileIds,
                format: format === 'excel' ? 'excel' : 'csv',
                filename: filename.trim(),
            }),
        });
        if (!response.ok) {
            const problem = await response.json().catch(() => ({}));
            if (response.status === 413) {
                throw new Error(message('filenameExportLimit', 'The selected filename list is too large to export in one file.'));
            }
            throw new Error(problem.error || `HTTP ${response.status}`);
        }

        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
        let downloadName = `selected_filenames.${extension}`;
        if (match) {
            try { downloadName = decodeURIComponent(match[1].replace(/\"/g, '')); }
            catch (_error) { downloadName = match[1].replace(/\"/g, ''); }
        }
        await saveExportBlob(blob, downloadName, destination);
        window.showSuccess?.(message('filenameExportReady', 'Filename export ready.'));
    } catch (error) {
        console.error('Selected filename export failed:', error);
        window.showError?.(`${message('filenameExportFailed', 'Could not export the filename list.')} ${error.message}`);
    }
};

window.clearFileSearch = function() {
    if (FileManagement && typeof FileManagement.clearFileSearch === 'function') {
        FileManagement.clearFileSearch();
    }
};

window.changeFilesPageSize = function() {
    if (FileManagement && typeof FileManagement.changeFilesPageSize === 'function') {
        FileManagement.changeFilesPageSize();
    }
};
