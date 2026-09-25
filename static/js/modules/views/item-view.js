/**
 * Item View Loader
 * Extracted from the legacy file-management-system.js
 */

import { navigationState } from '../core/state.js';
import { sectionLabels } from '../core/config.js';
import { endpoints } from '../api/endpoints.js';
import { apiGet } from '../api/api-client.js';
import { renderFilesView } from './file-view.js';
import { translations } from '../core/config.js';
import { loadSourceCategoriesKeywordsView } from './source-categories-keywords-view.js';

export async function loadItemView(section, itemId, itemName = null, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    
    // For geolocation, each item is a file - open file modal directly
    if (section === 'geolocation') {
        if (window.fms?.fileOperations?.fileDetails?.showFileDetails) {
            window.fms.fileOperations.fileDetails.showFileDetails(itemId, itemName || 'File');
        } else if (window.showFileDetails) {
            window.showFileDetails(itemId, itemName || 'File');
        }
        return;
    }
    
    // For sources and sides, show categories and keywords first instead of files
    if (section === 'sources' || section === 'sides') {
        await loadSourceCategoriesKeywordsView(section, itemId, itemName, page);
        return;
    }
    
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;

    try {
        // Use /api/archives/files endpoint with proper parameters
        const perPage = navigationState.filePagination?.perPage || 50;
        const apiUrl = endpoints.itemFiles(section, itemId, { page, limit: perPage });
        console.log('Loading files from:', apiUrl);
        
        const filesData = await apiGet(apiUrl);
        console.log('Files data received:', filesData);
        // apiGet already throws if filesData.success === false

        const state = { type: 'item', section, itemId, itemName };
        navigationState.currentFileSection = section;
        navigationState.currentFileItemId = itemId;

        // Update breadcrumb via navigator
        if (window.fms?.navigation?.navigateToItem) {
            // Navigation already updates breadcrumb
        }

        // API returns pagination object directly
        renderFilesView(filesData.files || [], section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
    } catch (error) {
        console.error('Error loading files:', error);
        console.error('Error details:', { section, itemId, itemName, page });
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
    }
}

export async function loadGroupedTitleFiles(groupId, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;

    try {
        const apiUrl = `/api/archives/titles/grouped/${groupId}?page=${page}`;
        const data = await apiGet(apiUrl);
        // apiGet already throws if data.success === false
        renderFilesView(data.files || [], 'titles', translations.titles || 'Titles', data.pagination);
    } catch (error) {
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
    }
}

/**
 * Load files with combined filters (e.g., source + keyword, source + category)
 */
export async function loadItemFilesWithFilters(section, itemId, itemName = null, sourceFilter = null, sideFilter = null, page = 1) {
    const contentView = document.getElementById('unifiedContentView');
    if (!contentView) return;
    contentView.innerHTML = `<div class="content-loading"><i class="bi bi-arrow-repeat"></i><div>${translations.loadingFiles || 'Loading files...'}</div></div>`;

    try {
        const perPage = navigationState.filePagination?.perPage || 50;
        const apiUrl = endpoints.itemFilesWithFilters(section, itemId, { 
            page, 
            limit: perPage,
            source_id: sourceFilter?.source_id,
            side_id: sideFilter?.side_id
        });
        console.log('Loading files with filters from:', apiUrl);
        
        const filesData = await apiGet(apiUrl);
        console.log('Files data received:', filesData);
        
        if (!filesData.success) {
            throw new Error(filesData.error || 'Failed to load files');
        }

        const state = { type: 'item', section, itemId, itemName, sourceFilter, sideFilter };
        navigationState.currentFileSection = section;
        navigationState.currentFileItemId = itemId;
        navigationState.currentSourceFilter = sourceFilter;
        navigationState.currentSideFilter = sideFilter;

        // Update breadcrumb to show the full path
        if (window.fms?.navigation?.breadcrumb?.updateBreadcrumb) {
            const breadcrumbPath = [
                { name: translations.home || 'Home', state: { type: 'root' } }
            ];
            
            // Add source/side to breadcrumb if we have a filter
            if (sourceFilter?.source_id && navigationState.currentSourceItemName) {
                breadcrumbPath.push({
                    name: sectionLabels.sources || 'Sources',
                    state: { type: 'section', section: 'sources' }
                });
                breadcrumbPath.push({
                    name: navigationState.currentSourceItemName,
                    state: { type: 'item', section: 'sources', itemId: sourceFilter.source_id, itemName: navigationState.currentSourceItemName }
                });
            } else if (sideFilter?.side_id && navigationState.currentSourceItemName) {
                breadcrumbPath.push({
                    name: sectionLabels.sides || 'Sides',
                    state: { type: 'section', section: 'sides' }
                });
                breadcrumbPath.push({
                    name: navigationState.currentSourceItemName,
                    state: { type: 'item', section: 'sides', itemId: sideFilter.side_id, itemName: navigationState.currentSourceItemName }
                });
            } else {
                breadcrumbPath.push({
                    name: sectionLabels[section] || section,
                    state: { type: 'section', section: section }
                });
            }
            
            // Add current category/keyword
            breadcrumbPath.push({
                name: itemName || sectionLabels[section] || 'Item',
                state: state
            });
            
            window.fms.navigation.breadcrumb.updateBreadcrumb(breadcrumbPath);
        }

        // Add to history
        if (window.fms?.navigation?.history?.addToHistory) {
            window.fms.navigation.history.addToHistory(state);
        }

        // API returns pagination object directly
        renderFilesView(filesData.files || [], section, itemName || sectionLabels[section] || 'Item', filesData.pagination);
    } catch (error) {
        console.error('Error loading files:', error);
        console.error('Error details:', { section, itemId, itemName, page, sourceFilter, sideFilter });
        contentView.innerHTML = `<div class="empty-state">${translations.errorLoadingFiles || 'Error loading files'}: ${error.message}</div>`;
    }
}

// Support pagination controls callbacks
export async function loadFilePage(page) {
    const { currentFileSection, currentFileItemId, currentSourceFilter, currentSideFilter } = navigationState;
    if (!currentFileSection || !currentFileItemId) {
        console.warn('Cannot load file page: missing section or item ID');
        return;
    }
    
    // Get current state from history to preserve item name
    const currentState = navigationState.history?.[navigationState.currentIndex];
    const itemName = currentState?.itemName || null;
    
    // If we have filters, use the filtered version
    if (currentSourceFilter || currentSideFilter) {
        await loadItemFilesWithFilters(currentFileSection, currentFileItemId, itemName, currentSourceFilter, currentSideFilter, page);
    } else {
        await loadItemView(currentFileSection, currentFileItemId, itemName, page);
    }
}

// Expose for pagination onclicks
if (typeof window !== 'undefined') {
    window.loadFilePage = loadFilePage;
}

