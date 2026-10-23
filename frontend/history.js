/**
 * StockPulse — Alert History & Outcome Verification Page
 */

(function () {
  'use strict';

  let allHistory = [];
  let filterResult = 'ALL';
  let searchQuery = '';

  const el = {
    kpiTotal: document.getElementById('kpi-total-alerts'),
    kpiHitRate: document.getElementById('kpi-hit-rate'),
    kpiTruePos: document.getElementById('kpi-true-pos'),
    kpiFalseAlarms: document.getElementById('kpi-false-alarms'),
    filterSelect: document.getElementById('history-filter-result'),
    searchInput: document.getElementById('history-search-input'),
    btnRefresh: document.getElementById('btn-refresh-history'),
    btnClear: document.getElementById('btn-clear-history'),
    tableBody: document.getElementById('history-table-body'),
    emptyState: document.getElementById('history-empty-state')
  };

  async function loadHistory() {
    try {
      const [resHistory, resStats] = await Promise.all([
        fetch('/api/history?limit=100'),
        fetch('/api/history/stats')
      ]);

      if (resHistory.ok) {
        allHistory = await resHistory.json();
        renderTable();
      }

      if (resStats.ok) {
        const stats = await resStats.json();
        renderKPIs(stats);
      }
    } catch (err) {
      console.warn('[History] Error loading alert history:', err);
    }
  }

  function renderKPIs(stats) {
    if (!stats) return;
    const total = stats.total_evaluated || stats.total_alerts || allHistory.length || 0;
    const hitRate = stats.precision_pct || stats.hit_rate_pct || (stats.true_positives ? Math.round((stats.true_positives / (stats.true_positives + (stats.false_positives || 0))) * 100) : 0);
    const truePos = stats.true_positives || 0;
    const falsePos = stats.false_positives || (total - truePos > 0 ? total - truePos : 0);

    if (el.kpiTotal) el.kpiTotal.textContent = total;
    if (el.kpiHitRate) el.kpiHitRate.textContent = `${hitRate}%`;
    if (el.kpiTruePos) el.kpiTruePos.textContent = truePos;
    if (el.kpiFalseAlarms) el.kpiFalseAlarms.textContent = falsePos;
  }

  function renderTable() {
    if (!el.tableBody) return;

    let filtered = allHistory;
    if (filterResult !== 'ALL') {
      filtered = filtered.filter(row => (row.result || row.status || '').toUpperCase() === filterResult);
    }
    if (searchQuery) {
      filtered = filtered.filter(row => 
        (row.symbol || '').toUpperCase().includes(searchQuery) ||
        String(row.id || '').includes(searchQuery)
      );
    }

    if (!filtered || filtered.length === 0) {
      el.tableBody.innerHTML = '';
      if (el.emptyState) el.emptyState.style.display = 'block';
      return;
    }

    if (el.emptyState) el.emptyState.style.display = 'none';
    el.tableBody.innerHTML = '';

    filtered.forEach(item => {
      const tr = document.createElement('tr');
      const resVal = (item.result || item.status || 'PENDING').toUpperCase();
      let badgeClass = 'pending';
      if (resVal === 'SUCCESS' || resVal === 'HIT' || resVal === 'TRUE_POSITIVE') badgeClass = 'success';
      else if (resVal === 'FAILED' || resVal === 'FALSE_POSITIVE') badgeClass = 'failed';
      else if (resVal === 'STOPPED') badgeClass = 'stopped';

      const fmt = (window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST : (s) => s ? new Date(s + 'Z').toLocaleTimeString('en-IN') : '--:--:--';
      const triggeredTime = item.triggered_at ? fmt(item.triggered_at, 'short') : '--:--';
      const createdDate = item.created_at ? fmt(item.created_at, 'date') : '--';
      const rawTarget = item.target_percent != null ? item.target_percent : (item.target_movement_pct || item.target_pct || 5.0);
      const numTarget = parseFloat(rawTarget);
      const absTarget = Math.abs(isNaN(numTarget) ? 5.0 : numTarget);
      const dirStr = String(item.direction || '').toUpperCase();

      const isDown = dirStr === 'DOWN' || (numTarget < 0 && dirStr !== 'UP');
      const isBoth = dirStr === 'BOTH';

      const targetSign = isDown ? '-' : (isBoth ? '±' : '+');
      const targetColor = isDown ? '#dc2626' : (isBoth ? '#2563eb' : '#16a34a');
      const dirIcon = isDown ? '<i class="fa-solid fa-arrow-trend-down"></i>' : (isBoth ? '<i class="fa-solid fa-arrows-up-down"></i>' : '<i class="fa-solid fa-arrow-trend-up"></i>');
      const formattedTarget = Number(absTarget).toFixed(2);
      const targetDisplay = `${targetSign}${formattedTarget}%`;

      const trigPrice = item.triggered_price || item.current_price || 0;
      const targetPrice = item.target_price || 0;

      tr.innerHTML = `
        <td style="font-family: var(--font-mono); font-size: 12px; color: #64748b;">#${item.id}</td>
        <td>
          <strong style="color: #0f172a;">${item.symbol}</strong>
          <span style="font-size: 11px; background: #e2e8f0; color: #475569; padding: 1px 5px; border-radius: 4px; margin-left: 4px;">NSE</span>
        </td>
        <td><strong style="color: ${targetColor}; font-family: var(--font-mono);">${targetDisplay}</strong></td>
        <td><span style="font-weight: 700; color: ${targetColor}; display: inline-flex; align-items: center; gap: 4px;">${dirIcon} ${dirStr || (isDown ? 'DOWN' : 'UP')}</span></td>
        <td style="font-family: var(--font-mono);">₹${trigPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
        <td style="font-family: var(--font-mono);">₹${targetPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
        <td style="font-size: 12px; color: #64748b;">
          <div>${triggeredTime}</div>
          <div style="font-size: 10px; color: #94a3b8; margin-top: 2px;">${createdDate}</div>
        </td>
        <td><span class="outcome-badge ${badgeClass}">${resVal}</span></td>
        <td style="font-size: 12px;"><i class="fa-solid fa-bullhorn" style="color: #2563eb;"></i> ${item.notification_method || item.channel || item.notification_channel || 'Popup'}</td>
      `;

      el.tableBody.appendChild(tr);
    });
  }

  // --- FILTERS & SEARCH ---
  if (el.filterSelect) {
    el.filterSelect.addEventListener('change', (e) => {
      filterResult = e.target.value.toUpperCase();
      renderTable();
    });
  }

  if (el.searchInput) {
    el.searchInput.addEventListener('input', (e) => {
      searchQuery = e.target.value.trim().toUpperCase();
      renderTable();
    });
  }

  if (el.btnRefresh) {
    el.btnRefresh.addEventListener('click', () => {
      loadHistory();
    });
  }

  if (el.btnClear) {
    el.btnClear.addEventListener('click', async () => {
      if (!confirm('Are you sure you want to clear all historical alert logs?')) return;
      try {
        await fetch('/api/history/clear', { method: 'DELETE' });
        loadHistory();
      } catch (e) {
        console.error(e);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
    setInterval(loadHistory, 10000);
  });

})();
