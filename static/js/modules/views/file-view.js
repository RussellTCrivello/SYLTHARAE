/**
 * File View Renderer
 * Extracted from the legacy file-management-system.js
 */

import { escapeAttribute, escapeHtml, formatFileSize } from '../core/utils.js';
import { translations } from '../core/config.js';
import { renderFilePaginationControls, updateNavItemCount, initializeFilePaginationControls } from '../rendering/pagination.js';
import { navigationState, fileNavigationState } from '../core/state.js';
import { getFileViewMode } from '../ui/view-mode.js';

/**
 * One delegated handler for the file list.
 *
 * Cards used to carry their whole payload inside inline handlers:
 *
 *     onclick="showFileDetails?.(12, '<name>', <files as JSON>, 0)"
 *
 * and a filename is not code. escapeHtml is a text-node escaper - it leaves
 * quotes alone - so a name with an apostrophe ended the JavaScript string
 * early and the browser refused to compile the handler ("missing ) after
 * argument list" repeated in the console); a name with a double quote closed
 * the attribute as well. Every card in the listing was dead: clicking did
 * nothing, and the console filled with errors.
 *
 * The click contract is therefore data, not source text:
 *
 *     data-file-id / data-file-name / data-file-index   who the card is
 *     data-file-action="open" | "export" | "full-view"  what the click does
 *
 * Names live in attributes escaped with escapeAttribute and are read back with
 * getAttribute(), which the browser un-escapes - so the modal still shows the
 * real filename, and no filename is ever parsed as code. Links, checkboxes and
 * other native controls keep their own behaviour.
 *
 * @param {Event} event - click event (delegated from the content container)
 * @returns {boolean} true when the click was handled here
 */
export function handleFileCardClick(event) {
    const target = event && event.target;
    if (!target || typeof target.closest !== 'function') return false;
    // Native controls first: an anchor navigates, a checkbox toggles.
    if (target.closest('a, input, select, textarea, label')) return false;

    const card = target.closest('.file-card, .file-grid-item, [data-file-id]');
    if (!card) return false;

    const actionEl = target.closest('[data-file-action]');
    const action = (actionEl && actionEl.getAttribute('data-file-action')) || 'open';

    const idSource = (actionEl && actionEl.getAttribute('data-file-id'))
        || card.getAttribute('data-file-id');
    const fileId = parseInt(idSource, 10);
    if (!Number.isInteger(fileId)) return false;

    if (action === 'export') {
        if (typeof window.exportFile === 'function') window.exportFile(fileId);
        return true;
    }
    if (action !== 'open') return false;

    const name = card.getAttribute('data-file-name') || 'File';
    const indexAttr = card.getAttribute('data-file-index');
    const index = indexAttr === null ? -1 : parseInt(indexAttr, 10);
    // The page on screen is the list this renderer stored, so Previous/Next in
    // the modal keeps walking the page the user is looking at.
    const files = Array.isArray(fileNavigationState.currentFiles)
        ? fileNavigationState.currentFiles : null;
    const details = window.fms && window.fms.fileOperations
        && window.fms.fileOperations.fileDetails;
    const open = (details && details.showFileDetails) || window.showFileDetails;
    if (typeof open !== 'function') return false;
    open(fileId, name, files, Number.isInteger(index) ? index : -1);
    return true;
}

//: Delegation is registered once per document, however often the list re-renders.
let fileCardDelegationBound = false;

/**
 * Attach the delegated click handler to the rendered file list (idempotent).
 */
export function ensureFileCardDelegation() {
    if (fileCardDelegationBound) return;
    const host = (typeof document !== 'undefined'
        && (document.getElementById('unifiedContentView') || document.body))
        || null;
    if (!host || typeof host.addEventListener !== 'function') return;
    host.addEventListener('click', handleFileCardClick);
    fileCardDelegationBound = true;
}

/**
 * Render a single file card (for list view)
 */
function renderFileCard(file, fileNumber, index) {
    const fileSize = formatFileSize(file.size || 0);
    const fileType = file.type || file.file_type || '';
    const fileDate = file.file_date || file.date || file.created_at || '';
    const fileSource = file.source || file.source_name || '';
    const fileSide = file.side || file.side_name || '';
    const displayName = file.name || `File ${fileNumber}`;
    const safeName = escapeHtml(displayName);
    // Attribute context needs the attribute escaper: a quote in a filename
    // must not be able to close the attribute or open a handler of its own.
    const nameAttr = escapeAttribute(displayName);
    
    return `
        <div class="file-card" data-file-id="${file.id}" data-file-action="open" data-file-name="${nameAttr}" data-file-index="${index}" style="border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 1rem; margin-bottom: 0.75rem; display: flex; gap: 1rem; align-items: flex-start; cursor: pointer; position: relative;">
            <div class="file-card-number" style="position: absolute; top: 0.5rem; left: 0.5rem; background: #3b82f6; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 600; z-index: 10; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">${fileNumber}</div>
            <div class="file-card-icon"><i class="bi bi-file-earmark" aria-hidden="true"></i></div>
            <div class="file-card-body" style="flex: 1;">
                <div class="file-card-name" style="font-weight: 600; margin-bottom: 0.25rem;">${safeName}</div>
                <div class="file-card-meta" style="color: #64748b; font-size: 0.875rem; display: flex; gap: 1rem; flex-wrap: wrap;">
                    <span><i class="bi bi-diagram-3"></i> ${escapeHtml(fileSide || '—')}</span>
                    <span><i class="bi bi-building"></i> ${escapeHtml(fileSource || '—')}</span>
                    <span><i class="bi bi-hdd"></i> ${fileSize}</span>
                    <span><i class="bi bi-calendar"></i> ${escapeHtml(fileDate || '—')}</span>
                    <span><i class="bi bi-tag"></i> ${escapeHtml(fileType || '—')}</span>
                </div>
                <div class="file-card-actions" style="margin-top: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap;">
                    <button class="action-btn" data-file-action="open" title="${translations.viewDetails || 'View Details'}" aria-label="${translations.viewDetailsFor || 'View Details for'} ${nameAttr}">
                        <i class="bi bi-eye" aria-hidden="true"></i>
                        <span>${translations.viewDetails || 'View Details'}</span>
                    </button>
                    <a class="action-btn" href="/file/${file.id}" target="_blank" rel="noopener" data-file-action="full-view" title="${translations.openFullView || 'Open Full View'}" aria-label="${translations.openFullViewFor || 'Open Full View for'} ${nameAttr}">
                        <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>
                        <span>${translations.fullView || 'Full View'}</span>
                    </a>
                    <button class="action-btn export-btn" data-file-action="export" title="${translations.exportFile || 'Export File'}" aria-label="${translations.export || 'Export'}: ${nameAttr}">
                        <i class="bi bi-download" aria-hidden="true"></i>
                        <span class="sr-only">${translations.export || 'Export'}</span>
                    </button>
                </div>
            </div>
            <div class="file-card-select" style="display: flex; align-items: center;">
                <input type="checkbox" class="file-checkbox" value="${file.id}" aria-label="${translations.selectFile || 'Select file'}: ${nameAttr}">
            </div>
        </div>
    `;
}

/**
 * Render a file grid item (for grid view)
 */
function renderFileGridItem(file, fileNumber, index) {
    const fileSize = formatFileSize(file.size || 0);
    const fileType = file.type || file.file_type || '';
    const fileDate = file.file_date || file.date || file.created_at || '';
    const displayName = file.name || `File ${fileNumber}`;
    const safeName = escapeHtml(displayName);
    const nameAttr = escapeAttribute(displayName);
    
    return `
        <div class="file-grid-item" data-file-id="${file.id}" data-file-action="open" data-file-name="${nameAttr}" data-file-index="${index}" style="border: 1px solid #e2e8f0; border-radius: 0.5rem; padding: 1rem; cursor: pointer; position: relative; background: white; transition: transform 0.2s, box-shadow 0.2s;" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 8px rgba(0,0,0,0.1)'" onmouseout="this.style.transform=''; this.style.boxShadow=''">
            <div class="file-card-number" style="position: absolute; top: 0.5rem; right: 0.5rem; background: #3b82f6; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 600; z-index: 10; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">${fileNumber}</div>
            <div style="text-align: center; margin-bottom: 0.75rem;">
                <div class="file-grid-icon" style="font-size: 3rem; color: #3b82f6; margin-bottom: 0.5rem;">
                    <i class="bi bi-file-earmark" aria-hidden="true"></i>
                </div>
            </div>
            <div class="file-grid-body">
                <div class="file-grid-name" style="font-weight: 600; margin-bottom: 0.5rem; text-align: center; font-size: 0.875rem; line-height: 1.4; min-height: 2.8em; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;" title="${nameAttr}">${safeName}</div>
                <div class="file-grid-meta" style="color: #64748b; font-size: 0.75rem; display: flex; flex-direction: column; gap: 0.25rem;">
                    <div><i class="bi bi-tag"></i> ${escapeHtml(fileType || '—')}</div>
                    <div><i class="bi bi-hdd"></i> ${fileSize}</div>
                    <div><i class="bi bi-calendar"></i> ${escapeHtml(fileDate || '—')}</div>
                </div>
                <div class="file-grid-actions" style="margin-top: 0.75rem; display: flex; gap: 0.25rem; justify-content: center; flex-wrap: wrap;">
                    <button class="action-btn" style="font-size: 0.75rem; padding: 0.25rem 0.5rem;" data-file-action="open" title="${translations.viewDetails || 'View Details'}" aria-label="${translations.viewDetailsFor || 'View Details for'} ${nameAttr}">
                        <i class="bi bi-eye" aria-hidden="true"></i>
                    </button>
                    <a class="action-btn" style="font-size: 0.75rem; padding: 0.25rem 0.5rem;" href="/file/${file.id}" target="_blank" rel="noopener" data-file-action="full-view" title="${translations.openFullView || 'Open Full View'}" aria-label="${translations.openFullViewFor || 'Open Full View for'} ${nameAttr}">
                        <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i>
                    </a>
                    <button class="action-btn export-btn" style="font-size: 0.75rem; padding: 0.25rem 0.5rem;" data-file-action="export" title="${translations.exportFile || 'Export File'}" aria-label="${translations.export || 'Export'}: ${nameAttr}">
                        <i class="bi bi-download" aria-hidden="true"></i>
                    </button>
                </div>
                <div class="file-grid-select" style="margin-top: 0.5rem; text-align: center;">
                    <input type="checkbox" class="file-checkbox" value="${file.id}" aria-label="${translations.selectFile || 'Select file'}: ${nameAttr}">
                </div>
            </div>
        </div>
    `;
}

export function renderFilesView(files, section, itemName, pagination) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;

    let html = '';
    const sectionLabel = section || 'Section';
    html += '<div class="section" style="margin-bottom: 1.5rem;">';
    html += '<div class="section-header">';
    html += `<div class="section-label"><i class="bi bi-folder"></i> ${escapeHtml(sectionLabel)}: ${escapeHtml(itemName || 'Item')}</div>`;
    html += '</div>';
    html += '</div>';

    if (pagination) {
        navigationState.filePagination = {
            currentPage: pagination.page,
            perPage: pagination.per_page,
            total: pagination.total,
            totalPages: pagination.total_pages,
            totalSize: pagination.total_size,
            has_prev: pagination.has_prev,
            has_next: pagination.has_next
        };
        const startItem = (pagination.page - 1) * pagination.per_page + 1;
        const endItem = Math.min(pagination.page * pagination.per_page, pagination.total);
        updateNavItemCount(startItem, endItem, pagination.total);
    }

    if (files && files.length > 0) {
        html += '<div class="section">';
        html += '<div class="section-header">';
        html += `<div class="section-label">${translations.files || 'Files'}</div>`;
        html += '<div style="display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;">';
        html += '<div style="position: relative; flex: 1; min-width: 200px; max-width: 300px;">';
        html += `<input type="text" id="fileSearchInput" placeholder="${translations.searchFiles || 'Search files...'}" oninput="filterDisplayedFiles?.(this.value)" style="width: 100%; padding: 0.5rem 2.5rem 0.5rem 0.75rem; border: 1px solid #e2e8f0; border-radius: 0.375rem; font-size: 0.875rem;" title="${translations.searchDisplayedFiles || 'Search displayed files'}" aria-label="${translations.searchDisplayedFiles || 'Search displayed files'}">`;
        html += '<i class="bi bi-search" style="position: absolute; right: 0.75rem; top: 50%; transform: translateY(-50%); color: #94a3b8; pointer-events: none;"></i>';
        html += '</div>';
        // Per Page control for files
        const currentPerPage = pagination ? pagination.per_page : (navigationState.filePagination?.perPage || 50);
        html += '<div class="per-page-control" style="display: flex; align-items: center; gap: 0.5rem;">';
        html += `<label for="perPageFiles" style="font-size: 0.875rem; color: var(--text-light); white-space: nowrap;">${translations.perPage || 'Per Page:'}</label>`;
        html += `<select id="perPageFiles" class="form-select form-select-sm" style="min-width: 80px; font-size: 0.875rem;" onchange="handleFilePerPageChange()" title="${translations.itemsPerPage || 'Items Per Page'}" aria-label="${translations.itemsPerPage || 'Items Per Page'}">`;
        html += `<option value="10" ${currentPerPage === 10 ? 'selected' : ''}>10</option>`;
        html += `<option value="25" ${currentPerPage === 25 ? 'selected' : ''}>25</option>`;
        html += `<option value="50" ${currentPerPage === 50 ? 'selected' : ''}>50</option>`;
        html += `<option value="100" ${currentPerPage === 100 ? 'selected' : ''}>100</option>`;
        html += `<option value="200" ${currentPerPage === 200 ? 'selected' : ''}>200</option>`;
        html += '</select>';
        html += '</div>';
        // View mode toggle for files
        const currentFileViewMode = getFileViewMode();
        html += '<div class="file-view-toggle" style="display: flex; align-items: center; gap: 0.25rem; border: 1px solid #e2e8f0; border-radius: 0.375rem; padding: 0.125rem; background: #f8fafc;">';
        html += `<button class="view-toggle-btn ${currentFileViewMode === 'list' ? 'active' : ''}" data-view="list" onclick="setFileViewMode('list')" style="padding: 0.375rem 0.75rem; border: none; background: ${currentFileViewMode === 'list' ? '#3b82f6' : 'transparent'}; color: ${currentFileViewMode === 'list' ? 'white' : '#64748b'}; border-radius: 0.25rem; cursor: pointer; font-size: 0.875rem; transition: all 0.2s;" title="${translations.listView || 'List View'}" aria-label="${translations.switchToListView || 'Switch to List View'}">`;
        html += '<i class="bi bi-list-ul" aria-hidden="true"></i>';
        html += '</button>';
        html += `<button class="view-toggle-btn ${currentFileViewMode === 'grid' ? 'active' : ''}" data-view="grid" onclick="setFileViewMode('grid')" style="padding: 0.375rem 0.75rem; border: none; background: ${currentFileViewMode === 'grid' ? '#3b82f6' : 'transparent'}; color: ${currentFileViewMode === 'grid' ? 'white' : '#64748b'}; border-radius: 0.25rem; cursor: pointer; font-size: 0.875rem; transition: all 0.2s;" title="${translations.gridView || 'Grid View'}" aria-label="${translations.switchToGridView || 'Switch to Grid View'}">`;
        html += '<i class="bi bi-grid-3x3" aria-hidden="true"></i>';
        html += '</button>';
        html += '</div>';
        html += `<button class="action-btn" onclick="selectAllFiles?.()" style="background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0;" title="${translations.selectAllFiles || 'Select All Files'}" aria-label="${translations.selectAllFiles || 'Select All Files'}">${translations.selectAll || 'Select All'}</button>`;
        html += `<button class="action-btn" onclick="deselectAllFiles?.()" style="background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0;" title="${translations.deselectAllFiles || 'Deselect All Files'}" aria-label="${translations.deselectAllFiles || 'Deselect All Files'}">${translations.deselectAll || 'Deselect All'}</button>`;
        html += `<button class="action-btn export-btn" onclick="exportSelectedFiles?.()" style="background: #10b981;" title="${translations.exportSelectedFiles || 'Export Selected Files'}" aria-label="${translations.exportSelected || 'Export Selected'}">${translations.exportSelected || 'Export Selected'}</button>`;
        html += `<button class="action-btn export-btn" onclick="exportSelectedOriginals?.()" style="background: #0ea5e9;" title="${translations.exportOriginalsTitle || 'Download the original source files of the selection as one zip'}" aria-label="${translations.exportOriginals || 'Export Originals'}"><i class="bi bi-file-earmark-zip me-1" aria-hidden="true"></i>${translations.exportOriginals || 'Export Originals'}</button>`;
        html += '</div>';
        html += '</div>';

        // Analyst (manual) categorization of the selection — appears when
        // files are checked, targets the analyst namespace only (FR-1.4).
        if (window.appData?.canCategorize) {
            html += `<div id="analystBulkBar" data-section-context="${escapeAttribute(`${sectionLabel}: ${itemName || ''}`)}"
                          style="display: none; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.75rem; padding: 0.6rem 0.75rem; border: 1px solid #ddd6fe; border-radius: 0.5rem; background: #f5f3ff;">`;
            html += `<span style="font-size: 0.875rem; color: #4c1d95;"><i class="bi bi-person-check me-1" aria-hidden="true"></i><strong id="analystBulkCount">0</strong> ${translations.selected || 'selected'}</span>`;
            html += `<select id="analystBulkCategorySelect" class="form-select form-select-sm" style="max-width: 240px;" title="${translations.analystBulkCategoryTitle || 'Analyst-defined categories only (never smart categories)'}"><option value="">${translations.chooseOrCreateCategory || 'Choose analyst category…'}</option></select>`;
            html += `<input type="text" id="analystBulkNewCategory" class="form-control form-control-sm" style="max-width: 200px;" placeholder="${translations.orNewCategoryName || '…or new category name'}" maxlength="255">`;
            html += `<button class="action-btn" onclick="assignAnalystCategoriesToSelection?.()" style="background: #7c3aed; color: #ffffff;" title="${translations.categorizeSelectedTitle || 'Assign the analyst category to every selected file'}">${translations.categorize || 'Categorize'}</button>`;
            html += `<button class="action-btn" onclick="removeAnalystCategoriesFromSelection?.()" style="background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0;" title="${translations.removeCategoriesTitle || 'Remove analyst categories from every selected file'}">${translations.removeCategories || 'Remove'}</button>`;
            html += '</div>';
        }

        const filesList = files.map(file => ({ id: file.id, name: file.name }));
        // Calculate starting number for pagination
        const startNumber = pagination ? (pagination.page - 1) * pagination.per_page + 1 : 1;
        
        // Render files based on view mode
        if (currentFileViewMode === 'grid') {
            // Grid view
            html += '<div class="files-grid-view" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; margin-top: 1rem;">';
            files.forEach((file, index) => {
                const fileNumber = startNumber + index;
                html += renderFileGridItem(file, fileNumber, index);
            });
            html += '</div>';
        } else {
            // List view (default)
            files.forEach((file, index) => {
                const fileNumber = startNumber + index;
                html += renderFileCard(file, fileNumber, index);
            });
        }

        fileNavigationState.currentFiles = filesList;
        html += renderFilePaginationControls();
        html += '</div>'; // section
    } else {
        html += `<div class="section"><div class="empty-state">${translations.noFilesFound || 'No files found'}</div></div>`;
    }

    contentView.innerHTML = html;

    // Clicks on the rendered cards are handled by one delegated listener that
    // reads the data attributes - never by a handler built out of a filename.
    ensureFileCardDelegation();

    // Initialize pagination controls after DOM is ready
    requestAnimationFrame(() => {
        if (files && files.length > 0 && pagination) {
            // Always initialize pagination if we have pagination data, even if only one page
            // This ensures the pagination info is displayed
            initializeFilePaginationControls();
        }
    });
}
