/**
 * Dashboard Page JavaScript
 * Extracted from Analysis/dashboard.html
 */

import {
    startProcessingProgressPolling as startSharedProgressPolling,
    stopProcessingProgressPolling as stopSharedProgressPolling,
    getProcessingProgressTracker as getSharedProgressTracker,
} from '../modules/ui/progress-tracker.js';

// Load translations from JSON script tag
let translations = {};
document.addEventListener('DOMContentLoaded', function() {
    const pageDataEl = document.getElementById('dashboard-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing dashboard page data:', e);
        }
    }
    
    console.log('Dashboard page loaded');
});

let charts = {};
let storageData = null;
let currentStorageView = 'type';

// Enhanced chart configuration for prominent, eye-catching charts
const CHART_CONFIG = {
    layout: {
        padding: {
            top: 30,
            right: 30,
            bottom: 40,
            left: 30
        }
    },
    plugins: {
        legend: {
            labels: {
                font: {
                    size: 16,
                    weight: '600'
                },
                padding: 25,
                boxWidth: 20,
                boxHeight: 20,
                usePointStyle: true
            }
        },
        tooltip: {
            padding: 12,
            titleFont: {
                size: 14,
                weight: '600'
            },
            bodyFont: {
                size: 13,
                weight: '500'
            },
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            titleColor: '#fff',
            bodyColor: '#fff',
            borderColor: (window.ChartColors && window.ChartColors.getThemeColors().primary) ? window.ChartColors.getThemeColors().primary : '#667eea',
            borderWidth: 2,
            cornerRadius: 8
        }
    },
        scales: {
            x: {
                ticks: {
                    font: {
                        size: 13,
                        weight: '600'
                    },
                    padding: 12
                },
                title: {
                    display: true,
                    font: {
                        size: 16,
                        weight: '700'
                    },
                    padding: {
                        top: 15
                    }
                }
            },
            y: {
                ticks: {
                    font: {
                        size: 13,
                        weight: '600'
                    },
                    padding: 12
                },
                title: {
                    display: true,
                    font: {
                        size: 16,
                        weight: '700'
                    },
                    padding: {
                        bottom: 15
                    }
                }
            }
        }
};

// Initialize dashboard - storage stats will load when storage tab is activated

// Load all statistics
async function loadAllStatistics() {
    await Promise.all([
        loadStorageStatistics(),
        loadProcessingStatistics(),
        loadContentStatistics()
    ]);
}

// Load storage statistics
async function loadStorageStatistics() {
    try {
        const response = await fetch('/api/analytics/storage-stats');
        const data = await response.json();
        storageData = data;
        
        // Update summary cards only if they exist (they're in the main dashboard)
        const totalFilesEl = document.getElementById('totalFiles');
        if (totalFilesEl) {
            totalFilesEl.textContent = data.total.files.toLocaleString();
        }
        
        // Render storage charts
        updateStorageView('type', null);
        renderStorageTimeline(data.timeline);
        renderLargestFiles(data.largest_files);
        
    } catch (error) {
        console.error('Error loading storage statistics:', error);
    }
}

// Update storage view
function updateStorageView(view, clickedButton) {
    currentStorageView = view;
    
    // Update active button - use clickedButton parameter if provided, otherwise use event
    const button = clickedButton || (typeof event !== 'undefined' && event.target ? event.target : null);
    document.querySelectorAll('.section-card .btn-control').forEach(btn => {
        btn.classList.remove('active');
    });
    if (clickedButton) {
        clickedButton.classList.add('active');
    } else {
        // Find button with matching onclick
        document.querySelectorAll('.section-card .btn-control').forEach(btn => {
            if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes(`'${view}'`)) {
                btn.classList.add('active');
            }
        });
    }
    
    if (!storageData) return;
    
    let chartData, detailsHtml;
    
    if (view === 'type') {
        chartData = {
            labels: storageData.by_type.map(t => t.type),
            data: storageData.by_type.map(t => t.total_size)
        };
        
        detailsHtml = storageData.by_type.map(t => `
            <div style="padding: 0.75rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between;">
                <span><strong>${t.type}</strong> (${t.count} ${translations.files})</span>
                <span>${formatFileSize(t.total_size)}</span>
            </div>
        `).join('');
        
    } else if (view === 'status') {
        chartData = {
            labels: storageData.by_status.map(s => s.status),
            data: storageData.by_status.map(s => s.total_size)
        };
        
        detailsHtml = storageData.by_status.map(s => `
            <div style="padding: 0.75rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between;">
                <span><strong>${s.status}</strong> (${s.count} ${translations.files})</span>
                <span>${formatFileSize(s.total_size)}</span>
            </div>
        `).join('');
        
    } else { // size
        const sizeCategories = [
            { name: 'Tiny (<100KB)', min: 0, max: 100 * 1024 },
            { name: 'Small (100KB-1MB)', min: 100 * 1024, max: 1024 * 1024 },
            { name: 'Medium (1-10MB)', min: 1024 * 1024, max: 10 * 1024 * 1024 },
            { name: 'Large (10-100MB)', min: 10 * 1024 * 1024, max: 100 * 1024 * 1024 },
            { name: 'Huge (>100MB)', min: 100 * 1024 * 1024, max: Infinity }
        ];
        
        // Calculate from by_type data
        const sizeData = sizeCategories.map(cat => ({
            name: cat.name,
            size: storageData.by_type.reduce((sum, t) => {
                if (t.avg_size >= cat.min && t.avg_size < cat.max) {
                    return sum + t.total_size;
                }
                return sum;
            }, 0)
        }));
        
        chartData = {
            labels: sizeData.map(s => s.name),
            data: sizeData.map(s => s.size)
        };
        
        detailsHtml = sizeData.map(s => `
            <div style="padding: 0.75rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between;">
                <span><strong>${s.name}</strong></span>
                <span>${formatFileSize(s.size)}</span>
            </div>
        `).join('');
    }
    
    document.getElementById('storageDetails').innerHTML = detailsHtml;
    renderStorageChart(chartData);
}

// Render storage chart
function renderStorageChart(chartData) {
    const canvas = document.getElementById('storageChart');
    
    if (charts.storage) {
        charts.storage.destroy();
    }
    
    const ctx = canvas.getContext('2d');
    charts.storage = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: chartData.labels,
            datasets: [{
                data: chartData.data,
                backgroundColor: window.ChartColors ? window.ChartColors.getChartColors(10) : [
                    '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
                    '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6'
                ]
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: CHART_CONFIG.layout,
            plugins: {
                legend: {
                    position: 'bottom',
                    ...CHART_CONFIG.plugins.legend
                },
                tooltip: {
                    ...CHART_CONFIG.plugins.tooltip,
                    callbacks: {
                        label: function(context) {
                            return context.label + ': ' + formatFileSize(context.parsed);
                        }
                    }
                }
            }
        }
    });
    
    // Attach chart controls (type selector and export)
    if (window.ChartExport) {
        const chartContainer = canvas.closest('.chart-container, .section-card');
        if (chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    }
}

// Render storage timeline
function renderStorageTimeline(timeline) {
    const canvas = document.getElementById('storageTimelineChart');
    
    if (charts.storageTimeline) {
        charts.storageTimeline.destroy();
    }
    
    const ctx = canvas.getContext('2d');
    charts.storageTimeline = new Chart(ctx, {
        type: 'line',
        data: {
            labels: timeline.map(t => t.month),
            datasets: [
                {
                    label: translations.filesAdded,
                    data: timeline.map(t => t.count),
                    borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                    backgroundColor: 'rgba(102, 126, 234, 0.1)',
                    yAxisID: 'y'
                },
                {
                    label: translations.storageAdded,
                    data: timeline.map(t => t.size / (1024 * 1024)),
                    borderColor: window.ChartColors ? window.ChartColors.getThemeColors().success : '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.1)',
                    yAxisID: 'y1'
                }
            ]
        },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: CHART_CONFIG.plugins.legend,
                    tooltip: CHART_CONFIG.plugins.tooltip
                },
                scales: {
                    x: {
                        ...CHART_CONFIG.scales.x
                    },
                    y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    title: {
                        display: true,
                        text: 'Files'
                    }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    title: {
                        display: true,
                        text: translations.storageMB
                    },
                    grid: {
                        drawOnChartArea: false
                    }
                }
            }
        }
    });
}

// Load processing statistics
async function loadProcessingStatistics() {
    try {
        const response = await fetch('/api/analytics/processing-statistics');
        const data = await response.json();
        
        // Update summary cards only if they exist
        const processingRateEl = document.getElementById('processingRate');
        if (processingRateEl) {
            const totalProcessed = data.by_type.reduce((sum, t) => sum + t.processed, 0);
            const totalFiles = data.by_type.reduce((sum, t) => sum + t.total, 0);
            const overallRate = totalFiles > 0 ? ((totalProcessed / totalFiles) * 100).toFixed(1) : 0;
            processingRateEl.textContent = overallRate + '%';
        }
        
        // Render charts
        renderSuccessRateChart(data.by_type);
        renderProcessingSpeedChart(data.daily_speed);
        
    } catch (error) {
        console.error('Error loading processing statistics:', error);
    }
}

// Render success rate chart
function renderSuccessRateChart(typeData) {
    const canvas = document.getElementById('successRateChart');
    
    if (charts.successRate) {
        charts.successRate.destroy();
    }
    
    const ctx = canvas.getContext('2d');
    charts.successRate = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: typeData.slice(0, 10).map(t => t.type),
            datasets: [{
                label: translations.successRate,
                data: typeData.slice(0, 10).map(t => t.success_rate),
                backgroundColor: window.ChartColors ? window.ChartColors.getThemeColors().success : '#10b981'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: CHART_CONFIG.layout,
            plugins: {
                legend: CHART_CONFIG.plugins.legend,
                tooltip: CHART_CONFIG.plugins.tooltip
            },
            scales: {
                x: {
                    ...CHART_CONFIG.scales.x
                },
                y: {
                    beginAtZero: true,
                    max: 100,
                    ...CHART_CONFIG.scales.y
                }
            }
        }
    });
    
    // Attach chart controls
    if (window.ChartExport) {
        const chartContainer = canvas.closest('.chart-container, .section-card');
        if (chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    }
}

// Render processing speed chart
function renderProcessingSpeedChart(speedData) {
    const canvas = document.getElementById('processingSpeedChart');
    
    if (charts.processingSpeed) {
        charts.processingSpeed.destroy();
    }
    
    const ctx = canvas.getContext('2d');
    charts.processingSpeed = new Chart(ctx, {
        type: 'line',
        data: {
            labels: speedData.map(d => new Date(d.date).toLocaleDateString()),
            datasets: [{
                label: translations.filesProcessed,
                data: speedData.map(d => d.files),
                borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                backgroundColor: 'rgba(102, 126, 234, 0.1)',
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: CHART_CONFIG.layout,
            plugins: {
                legend: CHART_CONFIG.plugins.legend,
                tooltip: CHART_CONFIG.plugins.tooltip
            },
            scales: {
                x: {
                    ...CHART_CONFIG.scales.x
                },
                y: {
                    beginAtZero: true,
                    ...CHART_CONFIG.scales.y
                }
            }
        }
    });
    
    // Attach chart controls
    if (window.ChartExport) {
        const chartContainer = canvas.closest('.chart-container, .section-card');
        if (chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    }
}

// Load content statistics
async function loadContentStatistics() {
    try {
        const response = await fetch('/api/analytics/content-statistics');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Validate response
        if (!data.success) {
            console.error('API returned error:', data.error || 'Unknown error');
            return;
        }
        
        // Update summary cards only if they exist
        const totalCategoriesEl = document.getElementById('totalCategories');
        if (totalCategoriesEl && data.categories && Array.isArray(data.categories)) {
            totalCategoriesEl.textContent = data.categories.length;
        }
        
        // Render charts with validation
        if (data.coverage) {
            renderContentCoverageChart(data.coverage);
        }
        if (data.categories && Array.isArray(data.categories) && data.categories.length > 0) {
            renderTopCategoriesChart(data.categories.slice(0, 10));
        }
        if (data.top_words && Array.isArray(data.top_words) && data.top_words.length > 0) {
            renderTopWordsCloud(data.top_words.slice(0, 30));
        }
        
    } catch (error) {
        console.error('Error loading content statistics:', error);
    }
}

// Render content coverage chart
function renderContentCoverageChart(coverage) {
    if (!coverage) {
        console.warn('No coverage data to render');
        return;
    }
    
    const canvas = document.getElementById('contentCoverageChart');
    if (!canvas) {
        console.error('contentCoverageChart canvas not found');
        return;
    }
    
    if (charts.contentCoverage) {
        charts.contentCoverage.destroy();
    }
    
    const ctx = canvas.getContext('2d');
    charts.contentCoverage = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: [translations.withContent, translations.withWords, translations.noContent],
            datasets: [{
                data: [
                    coverage.with_content || 0,
                    coverage.with_words || 0,
                    (coverage.total_files || 0) - (coverage.with_content || 0)
                ],
                backgroundColor: window.ChartColors ? [
                    window.ChartColors.getThemeColors().success,
                    window.ChartColors.getThemeColors().primary,
                    window.ChartColors.getThemeColors().danger
                ] : ['#10b981', '#667eea', '#ef4444']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: CHART_CONFIG.layout,
            plugins: {
                legend: {
                    position: 'bottom',
                    ...CHART_CONFIG.plugins.legend
                },
                tooltip: CHART_CONFIG.plugins.tooltip
            }
        }
    });
    
    // Attach chart controls
    if (window.ChartExport) {
        const chartContainer = canvas.closest('.chart-container, .section-card');
        if (chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    }
}

// Render top categories chart
function renderTopCategoriesChart(categories) {
    if (!categories || !Array.isArray(categories) || categories.length === 0) {
        console.warn('No categories data to render');
        return;
    }
    
    const canvas = document.getElementById('topCategoriesChart');
    if (!canvas) {
        console.error('topCategoriesChart canvas not found');
        return;
    }
    
    if (charts.topCategories) {
        charts.topCategories.destroy();
    }
    
    // Validate category structure - handle both {name, file_count} and {name, count} formats
    const labels = categories.map(c => c.name || c.category_name || 'Unknown');
    const values = categories.map(c => c.file_count || c.count || 0);
    
    const ctx = canvas.getContext('2d');
    charts.topCategories = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Files',
                data: values,
                backgroundColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            scales: {
                x: {
                    beginAtZero: true
                }
            }
        }
    });
    
    // Attach chart controls
    if (window.ChartExport) {
        const chartContainer = canvas.closest('.chart-container, .section-card');
        if (chartContainer) {
            setTimeout(() => {
                window.ChartExport.attachExportButtonsToCharts(chartContainer);
            }, 100);
        }
    }
}

// Render top words cloud
function renderTopWordsCloud(words) {
    const container = document.getElementById('topWordsCloud');
    container.innerHTML = '';
    
    words.forEach((word, index) => {
        const wordItem = document.createElement('div');
        wordItem.className = 'word-item';
        const fontSize = Math.min(0.875 + (word.files / words[0].files) * 0.75, 1.75);
        wordItem.style.fontSize = fontSize + 'rem';
        wordItem.innerHTML = `${word.word} <span class="count">(${word.files})</span>`;
        wordItem.onclick = () => {
            window.location.href = `/search?q=${encodeURIComponent(word.word)}`;
        };
        container.appendChild(wordItem);
    });
}

// Render largest files table
function renderLargestFiles(files) {
    const tbody = document.getElementById('largestFilesTable');
    tbody.innerHTML = '';
    
    files.forEach(file => {
        const row = document.createElement('tr');
        row.style.cursor = 'pointer';
        row.onclick = () => window.location.href = `/file/${file.id}`;
        
        row.innerHTML = `
            <td><strong>${file.name}</strong></td>
            <td><span class="badge bg-primary">${file.type || translations.unknown}</span></td>
            <td><strong>${formatFileSize(file.size)}</strong></td>
            <td>${file.source}</td>
            <td style="font-size: 0.875rem; color: var(--text-light);">${file.path}</td>
        `;
        
        tbody.appendChild(row);
    });
}

// Helper function
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}
// Additional dashboard variables (charts object already declared above)
let currentWordLimit = 50;
let currentTimelinePeriod = 'month';

// 🚀 OPTIMIZED: Initialize dashboard with lazy loading
document.addEventListener('DOMContentLoaded', function() {
    console.log('Dashboard page: DOMContentLoaded fired');
    
    // Update active navigation button based on current page
    const currentPath = window.location.pathname;
    document.querySelectorAll('.dashboard-nav-btn').forEach(btn => {
        btn.classList.remove('active');
        const href = btn.getAttribute('href');
        if (href && (currentPath === href || (href === '/' && currentPath === '/') || (href !== '/' && currentPath.startsWith(href)))) {
            btn.classList.add('active');
        }
    });
    
    // Load critical data immediately
    console.log('Dashboard page: Loading summary statistics...');
    loadDashboardSummary().catch(error => {
        console.error('Failed to load dashboard summary:', error);
    });
    loadTimelineData('month');
    loadCategoryDistribution();
    loadFileTypeDistribution();
    
    // 🚀 OPTIMIZED: Lazy load file types (only needed for search dropdown)
    // Load when search tab is activated instead of on page load
    const searchTab = document.getElementById('tab-search');
    if (searchTab) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && !searchTab.dataset.loaded) {
                    loadFileTypes();
                    searchTab.dataset.loaded = 'true';
                    observer.disconnect();
                }
            });
        }, { threshold: 0.1 });
        observer.observe(searchTab);
    }
});

// 🚀 OPTIMIZED: Tab switching with lazy loading
function switchTab(tabName, event) {
    console.log('switchTab called with:', tabName);
    // Update buttons
    document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
    // Find and activate the button that matches this tab
    document.querySelectorAll('.tab-button').forEach(btn => {
        const onclick = btn.getAttribute('onclick');
        if (onclick && onclick.includes(`'${tabName}'`)) {
            btn.classList.add('active');
        }
    });
    
    // Update content
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    const activeTab = document.getElementById('tab-' + tabName);
    if (activeTab) {
        activeTab.classList.add('active');
        
        // 🚀 OPTIMIZED: Load tab-specific data only when tab is activated
        switch(tabName) {
            case 'keywords':
                if (!activeTab.dataset.loaded) {
                    loadWordFrequency(currentWordLimit);
                    activeTab.dataset.loaded = 'true';
                }
                break;
            case 'paths':
                if (!activeTab.dataset.loaded) {
                    loadPathHierarchy();
                    activeTab.dataset.loaded = 'true';
                }
                break;
            case 'files':
                if (!activeTab.dataset.loaded) {
                    loadFileReports();
                    activeTab.dataset.loaded = 'true';
                }
                break;
            case 'categories':
                if (!activeTab.dataset.loaded) {
                    loadCategoryCharts();
                    activeTab.dataset.loaded = 'true';
                }
                break;
            case 'storage':
                if (!activeTab.dataset.loaded) {
                    loadAllStatistics();
                    activeTab.dataset.loaded = 'true';
                }
                break;
            case 'search':
                // Load file types when search tab is first opened
                if (!activeTab.dataset.loaded) {
                    loadFileTypes();
                    activeTab.dataset.loaded = 'true';
                }
                break;
        }
    }
}

// 🚀 OPTIMIZED: Load dashboard summary with error handling
async function loadDashboardSummary() {
    console.log('loadDashboardSummary: Starting...');
    try {
        console.log('loadDashboardSummary: Fetching from /api/analytics/dashboard-summary');
        const response = await fetch('/api/analytics/dashboard-summary');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log('loadDashboardSummary: Data received:', data);
        
        // 🚀 OPTIMIZED: Check if elements exist before updating
        const totalFilesEl = document.getElementById('totalFiles');
        console.log('loadDashboardSummary: totalFiles element found:', !!totalFilesEl);
        if (totalFilesEl) {
            totalFilesEl.textContent = (data.totalFiles || 0).toLocaleString();
            console.log('loadDashboardSummary: Updated totalFiles to', data.totalFiles);
        } else {
            console.warn('loadDashboardSummary: totalFiles element not found!');
        }
        
        const processedFilesEl = document.getElementById('processedFiles');
        if (processedFilesEl) processedFilesEl.textContent = (data.processedFiles || 0).toLocaleString();
        
        const uniqueTypesEl = document.getElementById('uniqueTypes');
        if (uniqueTypesEl) uniqueTypesEl.textContent = data.uniqueTypes || 0;
        
        const totalWordsEl = document.getElementById('totalWords');
        if (totalWordsEl) totalWordsEl.textContent = (data.totalWords || 0).toLocaleString();
        
        const totalCategoriesEl = document.getElementById('totalCategories');
        if (totalCategoriesEl) totalCategoriesEl.textContent = data.totalCategories || 0;
        
        const totalKeywordsEl = document.getElementById('totalKeywords');
        if (totalKeywordsEl) totalKeywordsEl.textContent = (data.totalKeywords || 0).toLocaleString();
        
        // Format storage size
        const storageSizeEl = document.getElementById('storageSize');
        if (storageSizeEl) {
            const sizeGB = ((data.totalSize || 0) / (1024 ** 3)).toFixed(2);
            storageSizeEl.textContent = sizeGB + ' GB';
        }
        
        // Format database size
        const databaseSizeEl = document.getElementById('databaseSize');
        if (databaseSizeEl) {
            databaseSizeEl.textContent = formatFileSize(data.databaseSize || 0);
        }
        
        // Update change indicators
        const processingRateEl = document.getElementById('processingRate');
        if (processingRateEl) {
            processingRateEl.textContent = (data.processingRate || 0).toFixed(1) + '% ' + translations.processed;
        }
        
        const filesChangeEl = document.getElementById('filesChange');
        if (filesChangeEl) {
            filesChangeEl.innerHTML = `<i class="bi bi-arrow-up"></i> ${data.recentFiles || 0} ${translations.thisWeek}`;
        }
        
        console.log('loadDashboardSummary: Successfully updated all elements');
        
    } catch (error) {
        console.error('Error loading dashboard summary:', error);
        console.error('Error details:', error.message, error.stack);
        // Show error in UI if elements exist
        const totalFilesEl = document.getElementById('totalFiles');
        if (totalFilesEl) {
            totalFilesEl.textContent = '-';
            console.log('loadDashboardSummary: Set totalFiles to "-" due to error');
        }
        
        // Try to update all elements to show error state
        const elements = ['totalFiles', 'processedFiles', 'uniqueTypes', 'totalWords', 'totalCategories', 'storageSize'];
        elements.forEach(id => {
            const el = document.getElementById(id);
            if (el && el.textContent === '-') {
                // Already set, keep it
            } else if (el) {
                el.textContent = '-';
            }
        });
    }
}

// Load timeline data
async function loadTimelineData(period) {
    currentTimelinePeriod = period;
    try {
        const response = await fetch(`/api/analytics/timeline-data?period=${period}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Validate response
        if (!data.success) {
            console.error('API returned error:', data.error || 'Unknown error');
            return;
        }
        
        // Validate data structure
        if (!data.labels || !data.fileCount || !data.processedCount || 
            !Array.isArray(data.labels) || !Array.isArray(data.fileCount) || !Array.isArray(data.processedCount)) {
            console.error('Invalid timeline data structure:', data);
            return;
        }
        
        if (charts.timeline) {
            charts.timeline.destroy();
        }
        
        const canvas = document.getElementById('timelineChart');
        if (!canvas) {
            console.error('timelineChart canvas not found');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        charts.timeline = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [
                    {
                        label: translations.totalFiles,
                        data: data.fileCount,
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        tension: 0.4,
                        fill: true
                    },
                    {
                        label: translations.processedFiles,
                        data: data.processedCount,
                        borderColor: window.ChartColors ? window.ChartColors.getThemeColors().success : '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        tension: 0.4,
                        fill: true
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                plugins: {
                    legend: {
                        position: 'top',
                        ...CHART_CONFIG.plugins.legend
                    },
                    tooltip: CHART_CONFIG.plugins.tooltip,
                    title: {
                        display: false
                    }
                },
                scales: {
                    x: {
                        ...CHART_CONFIG.scales.x
                    },
                    y: {
                        beginAtZero: true,
                        ...CHART_CONFIG.scales.y
                    }
                }
            }
        });
    } catch (error) {
        console.error('Error loading timeline data:', error);
    }
}

function updateTimeline(period) {
    document.querySelectorAll('#tab-overview .btn-control').forEach(btn => btn.classList.remove('active'));
    // Get event from global scope (available when called from onclick)
    if (typeof event !== 'undefined' && event.target) {
        event.target.classList.add('active');
    } else {
        // Fallback: find button by period
        document.querySelectorAll('#tab-overview .btn-control').forEach(btn => {
            if (btn.textContent.includes(period === 'day' ? '24H' : period.charAt(0).toUpperCase() + period.slice(1))) {
                btn.classList.add('active');
            }
        });
    }
    loadTimelineData(period);
}

// Load category distribution
async function loadCategoryDistribution() {
    try {
        const response = await fetch('/api/analytics/category-distribution');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Validate response
        if (!data.success) {
            console.error('API returned error:', data.error || 'Unknown error');
            return;
        }
        
        // Validate data structure
        if (!data.labels || !data.values || !Array.isArray(data.labels) || !Array.isArray(data.values)) {
            console.error('Invalid data structure:', data);
            return;
        }
        
        // Check if we have data
        if (data.labels.length === 0 || data.values.length === 0) {
            console.warn('No category data available');
            // Show empty state message in the chart container
            const canvas = document.getElementById('processingChart');
            if (canvas) {
                const container = canvas.closest('.chart-container, .section-card');
                if (container) {
                    const emptyMsg = document.createElement('div');
                    emptyMsg.className = 'empty-state';
                    emptyMsg.style.padding = '2rem';
                    emptyMsg.style.textAlign = 'center';
                    emptyMsg.innerHTML = '<i class="bi bi-inbox"></i><h3>No Category Data</h3><p>No categories with files found. Categories will appear here once files are categorized.</p>';
                    container.appendChild(emptyMsg);
                }
            }
            return;
        }
        
        if (charts.processing) {
            charts.processing.destroy();
        }
        
        const canvas = document.getElementById('processingChart');
        if (!canvas) {
            console.error('processingChart canvas not found');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        charts.processing = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: data.labels.slice(0, 5),
                datasets: [{
                    data: data.values.slice(0, 5),
                    backgroundColor: window.ChartColors ? [
                        window.ChartColors.getThemeColors().primary,
                        window.ChartColors.getThemeColors().success,
                        window.ChartColors.getThemeColors().warning,
                        window.ChartColors.getThemeColors().danger,
                        window.ChartColors.getThemeColors().secondary
                    ] : [
                        '#667eea',
                        '#10b981',
                        '#f59e0b',
                        '#ef4444',
                        '#06b6d4'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                plugins: {
                    legend: {
                        position: 'bottom',
                        ...CHART_CONFIG.plugins.legend
                    },
                    tooltip: CHART_CONFIG.plugins.tooltip
                }
            }
        });
        
        // Attach chart controls for this specific chart
        if (window.ChartExport && canvas) {
            setTimeout(() => {
                const chartContainer = canvas.closest('.chart-container');
                if (chartContainer) {
                    window.ChartExport.attachExportButtonsToCharts(chartContainer);
                }
            }, 150);
        }
    } catch (error) {
        console.error('Error loading category distribution:', error);
    }
}

// Load category charts (for categories tab)
async function loadCategoryCharts() {
    try {
        const response = await fetch('/api/analytics/category-distribution');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Validate response
        if (!data.success) {
            console.error('API returned error:', data.error || 'Unknown error');
            return;
        }
        
        // Validate data structure
        if (!data.labels || !data.values || !Array.isArray(data.labels) || !Array.isArray(data.values)) {
            console.error('Invalid data structure:', data);
            return;
        }
        
        // Check if we have data
        if (data.labels.length === 0 || data.values.length === 0) {
            console.warn('No category data available');
            // Show empty state message in the chart containers
            const pieCanvas = document.getElementById('categoryPieChart');
            const barCanvas = document.getElementById('categoryBarChart');
            if (pieCanvas) {
                const container = pieCanvas.closest('.chart-container, .section-card');
                if (container) {
                    const emptyMsg = document.createElement('div');
                    emptyMsg.className = 'empty-state';
                    emptyMsg.style.padding = '2rem';
                    emptyMsg.style.textAlign = 'center';
                    emptyMsg.innerHTML = '<i class="bi bi-inbox"></i><h3>No Category Data</h3><p>No categories with files found. Categories will appear here once files are categorized.</p>';
                    container.appendChild(emptyMsg);
                }
            }
            if (barCanvas) {
                const container = barCanvas.closest('.chart-container, .section-card');
                if (container && !container.querySelector('.empty-state')) {
                    const emptyMsg = document.createElement('div');
                    emptyMsg.className = 'empty-state';
                    emptyMsg.style.padding = '2rem';
                    emptyMsg.style.textAlign = 'center';
                    emptyMsg.innerHTML = '<i class="bi bi-inbox"></i><h3>No Category Data</h3><p>No categories with files found. Categories will appear here once files are categorized.</p>';
                    container.appendChild(emptyMsg);
                }
            }
            return;
        }
        
        // Pie chart
        if (charts.categoryPie) {
            charts.categoryPie.destroy();
        }
        
        const pieCanvas = document.getElementById('categoryPieChart');
        if (!pieCanvas) {
            console.error('categoryPieChart canvas not found');
            return;
        }
        
        const pieCtx = pieCanvas.getContext('2d');
        charts.categoryPie = new Chart(pieCtx, {
            type: 'pie',
            data: {
                labels: data.labels,
                datasets: [{
                    data: data.values,
                    backgroundColor: window.ChartColors ? window.ChartColors.getChartColors(15) : [
                        '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
                        '#06b6d4', '#8b5cf6', '#ec4899', '#f97316', '#14b8a6',
                        '#6366f1', '#84cc16', '#f43f5e', '#06b6d4', '#a855f7'
                    ]
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                plugins: {
                    legend: {
                        position: 'right',
                        ...CHART_CONFIG.plugins.legend
                    },
                    tooltip: CHART_CONFIG.plugins.tooltip,
                    title: {
                        display: true,
                        text: translations.categoryDistribution,
                        font: {
                            size: 16,
                            weight: '600'
                        },
                        padding: {
                            top: 10,
                            bottom: 20
                        }
                    }
                }
            }
        });
        
        // Bar chart
        if (charts.categoryBar) {
            charts.categoryBar.destroy();
        }
        
        const barCanvas = document.getElementById('categoryBarChart');
        if (!barCanvas) {
            console.error('categoryBarChart canvas not found');
            return;
        }
        
        const barCtx = barCanvas.getContext('2d');
        charts.categoryBar = new Chart(barCtx, {
            type: 'bar',
            data: {
                labels: data.labels,
                datasets: [{
                    label: translations.numberFiles,
                    data: data.values,
                    backgroundColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                indexAxis: 'y',
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: CHART_CONFIG.plugins.tooltip,
                    title: {
                        display: true,
                        text: translations.filesPerCategory,
                        font: {
                            size: 16,
                            weight: '600'
                        },
                        padding: {
                            top: 10,
                            bottom: 20
                        }
                    }
                },
                scales: {
                    x: {
                        beginAtZero: true,
                        ...CHART_CONFIG.scales.x
                    },
                    y: {
                        ...CHART_CONFIG.scales.y
                    }
                }
            }
        });
        
        // Attach chart controls for pie chart - ensure it gets its own controls
        if (window.ChartExport && pieCanvas && charts.categoryPie) {
            setTimeout(() => {
                const pieChartContainer = pieCanvas.closest('.chart-container');
                if (pieChartContainer && charts.categoryPie) {
                    // Use attachExportButtonsToCharts which will create controls for this specific chart
                    window.ChartExport.attachExportButtonsToCharts(pieChartContainer);
                }
            }, 200);
        }
        
        // Attach chart controls for bar chart - ensure it gets its own controls
        if (window.ChartExport && barCanvas && charts.categoryBar) {
            setTimeout(() => {
                const barChartContainer = barCanvas.closest('.chart-container');
                if (barChartContainer && charts.categoryBar) {
                    // Use attachExportButtonsToCharts which will create controls for this specific chart
                    window.ChartExport.attachExportButtonsToCharts(barChartContainer);
                }
            }, 200);
        }
    } catch (error) {
        console.error('Error loading category charts:', error);
    }
}

// Load file type distribution
async function loadFileTypeDistribution() {
    try {
        const response = await fetch('/api/analytics/file-type-distribution');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Validate response
        if (!data.success) {
            console.error('API returned error:', data.error || 'Unknown error');
            return;
        }
        
        // Validate data structure
        if (!data.types || !Array.isArray(data.types) || data.types.length === 0) {
            console.warn('No file type data available');
            return;
        }
        
        if (charts.fileType) {
            charts.fileType.destroy();
        }
        
        const canvas = document.getElementById('fileTypeChart');
        if (!canvas) {
            console.error('fileTypeChart canvas not found');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        charts.fileType = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.types.map(t => t.type || 'Unknown').slice(0, 10),
                datasets: [{
                    label: translations.numberFiles,
                    data: data.types.map(t => t.count || 0).slice(0, 10),
                    backgroundColor: window.ChartColors ? window.ChartColors.getThemeColors().primary : '#667eea'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: CHART_CONFIG.layout,
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: CHART_CONFIG.plugins.tooltip
                },
                scales: {
                    x: {
                        ...CHART_CONFIG.scales.x
                    },
                    y: {
                        beginAtZero: true,
                        ...CHART_CONFIG.scales.y
                    }
                }
            }
        });
    } catch (error) {
        console.error('Error loading file type distribution:', error);
    }
}

// Load word frequency
async function loadWordFrequency(limit) {
    currentWordLimit = limit;
    const grid = document.getElementById('wordFrequencyGrid');
    grid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingWordFrequency}</p></div>`;
    
    try {
        const response = await fetch(`/api/analytics/word-frequency?limit=${limit}`);
        const data = await response.json();
        
        if (data.words && data.words.length > 0) {
            grid.innerHTML = '';
            data.words.forEach(word => {
                const wordItem = document.createElement('div');
                wordItem.className = 'word-item';
                wordItem.innerHTML = `
                    <div class="word">${word.word}</div>
                    <div class="frequency">${word.frequency} ${translations.files}</div>
                `;
                wordItem.onclick = () => {
                    document.getElementById('searchQuery').value = word.word;
                    switchTab('search');
                    performSearch();
                };
                grid.appendChild(wordItem);
            });
        } else {
            grid.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i><h3>${translations.noWordData}</h3></div>`;
        }
    } catch (error) {
        console.error('Error loading word frequency:', error);
        grid.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-circle"></i><h3>${translations.errorLoadingData}</h3></div>`;
    }
}

function updateWordLimit(limit) {
    document.querySelectorAll('#tab-keywords .btn-control').forEach(btn => btn.classList.remove('active'));
    // Get event from global scope (available when called from onclick)
    if (typeof event !== 'undefined' && event.target) {
        event.target.classList.add('active');
    } else {
        // Fallback: find button by limit
        document.querySelectorAll('#tab-keywords .btn-control').forEach(btn => {
            if (btn.textContent.includes(limit.toString())) {
                btn.classList.add('active');
            }
        });
    }
    loadWordFrequency(limit);
}

// Load path hierarchy
async function loadPathHierarchy() {
    const tree = document.getElementById('pathTree');
    if (!tree) return; // Element might not exist if tab not active
    
    tree.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingPathHierarchy}</p></div>`;
    
    try {
        const response = await fetch('/api/analytics/path-hierarchy');
        const data = await response.json();
        
        // 🚀 FIXED: Use 'structure' instead of 'hierarchy' to match API response
        const structure = data.structure || data.hierarchy || [];
        
        if (structure.length > 0) {
            tree.innerHTML = '';
            structure.forEach(path => {
                const li = document.createElement('li');
                const processingRate = path.file_count > 0 ? 
                    ((path.processed_count / path.file_count) * 100).toFixed(1) : 0;
                li.innerHTML = `
                    <div class="path-item">
                        <div class="path-name">
                            <i class="bi ${path.isArchive ? 'bi-file-earmark-zip' : 'bi-folder'}"></i>
                            <span>${path.name || path.path || 'Root'}</span>
                        </div>
                        <div class="path-metrics">
                            <span><i class="bi bi-files"></i> ${path.file_count || path.totalFiles || 0} ${translations.files}</span>
                            <span><i class="bi bi-check-circle"></i> ${path.processed_count || path.processedFiles || 0} ${translations.processed}</span>
                            <span><i class="bi bi-hdd"></i> ${formatFileSize(path.total_size || path.totalSize || 0)}</span>
                            <span><i class="bi bi-percent"></i> ${processingRate}%</span>
                        </div>
                    </div>
                `;
                tree.appendChild(li);
            });
        } else {
            tree.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i><h3>${translations.noPathData}</h3></div>`;
        }
    } catch (error) {
        console.error('Error loading path hierarchy:', error);
        tree.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-circle"></i><h3>${translations.errorLoadingData}</h3></div>`;
    }
}

// Load file reports
async function loadFileReports() {
    const grid = document.getElementById('fileReportsGrid');
    grid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.loadingFileReports}</p></div>`;
    
    try {
        const response = await fetch('/api/analytics/search-files?limit=20');
        const data = await response.json();
        
        if (data.files && data.files.length > 0) {
            grid.innerHTML = '';
            data.files.forEach(file => {
                const card = document.createElement('div');
                card.className = 'file-report-card';
                card.onclick = () => window.location.href = `/file/${file.id}`;
                
                const fileIcon = getFileIcon(file.type);
                const fileSize = formatFileSize(file.size);
                
                card.innerHTML = `
                    <div class="file-icon">${fileIcon}</div>
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-meta">
                        <span><i class="bi bi-file-earmark"></i> ${file.type || 'Unknown'}</span>
                        <span><i class="bi bi-hdd"></i> ${fileSize}</span>
                        <span><i class="bi bi-calendar"></i> ${file.date ? new Date(file.date).toLocaleDateString() : 'N/A'}</span>
                    </div>
                    <div class="file-summary">
                        ${translations.source}: ${file.source || translations.unknown}<br>
                        ${translations.status}: ${file.status || translations.unknown}
                    </div>
                `;
                grid.appendChild(card);
            });
        } else {
            grid.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i><h3>${translations.noFilesAvailable}</h3></div>`;
        }
    } catch (error) {
        console.error('Error loading file reports:', error);
        grid.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-circle"></i><h3>${translations.errorLoadingData}</h3></div>`;
    }
}

// Load file types for dropdown
async function loadFileTypes() {
    try {
        const response = await fetch('/api/analytics/file-type-distribution');
        const data = await response.json();
        
        const select = document.getElementById('searchType');
        data.types.forEach(type => {
            const option = document.createElement('option');
            option.value = type.type;
            option.textContent = type.type;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading file types:', error);
    }
}

// Perform search
async function performSearch() {
    const query = document.getElementById('searchQuery').value;
    const type = document.getElementById('searchType').value;
    const resultsGrid = document.getElementById('searchResults');
    
    resultsGrid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${translations.searching}</p></div>`;
    
    try {
        const params = new URLSearchParams();
        if (query) params.append('q', query);
        if (type) params.append('type', type);
        params.append('limit', '50');
        
        const response = await fetch(`/api/analytics/search-files?${params.toString()}`);
        const data = await response.json();
        
        document.getElementById('searchResultCount').textContent = `${data.files.length} ${translations.results}`;
        
        if (data.files && data.files.length > 0) {
            resultsGrid.innerHTML = '';
            data.files.forEach(file => {
                const card = document.createElement('div');
                card.className = 'file-report-card';
                card.onclick = () => window.location.href = `/file/${file.id}`;
                
                const fileIcon = getFileIcon(file.type);
                const fileSize = formatFileSize(file.size);
                
                card.innerHTML = `
                    <div class="file-icon">${fileIcon}</div>
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-meta">
                        <span><i class="bi bi-file-earmark"></i> ${file.type || 'Unknown'}</span>
                        <span><i class="bi bi-hdd"></i> ${fileSize}</span>
                        <span><i class="bi bi-calendar"></i> ${file.date ? new Date(file.date).toLocaleDateString() : 'N/A'}</span>
                    </div>
                    <div class="file-summary">
                        ${translations.source}: ${file.source || translations.unknown}<br>
                        ${translations.status}: ${file.status || translations.unknown}
                    </div>
                `;
                resultsGrid.appendChild(card);
            });
        } else {
            resultsGrid.innerHTML = `<div class="empty-state"><i class="bi bi-search"></i><h3>${translations.noResultsFound}</h3><p>${translations.tryDifferentCriteria}</p></div>`;
        }
    } catch (error) {
        console.error('Error performing search:', error);
        resultsGrid.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-circle"></i><h3>${translations.searchError}</h3></div>`;
    }
}

// Helper functions
function getFileIcon(type) {
    const iconMap = {
        'pdf': '<i class="bi bi-file-pdf"></i>',
        'doc': '<i class="bi bi-file-word"></i>',
        'docx': '<i class="bi bi-file-word"></i>',
        'xls': '<i class="bi bi-file-excel"></i>',
        'xlsx': '<i class="bi bi-file-excel"></i>',
        'ppt': '<i class="bi bi-file-ppt"></i>',
        'pptx': '<i class="bi bi-file-ppt"></i>',
        'txt': '<i class="bi bi-file-text"></i>',
        'jpg': '<i class="bi bi-file-image"></i>',
        'jpeg': '<i class="bi bi-file-image"></i>',
        'png': '<i class="bi bi-file-image"></i>',
        'gif': '<i class="bi bi-file-image"></i>',
        'mp4': '<i class="bi bi-file-play"></i>',
        'mp3': '<i class="bi bi-file-music"></i>',
        'zip': '<i class="bi bi-file-zip"></i>',
        'rar': '<i class="bi bi-file-zip"></i>'
    };
    return iconMap[type?.toLowerCase()] || '<i class="bi bi-file-earmark"></i>';
}

// Allow Enter key to trigger search
document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('searchQuery').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            performSearch();
        }
    });
    
    // Start polling for active processing tasks
    startProcessingProgressPolling();
});

// Processing Progress Bar Functions
//
// PROGRESS: this page used to carry its own copy of the poller, which rebuilt
// the entire task list with innerHTML on every tick. That destroyed the node
// holding `transition: width .3s`, so even a correct percentage rendered as a
// snap rather than a movement, and the nested/dynamic totals the backend now
// reports were never shown. Both pages share one renderer instead.
let processingProgressInterval = null;

function startProcessingProgressPolling() {
    return startSharedProgressPolling({ intervalMs: 1500 });
}

function stopProcessingProgressPolling() {
    stopSharedProgressPolling();
    processingProgressInterval = null;
}

// Kept as a thin alias: older callers (and the DOMContentLoaded hook above)
// refer to the update by name.
async function updateProcessingProgress() {
    const tracker = getSharedProgressTracker();
    if (tracker) await tracker.refresh();
}

// Expose functions globally for onclick handlers in templates
// This must be done immediately when module loads, not in DOMContentLoaded
if (typeof window !== 'undefined') {
    window.switchTab = switchTab;
    window.updateTimeline = updateTimeline;
    window.updateWordLimit = updateWordLimit;
    window.updateStorageView = updateStorageView;
    window.loadFileTypes = loadFileTypes;
    window.performSearch = performSearch;
    // Note: clearSearch, exportChart, downloadChart, exportData, downloadData may not exist
    // Only expose if they're defined
    if (typeof clearSearch !== 'undefined') window.clearSearch = clearSearch;
    if (typeof exportChart !== 'undefined') window.exportChart = exportChart;
    if (typeof downloadChart !== 'undefined') window.downloadChart = downloadChart;
    if (typeof exportData !== 'undefined') window.exportData = exportData;
    if (typeof downloadData !== 'undefined') window.downloadData = downloadData;
    
    console.log('Dashboard functions exposed globally:', {
        switchTab: typeof window.switchTab,
        updateTimeline: typeof window.updateTimeline,
        performSearch: typeof window.performSearch
    });
}