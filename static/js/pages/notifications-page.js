/**
 * Notifications Page JavaScript
 * Extracted from notifications.html
 */

// This file will be populated with the notifications page JavaScript
// The full implementation is quite large, so we'll extract it systematically

const notificationsPage = {
    sources: {},
    sides: {},
    currentTab: 'all',
    currentSubTab: {
        all: 'all',
        duplicates: 'all',
        future: 'all'
    },
    filters: {
        all: { source: '', side: '', search: '', sort_by: 'created_at', sort_order: 'desc', page: 1 },
        duplicates: { source: '', side: '', search: '', sort_by: 'created_at', sort_order: 'desc', page: 1 },
        future: { source: '', side: '', search: '', sort_by: 'created_at', sort_order: 'desc', page: 1 }
    },
    searchTimeouts: {},
    currentNotificationId: null
};

// Track if already initialized to prevent double initialization
let initialized = false;

// Initialize page function
function initializeNotificationsPage() {
    // Prevent double initialization
    if (initialized) {
        return;
    }
    initialized = true;
    
    const pageDataEl = document.getElementById('notifications-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            notificationsPage.sources = data.sources || {};
            notificationsPage.sides = data.sides || {};
        } catch (e) {
            console.error('Error parsing notifications page data:', e);
        }
    }
    
    notificationsPage.loadTabData('all');
    notificationsPage.updateNotificationBadge();
    setInterval(() => notificationsPage.updateNotificationBadge(), 30000);
}

// Export default init function for universal-initializer.js
// This matches the pattern used in email-words-page.js
export default function init() {
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeNotificationsPage);
    } else {
        initializeNotificationsPage();
    }
}

// Export functions to window for onclick handlers
window.switchTab = function(tab) {
    notificationsPage.switchTab(tab);
};

// Scan for duplicates and future dates
window.scanForNotifications = async function() {
    const scanBtn = document.getElementById('scanBtn');
    const scanBtnText = document.getElementById('scanBtnText');
    const scanSpinner = document.getElementById('scanSpinner');
    
    if (!scanBtn || scanBtn.disabled) return;
    
    // Disable button and show spinner
    scanBtn.disabled = true;
    scanBtn.classList.add('disabled');
    scanSpinner.classList.remove('d-none');
    scanBtnText.textContent = 'Scanning...';
    
    try {
        const response = await fetch('/api/notifications/scan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
            }
        });
        
        const data = await response.json();
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        if (data.success) {
            // Show success message with details
            const duplicatesMsg = data.duplicates_found > 0 ? `${data.duplicates_found} duplicate file(s)` : '';
            const futureMsg = data.future_dates_found > 0 ? `${data.future_dates_found} future date(s)` : '';
            const createdMsg = data.notifications_created?.length > 0 ? `${data.notifications_created.length} notification(s) created` : 'No new notifications created';
            
            let messageParts = [];
            if (duplicatesMsg) messageParts.push(duplicatesMsg);
            if (futureMsg) messageParts.push(futureMsg);
            
            // Build message
            let message;
            if (messageParts.length > 0) {
                message = `Scan completed! Found ${messageParts.join(' and ')}. ${createdMsg}.`;
            } else {
                message = `Scan completed! ${createdMsg}.`;
            }
            
            // Use message system if available
            if (window.showMessage) {
                window.showMessage(message, 'success');
            } else {
                alert(message);
            }
            
            // Refresh notifications after a short delay to allow backend to finish
            setTimeout(() => {
                // Force reload all tabs to ensure notifications are visible
                notificationsPage.loadTabData('all');
                notificationsPage.loadTabData('duplicates');
                notificationsPage.loadTabData('future');
                notificationsPage.updateNotificationBadge();
                
                // Switch to appropriate tab based on results
                if (data.duplicates_found > 0) {
                    notificationsPage.switchTab('duplicates');
                } else if (data.future_dates_found > 0) {
                    notificationsPage.switchTab('future');
                } else {
                    // If no specific results, stay on current tab but refresh
                    notificationsPage.loadTabData(notificationsPage.currentTab);
                }
            }, 1000); // Increased delay to ensure backend has finished processing
        } else {
            throw new Error(data.error || data.message || 'Scan failed');
        }
    } catch (error) {
        console.error('Error scanning for notifications:', error);
        const errorMsg = `Error scanning: ${error.message}`;
        
        if (window.showMessage) {
            window.showMessage(errorMsg, 'error');
        } else {
            alert(errorMsg);
        }
    } finally {
        // Re-enable button
        scanBtn.disabled = false;
        scanBtn.classList.remove('disabled');
        scanSpinner.classList.add('d-none');
        scanBtnText.textContent = 'Scan for Duplicates & Future Dates';
    }
};

window.switchSubTab = function(tab, subTab, loadData = true) {
    notificationsPage.switchSubTab(tab, subTab, loadData);
};

window.setFilter = function(tab, filterType, value) {
    notificationsPage.setFilter(tab, filterType, value);
};

window.handleSearch = function(event, tab) {
    notificationsPage.handleSearch(event, tab);
};

window.refreshAll = function() {
    notificationsPage.refreshAll();
};

window.setSort = function(tab, sortValue) {
    notificationsPage.setSort(tab, sortValue);
};

window.changePage = function(tab, page) {
    notificationsPage.changePage(tab, page);
};

window.markAsRead = function(notificationId, reload = true) {
    notificationsPage.markAsRead(notificationId, reload);
};

window.markAllAsRead = function() {
    notificationsPage.markAllAsRead();
};

window.dismissNotification = function(notificationId) {
    notificationsPage.dismissNotification(notificationId);
};

window.openMessageDetail = function(notificationId) {
    notificationsPage.openMessageDetail(notificationId);
};

window.closeMessageDetail = function(event) {
    notificationsPage.closeMessageDetail(event);
};

window.markAsReadFromDetail = function() {
    notificationsPage.markAsReadFromDetail();
};

window.dismissNotificationFromDetail = function() {
    notificationsPage.dismissNotificationFromDetail();
};

// Implementation methods
notificationsPage.switchTab = function(tab) {
    this.currentTab = tab;
    
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.tab === tab) {
            btn.classList.add('active');
        }
    });
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    const tabContent = document.getElementById(`tab-${tab}`);
    if (tabContent) {
        tabContent.classList.add('active');
    }
    
    // Show the active sub-tab for this tab
    this.switchSubTab(tab, this.currentSubTab[tab], false);
    
    // Load data for this tab
    this.loadTabData(tab);
};

notificationsPage.switchSubTab = function(tab, subTab, loadData = true) {
    this.currentSubTab[tab] = subTab;
    
    // Update sub-tab buttons for this main tab
    const tabContent = document.getElementById(`tab-${tab}`);
    if (tabContent) {
        tabContent.querySelectorAll('.sub-tab-btn').forEach(btn => {
            btn.classList.remove('active');
            if (btn.dataset.subTab === subTab) {
                btn.classList.add('active');
            }
        });
        
        // Update sub-tab content
        tabContent.querySelectorAll('.sub-tab-content').forEach(content => {
            content.classList.remove('active');
        });
        const subTabContent = document.getElementById(`sub-tab-${tab}-${subTab}`);
        if (subTabContent) {
            subTabContent.classList.add('active');
        }
    }
    
    // Load data if requested
    if (loadData) {
        this.loadTabData(tab);
    }
};

notificationsPage.setFilter = function(tab, filterType, value) {
    this.filters[tab][filterType] = value;
    this.filters[tab].page = 1;
    this.loadTabData(tab);
};

notificationsPage.handleSearch = function(event, tab) {
    if (event.key === 'Enter' || event.keyCode === 13) {
        this.filters[tab].search = event.target.value.trim();
        this.filters[tab].page = 1;
        this.loadTabData(tab);
        return;
    }
    
    clearTimeout(this.searchTimeouts[tab]);
    this.searchTimeouts[tab] = setTimeout(() => {
        this.filters[tab].search = event.target.value.trim();
        this.filters[tab].page = 1;
        this.loadTabData(tab);
    }, 500);
};

notificationsPage.refreshAll = function() {
    this.loadTabData(this.currentTab);
};

notificationsPage.setSort = function(tab, sortValue) {
    let sortBy, sortOrder;
    if (sortValue.startsWith('created_at_')) {
        sortBy = 'created_at';
        sortOrder = sortValue.replace('created_at_', '');
    } else if (sortValue.startsWith('priority_')) {
        sortBy = 'priority';
        sortOrder = sortValue.replace('priority_', '');
    } else if (sortValue.startsWith('title_')) {
        sortBy = 'title';
        sortOrder = sortValue.replace('title_', '');
    } else {
        const parts = sortValue.split('_');
        sortBy = parts[0];
        sortOrder = parts[parts.length - 1];
    }
    
    this.filters[tab].sort_by = sortBy;
    this.filters[tab].sort_order = sortOrder;
    this.filters[tab].page = 1;
    this.loadTabData(tab);
};

notificationsPage.loadTabData = async function(tab) {
    const allListId = `messages${tab.charAt(0).toUpperCase() + tab.slice(1)}All`;
    const unreadListId = `messages${tab.charAt(0).toUpperCase() + tab.slice(1)}Unread`;
    const readListId = `messages${tab.charAt(0).toUpperCase() + tab.slice(1)}Read`;
    const allList = document.getElementById(allListId);
    const unreadList = document.getElementById(unreadListId);
    const readList = document.getElementById(readListId);
    const loadingText = window.appTranslations?.['Loading...'] || 'Loading...';
    
    if (allList) allList.innerHTML = `<div class="loading-spinner"><i class="bi bi-arrow-repeat"></i><p>${loadingText}</p></div>`;
    if (unreadList) unreadList.innerHTML = `<div class="loading-spinner"><i class="bi bi-arrow-repeat"></i><p>${loadingText}</p></div>`;
    if (readList) readList.innerHTML = `<div class="loading-spinner"><i class="bi bi-arrow-repeat"></i><p>${loadingText}</p></div>`;
    
    try {
        const params = new URLSearchParams({
            page: this.filters[tab].page,
            per_page: 1000,
            search: this.filters[tab].search || '',
            show_read: 'true',
            sort_by: this.filters[tab].sort_by || 'created_at',
            sort_order: this.filters[tab].sort_order || 'desc'
        });
        
        // Tab-specific filters
        if (tab === 'all') {
            if (this.filters[tab].source) params.append('source_id', this.filters[tab].source);
            if (this.filters[tab].side) params.append('side_id', this.filters[tab].side);
        } else if (tab === 'duplicates') {
            params.append('type', 'similar_files');
            if (this.filters[tab].source) params.append('source_id', this.filters[tab].source);
            if (this.filters[tab].side) params.append('side_id', this.filters[tab].side);
        } else if (tab === 'future') {
            params.append('type', 'future_date,future_event');
            if (this.filters[tab].source) params.append('source_id', this.filters[tab].source);
            if (this.filters[tab].side) params.append('side_id', this.filters[tab].side);
        }
        
        const response = await fetch(`/api/notifications/paginated?${params}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.success) {
            // Debug logging
            console.log(`Loaded ${data.notifications?.length || 0} notifications for tab: ${tab}`);
            
            let allNotifications = data.notifications || [];
            let unreadNotifications = allNotifications.filter(n => !n.read);
            let readNotifications = allNotifications.filter(n => n.read);
            
            allNotifications = this.sortNotifications(allNotifications, this.filters[tab].sort_by, this.filters[tab].sort_order);
            unreadNotifications = this.sortNotifications(unreadNotifications, this.filters[tab].sort_by, this.filters[tab].sort_order);
            readNotifications = this.sortNotifications(readNotifications, this.filters[tab].sort_by, this.filters[tab].sort_order);
            
            this.renderNotifications(allNotifications, tab, 'all');
            this.renderNotifications(unreadNotifications, tab, 'unread');
            this.renderNotifications(readNotifications, tab, 'read');
            this.updateCategoryStats(tab, data.notifications);
            
            const sortSelect = document.getElementById(`sortBy${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
            if (sortSelect) {
                const sortValue = `${this.filters[tab].sort_by}_${this.filters[tab].sort_order}`;
                sortSelect.value = sortValue;
            }
        } else {
            const errorMsg = window.appTranslations?.['Error loading notifications'] || 'Error loading notifications';
            if (allList) allList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${data.error || errorMsg}</p></div>`;
            if (unreadList) unreadList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${data.error || errorMsg}</p></div>`;
            if (readList) readList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${data.error || errorMsg}</p></div>`;
        }
    } catch (error) {
        console.error('Error loading notifications:', error);
        const errorMsg = window.appTranslations?.['Error loading notifications'] || 'Error loading notifications';
        if (allList) allList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${errorMsg}</p></div>`;
        if (unreadList) unreadList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${errorMsg}</p></div>`;
        if (readList) readList.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle"></i><h3>Error</h3><p>${errorMsg}</p></div>`;
    }
};

notificationsPage.renderNotifications = function(notifications, tab, section) {
    const listId = `messages${tab.charAt(0).toUpperCase() + tab.slice(1)}${section.charAt(0).toUpperCase() + section.slice(1)}`;
    const messagesList = document.getElementById(listId);
    const countId = `${section}Count${tab.charAt(0).toUpperCase() + tab.slice(1)}`;
    const countElement = document.getElementById(countId);
    const subTabCountElement = document.querySelector(`#tab-${tab} .sub-tab-btn[data-sub-tab="${section}"] .sub-tab-count`);
    
    const markReadTitle = window.appTranslations?.['Mark as Read'] || 'Mark as Read';
    const dismissTitle = window.appTranslations?.['Dismiss'] || 'Dismiss';
    
    if (countElement) {
        countElement.textContent = notifications.length;
    }
    if (subTabCountElement) {
        subTabCountElement.textContent = notifications.length;
    }
    
    if (notifications.length === 0) {
        const noNotifications = window.appTranslations?.['No Notifications'] || 'No Notifications';
        const noNotificationsText = window.appTranslations?.['No notifications found matching your criteria.'] || 'No notifications found matching your criteria.';
        if (messagesList) {
            messagesList.innerHTML = `
                <div class="empty-state">
                    <i class="bi bi-bell-slash"></i>
                    <h3>${noNotifications}</h3>
                    <p>${noNotificationsText}</p>
                </div>
            `;
        }
        return;
    }
    
    if (messagesList) {
        messagesList.innerHTML = notifications.map(n => {
            const timeAgo = this.formatTime(n.created_at);
            const typeIcon = this.getTypeIcon(n.type);
            const typeLabel = n.type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            const priorityLabel = n.priority.charAt(0).toUpperCase() + n.priority.slice(1);
            const preview = n.message.length > 150 ? n.message.substring(0, 150) + '...' : n.message;
            const unreadClass = n.read ? '' : 'unread';
            
            return `
                <div class="message-card ${unreadClass}" onclick="openMessageDetail(${n.id})" data-id="${n.id}" data-type="${n.type}">
                    <div class="message-header">
                        <div class="message-avatar priority-${n.priority}">
                            ${typeIcon}
                        </div>
                        <div class="message-info">
                            <div class="message-title-row">
                                <h3 class="message-title">${this.escapeHtml(n.title)}</h3>
                                <span class="message-time">${timeAgo}</span>
                            </div>
                            <div class="message-badges">
                                <span class="type-badge">${typeLabel}</span>
                                <span class="priority-badge priority-${n.priority}">${priorityLabel}</span>
                            </div>
                        </div>
                    </div>
                    <div class="message-content">
                        ${this.escapeHtml(preview)}
                    </div>
                    <div class="message-footer">
                        <div class="message-meta">
                            ${n.file_name ? `<span><i class="bi bi-file-earmark me-1"></i>${this.escapeHtml(n.file_name.length > 30 ? n.file_name.substring(0, 30) + '...' : n.file_name)}</span>` : ''}
                            ${n.event_date ? `<span><i class="bi bi-calendar me-1"></i>${new Date(n.event_date).toLocaleDateString()}</span>` : ''}
                        </div>
                        <div class="message-actions">
                            <button class="message-action-btn" onclick="event.stopPropagation(); markAsRead(${n.id})" title="${markReadTitle}">
                                <i class="bi bi-check"></i>
                            </button>
                            <button class="message-action-btn danger" onclick="event.stopPropagation(); dismissNotification(${n.id})" title="${dismissTitle}">
                                <i class="bi bi-x"></i>
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }
};

notificationsPage.sortNotifications = function(notifications, sortBy, sortOrder) {
    const reverse = sortOrder === 'desc';
    const sorted = [...notifications];
    
    if (sortBy === 'priority') {
        const priorityOrder = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1};
        sorted.sort((a, b) => {
            const aPriority = priorityOrder[a.priority] || 0;
            const bPriority = priorityOrder[b.priority] || 0;
            return reverse ? bPriority - aPriority : aPriority - bPriority;
        });
    } else if (sortBy === 'title') {
        sorted.sort((a, b) => {
            const aTitle = (a.title || '').toLowerCase();
            const bTitle = (b.title || '').toLowerCase();
            if (reverse) return bTitle.localeCompare(aTitle);
            return aTitle.localeCompare(bTitle);
        });
    } else {
        sorted.sort((a, b) => {
            const aDate = new Date(a.created_at);
            const bDate = new Date(b.created_at);
            return reverse ? bDate - aDate : aDate - bDate;
        });
    }
    
    return sorted;
};

notificationsPage.updateCategoryStats = function(tab, notifications) {
    const total = notifications.length;
    const unread = notifications.filter(n => !n.read).length;
    const read = notifications.filter(n => n.read).length;
    
    if (tab === 'all') {
        const statAllTotal = document.getElementById('statAllTotal');
        const statAllUnread = document.getElementById('statAllUnread');
        const statAllRead = document.getElementById('statAllRead');
        
        if (statAllTotal) statAllTotal.textContent = total;
        if (statAllUnread) statAllUnread.textContent = unread;
        if (statAllRead) statAllRead.textContent = read;
    } else if (tab === 'duplicates') {
        const statDuplicatesTotal = document.getElementById('statDuplicatesTotal');
        const statDuplicatesUnread = document.getElementById('statDuplicatesUnread');
        const statDuplicatesRead = document.getElementById('statDuplicatesRead');
        
        if (statDuplicatesTotal) statDuplicatesTotal.textContent = total;
        if (statDuplicatesUnread) statDuplicatesUnread.textContent = unread;
        if (statDuplicatesRead) statDuplicatesRead.textContent = read;
    } else if (tab === 'future') {
        const statFutureTotal = document.getElementById('statFutureTotal');
        const statFutureUnread = document.getElementById('statFutureUnread');
        const statFutureRead = document.getElementById('statFutureRead');
        
        if (statFutureTotal) statFutureTotal.textContent = total;
        if (statFutureUnread) statFutureUnread.textContent = unread;
        if (statFutureRead) statFutureRead.textContent = read;
    }
};

notificationsPage.getTypeIcon = function(type) {
    const icons = {
        'similar_files': '<i class="bi bi-files" aria-hidden="true"></i>',
        'future_date': '<i class="bi bi-calendar-event" aria-hidden="true"></i>',
        'future_event': '<i class="bi bi-calendar2-week" aria-hidden="true"></i>',
        'processing_complete': '<i class="bi bi-check-circle-fill" aria-hidden="true"></i>',
        'batch_complete': '<i class="bi bi-box-seam" aria-hidden="true"></i>',
        'error': '<i class="bi bi-x-octagon-fill" aria-hidden="true"></i>',
        'warning': '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i>',
        'info': '<i class="bi bi-info-circle-fill" aria-hidden="true"></i>'
    };
    return icons[type] || '<i class="bi bi-bell-fill" aria-hidden="true"></i>';
};

notificationsPage.formatTime = function(dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    const justNow = window.appTranslations?.['Just now'] || 'Just now';
    const mAgo = window.appTranslations?.['m ago'] || 'm ago';
    const hAgo = window.appTranslations?.['h ago'] || 'h ago';
    const dAgo = window.appTranslations?.['d ago'] || 'd ago';
    
    if (minutes < 1) return justNow;
    if (minutes < 60) return `${minutes} ${mAgo}`;
    if (hours < 24) return `${hours} ${hAgo}`;
    if (days < 7) return `${days} ${dAgo}`;
    return date.toLocaleDateString();
};

notificationsPage.escapeHtml = function(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};

notificationsPage.markAsRead = async function(notificationId, reload = true) {
    try {
        const response = await fetch(`/api/notifications/${notificationId}/read`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || ''
            }
        });
        
        const data = await response.json();
        if (data.success) {
            this.loadTabData(this.currentTab);
            this.updateNotificationBadge();
        }
    } catch (error) {
        console.error('Error marking as read:', error);
    }
};

notificationsPage.markAllAsRead = async function() {
    const confirmMsg = window.appTranslations?.['Mark all notifications as read?'] || 'Mark all notifications as read?';
    if (!confirm(confirmMsg)) {
        return;
    }
    
    try {
        const cards = document.querySelectorAll('.message-card[data-id]');
        const promises = Array.from(cards).map(card => {
            const id = card.dataset.id;
            return fetch(`/api/notifications/${id}/read`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || ''
                }
            });
        });
        
        await Promise.all(promises);
        this.loadTabData(this.currentTab);
        this.updateNotificationBadge();
    } catch (error) {
        console.error('Error marking all as read:', error);
    }
};

notificationsPage.dismissNotification = async function(notificationId) {
    const confirmMsg = window.appTranslations?.['Dismiss this notification?'] || 'Dismiss this notification?';
    if (!confirm(confirmMsg)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/notifications/${notificationId}/dismiss`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || ''
            }
        });
        
        const data = await response.json();
        if (data.success) {
            this.loadTabData(this.currentTab);
            this.updateNotificationBadge();
        }
    } catch (error) {
        console.error('Error dismissing notification:', error);
    }
};

notificationsPage.updateNotificationBadge = async function() {
    try {
        const response = await fetch('/api/notifications/stats');
        const data = await response.json();
        
        if (data.success && data.stats) {
            const badge = document.getElementById('notificationsBadge');
            if (badge) {
                const unreadCount = data.stats.unread || 0;
                if (unreadCount > 0) {
                    badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
                    badge.style.display = 'block';
                } else {
                    badge.style.display = 'none';
                }
            }
        }
    } catch (error) {
        console.error('Error updating notification badge:', error);
    }
};

notificationsPage.openMessageDetail = function(notificationId) {
    this.currentNotificationId = notificationId;
    const modal = document.getElementById('messageDetailModal');
    const detailBody = document.getElementById('detailBody');
    const detailTitle = document.getElementById('detailTitle');
    
    fetch(`/api/notifications/${notificationId}`)
        .then(res => res.json())
        .then(data => {
            if (data.success && data.notification) {
                const n = data.notification;
                const typeValue = n.type.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                const priorityValue = n.priority.charAt(0).toUpperCase() + n.priority.slice(1);
                const createdDate = new Date(n.created_at).toLocaleString();
                const eventDate = n.event_date ? new Date(n.event_date).toLocaleDateString() : null;
                
                const sourceName = n.metadata?.source_id ? (this.sources[n.metadata.source_id] || `Source ${n.metadata.source_id}`) : null;
                const sideName = n.metadata?.side_id ? (this.sides[n.metadata.side_id] || `Side ${n.metadata.side_id}`) : null;
                
                if (detailTitle) detailTitle.textContent = n.title;
                
                const msgLabel = window.appTranslations?.['Message'] || 'Message';
                const typeLabelText = window.appTranslations?.['Type'] || 'Type';
                const priorityLabelText = window.appTranslations?.['Priority'] || 'Priority';
                const createdLabel = window.appTranslations?.['Created'] || 'Created';
                const eventDateLabel = window.appTranslations?.['Event Date'] || 'Event Date';
                const fileLabel = window.appTranslations?.['File'] || 'File';
                const sourceLabel = window.appTranslations?.['Source'] || 'Source';
                const sideLabel = window.appTranslations?.['Side'] || 'Side';
                const filePathLabel = window.appTranslations?.['File Path'] || 'File Path';
                const additionalInfoLabel = window.appTranslations?.['Additional Information'] || 'Additional Information';
                
                let detailHtml = `
                    <div class="message-detail-section">
                        <h4>${msgLabel}</h4>
                        <p>${this.escapeHtml(n.message)}</p>
                    </div>
                    
                    <div class="message-detail-info-grid">
                        <div class="message-detail-info-item">
                            <strong>${typeLabelText}</strong>
                            <span>${typeValue}</span>
                        </div>
                        <div class="message-detail-info-item">
                            <strong>${priorityLabelText}</strong>
                            <span class="priority-badge priority-${n.priority}">${priorityValue}</span>
                        </div>
                        <div class="message-detail-info-item">
                            <strong>${createdLabel}</strong>
                            <span>${createdDate}</span>
                        </div>
                `;
                
                if (eventDate) {
                    detailHtml += `
                        <div class="message-detail-info-item">
                            <strong>${eventDateLabel}</strong>
                            <span>${eventDate}</span>
                        </div>
                    `;
                }
                
                if (n.file_name) {
                    detailHtml += `
                        <div class="message-detail-info-item">
                            <strong>${fileLabel}</strong>
                            <span>${n.file_id ? `<a href="/file/${n.file_id}">${this.escapeHtml(n.file_name)}</a>` : this.escapeHtml(n.file_name)}</span>
                        </div>
                    `;
                }
                
                if (sourceName) {
                    detailHtml += `
                        <div class="message-detail-info-item">
                            <strong>${sourceLabel}</strong>
                            <span>${this.escapeHtml(sourceName)}</span>
                        </div>
                    `;
                }
                
                if (sideName) {
                    detailHtml += `
                        <div class="message-detail-info-item">
                            <strong>${sideLabel}</strong>
                            <span>${this.escapeHtml(sideName)}</span>
                        </div>
                    `;
                }
                
                if (n.file_path) {
                    detailHtml += `
                        <div class="message-detail-info-item" style="grid-column: 1 / -1;">
                            <strong>${filePathLabel}</strong>
                            <span style="word-break: break-all;">${this.escapeHtml(n.file_path)}</span>
                        </div>
                    `;
                }
                
                detailHtml += `</div>`;
                
                if (n.metadata && Object.keys(n.metadata).length > 0) {
                    detailHtml += `
                        <div class="message-detail-section">
                            <h4>${additionalInfoLabel}</h4>
                            <div style="background: var(--bg-section, #f8f9fa); padding: 1rem; border-radius: 8px; font-family: monospace; font-size: 0.875rem; overflow-x: auto;">
                                <pre style="margin: 0; white-space: pre-wrap;">${this.escapeHtml(JSON.stringify(n.metadata, null, 2))}</pre>
                            </div>
                        </div>
                    `;
                }
                
                if (detailBody) detailBody.innerHTML = detailHtml;
                if (modal) modal.classList.add('active');
                
                this.markAsRead(notificationId, false);
            }
        })
        .catch(error => {
            console.error('Error loading notification details:', error);
            if (detailBody) detailBody.innerHTML = `<div class="empty-state"><p>Error loading notification details</p></div>`;
            if (modal) modal.classList.add('active');
        });
};

notificationsPage.closeMessageDetail = function(event) {
    if (event && event.target !== event.currentTarget) return;
    const modal = document.getElementById('messageDetailModal');
    if (modal) modal.classList.remove('active');
    this.currentNotificationId = null;
};

notificationsPage.markAsReadFromDetail = function() {
    if (this.currentNotificationId) {
        this.markAsRead(this.currentNotificationId, true);
        this.closeMessageDetail();
    }
};

notificationsPage.dismissNotificationFromDetail = function() {
    if (this.currentNotificationId) {
        this.dismissNotification(this.currentNotificationId);
        this.closeMessageDetail();
    }
};

// Keyboard shortcut for closing modal
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        notificationsPage.closeMessageDetail();
    }
});

