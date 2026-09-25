/**
 * Analysis Batch Page JavaScript
 * Extracted from Analysis/analysis_batch.html
 */

// Load translations from JSON script tag
let translations = {};

document.addEventListener('DOMContentLoaded', function() {
    // Load translations from JSON script tag
    const pageDataEl = document.getElementById('analysis-batch-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            translations = data.translations || {};
        } catch (e) {
            console.error('Error parsing analysis batch page data:', e);
        }
    }
    
    console.log('Analysis batch page loaded');
});

let analysisStartTime = null;
let processedFiles = 0;
let totalFiles = 0;
let isPaused = false;

function toggleAll(checkbox) {
    document.querySelectorAll('.file-checkbox').forEach(cb => {
        cb.checked = checkbox.checked;
    });
    updateSelectedCount();
    updateEstimatedTime();
}

function selectAll() {
    document.querySelectorAll('.file-checkbox').forEach(cb => cb.checked = true);
    document.getElementById('selectAllCheckbox').checked = true;
    updateSelectedCount();
    updateEstimatedTime();
}

function deselectAll() {
    document.querySelectorAll('.file-checkbox').forEach(cb => cb.checked = false);
    document.getElementById('selectAllCheckbox').checked = false;
    updateSelectedCount();
    updateEstimatedTime();
}

function selectHighPriority() {
    document.querySelectorAll('.file-row').forEach(row => {
        const checkbox = row.querySelector('.file-checkbox');
        checkbox.checked = true;
    });
    updateSelectedCount();
    updateEstimatedTime();
}

function updateSelectedCount() {
    const count = document.querySelectorAll('.file-checkbox:checked').length;
    document.getElementById('selectedCount').textContent = count;
}

function updateEstimatedTime() {
    const selectedCount = document.querySelectorAll('.file-checkbox:checked').length;
    const estimatedMinutes = (selectedCount * 2.3 / 60).toFixed(1);
    document.getElementById('estimatedTime').textContent = estimatedMinutes + 'm';
}

async function startAnalysis() {
    const selected = Array.from(document.querySelectorAll('.file-checkbox:checked'))
                          .map(cb => parseInt(cb.value));
    
    if (selected.length === 0) {
        alert(translations.pleaseSelectFiles);
        return;
    }
    
    analysisStartTime = Date.now();
    totalFiles = selected.length;
    processedFiles = 0;
    isPaused = false;
    
    document.getElementById('analyzeBtn').disabled = true;
    document.getElementById('progressCard').style.display = 'block';
    document.getElementById('remainingCount').textContent = totalFiles;
    
    try {
        // Build request body with template configuration if available
        const requestBody = {
            file_ids: selected
        };
        
        // Add template configuration if one was applied
        if (currentTemplateConfig && currentTemplateConfig.config) {
            requestBody.config = currentTemplateConfig.config;
        }
        
        // Add UI settings if available
        if (currentTemplateConfig && currentTemplateConfig.ui) {
            requestBody.ui_settings = currentTemplateConfig.ui;
        }
        
        const response = await fetch('/analysis/batch/process', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(requestBody)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            for (let i = 0; i < data.results.length; i++) {
                if (isPaused) break;
                
                const result = data.results[i];
                const status = result.status === 'success' ? 'success' : 'error';
                
                processedFiles++;
                
                const progress = Math.round((processedFiles / totalFiles) * 100);
                document.getElementById('progressBar').style.width = progress + '%';
                document.getElementById('progressText').textContent = progress + '%';
                document.getElementById('completedCount').textContent = processedFiles;
                document.getElementById('remainingCount').textContent = totalFiles - processedFiles;
                
                // Add file progress item
                addFileProgressItem(result.file_id, status);
                
                await new Promise(resolve => setTimeout(resolve, 100));
            }
            
            if (!isPaused) {
                document.getElementById('currentFile').textContent = translations.analysisComplete;
                setTimeout(() => {
                    // ✅ Complete page reload with cache-busting
                    window.location.href = window.location.pathname + '?t=' + Date.now();
                }, 2000);
            }
        }
    } catch (error) {
        alert(translations.error + ': ' + error.message);
        document.getElementById('analyzeBtn').disabled = false;
    }
}

function addFileProgressItem(fileId, status) {
    const container = document.getElementById('fileProgressList');
    const item = document.createElement('div');
    item.className = `file-progress-item ${status}`;
    item.innerHTML = `
        <div class="d-flex justify-content-between align-items-center">
            <span>${translations.file} #${fileId}</span>
            <span class="badge bg-${status === 'success' ? 'success' : 'danger'}">
                ${status === 'success' ? translations.success : translations.error}
            </span>
        </div>
    `;
    container.appendChild(item);
    container.scrollTop = container.scrollHeight;
}

function pauseAnalysis() {
    isPaused = true;
    document.getElementById('pauseBtn').disabled = true;
    alert(translations.analysisPaused);
}

function stopAnalysis() {
    if (confirm(translations.stopAnalysisConfirm)) {
        isPaused = true;
        document.getElementById('progressCard').style.display = 'none';
        document.getElementById('analyzeBtn').disabled = false;
    }
}

// Template configurations
const analysisTemplates = {
    'quick': {
        name: 'Quick Analysis',
        description: 'Fast processing, standard settings',
        config: {
            enable_sentiment: false,
            enable_topics: false,
            enable_trends: false,
            enable_patterns: false,
            enable_entities: false,
            enable_statistics: true,
            batch_size: 200,
            chunk_size: 5000
        },
        ui: {
            priority: 'high',
            errorHandling: 'skip',
            resourceLimit: 'unlimited'
        }
    },
    'deep': {
        name: 'Deep Analysis',
        description: 'Thorough processing, all features',
        config: {
            enable_sentiment: true,
            enable_topics: true,
            enable_trends: true,
            enable_patterns: true,
            enable_entities: true,
            enable_statistics: true,
            batch_size: 50,
            chunk_size: 20000,
            min_word_frequency: 1,
            top_n_words: 200,
            top_n_topics: 20
        },
        ui: {
            priority: 'high',
            errorHandling: 'retry',
            resourceLimit: '50'
        }
    },
    'offhours': {
        name: 'Off-Hours Analysis',
        description: 'Scheduled for 11 PM, comprehensive analysis',
        config: {
            enable_sentiment: true,
            enable_topics: true,
            enable_trends: true,
            enable_patterns: true,
            enable_entities: true,
            enable_statistics: true,
            batch_size: 100,
            chunk_size: 15000
        },
        ui: {
            priority: 'medium',
            errorHandling: 'retry',
            resourceLimit: '25',
            schedule: 'off-hours'
        }
    }
};

// Store current template configuration
let currentTemplateConfig = null;

function applyTemplate(templateName) {
    try {
        const template = analysisTemplates[templateName];
        
        if (!template) {
            console.error(`Template "${templateName}" not found`);
            alert(translations.templateNotFound || `Template "${templateName}" not found`);
            return;
        }
        
        // Store template configuration
        currentTemplateConfig = template;
        
        // Apply UI settings if available
        if (template.ui) {
            if (template.ui.priority && document.getElementById('prioritySelect')) {
                document.getElementById('prioritySelect').value = template.ui.priority;
            }
            if (template.ui.errorHandling && document.getElementById('errorHandling')) {
                document.getElementById('errorHandling').value = template.ui.errorHandling;
            }
            if (template.ui.resourceLimit && document.getElementById('resourceLimit')) {
                document.getElementById('resourceLimit').value = template.ui.resourceLimit;
            }
            if (template.ui.schedule && document.getElementById('scheduleSelect')) {
                document.getElementById('scheduleSelect').value = template.ui.schedule;
            }
        }
        
        // Show success message
        const message = (translations.templateApplied || 'Template applied') + ': ' + template.name;
        if (window.showSuccess) {
            window.showSuccess(message);
        } else {
            alert(message);
        }
        
        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('templatesModal'));
        if (modal) {
            modal.hide();
        }
        
        console.log('Template applied:', templateName, template);
    } catch (error) {
        console.error('Error applying template:', error);
        alert((translations.error || 'Error') + ': ' + error.message);
    }
}

async function retryFile(fileId, buttonElement) {
    // Disable button and show loading state
    const originalHTML = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i> ' + (translations.processing || 'Processing...');
    
    try {
        const response = await fetch(`/api/analysis/retry/${fileId}`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            // Success - update button to show success state
            buttonElement.classList.remove('btn-primary');
            buttonElement.classList.add('btn-success');
            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i> ' + (translations.success || 'Success');
            
            // Find the row and update it
            const row = buttonElement.closest('tr');
            if (row) {
                row.classList.add('table-success');
                // Remove error message if present
                const errorCell = row.querySelector('td:nth-child(6)');
                if (errorCell) {
                    errorCell.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i> ' + (translations.processed || 'Processed') + '</span>';
                }
            }
            
            // Show success message
            if (data.stored) {
                alert((translations.fileRetrySuccess || 'File processed and saved successfully!'));
            } else {
                alert((translations.fileRetryPartial || 'File processed but may not have been saved. Check logs for details.'));
            }
            
            // Reload page after 2 seconds to refresh the list
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        } else {
            // Error - show error state
            buttonElement.classList.remove('btn-primary');
            buttonElement.classList.add('btn-danger');
            buttonElement.innerHTML = '<i class="bi bi-x-circle me-1"></i> ' + (translations.failed || 'Failed');
            
            // Show error message
            alert((translations.fileRetryError || 'Error') + ': ' + (data.error || 'Unknown error'));
            
            // Re-enable button after 3 seconds
            setTimeout(() => {
                buttonElement.disabled = false;
                buttonElement.classList.remove('btn-danger');
                buttonElement.classList.add('btn-primary');
                buttonElement.innerHTML = originalHTML;
            }, 3000);
        }
    } catch (error) {
        // Network or other error
        buttonElement.classList.remove('btn-primary');
        buttonElement.classList.add('btn-danger');
        buttonElement.innerHTML = '<i class="bi bi-x-circle me-1"></i> ' + (translations.error || 'Error');
        
        alert((translations.fileRetryError || 'Error') + ': ' + error.message);
        
        // Re-enable button after 3 seconds
        setTimeout(() => {
            buttonElement.disabled = false;
            buttonElement.classList.remove('btn-danger');
            buttonElement.classList.add('btn-primary');
            buttonElement.innerHTML = originalHTML;
        }, 3000);
    }
}

// Filter functionality
document.getElementById('sourceFilter').addEventListener('change', filterRows);
document.getElementById('sideFilter').addEventListener('change', filterRows);
document.getElementById('fileTypeFilter').addEventListener('change', filterRows);
document.getElementById('sizeFilter').addEventListener('change', filterRows);

function filterRows() {
    const sourceFilter = document.getElementById('sourceFilter').value;
    const sideFilter = document.getElementById('sideFilter').value;
    
    document.querySelectorAll('.file-row').forEach(row => {
        const source = row.dataset.source;
        const side = row.dataset.side;
        
        const showSource = !sourceFilter || source.includes(sourceFilter);
        const showSide = !sideFilter || side.includes(sideFilter);
        
        row.style.display = (showSource && showSide) ? '' : 'none';
    });
    
    updateSelectedCount();
    updateEstimatedTime();
}

// Expose functions globally for onclick handlers in templates
if (typeof window !== 'undefined') {
    window.toggleAll = toggleAll;
    window.selectAll = selectAll;
    window.deselectAll = deselectAll;
    window.selectHighPriority = selectHighPriority;
    window.startAnalysis = startAnalysis;
    window.pauseAnalysis = pauseAnalysis;
    window.stopAnalysis = stopAnalysis;
    window.applyTemplate = applyTemplate;
    window.retryFile = retryFile;
}