// ========== Sidebar Navigation ==========
const navItems = document.querySelectorAll('.nav-item');
const pages = document.querySelectorAll('.page');
const pageTitle = document.getElementById('pageTitle');
const menuToggle = document.getElementById('menuToggle');
const sidebar = document.getElementById('sidebar');

const pageTitles = {
  dashboard: 'Dashboard',
  upload: 'Upload Doc',
  analysis: 'Analysis',
  settings: 'Settings'
};

navItems.forEach(item => {
  item.addEventListener('click', (e) => {
    e.preventDefault();
    const targetPage = item.dataset.page;
    navItems.forEach(nav => nav.classList.remove('active'));
    item.classList.add('active');
    pages.forEach(page => page.classList.remove('active'));
    document.getElementById(`page-${targetPage}`).classList.add('active');
    pageTitle.textContent = pageTitles[targetPage];
    sidebar.classList.remove('open');
    if (targetPage === 'dashboard') loadDashboard();
    if (targetPage === 'analysis') { showAnalysisList(); loadDocuments(); }
    if (targetPage === 'settings') loadSettings();
  });
});

menuToggle.addEventListener('click', () => sidebar.classList.toggle('open'));

document.addEventListener('click', (e) => {
  if (window.innerWidth <= 768 && !sidebar.contains(e.target) && !menuToggle.contains(e.target))
    sidebar.classList.remove('open');
});


// ========== Utility Functions ==========

function fmtMoney(val) {
  if (val === null || val === undefined) return '--';
  if (val === 0) return '$0';
  const abs = Math.abs(val);
  const sign = val < 0 ? '(' : '';
  const end = val < 0 ? ')' : '';
  if (abs >= 1000) return sign + '$' + (abs / 1000).toFixed(1) + 'M' + end;
  return sign + '$' + abs.toFixed(0) + 'K' + end;
}

function fmtPct(val) {
  if (val === null || val === undefined) return '--';
  return (val * 100).toFixed(1) + '%';
}

function fmtMultiple(val) {
  if (val === null || val === undefined) return '--';
  return val.toFixed(1) + 'x';
}

function formatCurrency(num) {
  if (num == null) return '--';
  if (num === 0) return '$0';
  if (num >= 1e9) return '$' + (num / 1e9).toFixed(1) + 'B';
  if (num >= 1e6) return '$' + (num / 1e6).toFixed(1) + 'M';
  if (num >= 1e3) return '$' + (num / 1e3).toFixed(0) + 'K';
  return '$' + num.toLocaleString();
}

function formatDate(dateStr) {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function escapeHtml(text) {
  if (!text) return '';
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function moneyCell(val, source) {
  if (val === null || val === undefined) return '<td>--</td>';
  const suffix = source === 'derived' ? ' <span class="derived-tag">(est.)</span>' : '';
  if (val < 0) return `<td class="negative">${fmtMoney(val)}${suffix}</td>`;
  return `<td>${fmtMoney(val)}${suffix}</td>`;
}

function pctCell(val) {
  return `<td>${fmtPct(val)}</td>`;
}

function capexCell(val, source) {
  if (val === null || val === undefined) return '<td>--</td>';
  const formatted = fmtMoney(val);
  if (!source || source === 'direct') {
    if (val < 0) return `<td class="negative">${formatted}</td>`;
    return `<td>${formatted}</td>`;
  }
  const tooltip = getCapexMethodTooltip(source);
  const badge = ` <span class="derived-badge" title="${tooltip}">(est.)</span>`;
  if (val < 0) return `<td class="negative">${formatted}${badge}</td>`;
  return `<td>${formatted}${badge}</td>`;
}

function getCapexMethodTooltip(source) {
  const tooltips = {
    'derived:capex_revenue_ratio': 'Estimated using CapEx-to-Revenue ratio from known years',
    'derived:flat_last_known': 'Estimated using most recent known CapEx value (flat)',
    'derived:depreciation_ratio': 'Estimated as 25% of Depreciation (maintenance assumption)',
    'derived:industry_default': 'Estimated using 0.5% of Revenue industry default',
    'derived': 'Estimated value — not directly from document'
  };
  return tooltips[source] || 'Estimated value — not directly from document';
}

// Helper to check if a field was derived
function getSource(sources, key, index) {
  if (!sources) return null;
  return sources[key + '_' + index] || sources[key] || null;
}


// ========== Dashboard ==========

async function loadDashboard() {
  try {
    const res = await fetch('/api/dashboard');
    const data = await res.json();
    document.getElementById('statTotal').textContent = data.total;
    document.getElementById('statAnalyzed').textContent = data.fully_analyzed || data.analyzed;
    document.getElementById('statPending').textContent = data.pending;
    document.getElementById('statAccuracy').textContent = data.accuracy + '%';
    document.getElementById('statExtractionConf').textContent = (data.avg_extraction_confidence || 0) + '%';
    document.getElementById('statDealValue').textContent = fmtMoney(data.total_deal_value);

    const activityList = document.getElementById('activityList');
    if (data.recent.length === 0) {
      activityList.innerHTML = '<div class="activity-item"><span class="activity-dot"></span><span>No recent activity yet.</span></div>';
    } else {
      activityList.innerHTML = data.recent.map(doc => {
        const displayStatus = _getOverallStatus(doc);
        const dotClass = displayStatus === 'completed' ? 'dot-success' : displayStatus === 'failed' ? 'dot-failed' : '';
        return `<div class="activity-item"><span class="activity-dot ${dotClass}"></span><span>${escapeHtml(doc.company_name || doc.original_name)}</span><span class="activity-status">${displayStatus}</span><span class="activity-date">${formatDate(doc.upload_date)}</span></div>`;
      }).join('');
    }
  } catch (err) { console.error('Failed to load dashboard:', err); }
}

function _getOverallStatus(doc) {
  if (doc.extraction_status === 'completed') return 'completed';
  if (doc.extraction_status === 'processing') return 'extracting';
  if (doc.extraction_status === 'failed') return 'failed';
  if (doc.ocr_status === 'completed') return 'ocr_done';
  if (doc.ocr_status === 'processing') return 'processing';
  if (doc.ocr_status === 'failed') return 'failed';
  return 'pending';
}


// ========== File Upload ==========

const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const uploadedFiles = document.getElementById('uploadedFiles');
const activePollers = {};

uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => { e.preventDefault(); uploadArea.classList.remove('dragover'); uploadFiles(e.dataTransfer.files); });
fileInput.addEventListener('change', () => { uploadFiles(fileInput.files); fileInput.value = ''; });

async function uploadFiles(files) {
  const formData = new FormData();
  Array.from(files).forEach(file => formData.append('files', file));
  uploadedFiles.innerHTML = '';
  Array.from(files).forEach(file => {
    const fi = document.createElement('div');
    fi.className = 'file-item';
    fi.innerHTML = `<span class="file-name">${escapeHtml(file.name)}</span><span class="file-size">Uploading...</span>`;
    uploadedFiles.appendChild(fi);
  });
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    uploadedFiles.innerHTML = '';
    data.uploaded.forEach(file => {
      const fi = document.createElement('div');
      fi.className = 'file-item'; fi.id = `upload-status-${file.id}`;
      fi.innerHTML = `<span class="file-name">${escapeHtml(file.original_name)}</span><span class="file-size ocr-status-text"><span class="spinner"></span> OCR Processing...</span>`;
      uploadedFiles.appendChild(fi);
      startPipelinePolling(file.id);
    });
  } catch (err) {
    uploadedFiles.innerHTML = '<div class="file-item"><span class="file-name" style="color:#ef4444;">Upload failed. Please try again.</span></div>';
  }
}

function startPipelinePolling(docId) {
  const interval = setInterval(async () => {
    try {
      const res = await fetch(`/api/documents/${docId}/status`);
      const data = await res.json();
      const el = document.getElementById(`upload-status-${docId}`);
      if (!el) { clearInterval(interval); return; }
      const ss = el.querySelector('.file-size');
      if (data.ocr_status === 'processing') {
        ss.innerHTML = '<span class="spinner"></span> <strong>Stage 1/2:</strong> Extracting text from PDF...';
      } else if (data.ocr_status === 'failed') {
        ss.innerHTML = `<span style="color:#ef4444;">&#10007; OCR Failed: ${escapeHtml(data.error_message || 'Unknown')}</span>`;
        clearInterval(interval); delete activePollers[docId]; loadDashboard(); return;
      }
      if (data.ocr_status === 'completed' && data.extraction_status === 'processing') {
        ss.innerHTML = '<span class="spinner"></span> <strong>Stage 2/2:</strong> AI analyzing financials...';
      } else if (data.extraction_status === 'completed') {
        ss.innerHTML = `<span style="color:#22c55e;">&#10003; Analysis Complete &mdash; ${escapeHtml(data.company_name || data.filename)}</span>`;
        clearInterval(interval); delete activePollers[docId]; loadDashboard();
      } else if (data.extraction_status === 'failed') {
        ss.innerHTML = `<span style="color:#eab308;">&#9888; OCR done but extraction failed: ${escapeHtml(data.extraction_error || 'Unknown')}</span>`;
        clearInterval(interval); delete activePollers[docId]; loadDashboard();
      } else if (data.ocr_status === 'completed' && (!data.extraction_status || data.extraction_status === 'pending')) {
        ss.innerHTML = '<span class="spinner"></span> <strong>Stage 2/2:</strong> Starting AI extraction...';
      }
    } catch (err) { console.error(`Polling error for doc ${docId}:`, err); }
  }, 3000);
  activePollers[docId] = interval;
}


// ========== Analysis / Documents List ==========

function getStatusBadge(doc) {
  const ext = doc.extraction_status, ocr = doc.ocr_status || doc.status;
  if (ext === 'completed') return '<span class="status-badge status-analyzed">Analyzed</span>';
  if (ext === 'processing') return '<span class="status-badge status-extracting"><span class="spinner-sm"></span> Extracting</span>';
  if (ext === 'failed') return '<span class="status-badge status-failed">Extract Failed</span>';
  if (ocr === 'completed') return '<span class="status-badge status-ocr-done">OCR Done</span>';
  if (ocr === 'processing') return '<span class="status-badge status-processing"><span class="spinner-sm"></span> OCR</span>';
  if (ocr === 'failed') return '<span class="status-badge status-failed">Failed</span>';
  return '<span class="status-badge status-pending">Pending</span>';
}

function showAnalysisList() {
  document.getElementById('analysisListView').style.display = '';
  document.getElementById('analysisDetailView').style.display = 'none';
}

async function loadDocuments() {
  const container = document.getElementById('documentsList');
  try {
    const res = await fetch('/api/documents');
    const docs = await res.json();
    if (docs.length === 0) { container.innerHTML = '<p class="placeholder-text">Upload a document to start analysis.</p>'; return; }
    container.innerHTML = `<table class="docs-table"><thead><tr><th>Document</th><th>Company</th><th>Size</th><th>Pages</th><th>Confidence</th><th>Uploaded</th><th>Status</th><th>Actions</th></tr></thead><tbody>${docs.map(doc => `<tr><td>${escapeHtml(doc.original_name)}</td><td>${escapeHtml(doc.company_name || '\u2014')}</td><td>${formatFileSize(doc.size)}</td><td>${doc.page_count || '\u2014'}</td><td>${doc.confidence_score > 0 ? (doc.confidence_score * 100).toFixed(0) + '%' : '\u2014'}</td><td>${formatDate(doc.upload_date)}</td><td>${getStatusBadge(doc)}</td><td class="action-buttons">${doc.extraction_status === 'completed' ? `<button class="btn-view" onclick="viewAnalysis(${doc.id})">View</button>` : ''}<button class="btn-delete" onclick="deleteDocument(${doc.id})">Delete</button></td></tr>`).join('')}</tbody></table>`;
  } catch (err) { container.innerHTML = '<p class="placeholder-text" style="color:#ef4444;">Failed to load documents.</p>'; }
}

async function deleteDocument(id) {
  try { await fetch(`/api/documents/${id}`, { method: 'DELETE' }); loadDocuments(); loadDashboard(); } catch (err) {}
}


// ========== Analysis Detail View ==========

let financialChart = null;

document.getElementById('btnBackToList').addEventListener('click', () => { showAnalysisList(); loadDocuments(); });
document.getElementById('btnToggleJson').addEventListener('click', () => {
  const v = document.getElementById('jsonViewer'), b = document.getElementById('btnToggleJson');
  if (v.style.display === 'none') { v.style.display = 'block'; b.textContent = 'Hide JSON'; }
  else { v.style.display = 'none'; b.textContent = 'Show JSON'; }
});

async function viewAnalysis(docId) {
  document.getElementById('analysisListView').style.display = 'none';
  document.getElementById('analysisDetailView').style.display = '';

  try {
    const res = await fetch(`/api/documents/${docId}/analysis`);
    const data = await res.json();

    if (!data.extraction) {
      document.getElementById('dealCompanyName').textContent = 'No extraction data';
      return;
    }

    const ext = data.extraction;
    const fin = ext.financials || {};
    const deal = ext.deal || {};
    const coll = ext.collateral || {};
    const rates = ext.rates || {};
    const qual = ext.qualitative || {};
    const conf = ext.confidence || {};
    const histYears = ext.historical_years || [];
    const projYears = ext.projection_years || [];
    const fieldSources = ext.field_sources || {};
    const derivations = ext._derivations_applied || [];

    // Deal Header
    document.getElementById('dealCompanyName').textContent = ext.company_name || data.filename;
    document.getElementById('dealSubtitle').textContent = [ext.industry, ext.geography].filter(Boolean).join(' | ');

    const confBadge = document.getElementById('dealConfidence');
    const overallConf = conf.overall_confidence || 0;
    const confPct = overallConf > 1 ? overallConf : overallConf * 100;
    confBadge.textContent = confPct.toFixed(0) + '% Confidence';
    confBadge.className = 'confidence-badge ' + (confPct >= 70 ? 'conf-high' : confPct >= 40 ? 'conf-med' : 'conf-low');
    document.getElementById('dealPipeline').textContent = data.extraction_status === 'completed' ? 'Analysis Complete' : data.extraction_status;

    // Corrections badge
    const correctionsBadge = document.getElementById('correctionsBadge');
    const corrections = ext._corrections_applied || [];
    if (correctionsBadge) {
      if (corrections.length > 0) {
        correctionsBadge.style.display = '';
        correctionsBadge.textContent = corrections.length + ' auto-correction' + (corrections.length > 1 ? 's' : '') + ' applied';
        correctionsBadge.title = corrections.join('\n');
      } else {
        correctionsBadge.style.display = 'none';
      }
    }

    // Derivations badge
    const derivBadge = document.getElementById('derivationsBadge');
    if (derivBadge) {
      if (derivations.length > 0) {
        derivBadge.style.display = '';
        derivBadge.textContent = derivations.length + ' derived value' + (derivations.length > 1 ? 's' : '');
        derivBadge.title = derivations.join('\n');
      } else {
        derivBadge.style.display = 'none';
      }
    }

    // Data integrity warnings
    renderIntegrityWarnings(ext);

    // KPI Cards
    document.getElementById('kpiPurchasePrice').textContent = fmtMoney(deal.purchase_price_calculated);
    document.getElementById('kpiEntryMultiple').textContent = fmtMultiple(deal.entry_multiple);
    document.getElementById('kpiRevenue').textContent = fmtMoney(deal.revenue_ltm);
    document.getElementById('kpiEbitda').textContent = fmtMoney(deal.ebitda_ltm);
    document.getElementById('kpiEV').textContent = fmtMoney(deal.enterprise_value);
    document.getElementById('kpiLeverage').textContent = deal.leverage_ratio != null ? deal.leverage_ratio.toFixed(1) + 'x' : '0.0x';

    // Chart
    renderFinancialChart(fin, histYears, projYears);

    // Tables
    renderHistoricalTable(fin, histYears, fieldSources);
    renderProjectionsTable(fin, projYears, fieldSources);

    // Collateral
    renderCollateral(coll);

    // Deal Structure
    renderDealStructure(deal, rates, coll, fieldSources);

    // Highlights & Risks
    renderList('highlightsList', qual.key_highlights || []);
    renderList('risksList', qual.risks || []);

    // Confidence
    renderConfidenceGrid(conf);
    renderFieldConfidence(conf.field_level || {});

    // Raw JSON
    document.getElementById('jsonViewer').textContent = JSON.stringify(ext, null, 2);
    document.getElementById('jsonViewer').style.display = 'none';
    document.getElementById('btnToggleJson').textContent = 'Show JSON';

  } catch (err) {
    console.error('Failed to load analysis:', err);
    document.getElementById('dealCompanyName').textContent = 'Error loading analysis';
  }
}


// ========== Data Integrity Warnings ==========

function renderIntegrityWarnings(ext) {
  const warnings = [];
  const f = ext.financials || {};
  const corrections = ext._corrections_applied || [];
  const derivations = ext._derivations_applied || [];

  // Check 1: Revenue completeness
  const revHist = f.net_revenue_hist || [];
  const nullRevCount = revHist.filter(v => v === null).length;
  if (nullRevCount > 0) {
    warnings.push({
      level: 'error',
      message: `Revenue missing for ${nullRevCount} of 3 historical year(s). Margins and growth rates cannot be calculated.`
    });
  }

  // Check 2: EBITDA integrity vs Operating Income
  const gp = f.gross_profit_hist || [];
  const sga = f.sga_hist || [];
  const ebitda = f.adj_ebitda_hist || [];
  for (let i = 0; i < 3; i++) {
    if (gp[i] != null && sga[i] != null && ebitda[i] != null) {
      const oi = gp[i] - sga[i];
      if (oi > ebitda[i]) {
        warnings.push({
          level: 'error',
          message: `Year ${i+1}: Operating Income (${fmtMoney(oi)}) exceeds EBITDA (${fmtMoney(ebitda[i])}) — rows may be swapped`
        });
      }
    }
  }

  // Check 3: Depreciation = EBITDA (copy bug)
  const dep = f.depreciation_hist || [];
  for (let i = 0; i < 3; i++) {
    if (dep[i] != null && ebitda[i] != null && Math.abs(dep[i] - ebitda[i]) < Math.abs(ebitda[i]) * 0.15) {
      warnings.push({
        level: 'error',
        message: `Year ${i+1}: Depreciation (${fmtMoney(dep[i])}) nearly equals EBITDA (${fmtMoney(ebitda[i])}) — likely a copy error`
      });
    }
  }

  // Check 4: Corrections/derivations applied
  if (corrections.length > 0 || derivations.length > 0) {
    warnings.push({
      level: 'info',
      message: `${corrections.length} auto-correction(s) and ${derivations.length} derivation(s) applied`
    });
  }

  const container = document.getElementById('integrityWarnings');
  const countEl = document.getElementById('warningCount');
  const listEl = document.getElementById('warningList');

  if (!container) return;

  if (warnings.length === 0) {
    container.style.display = 'none';
    return;
  }

  const errorCount = warnings.filter(w => w.level === 'error').length;
  container.style.display = '';
  container.className = 'integrity-warnings' + (errorCount > 0 ? ' has-errors' : '');
  countEl.textContent = warnings.length + ' data quality issue' + (warnings.length > 1 ? 's' : '') + ' detected';
  listEl.innerHTML = warnings.map(w =>
    `<div class="warning-row ${w.level}">${w.level === 'error' ? '&#10007;' : '&#9432;'} ${escapeHtml(w.message)}</div>`
  ).join('');
}

// Warning toggle
document.getElementById('warningToggle').addEventListener('click', () => {
  const list = document.getElementById('warningList');
  list.style.display = list.style.display === 'none' ? '' : 'none';
});


// ========== Chart ==========

function renderFinancialChart(fin, histYears, projYears) {
  const ctx = document.getElementById('financialChart').getContext('2d');
  if (financialChart) financialChart.destroy();

  const revH = fin.net_revenue_hist || [];
  const revP = fin.net_revenue_proj || [];
  const ebitdaH = fin.adj_ebitda_hist || [];
  const ebitdaP = fin.adj_ebitda_proj || [];
  const marginH = fin.ebitda_margin_hist || [];
  const marginP = fin.ebitda_margin_proj || [];

  // Filter null years from projections for chart labels
  const validProjYears = projYears.filter(y => y !== null);
  const allLabels = [...histYears, ...validProjYears];
  const nHist = histYears.length;
  const nProj = validProjYears.length;
  const revenueData = [...revH.slice(0, nHist), ...revP.slice(0, nProj)];
  const ebitdaData = [...ebitdaH.slice(0, nHist), ...ebitdaP.slice(0, nProj)];
  const marginData = [...marginH.slice(0, nHist), ...marginP.slice(0, nProj)]
    .map(v => v != null ? parseFloat((v * 100).toFixed(1)) : null);

  const isDark = !document.body.classList.contains('light');
  const gridColor = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
  const textColor = isDark ? '#a1a1aa' : '#71717a';

  financialChart = new Chart(ctx, {
    data: {
      labels: allLabels,
      datasets: [
        {
          type: 'bar', label: 'Revenue', data: revenueData,
          backgroundColor: 'rgba(99, 102, 241, 0.8)', borderColor: '#6366f1',
          borderWidth: 1, borderRadius: 4, yAxisID: 'y', order: 2
        },
        {
          type: 'bar', label: 'EBITDA', data: ebitdaData,
          backgroundColor: 'rgba(34, 197, 94, 0.8)', borderColor: '#22c55e',
          borderWidth: 1, borderRadius: 4, yAxisID: 'y', order: 3
        },
        {
          type: 'line', label: 'EBITDA Margin %', data: marginData,
          borderColor: 'rgba(251, 146, 60, 1)', backgroundColor: 'transparent',
          pointBackgroundColor: 'rgba(251, 146, 60, 1)', borderWidth: 2,
          pointRadius: 4, yAxisID: 'y2', tension: 0.3, order: 1
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: textColor } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              if (ctx.dataset.yAxisID === 'y2') return ctx.dataset.label + ': ' + (ctx.raw != null ? ctx.raw + '%' : 'N/A');
              return ctx.dataset.label + ': ' + (ctx.raw != null ? fmtMoney(ctx.raw) : 'N/A');
            }
          }
        }
      },
      scales: {
        x: { grid: { color: gridColor }, ticks: { color: textColor } },
        y: {
          type: 'linear', position: 'left',
          grid: { color: gridColor },
          ticks: { color: textColor, callback: v => fmtMoney(v) }
        },
        y2: {
          type: 'linear', position: 'right',
          grid: { drawOnChartArea: false },
          ticks: { color: textColor, callback: v => v + '%' },
          min: 0, max: 50
        }
      }
    }
  });
}


// ========== Historical Table ==========

function renderHistoricalTable(fin, years, sources) {
  const container = document.getElementById('historicalTable');
  if (!years.length) { container.innerHTML = '<p class="placeholder-text">No historical data available.</p>'; return; }

  const rev = fin.net_revenue_hist || [];
  const revGrowth = fin.revenue_growth_hist || [];
  const gp = fin.gross_profit_hist || [];
  const gmPct = fin.gm_pct_hist || [];
  const sga = fin.sga_hist || [];
  const adj = fin.adjustments_hist || [];
  const ebitda = fin.adj_ebitda_hist || [];
  const margin = fin.ebitda_margin_hist || [];
  const dep = fin.depreciation_hist || [];
  const capex = fin.capex_hist || [];

  const n = years.length;
  const opIncome = [];
  for (let i = 0; i < n; i++) {
    opIncome.push(gp[i] != null && sga[i] != null ? gp[i] - sga[i] : null);
  }

  function row(label, arr, fmt, cls, sourceKey) {
    const c = cls ? ` class="${cls}"` : '';
    return `<tr${c}><td class="row-label">${label}</td>${arr.slice(0, n).map((v, i) => fmt(v, sourceKey ? getSource(sources, sourceKey, i) : null)).join('')}</tr>`;
  }

  function staticRow(label, arr, text, cls) {
    const c = cls ? ` class="${cls}"` : '';
    return `<tr${c}><td class="row-label">${label}</td>${arr.slice(0, n).map(() => `<td class="hint-text">${text}</td>`).join('')}</tr>`;
  }

  container.innerHTML = `<table class="financial-table"><thead><tr><th>$000s</th>${years.map(y => `<th>${escapeHtml(y)}</th>`).join('')}</tr></thead><tbody>
    ${row('Revenue', rev, moneyCell)}
    ${row('Revenue Growth', revGrowth, pctCell)}
    ${row('Gross Profit', gp, moneyCell)}
    ${row('GP Margin %', gmPct, pctCell)}
    ${row('SG&A', sga, moneyCell, '', 'sga_hist')}
    <tr class="separator-row"><td colspan="${n + 1}"></td></tr>
    ${row('Operating Income', opIncome, moneyCell, '', 'sga_hist')}
    ${row('Add-backs / 1X Adj.', adj, moneyCell, '', 'adjustments_hist')}
    ${row('Adj. EBITDA', ebitda, moneyCell, 'bold-row')}
    ${row('EBITDA Margin %', margin, pctCell, 'bold-row')}
    <tr class="separator-row"><td colspan="${n + 1}"></td></tr>
    ${row('Depreciation', dep, moneyCell, '', 'depreciation_hist')}
    ${row('CapEx', capex, capexCell, '', 'capex_hist')}
    ${staticRow('Net Income', Array(n).fill(null), 'Not in CIM')}
    ${staticRow('Free Cash Flow', Array(n).fill(null), 'See Model')}
  </tbody></table>`;
}


// ========== Projections Table ==========

function renderProjectionsTable(fin, years, sources) {
  const container = document.getElementById('projectionsTable');
  const validYears = years.filter(y => y !== null);
  if (!validYears.length) { container.innerHTML = '<p class="placeholder-text">No projection data available.</p>'; return; }

  const rev = fin.net_revenue_proj || [];
  const gp = fin.gross_profit_proj || [];
  const gmPct = fin.gm_pct_proj || [];
  const sga = fin.sga_proj || [];
  const adj = fin.adjustments_proj || [];
  const ebitda = fin.adj_ebitda_proj || [];
  const margin = fin.ebitda_margin_proj || [];
  const dep = fin.depreciation_proj || [];
  const capex = fin.capex_proj || [];
  const mgmt = fin.mgmt_fees_proj || [];

  const n = validYears.length;
  years = validYears;
  const revGrowth = [null];
  for (let i = 1; i < n; i++) {
    if (rev[i] && rev[i - 1] && rev[i - 1] !== 0) revGrowth.push((rev[i] - rev[i - 1]) / rev[i - 1]);
    else revGrowth.push(null);
  }

  function row(label, arr, fmt, cls, sourceKey) {
    const c = cls ? ` class="${cls}"` : '';
    return `<tr${c}><td class="row-label">${label}</td>${arr.slice(0, n).map((v, i) => fmt(v, sourceKey ? getSource(sources, sourceKey, i) : null)).join('')}</tr>`;
  }

  container.innerHTML = `<table class="financial-table"><thead><tr><th>$000s</th>${years.map(y => `<th>${escapeHtml(y)}</th>`).join('')}</tr></thead><tbody>
    ${row('Revenue', rev, moneyCell)}
    ${row('Revenue Growth', revGrowth, pctCell)}
    ${row('Gross Profit', gp, moneyCell)}
    ${row('GP Margin %', gmPct, pctCell)}
    ${row('SG&A', sga, moneyCell)}
    ${row('Adj. EBITDA', ebitda, moneyCell, 'bold-row')}
    ${row('EBITDA Margin %', margin, pctCell, 'bold-row')}
    <tr class="separator-row"><td colspan="${n + 1}"></td></tr>
    ${row('Depreciation', dep, moneyCell, '', 'depreciation_proj')}
    ${row('CapEx', capex, capexCell, '', 'capex_proj')}
    ${row('Mgmt Fees', mgmt, moneyCell)}
  </tbody></table>`;
}


// ========== Collateral ==========

function renderCollateral(coll) {
  const container = document.getElementById('collateralGrid');
  const items = [
    { label: 'Accounts Receivable', value: coll.ar_value, rate: coll.ar_advance_rate || 0.75 },
    { label: 'Inventory', value: coll.inventory_value, rate: coll.inventory_advance_rate || 0.70 },
    { label: 'M&E Equipment', value: coll.equipment_value, rate: coll.equipment_advance_rate || 0 },
    { label: 'Building & Land', value: coll.building_land_value, rate: coll.building_advance_rate || 0 },
  ];

  const hasAny = items.some(i => i.value != null);
  if (!hasAny) { container.innerHTML = '<p class="placeholder-text">No collateral data in document.</p>'; return; }

  let totalAvail = 0;
  const rows = items.map(i => {
    const avail = i.value ? i.value * i.rate : 0;
    totalAvail += avail;
    return `<tr><td class="row-label">${i.label}</td><td>${fmtMoney(i.value)}</td><td>${fmtPct(i.rate)}</td><td>${avail > 0 ? fmtMoney(avail) : '--'}</td></tr>`;
  }).join('');

  container.innerHTML = `<table class="financial-table"><thead><tr><th>Asset</th><th>Gross Value</th><th>Advance Rate</th><th>ABL Availability</th></tr></thead><tbody>${rows}<tr class="bold-row"><td class="row-label">TOTAL</td><td></td><td></td><td>${fmtMoney(totalAvail)}</td></tr></tbody></table>`;
}


// ========== Deal Structure ==========

function renderDealStructure(deal, rates, coll, sources) {
  const container = document.getElementById('dealStructureGrid');

  function dealVal(val, fmt, sourceKey) {
    const formatted = fmt(val);
    if (formatted === '--') return '--';
    const isDerived = sources && sources[sourceKey] === 'derived';
    return formatted + (isDerived ? ' <span class="derived-tag">(est.)</span>' : '');
  }

  const items = [
    ['Purchase Price', dealVal(deal.purchase_price_calculated, fmtMoney, 'purchase_price')],
    ['Entry Multiple', dealVal(deal.entry_multiple, fmtMultiple, 'entry_multiple')],
    ['% Acquired', deal.pct_acquired != null ? fmtPct(deal.pct_acquired) : '100%'],
    ['Exit Multiple', fmtMultiple(deal.exit_multiple)],
    ['Leverage Ratio', deal.leverage_ratio != null ? deal.leverage_ratio.toFixed(1) + 'x' : '0.0x'],
    ['ABL Rate', fmtPct(rates.abl_rate)],
    ['Term Rate', fmtPct(rates.term_rate)],
    ['ABL Availability', fmtMoney(coll.abl_availability_calculated)],
  ];

  container.innerHTML = `<table class="deal-structure-table"><tbody>${items.map(([label, value]) =>
    `<tr><td class="row-label">${label}</td><td class="deal-value">${value}</td></tr>`
  ).join('')}</tbody></table>`;
}


// ========== Lists ==========

function renderList(elementId, items) {
  const el = document.getElementById(elementId);
  if (!items.length) { el.innerHTML = '<li class="placeholder-text">None found.</li>'; return; }
  el.innerHTML = items.map(item => `<li>${escapeHtml(item)}</li>`).join('');
}


// ========== Confidence ==========

function renderConfidenceGrid(conf) {
  const container = document.getElementById('confidenceGrid');
  const sections = [
    { label: 'Deal Overview', key: 'deal_overview_confidence' },
    { label: 'Financial Summary', key: 'financial_summary_confidence' },
    { label: 'Deal Metrics', key: 'deal_metrics_confidence' },
    { label: 'Collateral', key: 'collateral_confidence' },
    { label: 'Projections', key: 'projections_confidence' },
    { label: 'Overall', key: 'overall_confidence' },
  ];

  container.innerHTML = sections.map(s => {
    const raw = conf[s.key] || 0;
    const pct = raw > 1 ? raw : Math.round(raw * 100);
    const cls = pct >= 70 ? 'conf-high' : pct >= 40 ? 'conf-med' : 'conf-low';
    return `<div class="confidence-item"><div class="confidence-label">${s.label}</div><div class="confidence-bar-bg"><div class="confidence-bar ${cls}" style="width:${pct}%"></div></div><div class="confidence-pct ${cls}">${pct}%</div></div>`;
  }).join('');
}

function renderFieldConfidence(fieldLevel) {
  const container = document.getElementById('fieldConfidenceGrid');
  const fields = [
    ['Net Revenue', 'net_revenue'], ['Gross Profit', 'gross_profit'],
    ['SG&A', 'sga'], ['Adj. EBITDA', 'adj_ebitda'],
    ['Adjustments', 'adjustments'], ['Depreciation', 'depreciation'],
    ['CapEx', 'capex'], ['Projections', 'projections'],
    ['AR Value', 'ar_value'], ['Inventory', 'inventory'],
    ['Entry Multiple', 'entry_multiple'], ['Purchase Price', 'purchase_price'],
    ['ABL Rate', 'abl_rate'], ['Exit Multiple', 'exit_multiple'],
  ];

  const colorMap = {
    high: 'badge-high', medium: 'badge-medium', low: 'badge-low', not_found: 'badge-none'
  };

  container.innerHTML = fields.map(([label, key]) => {
    const level = fieldLevel[key] || 'not_found';
    const cls = colorMap[level] || 'badge-none';
    return `<div class="field-badge-item"><span class="field-badge-label">${label}</span><span class="field-badge ${cls}">${level.toUpperCase().replace('_', ' ')}</span></div>`;
  }).join('');
}


// ========== Settings ==========

const darkModeToggle = document.getElementById('darkModeToggle');
const notificationsToggle = document.getElementById('notificationsToggle');
const autoAnalyzeToggle = document.getElementById('autoAnalyzeToggle');

async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const settings = await res.json();
    darkModeToggle.checked = settings.dark_mode === 'true';
    notificationsToggle.checked = settings.notifications === 'true';
    autoAnalyzeToggle.checked = settings.auto_analyze === 'true';
    document.body.classList.toggle('light', !darkModeToggle.checked);
  } catch (err) { console.error('Failed to load settings:', err); }
}

async function saveSetting(key, value) {
  try { await fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [key]: value }) }); } catch (err) {}
}

darkModeToggle.addEventListener('change', () => { document.body.classList.toggle('light', !darkModeToggle.checked); saveSetting('dark_mode', darkModeToggle.checked.toString()); });
notificationsToggle.addEventListener('change', () => saveSetting('notifications', notificationsToggle.checked.toString()));
autoAnalyzeToggle.addEventListener('change', () => saveSetting('auto_analyze', autoAnalyzeToggle.checked.toString()));


// ========== Initial Load ==========
loadDashboard();
loadSettings();
