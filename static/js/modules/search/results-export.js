/**
 * Search Results Export and Print Module
 * Reusable module for exporting and printing search results across all search pages
 */

/**
 * Export search results in various formats
 * @param {Array} results - Array of search result objects
 * @param {Object} options - Export options
 * @param {string} options.format - Export format: 'csv', 'excel', 'json'
 * @param {string} options.query - Search query string
 * @param {number} options.totalResults - Total number of results
 * @param {number} options.searchTime - Search execution time in seconds
 * @param {Object} options.filters - Applied filters object
 */
export function exportSearchResults(results, options = {}) {
    const {
        format = 'csv',
        query = 'search',
        totalResults = results.length,
        searchTime = 0,
        filters = {}
    } = options;
    
    if (!results || results.length === 0) {
        alert('No results to export');
        return;
    }
    
    const timestamp = new Date().toISOString().split('T')[0];
    
    switch (format) {
        case 'csv':
            exportAsCSV(results, { query, timestamp, totalResults, searchTime, filters });
            break;
        case 'excel':
            exportAsExcel(results, { query, timestamp, totalResults, searchTime, filters });
            break;
        case 'json':
            exportAsJSON(results, { query, timestamp, totalResults, searchTime, filters });
            break;
        default:
            exportAsCSV(results, { query, timestamp, totalResults, searchTime, filters });
    }
}

/**
 * Export as CSV format
 */
function exportAsCSV(results, options) {
    const { query, timestamp } = options;
    
    const headers = [
        'File ID',
        'File Name',
        'File Type',
        'File Size (bytes)',
        'File Size (formatted)',
        'File Date',
        'File Status',
        'Source Name',
        'Source ID',
        'Side Name',
        'Side ID',
        'Relevance Score',
        'Categories',
        'Date Created',
        'Snippet'
    ];
    
    const csvRows = [headers.join(',')];
    
    results.forEach(result => {
        const row = [
            escapeCSV(result.id || ''),
            escapeCSV(result.file_name || ''),
            escapeCSV(result.file_type || ''),
            result.file_size || 0,
            escapeCSV(formatFileSize(result.file_size || 0)),
            escapeCSV(result.file_date ? new Date(result.file_date).toLocaleDateString() : ''),
            escapeCSV(result.file_status || ''),
            escapeCSV(result.source_name || ''),
            result.source_id || '',
            escapeCSV(result.side_name || ''),
            result.side_id || '',
            (result.relevance_score || 0).toFixed(2),
            escapeCSV(Array.isArray(result.categories) ? result.categories.join('; ') : ''),
            escapeCSV(result.date_creation ? new Date(result.date_creation).toLocaleDateString() : ''),
            escapeCSV(result.snippet || '')
        ];
        csvRows.push(row.join(','));
    });
    
    const csv = csvRows.join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    downloadBlob(blob, `search_results_${sanitizeFilename(query)}_${timestamp}.csv`);
}

/**
 * Export as Excel format (CSV with .xlsx extension for now)
 */
function exportAsExcel(results, options) {
    // For now, use CSV format
    // In production, use a library like SheetJS for proper Excel export
    exportAsCSV(results, options);
}

/**
 * Export as JSON format
 */
function exportAsJSON(results, options) {
    const { query, timestamp, totalResults, searchTime, filters } = options;
    
    const exportData = {
        query: query,
        timestamp: new Date().toISOString(),
        total_results: totalResults,
        search_time: searchTime,
        filters: filters,
        results: results.map(result => ({
            id: result.id,
            file_name: result.file_name,
            file_type: result.file_type,
            file_size: result.file_size,
            file_size_formatted: formatFileSize(result.file_size || 0),
            file_date: result.file_date,
            file_status: result.file_status,
            source_name: result.source_name,
            source_id: result.source_id,
            side_name: result.side_name,
            side_id: result.side_id,
            relevance_score: result.relevance_score,
            categories: result.categories || [],
            date_creation: result.date_creation,
            snippet: result.snippet
        }))
    };
    
    const json = JSON.stringify(exportData, null, 2);
    const blob = new Blob([json], { type: 'application/json;charset=utf-8;' });
    downloadBlob(blob, `search_results_${sanitizeFilename(query)}_${timestamp}.json`);
}

/**
 * Print search results
 * @param {Array} results - Array of search result objects
 * @param {Object} options - Print options
 * @param {string} options.query - Search query string
 * @param {number} options.totalResults - Total number of results
 * @param {number} options.searchTime - Search execution time in seconds
 */
export function printSearchResults(results, options = {}) {
    const {
        query = 'Search Results',
        totalResults = results.length,
        searchTime = 0
    } = options;
    
    if (!results || results.length === 0) {
        alert('No results to print');
        return;
    }
    
    const printWindow = window.open('', '_blank');
    
    const printContent = `
<!DOCTYPE html>
<html>
<head>
    <title>Search Results - ${escapeHtml(query)}</title>
    <style>
        @media print {
            @page { margin: 1cm; }
            body { font-family: Arial, sans-serif; font-size: 10pt; }
            h1 { font-size: 18pt; margin-bottom: 10pt; }
            h2 { font-size: 14pt; margin-top: 15pt; margin-bottom: 8pt; }
            table { width: 100%; border-collapse: collapse; margin-top: 10pt; page-break-inside: auto; }
            th, td { border: 1px solid #ddd; padding: 6pt; text-align: left; }
            th { background-color: #f2f2f2; font-weight: bold; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            tr { page-break-inside: avoid; page-break-after: auto; }
            .header-info { margin-bottom: 15pt; }
            .header-info p { margin: 3pt 0; }
            .no-print { display: none; }
        }
        body { font-family: Arial, sans-serif; font-size: 10pt; padding: 20px; }
        h1 { font-size: 18pt; margin-bottom: 10pt; }
        h2 { font-size: 14pt; margin-top: 15pt; margin-bottom: 8pt; }
        table { width: 100%; border-collapse: collapse; margin-top: 10pt; }
        th, td { border: 1px solid #ddd; padding: 6pt; text-align: left; }
        th { background-color: #f2f2f2; font-weight: bold; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        .header-info { margin-bottom: 15pt; }
        .header-info p { margin: 3pt 0; }
    </style>
</head>
<body>
    <h1>Search Results: ${escapeHtml(query)}</h1>
    <div class="header-info">
        <p><strong>Total Results:</strong> ${totalResults.toLocaleString()}</p>
        <p><strong>Search Time:</strong> ${searchTime} seconds</p>
        <p><strong>Date:</strong> ${new Date().toLocaleString()}</p>
    </div>
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>File Name</th>
                <th>Type</th>
                <th>Size</th>
                <th>Date</th>
                <th>Source</th>
                <th>Side</th>
                <th>Relevance</th>
            </tr>
        </thead>
        <tbody>
            ${results.map((result, index) => `
                <tr>
                    <td>${index + 1}</td>
                    <td>${escapeHtml(result.file_name || 'N/A')}</td>
                    <td>${escapeHtml(result.file_type || 'N/A')}</td>
                    <td>${formatFileSize(result.file_size || 0)}</td>
                    <td>${result.file_date ? new Date(result.file_date).toLocaleDateString() : 'N/A'}</td>
                    <td>${escapeHtml(result.source_name || 'N/A')}</td>
                    <td>${escapeHtml(result.side_name || 'N/A')}</td>
                    <td>${result.relevance_score ? (result.relevance_score * 100).toFixed(1) + '%' : 'N/A'}</td>
                </tr>
            `).join('')}
        </tbody>
    </table>
    <script>
        window.onload = function() {
            window.print();
        };
    </script>
</body>
</html>`;
    
    printWindow.document.write(printContent);
    printWindow.document.close();
}

/**
 * Helper function to escape CSV values
 */
function escapeCSV(value) {
    if (value === null || value === undefined) return '""';
    const stringValue = String(value);
    if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
        return `"${stringValue.replace(/"/g, '""')}"`;
    }
    return stringValue;
}

/**
 * Helper function to escape HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Helper function to format file size
 */
function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

/**
 * Helper function to sanitize filename
 */
function sanitizeFilename(filename) {
    return filename.replace(/[^a-z0-9]/gi, '_').toLowerCase().substring(0, 50);
}

/**
 * Helper function to download blob
 */
function downloadBlob(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
}

