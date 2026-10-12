/**
 * StockPulse — Dynamic Monitoring & Alert Configuration Page
 */

(function () {
  'use strict';

  let currentStock = StockPulse.getSelectedStock() || 'TCS';
  let targetPct = 1.20;
  let windowMin = 2;
  let direction = 'UP';
  let notificationChannel = 'Popup';
  let isMonitoringActive = false;
  let pollSessionsTimer = null;

  // DOM Elements
  const el = {
    stockInput: document.getElementById('cfg-stock-input'),
    btnToggleStockDropdown: document.getElementById('btn-toggle-stock-dropdown'),
    stockDropdown: document.getElementById('stock-control-dropdown'),
    targetPctInput: document.getElementById('cfg-target-pct'),
    targetSuffix: document.getElementById('cfg-target-suffix'),
    targetLiveHint: document.getElementById('target-live-hint'),
    targetHintBadge: document.getElementById('target-hint-badge'),
    targetHintText: document.getElementById('target-hint-text'),
    pctPresets: document.getElementById('pct-presets'),
    warningWindowSelect: document.getElementById('cfg-warning-window'),
    windowPresets: document.getElementById('window-presets'),
    directionSelect: document.getElementById('cfg-direction'),
    dirModeAux: document.getElementById('dir-mode-aux'),
    hudAdaptiveThresholdVal: document.getElementById('hud-adaptive-threshold-val'),
    hudModelHorizonVal: document.getElementById('hud-model-horizon-val'),
    btnResetConfig: document.getElementById('btn-reset-config'),
    btnStartMonitoring: document.getElementById('btn-start-monitoring'),
    btnStartLabel: document.getElementById('btn-start-label'),
    activeConfigPill: document.getElementById('active-config-pill'),
    indicatorSummaryText: document.getElementById('indicator-summary-text'),
    activeConfigBanner: document.getElementById('active-config-banner'),
    bannerSymbol: document.getElementById('banner-symbol'),
    bannerDirection: document.getElementById('banner-direction'),
    bannerTarget: document.getElementById('banner-target'),
    bannerWindow: document.getElementById('banner-window'),
    activeSessionsSection: document.getElementById('active-sessions-section'),
    activeCardsGrid: document.getElementById('active-cards-grid'),
    // Modal
    modalBackdrop: document.getElementById('config-modal-backdrop'),
    btnCloseModal: document.getElementById('btn-close-modal'),
    btnDismissModal: document.getElementById('btn-dismiss-modal'),
    btnAcknowledgeModal: document.getElementById('btn-acknowledge-modal'),
    modalStock: document.getElementById('modal-summary-stock'),
    modalPrice: document.getElementById('modal-summary-price'),
    modalTargetPrice: document.getElementById('modal-summary-target-price'),
    modalTarget: document.getElementById('modal-summary-target'),
    modalWindow: document.getElementById('modal-summary-window'),
    modalDirection: document.getElementById('modal-summary-direction'),
    modalNotif: document.getElementById('modal-summary-notif'),
    prevLivePrice: document.getElementById('prev-live-price'),
    prevTargetPrice: document.getElementById('prev-target-price'),
    prevProfit100: document.getElementById('prev-profit-100'),

    // Target Feasibility & Historical Analysis Elements
    feasibilityCard: document.getElementById('target-feasibility-card'),
    feasibilityStatusBadge: document.getElementById('feasibility-status-badge'),
    feasibilityStatusText: document.getElementById('feasibility-status-text'),
    feasibilitySubtext: document.getElementById('feasibility-subtext'),
    feasibilityScoreNumber: document.getElementById('feasibility-score-number'),
    feasibilityProgressFill: document.getElementById('feasibility-progress-fill'),
    fStatAdr: document.getElementById('f-stat-adr'),
    fStatMax: document.getElementById('f-stat-max'),
    fStatDistance: document.getElementById('f-stat-distance'),
    fStatFreq: document.getElementById('f-stat-freq'),
    feasibilityExplanation: document.getElementById('feasibility-explanation'),
    btnUseRecommendedTarget: document.getElementById('btn-use-recommended-target'),
    btnRecommendedLabel: document.getElementById('btn-recommended-label'),

    // Target Feasibility Confirmation Modal
    feasibilityModal: document.getElementById('target-feasibility-modal'),
    modalTargetPctVal: document.getElementById('modal-target-pct-val'),
    modalScoreVal: document.getElementById('modal-score-val'),
    modalSymbolVal: document.getElementById('modal-symbol-val'),
    modalStatAdr: document.getElementById('modal-stat-adr'),
    modalStatMax: document.getElementById('modal-stat-max'),
    modalStatNeeded: document.getElementById('modal-stat-needed'),
    modalStatFreq: document.getElementById('modal-stat-freq'),
    modalRecVal: document.getElementById('modal-rec-val'),
    btnModalAutoAdjust: document.getElementById('btn-modal-auto-adjust'),
    btnModalProceedAnyway: document.getElementById('btn-modal-proceed-anyway')
  };

  let cachedStockSnapshot = null;
  let currentFeasibilityResult = null;
  let feasibilityDebounceTimer = null;
  let bypassFeasibilityCheck = false;

  function triggerFeasibilityEvaluation() {
    clearTimeout(feasibilityDebounceTimer);
    feasibilityDebounceTimer = setTimeout(async () => {
      await evaluateTargetFeasibilityLive();
    }, 250);
  }

  async function evaluateTargetFeasibilityLive() {
    const sym = el.stockInput ? el.stockInput.value.trim().toUpperCase() : currentStock;
    if (!sym) return;

    const rawVal = el.targetPctInput ? el.targetPctInput.value.trim() : '+1.20';
    const mag = extractTargetMagnitude(rawVal);
    const selectedDir = ((el.directionSelect ? el.directionSelect.value : direction) || 'UP').trim().toUpperCase();
    const isDown = selectedDir === 'DOWN';
    const parsedTarget = (isDown ? -1 : 1) * mag;

    const parsedWindow = parseInt(el.warningWindowSelect ? el.warningWindowSelect.value : windowMin, 10) || windowMin || 2;

    try {
      const res = await fetch('/api/alerts/validate_target', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: sym,
          target_percent: parsedTarget,
          warning_minutes: parsedWindow,
          direction: selectedDir
        })
      });

      if (!res.ok) return;
      const data = await res.json();
      currentFeasibilityResult = data;
      renderFeasibilityCard(data);
    } catch (e) {
      console.warn('[StockPulse] Feasibility validation notice:', e);
    }
  }

  function renderFeasibilityCard(data) {
    if (!data) return;

    const score = data.achievability_percent != null ? data.achievability_percent : 0;
    const statusColor = data.status_color || '#10b981';

    // 1. Status Badge
    if (el.feasibilityStatusBadge && el.feasibilityStatusText) {
      el.feasibilityStatusBadge.className = 'feasibility-badge';
      if (score >= 70) {
        el.feasibilityStatusBadge.classList.add('status-green');
      } else if (score >= 40) {
        el.feasibilityStatusBadge.classList.add('status-amber');
      } else if (score >= 20) {
        el.feasibilityStatusBadge.classList.add('status-orange');
      } else {
        el.feasibilityStatusBadge.classList.add('status-red');
      }
      el.feasibilityStatusText.textContent = data.badge_text || `${score}% Achievable`;
    }

    // 2. Score Number
    if (el.feasibilityScoreNumber) {
      el.feasibilityScoreNumber.textContent = `${score}%`;
      el.feasibilityScoreNumber.style.color = statusColor;
    }

    // 3. Progress Track
    if (el.feasibilityProgressFill) {
      el.feasibilityProgressFill.style.width = `${Math.max(4, score)}%`;
      el.feasibilityProgressFill.style.background = statusColor;
    }

    // 4. Stats Grid
    const stats = data.historical_stats || {};
    if (el.fStatAdr) {
      el.fStatAdr.textContent = stats.average_daily_range_pct != null ? `${stats.average_daily_range_pct.toFixed(2)}%` : '--%';
    }
    if (el.fStatMax) {
      el.fStatMax.textContent = stats.max_daily_move_pct != null ? `${stats.max_daily_move_pct.toFixed(2)}%` : '--%';
    }
    if (el.fStatDistance) {
      el.fStatDistance.textContent = data.needed_distance_pct != null ? `+${data.needed_distance_pct.toFixed(2)}%` : '--%';
    }
    if (el.fStatFreq) {
      el.fStatFreq.textContent = stats.historical_hit_frequency_pct != null ? `${stats.historical_hit_frequency_pct.toFixed(1)}% of days` : '--%';
    }

    // 5. Explanation
    if (el.feasibilityExplanation) {
      el.feasibilityExplanation.textContent = data.explanation || '';
    }

    // 6. Recommended Quick-Pill
    if (el.btnUseRecommendedTarget && el.btnRecommendedLabel) {
      if (!data.is_valid && data.recommended_target_percent != null) {
        el.btnUseRecommendedTarget.style.display = 'inline-flex';
        const recSign = data.recommended_target_percent >= 0 ? '+' : '';
        el.btnRecommendedLabel.textContent = `Auto-Adjust to Recommended (${recSign}${data.recommended_target_percent.toFixed(2)}%)`;
      } else {
        el.btnUseRecommendedTarget.style.display = 'none';
      }
    }
  }

  function openFeasibilityWarningModal(data) {
    if (!el.feasibilityModal) return;
    const targetSign = data.target_percent >= 0 ? '+' : '';
    if (el.modalTargetPctVal) el.modalTargetPctVal.textContent = `${targetSign}${data.target_percent.toFixed(2)}%`;
    if (el.modalScoreVal) {
      el.modalScoreVal.textContent = `${data.achievability_percent}%`;
      el.modalScoreVal.style.color = data.status_color || '#ef4444';
    }
    if (el.modalSymbolVal) el.modalSymbolVal.textContent = data.symbol;

    const stats = data.historical_stats || {};
    if (el.modalStatAdr) el.modalStatAdr.textContent = `Avg Daily Range: ${stats.average_daily_range_pct ? stats.average_daily_range_pct.toFixed(2) : '--'}%`;
    if (el.modalStatMax) el.modalStatMax.textContent = `${stats.max_daily_move_pct ? stats.max_daily_move_pct.toFixed(2) : '--'}%`;
    if (el.modalStatNeeded) el.modalStatNeeded.textContent = `+${data.needed_distance_pct ? data.needed_distance_pct.toFixed(2) : '--'}% from live price`;
    if (el.modalStatFreq) el.modalStatFreq.textContent = `${stats.historical_hit_frequency_pct != null ? stats.historical_hit_frequency_pct.toFixed(1) : '0.0'}% of days`;

    const recSign = (data.recommended_target_percent != null && data.recommended_target_percent >= 0) ? '+' : '';
    if (el.modalRecVal) {
      el.modalRecVal.textContent = `${recSign}${data.recommended_target_percent ? data.recommended_target_percent.toFixed(2) : '2.00'}%`;
    }

    el.feasibilityModal.style.display = 'flex';
  }

  function closeFeasibilityModal() {
    if (el.feasibilityModal) el.feasibilityModal.style.display = 'none';
  }

  async function refreshCurrentStockSnapshot() {
    try {
      const sym = el.stockInput ? el.stockInput.value.trim().toUpperCase() : currentStock;
      if (!sym) return;
      const res = await fetch(`/api/stocks/${encodeURIComponent(sym)}/live`);
      if (res.ok) {
        cachedStockSnapshot = await res.json();
        updateTargetInputStyling();
      }
      fetchMonitoringChartData();
    } catch (e) { }
  }

  function extractTargetMagnitude(valStr) {
    if (valStr == null) return 1.20;
    const cleaned = String(valStr).replace(/[^\d.]/g, '');
    const parsed = parseFloat(cleaned);
    return (!isNaN(parsed) && parsed > 0) ? parsed : 1.20;
  }

  function updateTargetInputStyling() {
    if (!el.targetPctInput) return;
    const rawVal = el.targetPctInput.value.trim();

    // Extract clean positive numeric magnitude
    const mag = extractTargetMagnitude(rawVal);
    targetPct = mag;

    const hasMinus = rawVal.startsWith('-');
    const hasPlus = rawVal.startsWith('+');

    let isDown = false;
    if (hasMinus) {
      isDown = true;
      if (el.directionSelect && el.directionSelect.value !== 'DOWN') {
        el.directionSelect.value = 'DOWN';
        direction = 'DOWN';
      }
    } else if (hasPlus) {
      isDown = false;
      if (el.directionSelect && el.directionSelect.value !== 'UP') {
        el.directionSelect.value = 'UP';
        direction = 'UP';
      }
    } else {
      // Respect explicit user dropdown choice when no +/- sign is typed
      direction = el.directionSelect ? el.directionSelect.value : direction;
      isDown = direction === 'DOWN';
    }

    const liveChange = (cachedStockSnapshot && typeof cachedStockSnapshot.change_pct === 'number') ? cachedStockSnapshot.change_pct : 0.0;
    const livePrice = (cachedStockSnapshot && cachedStockSnapshot.price) ? parseFloat(cachedStockSnapshot.price) : 0.0;
    const prevClose = (cachedStockSnapshot && cachedStockSnapshot.previous_close) ? parseFloat(cachedStockSnapshot.previous_close) : (livePrice || 1.0);

    // Apply vibrant green for UP (+) and red for DOWN (-)
    if (isDown) {
      el.targetPctInput.classList.remove('dir-positive');
      el.targetPctInput.classList.add('dir-negative');
      if (el.targetSuffix) {
        el.targetSuffix.classList.remove('dir-positive');
        el.targetSuffix.classList.add('dir-negative');
      }
      if (el.directionSelect) {
        el.directionSelect.style.color = '#dc2626';
        el.directionSelect.style.fontWeight = '700';
      }
    } else {
      el.targetPctInput.classList.remove('dir-negative');
      el.targetPctInput.classList.add('dir-positive');
      if (el.targetSuffix) {
        el.targetSuffix.classList.remove('dir-negative');
        el.targetSuffix.classList.add('dir-positive');
      }
      if (el.directionSelect) {
        el.directionSelect.style.color = '#16a34a';
        el.directionSelect.style.fontWeight = '700';
      }
    }

    // Dynamic Live Rupee & P&L Calculation using Market Day % Rule
    if (el.prevLivePrice) {
      el.prevLivePrice.textContent = livePrice > 0 ? `₹${livePrice.toFixed(2)}` : 'Fetching...';
    }

    const signedTargetDayPct = (isDown ? -1 : 1) * mag;

    if (livePrice && livePrice > 0 && prevClose > 0) {
      // Target price = prevClose * (1 + signedTargetDayPct / 100)
      const tPrice = prevClose * (1.0 + signedTargetDayPct / 100.0);
      const tDiff = Math.abs(tPrice - livePrice);

      if (el.prevTargetPrice) {
        el.prevTargetPrice.textContent = `₹${tPrice.toFixed(2)} (${tPrice >= livePrice ? '+' : '-'}₹${tDiff.toFixed(2)})`;
      }
      if (el.prevProfit100) {
        el.prevProfit100.textContent = `+₹${(tDiff * 100).toFixed(2)}`;
      }
    }

    // Dynamic HUD Explanation of Day Change vs Target Day %
    const needDist = Math.abs(signedTargetDayPct - liveChange);
    const needSign = signedTargetDayPct >= liveChange ? '+' : '-';

    if (el.targetHintBadge) {
      el.targetHintBadge.textContent = `${isDown ? '-' : '+'}${mag.toFixed(2)}% ${isDown ? 'DOWN' : (direction === 'BOTH' ? 'BOTH' : 'UP')}`;
      el.targetHintBadge.className = `hud-badge-pill ${isDown ? 'red' : 'green'}`;
    }

    if (el.targetHintText) {
      const liveStr = `${liveChange >= 0 ? '+' : ''}${liveChange.toFixed(2)}%`;
      const goalStr = `${isDown ? '-' : '+'}${mag.toFixed(2)}%`;
      el.targetHintText.innerHTML = `Live: <strong class="${liveChange >= 0 ? 'text-green' : 'text-red'}">${liveStr}</strong> ➔ Target: <strong class="${isDown ? 'text-red' : 'text-green'}">${goalStr}</strong> (Need <strong>${needSign}${needDist.toFixed(2)}%</strong> to Hit in ${windowMin}m)`;
    }

    // Update target preset chips labels and active states
    if (el.pctPresets) {
      const chips = el.pctPresets.querySelectorAll('.preset-chip');
      chips.forEach(chip => {
        const val = parseFloat(chip.getAttribute('data-val'));
        chip.classList.toggle('active', Math.abs(val - mag) < 0.05);
      });
    }

    // Update window preset chips active state
    if (el.windowPresets) {
      const wchips = el.windowPresets.querySelectorAll('.preset-chip');
      wchips.forEach(chip => {
        const minVal = parseInt(chip.getAttribute('data-min'), 10);
        chip.classList.toggle('active', minVal === windowMin);
      });
    }

    // Compute dynamic adaptive threshold based on configuration velocity:
    const velocity = needDist / Math.max(1, windowMin);
    let adaptiveThresh = 0.70;
    if (velocity <= 0.05) adaptiveThresh = 0.60;
    else if (velocity <= 0.10) adaptiveThresh = 0.65;
    else if (velocity <= 0.20) adaptiveThresh = 0.70;
    else if (velocity <= 0.40) adaptiveThresh = 0.75;
    else adaptiveThresh = 0.80;

    if (el.hudAdaptiveThresholdVal) {
      el.hudAdaptiveThresholdVal.textContent = `${(adaptiveThresh * 100).toFixed(1)}%`;
    }
    if (el.hudModelHorizonVal) {
      el.hudModelHorizonVal.textContent = `${windowMin} min Early-Warning`;
    }
    if (el.dirModeAux) {
      el.dirModeAux.textContent = isDown ? 'Bearish (-)' : (direction === 'BOTH' ? 'Bi-dir (±)' : 'Bullish (+)');
    }

    // Trigger debounced model-driven target feasibility evaluation
    triggerFeasibilityEvaluation();
  }

  // --- INIT DROPDOWNS & PRESETS ---
  function initForm() {
    if (el.stockInput) el.stockInput.value = currentStock;
    if (el.targetPctInput) el.targetPctInput.value = '+1.20';
    if (el.warningWindowSelect) el.warningWindowSelect.value = String(windowMin);
    if (el.directionSelect) el.directionSelect.value = direction;
    updateTargetInputStyling();
    refreshCurrentStockSnapshot();
  }

  // --- PRESET CHIPS ---
  if (el.pctPresets) {
    el.pctPresets.addEventListener('click', (e) => {
      const chip = e.target.closest('.preset-chip');
      if (!chip) return;
      const val = parseFloat(chip.getAttribute('data-val'));
      if (isNaN(val) || val <= 0) return;
      targetPct = val;
      const isDown = (el.directionSelect ? el.directionSelect.value : direction) === 'DOWN';
      if (el.targetPctInput) el.targetPctInput.value = (isDown ? '-' : '+') + val.toFixed(2);
      updateTargetInputStyling();
    });
  }

  if (el.windowPresets) {
    el.windowPresets.addEventListener('click', (e) => {
      const chip = e.target.closest('.preset-chip');
      if (!chip) return;
      const val = parseInt(chip.getAttribute('data-min'), 10);
      windowMin = val;
      if (el.warningWindowSelect) el.warningWindowSelect.value = String(val);
      updateTargetInputStyling();
    });
  }

  if (el.targetPctInput) {
    el.targetPctInput.addEventListener('input', () => {
      updateTargetInputStyling();
    });

    const formatCleanTarget = () => {
      if (!el.targetPctInput) return;
      const mag = extractTargetMagnitude(el.targetPctInput.value);
      targetPct = mag;
      const isDown = (el.directionSelect ? el.directionSelect.value : direction) === 'DOWN';
      el.targetPctInput.value = (isDown ? '-' : '+') + mag.toFixed(2);
      updateTargetInputStyling();
    };

    el.targetPctInput.addEventListener('change', formatCleanTarget);
    el.targetPctInput.addEventListener('blur', formatCleanTarget);
  }

  if (el.warningWindowSelect) {
    const handleWinChange = (e) => {
      windowMin = Math.max(1, parseInt(e.target.value, 10) || 1);
      updateTargetInputStyling();
    };
    el.warningWindowSelect.addEventListener('input', handleWinChange);
    el.warningWindowSelect.addEventListener('change', handleWinChange);
  }

  if (el.directionSelect) {
    el.directionSelect.addEventListener('change', (e) => {
      direction = e.target.value;
      const isDown = direction === 'DOWN';
      const mag = extractTargetMagnitude(el.targetPctInput ? el.targetPctInput.value : targetPct);
      targetPct = mag;
      if (el.targetPctInput) {
        el.targetPctInput.value = (isDown ? '-' : '+') + mag.toFixed(2);
      }
      updateTargetInputStyling();
    });
  }


  // --- STOCK AUTOCOMPLETE ---
  function setupStockSearch() {
    if (!el.stockInput || !el.stockDropdown) return;

    let timeout = null;
    el.stockInput.addEventListener('input', (e) => {
      clearTimeout(timeout);
      const q = e.target.value.trim();
      if (!q) {
        el.stockDropdown.style.display = 'none';
        return;
      }
      timeout = setTimeout(async () => {
        try {
          const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(q)}`);
          if (res.ok) {
            const list = await res.json();
            renderDropdown(list);
          }
        } catch (err) { }
      }, 150);
    });

    if (el.btnToggleStockDropdown) {
      el.btnToggleStockDropdown.addEventListener('click', async () => {
        if (el.stockDropdown.style.display === 'block') {
          el.stockDropdown.style.display = 'none';
        } else {
          try {
            const res = await fetch('/api/stocks');
            if (res.ok) {
              const list = await res.json();
              renderDropdown(list);
            }
          } catch (e) { }
        }
      });
    }

    document.addEventListener('click', (e) => {
      if (!el.stockInput.contains(e.target) && !el.stockDropdown.contains(e.target) && (!el.btnToggleStockDropdown || !el.btnToggleStockDropdown.contains(e.target))) {
        el.stockDropdown.style.display = 'none';
      }
    });

    const commitStockInput = () => {
      const q = el.stockInput ? el.stockInput.value.trim().toUpperCase() : '';
      if (q && q !== currentStock) {
        currentStock = StockPulse.setSelectedStock(q);
        refreshCurrentStockSnapshot();
        fetchMonitoringChartData(true);
        refreshSuperTrendPreview();
      }
    };
    el.stockInput.addEventListener('change', commitStockInput);
    el.stockInput.addEventListener('blur', commitStockInput);
  }

  function renderDropdown(stocks) {
    if (!el.stockDropdown) return;
    el.stockDropdown.innerHTML = '';
    const q = el.stockInput ? el.stockInput.value.trim().toUpperCase() : '';
    let items = Array.isArray(stocks) ? [...stocks] : [];
    if (q && !items.some(it => it.symbol.toUpperCase() === q)) {
      items.unshift({
        symbol: q,
        name: q === 'OLX' ? 'OLX India / CarTrade Tech Ltd' : `${q} Share / Equity`,
        exchange: 'NSE'
      });
    }
    items.slice(0, 15).forEach(stk => {
      const item = document.createElement('div');
      item.className = 'dropdown-item';
      item.style.cssText = 'padding: 8px 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;';
      item.onmouseenter = () => { item.style.background = '#f8fafc'; };
      item.onmouseleave = () => { item.style.background = '#ffffff'; };
      item.innerHTML = `<strong>${stk.symbol}</strong> <span style="font-size: 11px; color: #64748b;">${stk.name}</span>`;
      item.addEventListener('click', () => {
        currentStock = StockPulse.setSelectedStock(stk.symbol);
        el.stockInput.value = stk.symbol;
        el.stockDropdown.style.display = 'none';
        refreshCurrentStockSnapshot();
        fetchMonitoringChartData(true);
        refreshSuperTrendPreview();
      });
      el.stockDropdown.appendChild(item);
    });
    el.stockDropdown.style.display = 'block';
  }

  // --- START MONITORING ---
  async function startMonitoring() {
    const symbol = el.stockInput ? el.stockInput.value.trim().toUpperCase() : currentStock;
    if (!symbol) return;
    StockPulse.setSelectedStock(symbol);

    const user = StockPulse.getUser();

    // Parse target % and warning minutes properly (Market Day % rule)
    const rawVal = el.targetPctInput ? el.targetPctInput.value.trim() : '+1.20';
    const mag = extractTargetMagnitude(rawVal);
    const selectedDir = ((el.directionSelect ? el.directionSelect.value : direction) || 'UP').trim().toUpperCase();
    direction = selectedDir;
    const isDown = selectedDir === 'DOWN';
    const parsedTarget = (isDown ? -1 : 1) * mag;
    const parsedWindow = parseInt(el.warningWindowSelect ? el.warningWindowSelect.value : windowMin, 10) || windowMin || 2;

    // Target Feasibility Validation:
    // If the user entered an unrealistic/improbable target (< 25% achievability) and has not clicked proceed anyway:
    if (currentFeasibilityResult && !currentFeasibilityResult.is_valid && !bypassFeasibilityCheck) {
      openFeasibilityWarningModal(currentFeasibilityResult);
      return;
    }
    bypassFeasibilityCheck = false;

    const stEnableEl = document.getElementById('cfg-st-enable');
    const stEnable = stEnableEl ? stEnableEl.checked : true;
    const stProxEl = document.getElementById('cfg-st-proximity');
    const stProx = stProxEl ? (parseFloat(stProxEl.value) || 1.0) : 1.0;

    const payload = {
      symbol: symbol,
      target_percent: parsedTarget,
      stop_loss_percent: null,
      warning_minutes: parsedWindow,
      direction: selectedDir,
      notification_method: 'popup',
      channels: ['dashboard', 'popup'],
      monitoring_interval: 1,
      enable_supertrend: stEnable,
      supertrend_proximity_points: stProx
    };

    let res;
    try {
      if (el.btnStartMonitoring) {
        el.btnStartMonitoring.disabled = true;
        if (el.btnStartLabel) el.btnStartLabel.textContent = 'STARTING...';
      }
      res = await fetch('/api/alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.error('[StockPulse] Network error on POST /api/alerts:', e);
      if (window.StockPulse && StockPulse.showCustomizedPopup) {
        StockPulse.showCustomizedPopup({
          symbol: symbol,
          title: 'Alert Service Connection',
          event_type: 'WARNING',
          short_summary: 'Could not connect to alert service. Please verify the server is running and retry.',
          message: e.message || 'Network connection error'
        });
      } else {
        alert('Network error connecting to alert service. Please verify the server is running and retry.');
      }
      return;
    } finally {
      if (el.btnStartMonitoring) {
        el.btnStartMonitoring.disabled = false;
        if (!res || !res.ok) {
          if (el.btnStartLabel) el.btnStartLabel.textContent = 'START MONITORING';
        }
      }
    }

    if (res && res.ok) {
      isMonitoringActive = true;
      if (el.btnStartLabel) {
        el.btnStartLabel.innerHTML = '<i class="fa-solid fa-circle-check" style="color: #22c55e;"></i> ARMED &amp; MONITORING';
        setTimeout(() => {
          if (el.btnStartLabel) el.btnStartLabel.textContent = 'START MONITORING';
        }, 2000);
      }
      try {
        StockPulse.playNotificationSound('MONITORING_STARTED');
      } catch (err) { }
      try {
        StockPulse.showCustomizedPopup({
          symbol: symbol,
          title: `Monitoring Armed: ${symbol}`,
          event_type: 'MONITORING_STARTED',
          short_summary: `Live tracking ${selectedDir} target ${parsedTarget}% (${parsedWindow}m window). Audio chimes & system popups ready.`,
          extra_data: JSON.stringify({
            symbol: symbol,
            direction: selectedDir,
            target_percent: parsedTarget,
            warning_minutes: parsedWindow
          })
        });
      } catch (err) { }
      try {
        const currentLiveP = cachedStockSnapshot && typeof cachedStockSnapshot.price === 'number' ? cachedStockSnapshot.price : null;
        const prevCloseP = cachedStockSnapshot && typeof cachedStockSnapshot.previous_close === 'number' ? cachedStockSnapshot.previous_close : currentLiveP;
        const targetPrice = prevCloseP != null ? Math.round(prevCloseP * (1 + parsedTarget / 100) * 100) / 100 : currentLiveP;
        const notifLabel = mappedMethod === 'email' ? 'Email' : (rawNotif === 'all' ? 'All Channels' : 'Popup');
        openConfirmationModal(symbol, parsedTarget, parsedWindow, selectedDir, notifLabel, currentLiveP, targetPrice);
      } catch (modalErr) {
        console.warn('[StockPulse] Confirmation modal error:', modalErr);
      }
      try {
        isFetchingSessions = false;
        await fetchActiveSessions();
        if (el.activeSessionsSection) {
          el.activeSessionsSection.style.display = 'block';
        }
        if (stEnable) {
          addSuperTrendShare(symbol);
        }
      } catch (err) { }
    } else if (res) {
      let errorMsg = 'Could not register alert.';
      try {
        const err = await res.json();
        if (typeof err.detail === 'string') {
          errorMsg = err.detail;
        } else if (Array.isArray(err.detail)) {
          errorMsg = err.detail.map(d => {
            const field = d.loc ? d.loc.slice(-1)[0] : 'field';
            return `${field}: ${d.msg}`;
          }).join('; ');
        } else if (err.message) {
          errorMsg = err.message;
        }
      } catch (err) { }
      if (window.StockPulse && StockPulse.showCustomizedPopup) {
        StockPulse.showCustomizedPopup({
          symbol: symbol,
          title: 'Monitoring Alert Error',
          event_type: 'WARNING',
          short_summary: errorMsg
        });
      } else {
        alert(`Error starting monitoring: ${errorMsg}`);
      }
    }
  }

  function updateMultiSessionBanner(alerts) {
    const activeBanner = el.activeConfigBanner || document.getElementById('active-config-banner');
    const activePill = el.activeConfigPill || document.getElementById('active-config-pill');
    const summaryText = el.indicatorSummaryText || document.getElementById('indicator-summary-text');

    if (!alerts || alerts.length === 0) {
      if (activeBanner) activeBanner.style.display = 'none';
      if (activePill) activePill.style.display = 'none';
      return;
    }

    // Top hero pill
    if (activePill) {
      activePill.style.display = 'inline-flex';
      if (summaryText) {
        if (alerts.length === 1) {
          const a = alerts[0];
          const isDown = a.direction === 'DOWN';
          const dirSign = isDown ? '-' : '+';
          const dirLabel = isDown ? 'DOWN (-)' : 'UP (+)';
          summaryText.textContent = `${a.symbol} · ${dirLabel} · ${dirSign}${Math.abs(Number(a.target_percent)).toFixed(2)}% · ${a.warning_minutes}m`;
        } else {
          const items = alerts.map(a => {
            const isDown = a.direction === 'DOWN';
            const dirSign = isDown ? '-' : '+';
            return `${a.symbol} (${dirSign}${Math.abs(Number(a.target_percent)).toFixed(2)}%)`;
          }).join(', ');
          summaryText.textContent = `${alerts.length} Active Threads: ${items}`;
        }
      }
    }

    // Active Config Banner
    if (activeBanner) {
      activeBanner.style.display = 'flex';
      const statusTitle = document.getElementById('banner-status-title');
      if (statusTitle) {
        statusTitle.textContent = `${alerts.length} ACTIVE THREAD${alerts.length > 1 ? 'S' : ''}`;
      }
      const countLabel = document.getElementById('banner-count-label');
      if (countLabel) {
        countLabel.textContent = `View Active Threads (${alerts.length})`;
      }
      const summaryContainer = document.getElementById('banner-threads-summary');
      if (summaryContainer) {
        const items = alerts.map(a => {
          const isDown = a.direction === 'DOWN';
          const dirSign = isDown ? '-' : '+';
          const dirColor = isDown ? '#dc2626' : '#16a34a';
          const dirLabel = isDown ? 'DOWN (-)' : 'UP (+)';
          return `<strong>${a.symbol}</strong> (<span style="color: ${dirColor}; font-weight: 700;">${dirSign}${Math.abs(Number(a.target_percent)).toFixed(2)}%</span>, ${dirLabel}, ${a.warning_minutes}m)`;
        }).join(' &bull; ');
        summaryContainer.innerHTML = `${items} &bull; Continuous 1m Intraday Early-Warning Active`;
      }
    }
  }

  // --- CONFIRMATION MODAL ---
  function openConfirmationModal(sym, target, win, dir, notif, livePrice, targetPrice) {
    if (!el.modalBackdrop) return;
    const isDown = String(dir).toUpperCase() === 'DOWN';
    const isUp = String(dir).toUpperCase() === 'UP';
    const dirSign = isDown ? '-' : (isUp ? '+' : '±');
    const dirLabel = isDown ? 'DOWN (-)' : (isUp ? 'UP (+)' : 'BOTH (±)');
    const targetStr = `${dirSign}${Math.abs(Number(target)).toFixed(2)}%`;

    if (el.modalStock) el.modalStock.textContent = sym;
    if (el.modalPrice) {
      el.modalPrice.textContent = (livePrice != null && !isNaN(livePrice))
        ? `₹${Number(livePrice).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : '--';
    }
    if (el.modalTargetPrice) {
      el.modalTargetPrice.textContent = (targetPrice != null && !isNaN(targetPrice))
        ? `₹${Number(targetPrice).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
        : '--';
      el.modalTargetPrice.style.color = isDown ? '#dc2626' : (isUp ? '#16a34a' : '#2563eb');
    }
    if (el.modalTarget) {
      el.modalTarget.textContent = targetStr;
      el.modalTarget.style.color = isDown ? '#dc2626' : (isUp ? '#16a34a' : '#2563eb');
    }
    if (el.modalWindow) el.modalWindow.textContent = `${win} minutes`;
    if (el.modalDirection) {
      el.modalDirection.textContent = dirLabel;
      el.modalDirection.style.color = isDown ? '#dc2626' : (isUp ? '#16a34a' : '#2563eb');
    }
    if (el.modalNotif) el.modalNotif.textContent = 'In-App Popup';
    el.modalBackdrop.style.display = 'flex';
  }

  function closeModal() {
    if (el.modalBackdrop) el.modalBackdrop.style.display = 'none';
    if (el.activeSessionsSection) {
      el.activeSessionsSection.scrollIntoView({ behavior: 'smooth' });
    }
  }

  // --- ACTIVE PREDICTIVE SESSIONS ---
  let isFetchingSessions = false;
  async function fetchActiveSessions() {
    if (isFetchingSessions) return;
    isFetchingSessions = true;
    try {
      const res = await fetch('/api/alerts/active');
      if (!res.ok) return;
      const alerts = await res.json();
      renderActiveSessions(alerts);
    } catch (e) {
      console.warn('[StockPulse] fetchActiveSessions error:', e);
    } finally {
      isFetchingSessions = false;
    }
  }

  function renderActiveSessions(alerts) {
    const activeSection = el.activeSessionsSection || document.getElementById('active-sessions-section');
    const cardsGrid = el.activeCardsGrid || document.getElementById('active-cards-grid');
    const activePill = el.activeConfigPill || document.getElementById('active-config-pill');
    const activeBanner = el.activeConfigBanner || document.getElementById('active-config-banner');

    if (!activeSection || !cardsGrid) return;

    if (!Array.isArray(alerts) || alerts.length === 0) {
      activeSection.style.display = 'none';
      if (activePill) activePill.style.display = 'none';
      if (activeBanner) activeBanner.style.display = 'none';
      isMonitoringActive = false;
      cardsGrid.innerHTML = '';
      return;
    }

    isMonitoringActive = true;
    activeSection.style.display = 'block';
    cardsGrid.innerHTML = '';

    // Update banner & pill with all active symbols
    updateMultiSessionBanner(alerts);

    const activeCountTag = document.getElementById('active-count-tag');
    if (activeCountTag) {
      activeCountTag.textContent = `${alerts.length} Active Thread${alerts.length > 1 ? 's' : ''}`;
    }

    alerts.forEach(alert => {
      const isUp = alert.direction === 'UP';
      const isDown = alert.direction === 'DOWN';
      const dirSign = isDown ? '-' : (isUp ? '+' : '±');
      const dirLabel = isDown ? 'DOWN (-)' : (isUp ? 'UP (+)' : 'BOTH (±)');
      const dirClass = isDown ? 'dir-down' : (isUp ? 'dir-up' : 'dir-both');
      const dirIcon = isDown ? 'fa-arrow-trend-down' : (isUp ? 'fa-arrow-trend-up' : 'fa-arrows-up-down');
      const dirColor = isDown ? '#dc2626' : (isUp ? '#16a34a' : '#2563eb');
      const targetVal = alert.target_percent != null ? alert.target_percent : 5.0;
      const targetStr = `${dirSign}${Math.abs(Number(targetVal)).toFixed(2)}%`;
      const winVal = alert.warning_minutes != null ? alert.warning_minutes : 5;
      const notifMethod = alert.notification_method || 'Popup';
      const isEmail = String(notifMethod).toLowerCase().includes('email');

      let statusClass = 'monitoring';
      let statusLabel = 'MONITORING';
      let dotColor = 'green';
      if (alert.status === 'EARLY_WARNING') {
        statusClass = 'early-warning';
        statusLabel = 'EARLY WARNING';
        dotColor = 'amber';
      } else if (alert.status === 'TARGET_TRIGGERED' || alert.status === 'SUCCESS') {
        statusClass = 'target-triggered';
        statusLabel = 'SUCCESS — TASK COMPLETE';
        dotColor = 'green';
      } else if (alert.status === 'EXPIRED') {
        statusClass = 'expired';
        statusLabel = 'WINDOW EXPIRED';
        dotColor = 'gray';
      } else if (alert.status === 'DATA_UNAVAILABLE') {
        statusClass = 'early-warning';
        statusLabel = 'RECONNECTING';
        dotColor = 'red';
      }

      const card = document.createElement('div');
      card.className = `active-session-card status-${statusClass}`;
      card.id = `session-card-${alert.id}`;

      const currPriceStr = alert.current_price ? `₹${Number(alert.current_price).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : 'Fetching...';

      const currentMoveVal = (alert.current_movement != null && !isNaN(alert.current_movement)) ? Number(alert.current_movement) : null;
      const moveSign = (currentMoveVal != null && currentMoveVal > 0) ? '+' : '';
      const moveColor = (currentMoveVal != null && currentMoveVal > 0) ? '#16a34a' : (currentMoveVal != null && currentMoveVal < 0 ? '#dc2626' : '#64748b');
      const moveStr = currentMoveVal != null ? `${moveSign}${currentMoveVal.toFixed(2)}%` : '--';

      const remVal = (alert.remaining_percent != null && !isNaN(alert.remaining_percent)) ? Number(alert.remaining_percent) : null;
      const remStr = remVal != null ? `${remVal.toFixed(2)}%` : '--';

      const progressVal = alert.progress_percent != null ? Number(alert.progress_percent) : 0;
      const distRupees = alert.target_distance_rupees != null ? `₹${Number(alert.target_distance_rupees).toFixed(2)}` : '--';
      const targetPriceStr = alert.target_price ? `₹${Number(alert.target_price).toFixed(2)}` : '--';
      const stopLossStr = alert.stop_loss_price ? `₹${Number(alert.stop_loss_price).toFixed(2)}` : (alert.stop_loss_percent ? `${alert.stop_loss_percent}%` : '-0.03%');
      const milestoneBadge = alert.milestone_50_sent ? `<span style="background: #ede9fe; color: #7c3aed; font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-flag-checkered"></i> 50% Milestone Reached</span>` : '';

      let stBadge = '';
      if (alert.enable_supertrend) {
        const stP = alert.supertrend_price != null ? `₹${Number(alert.supertrend_price).toFixed(2)}` : null;
        const stDir = alert.supertrend_direction || alert.last_supertrend_direction || 'BUY';
        const isStBuy = stDir === 'BUY';
        const isApp = Boolean(alert.supertrend_is_approaching);
        const gapPts = alert.supertrend_gap_points != null ? `${alert.supertrend_gap_points} pts` : '';
        const proxPts = alert.supertrend_proximity_points != null ? alert.supertrend_proximity_points : 1.0;
        const badgeCls = isApp ? 'approaching' : (isStBuy ? 'buy' : 'sell');
        const badgeIcon = isApp ? 'fa-bolt' : (isStBuy ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down');
        const badgeLabel = isApp
          ? `⚡ ST Within ${gapPts || (proxPts + ' pts')} (Early ≤ ${proxPts} pts)`
          : `ST: ${stP || stDir} (Early ≤ ${proxPts} pts)`;
        stBadge = `<span class="supertrend-badge-pill ${badgeCls}" title="SuperTrend: ${stP || ''} (${stDir})"><i class="fa-solid ${badgeIcon}"></i> ${badgeLabel}</span>`;
      }

      card.innerHTML = `
        <div class="session-card-head">
          <div class="session-symbol-group">
            <span class="session-card-symbol">${alert.symbol}</span>
            <span class="session-dir-badge ${dirClass}">
              <i class="fa-solid ${dirIcon}"></i>
              <span>${dirLabel}</span>
            </span>
            ${stBadge}
            ${milestoneBadge}
          </div>
          <div class="session-status-badge ${statusClass}">
            <span class="pulse-dot ${dotColor}"></span>
            <span class="status-label-text">${statusLabel}</span>
          </div>
        </div>

        <div class="session-metrics-grid">
          <div class="session-metric-cell">
            <span class="session-metric-label">Target Level</span>
            <span class="session-metric-value" style="color: ${dirColor};">${targetPriceStr} (${targetStr})</span>
          </div>
          <div class="session-metric-cell">
            <span class="session-metric-label">Live Price</span>
            <span class="session-metric-value">${currPriceStr} (${moveStr})</span>
          </div>
          <div class="session-metric-cell">
            <span class="session-metric-label">Distance</span>
            <span class="session-metric-value" style="color: #2563eb;">${distRupees} (${remStr})</span>
          </div>
          <div class="session-metric-cell">
            <span class="session-metric-label">Window</span>
            <span class="session-metric-value">${winVal} min</span>
          </div>
        </div>

        <!-- Real-Time Target Progress Gauge -->
        <div class="session-gauge-wrapper">
          <div class="session-gauge-header">
            <span style="color: #64748b;"><i class="fa-solid fa-gauge-high"></i> Target Progress</span>
            <span style="color: ${dirColor}; font-weight: 700;">${progressVal.toFixed(0)}%</span>
          </div>
          <div class="session-gauge-track">
            <div class="session-gauge-fill" style="width: ${progressVal}%; background: ${dirColor};"></div>
          </div>
          <div class="session-gauge-footer">
            <span>Remaining: ${distRupees}</span>
            <span>Target: ${targetStr}</span>
          </div>
        </div>

        <div class="session-card-foot">
          <span class="session-channel-pill">
            <i class="fa-solid ${isEmail ? 'fa-envelope' : 'fa-bell'}"></i>
            <span>${notifMethod}</span>
          </span>
          <div style="display: flex; align-items: center; gap: 6px;">
            <button type="button" class="btn-extend-session" data-alert-id="${alert.id}" title="Extend warning window by 1 minute" style="font-size: 11px; font-weight: 700; padding: 5px 9px; border-radius: 6px; border: 1px solid #cbd5e1; background: #ffffff; cursor: pointer; display: inline-flex; align-items: center; gap: 3px;">
              <i class="fa-solid fa-clock"></i> +1m
            </button>
            <button type="button" class="btn-reset-session" data-alert-id="${alert.id}" title="Reset reference price to current live quote" style="font-size: 11px; font-weight: 700; padding: 5px 9px; border-radius: 6px; border: 1px solid #cbd5e1; background: #ffffff; cursor: pointer; display: inline-flex; align-items: center; gap: 3px;">
              <i class="fa-solid fa-arrow-rotate-left"></i> Reset
            </button>
            <button type="button" class="btn-stop-session" data-alert-id="${alert.id}">
              <i class="fa-solid fa-stop"></i> Stop
            </button>
          </div>
        </div>
      `;

      const extendBtn = card.querySelector('.btn-extend-session');
      if (extendBtn) {
        extendBtn.addEventListener('click', async () => {
          extendBtn.disabled = true;
          try {
            await fetch(`/api/alerts/${alert.id}/extend?minutes=1`, { method: 'POST' });
            await fetchActiveSessions();
          } catch (e) { }
        });
      }

      const resetBtn = card.querySelector('.btn-reset-session');
      if (resetBtn) {
        resetBtn.addEventListener('click', async () => {
          resetBtn.disabled = true;
          try {
            await fetch(`/api/alerts/${alert.id}/reset_ref`, { method: 'POST' });
            await fetchActiveSessions();
          } catch (e) { }
        });
      }

      const stopBtn = card.querySelector('.btn-stop-session');
      if (stopBtn) {
        stopBtn.addEventListener('click', async () => {
          stopBtn.disabled = true;
          stopBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Stopping...';
          await stopSession(alert.id);
        });
      }

      el.activeCardsGrid.appendChild(card);
    });
  }

  async function stopSession(alertId) {
    try {
      const res = await fetch(`/api/alerts/${alertId}/stop`, { method: 'POST' });
      if (res.ok) {
        await fetchActiveSessions();
      } else {
        const delRes = await fetch(`/api/alerts/${alertId}`, { method: 'DELETE' });
        if (delRes.ok) await fetchActiveSessions();
      }
    } catch (e) {
      console.error(e);
    }
  }

  // --- RESET DEFAULTS ---
  if (el.btnResetConfig) {
    el.btnResetConfig.addEventListener('click', () => {
      currentStock = 'TCS';
      targetPct = 5.0;
      windowMin = 5;
      direction = 'UP';
      notificationChannel = 'Popup';
      initForm();
    });
  }

  // --- BUTTON ACTIONS ---
  if (el.btnStartMonitoring) {
    el.btnStartMonitoring.addEventListener('click', startMonitoring);
  }

  const handleStopAll = async () => {
    try {
      const res = await fetch('/api/alerts/active');
      if (res.ok) {
        const list = await res.json();
        for (const item of list) {
          await fetch(`/api/alerts/${item.id}/stop`, { method: 'POST' });
        }
      }
    } catch (e) { }
    await fetchActiveSessions();
  };

  const btnStopAllThreads = document.getElementById('btn-stop-all-threads');
  if (btnStopAllThreads) {
    btnStopAllThreads.addEventListener('click', handleStopAll);
  }

  const btnScrollSessions = document.getElementById('btn-scroll-to-sessions');
  if (btnScrollSessions) {
    btnScrollSessions.addEventListener('click', () => {
      if (el.activeSessionsSection) {
        el.activeSessionsSection.scrollIntoView({ behavior: 'smooth' });
      }
    });
  }

  // --- SuperTrend Hit Notifier Controls ---
  const cfgStEnable = document.getElementById('cfg-st-enable');
  const cfgStToggleText = document.getElementById('cfg-st-toggle-text');
  const cfgStStatusBadge = document.getElementById('cfg-st-status-badge');
  if (cfgStEnable) {
    cfgStEnable.addEventListener('change', () => {
      const active = cfgStEnable.checked;
      if (cfgStToggleText) cfgStToggleText.textContent = active ? 'Active' : 'Disabled';
      if (cfgStStatusBadge) {
        cfgStStatusBadge.textContent = active ? 'ACTIVE' : 'OFF';
        cfgStStatusBadge.className = active ? 'supertrend-badge-pill buy' : 'supertrend-badge-pill sell';
      }
    });
  }

  let testSignalCycle = 0;
  const btnTestSupertrend = document.getElementById('btn-test-supertrend');
  if (btnTestSupertrend) {
    btnTestSupertrend.addEventListener('click', async () => {
      const sym = (el.stockInput ? el.stockInput.value.trim().toUpperCase() : currentStock) || 'TCS';
      const signals = ['APPROACHING', 'BUY', 'SELL'];
      const sig = signals[testSignalCycle % signals.length];
      testSignalCycle++;
      btnTestSupertrend.disabled = true;
      const originalHtml = btnTestSupertrend.innerHTML;
      btnTestSupertrend.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Triggering ${sig}...`;
      try {
        const res = await fetch(`/api/stocks/${encodeURIComponent(sym)}/supertrend/test-notify?signal_type=${sig}`, {
          method: 'POST'
        });
        if (res.ok) {
          btnTestSupertrend.innerHTML = `<i class="fa-solid fa-check"></i> ${sig} Alert Sent!`;
        } else {
          btnTestSupertrend.innerHTML = `<i class="fa-solid fa-xmark"></i> Failed`;
        }
      } catch (err) {
        console.warn('SuperTrend test error:', err);
        btnTestSupertrend.innerHTML = `<i class="fa-solid fa-xmark"></i> Error`;
      } finally {
        setTimeout(() => {
          btnTestSupertrend.disabled = false;
          btnTestSupertrend.innerHTML = originalHtml;
        }, 1800);
      }
    });
  }

  // Modal close handlers
  if (el.btnCloseModal) el.btnCloseModal.addEventListener('click', closeModal);
  if (el.btnDismissModal) el.btnDismissModal.addEventListener('click', closeModal);
  if (el.btnAcknowledgeModal) el.btnAcknowledgeModal.addEventListener('click', closeModal);

  // Target Feasibility Modal Action Listeners
  if (el.btnModalAutoAdjust) {
    el.btnModalAutoAdjust.addEventListener('click', () => {
      closeFeasibilityModal();
      if (currentFeasibilityResult && currentFeasibilityResult.recommended_target_percent != null) {
        const rec = currentFeasibilityResult.recommended_target_percent;
        const recDir = rec < 0 ? 'DOWN' : 'UP';
        direction = recDir;
        if (el.directionSelect) el.directionSelect.value = recDir;
        const absRec = Math.abs(rec);
        targetPct = absRec;
        if (el.targetPctInput) el.targetPctInput.value = (rec < 0 ? '-' : '+') + absRec.toFixed(2);
        updateTargetInputStyling();
        bypassFeasibilityCheck = true;
        startMonitoring();
      }
    });
  }

  if (el.btnModalProceedAnyway) {
    el.btnModalProceedAnyway.addEventListener('click', () => {
      closeFeasibilityModal();
      bypassFeasibilityCheck = true;
      startMonitoring();
    });
  }

  if (el.btnUseRecommendedTarget) {
    el.btnUseRecommendedTarget.addEventListener('click', () => {
      if (currentFeasibilityResult && currentFeasibilityResult.recommended_target_percent != null) {
        const rec = currentFeasibilityResult.recommended_target_percent;
        const recDir = rec < 0 ? 'DOWN' : 'UP';
        direction = recDir;
        if (el.directionSelect) el.directionSelect.value = recDir;
        const absRec = Math.abs(rec);
        targetPct = absRec;
        if (el.targetPctInput) el.targetPctInput.value = (rec < 0 ? '-' : '+') + absRec.toFixed(2);
        updateTargetInputStyling();
      }
    });
  }

  // --- SUPERTREND DYNAMIC EARLY-POINT & MULTI-SHARE WATCHLIST SECTION ---
  let stSelectedSymbols = [];
  let stPopularSymbols = ['TCS', 'INFY', 'RELIANCE', 'HDFCBANK', 'TATAMOTORS', 'SBIN', 'ICICIBANK', 'BHARTIARTL', 'ITC', 'WIPRO'];
  let stPreviewingSymbol = null;
  let isSavingSTSettings = false;

  async function loadSuperTrendSettings() {
    try {
      const res = await fetch('/api/stocks/supertrend/settings');
      if (res.ok) {
        const data = await res.json();
        stSelectedSymbols = Array.isArray(data.selected_symbols) ? data.selected_symbols : [];
        if (Array.isArray(data.popular_symbols) && data.popular_symbols.length > 0) {
          stPopularSymbols = data.popular_symbols;
        }

        const toggle = document.getElementById('cfg-st-enable');
        const toggleText = document.getElementById('cfg-st-toggle-text');
        const statusBadge = document.getElementById('cfg-st-status-badge');
        const proxInput = document.getElementById('cfg-st-proximity');

        if (toggle && data.enabled !== undefined) {
          toggle.checked = Boolean(data.enabled);
          if (toggleText) toggleText.textContent = toggle.checked ? 'Active' : 'Disabled';
          if (statusBadge) {
            statusBadge.textContent = toggle.checked ? 'ACTIVE' : 'OFF';
            statusBadge.className = `supertrend-badge-pill ${toggle.checked ? 'buy' : 'sell'}`;
          }
        }

        if (proxInput && data.proximity_points) {
          proxInput.value = data.proximity_points;
          const chips = document.querySelectorAll('.st-preset-chip');
          chips.forEach(c => {
            if (parseFloat(c.getAttribute('data-points')) === parseFloat(data.proximity_points)) {
              c.classList.add('active');
            } else {
              c.classList.remove('active');
            }
          });
        }
      }
    } catch (e) {
      console.warn('[Monitoring] Failed to load ST settings:', e);
      if (!Array.isArray(stSelectedSymbols)) {
        stSelectedSymbols = [];
      }
    }

    renderSuperTrendSharesUI();
    refreshSuperTrendPreview();
  }

  async function persistSuperTrendSettings() {
    if (isSavingSTSettings) return;
    isSavingSTSettings = true;
    try {
      const toggle = document.getElementById('cfg-st-enable');
      const proxInput = document.getElementById('cfg-st-proximity');
      const payload = {
        enabled: toggle ? toggle.checked : true,
        proximity_points: proxInput ? (parseFloat(proxInput.value) || 1.0) : 1.0,
        selected_symbols: stSelectedSymbols
      };
      await fetch('/api/stocks/supertrend/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      console.warn('[Monitoring] Failed to persist ST settings:', e);
    } finally {
      isSavingSTSettings = false;
    }
  }

  function renderSuperTrendSharesUI() {
    const countBadge = document.getElementById('st-shares-count-badge');
    const popularContainer = document.getElementById('st-popular-chips');
    const tagsList = document.getElementById('st-selected-tags-list');

    const totalCount = stSelectedSymbols.length;
    if (countBadge) {
      countBadge.textContent = `${totalCount} Share${totalCount === 1 ? '' : 's'}`;
    }

    // Render popular chips
    if (popularContainer) {
      popularContainer.innerHTML = '';
      stPopularSymbols.forEach(sym => {
        const isSelected = stSelectedSymbols.includes(sym);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `st-pop-chip ${isSelected ? 'selected' : ''}`;
        btn.innerHTML = `${isSelected ? '<i class="fa-solid fa-check"></i>' : '<i class="fa-solid fa-plus"></i>'} ${sym}`;
        btn.addEventListener('click', () => {
          toggleSuperTrendShare(sym);
        });
        popularContainer.appendChild(btn);
      });
    }

    // Render active selected tags list
    if (tagsList) {
      tagsList.innerHTML = '';
      if (totalCount === 0) {
        tagsList.innerHTML = `<span class="st-empty-tag-hint">No shares selected</span>`;
      } else {
        stSelectedSymbols.forEach(sym => {
          const isPreviewing = stPreviewingSymbol === sym || (!stPreviewingSymbol && stSelectedSymbols[0] === sym);
          const tag = document.createElement('div');
          tag.className = `st-active-tag ${isPreviewing ? 'previewing' : ''}`;
          tag.title = `Click to preview ${sym} in conversion simulator`;
          tag.innerHTML = `
            <span class="st-tag-symbol">${sym}</span>
            <span class="st-tag-badge buy" id="st-tag-badge-${sym}">ST</span>
            <button type="button" class="st-tag-remove-btn" title="Remove ${sym}">
              <i class="fa-solid fa-xmark"></i>
            </button>
          `;

          tag.addEventListener('click', (e) => {
            if (e.target.closest('.st-tag-remove-btn')) return;
            stPreviewingSymbol = sym;
            renderSuperTrendSharesUI();
            refreshSuperTrendPreview();
          });

          const removeBtn = tag.querySelector('.st-tag-remove-btn');
          if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              removeSuperTrendShare(sym);
            });
          }

          tagsList.appendChild(tag);
        });
      }
    }
  }

  function addSuperTrendShare(rawSym) {
    if (!rawSym) return;
    const clean = rawSym.trim().toUpperCase().replace(/\.(NS|BO)$/, '');
    if (!clean) return;
    if (!stSelectedSymbols.includes(clean)) {
      stSelectedSymbols.push(clean);
      stPreviewingSymbol = clean;
      persistSuperTrendSettings();
      renderSuperTrendSharesUI();
      refreshSuperTrendPreview();
    }
  }

  function removeSuperTrendShare(sym) {
    const idx = stSelectedSymbols.indexOf(sym);
    if (idx !== -1) {
      stSelectedSymbols.splice(idx, 1);
      if (stPreviewingSymbol === sym) {
        stPreviewingSymbol = stSelectedSymbols[0] || null;
      }
      persistSuperTrendSettings();
      renderSuperTrendSharesUI();
      refreshSuperTrendPreview();
    }
  }

  function toggleSuperTrendShare(sym) {
    if (stSelectedSymbols.includes(sym)) {
      removeSuperTrendShare(sym);
    } else {
      addSuperTrendShare(sym);
    }
  }

  function setupSuperTrendSection() {
    const chips = document.querySelectorAll('.st-preset-chip');
    const proxInput = document.getElementById('cfg-st-proximity');
    const toggle = document.getElementById('cfg-st-enable');
    const toggleText = document.getElementById('cfg-st-toggle-text');
    const statusBadge = document.getElementById('cfg-st-status-badge');

    const searchInput = document.getElementById('st-stock-search-input');
    const searchDropdown = document.getElementById('st-stock-search-dropdown');
    const btnAdd = document.getElementById('btn-st-add-stock');
    const btnSelectPopular = document.getElementById('btn-st-select-popular');
    const btnClearAll = document.getElementById('btn-st-clear-all');

    if (chips && proxInput) {
      chips.forEach(chip => {
        chip.addEventListener('click', () => {
          chips.forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          const val = chip.getAttribute('data-points');
          proxInput.value = val;
          persistSuperTrendSettings();
          refreshSuperTrendPreview();
        });
      });

      proxInput.addEventListener('input', () => {
        const curVal = parseFloat(proxInput.value);
        chips.forEach(c => {
          if (parseFloat(c.getAttribute('data-points')) === curVal) {
            c.classList.add('active');
          } else {
            c.classList.remove('active');
          }
        });
        persistSuperTrendSettings();
        refreshSuperTrendPreview();
      });
    }

    if (toggle) {
      toggle.addEventListener('change', () => {
        const isActive = toggle.checked;
        if (toggleText) toggleText.textContent = isActive ? 'Active' : 'Disabled';
        if (statusBadge) {
          statusBadge.textContent = isActive ? 'ACTIVE' : 'OFF';
          statusBadge.className = `supertrend-badge-pill ${isActive ? 'buy' : 'sell'}`;
        }
        persistSuperTrendSettings();
      });
    }

    // Quick Action: Select Top 5
    if (btnSelectPopular) {
      btnSelectPopular.addEventListener('click', () => {
        const top5 = ['TCS', 'INFY', 'RELIANCE', 'HDFCBANK', 'TATAMOTORS'];
        top5.forEach(s => {
          if (!stSelectedSymbols.includes(s)) stSelectedSymbols.push(s);
        });
        stPreviewingSymbol = stSelectedSymbols[0] || 'TCS';
        persistSuperTrendSettings();
        renderSuperTrendSharesUI();
        refreshSuperTrendPreview();
      });
    }

    // Quick Action: Clear All
    if (btnClearAll) {
      btnClearAll.addEventListener('click', () => {
        stSelectedSymbols = [];
        stPreviewingSymbol = null;
        persistSuperTrendSettings();
        renderSuperTrendSharesUI();
        refreshSuperTrendPreview();
      });
    }

    // Search input autocomplete & add
    if (searchInput && searchDropdown) {
      let searchTimeout = null;
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        const query = searchInput.value.trim();
        if (!query) {
          searchDropdown.style.display = 'none';
          return;
        }
        searchTimeout = setTimeout(async () => {
          try {
            const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
            if (res.ok) {
              const list = await res.json();
              renderSTSearchDropdown(list, searchInput, searchDropdown);
            }
          } catch (e) { }
        }, 120);
      });

      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const first = searchDropdown.querySelector('.dropdown-item');
          if (first && searchDropdown.style.display !== 'none') {
            first.click();
          } else if (searchInput.value.trim()) {
            addSuperTrendShare(searchInput.value.trim());
            searchInput.value = '';
            searchDropdown.style.display = 'none';
          }
        }
      });

      document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchDropdown.contains(e.target)) {
          searchDropdown.style.display = 'none';
        }
      });
    }

    if (btnAdd && searchInput) {
      btnAdd.addEventListener('click', () => {
        const val = searchInput.value.trim();
        if (val) {
          addSuperTrendShare(val);
          searchInput.value = '';
          if (searchDropdown) searchDropdown.style.display = 'none';
        }
      });
    }

    loadSuperTrendSettings();
  }

  function renderSTSearchDropdown(stocks, input, dropdown) {
    if (!dropdown) return;
    dropdown.innerHTML = '';
    const q = input.value.trim().toUpperCase();
    let items = Array.isArray(stocks) ? [...stocks] : [];
    if (q && !items.some(it => it.symbol.toUpperCase() === q)) {
      items.unshift({ symbol: q, name: `${q} Equity / Share`, exchange: 'NSE' });
    }
    items.slice(0, 10).forEach(stk => {
      const item = document.createElement('div');
      item.className = 'dropdown-item';
      item.style.cssText = 'padding: 8px 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;';
      item.onmouseenter = () => { item.style.background = '#f8fafc'; };
      item.onmouseleave = () => { item.style.background = '#ffffff'; };
      item.innerHTML = `<strong>${stk.symbol}</strong> <span style="font-size: 11px; color: #64748b;">${stk.name}</span>`;
      item.addEventListener('click', () => {
        addSuperTrendShare(stk.symbol);
        input.value = '';
        dropdown.style.display = 'none';
      });
      dropdown.appendChild(item);
    });
    dropdown.style.display = 'block';
  }

  let cachedSTPreview = null;
  async function refreshSuperTrendPreview() {
    const sym = stPreviewingSymbol || (stSelectedSymbols.length > 0 ? stSelectedSymbols[0] : null);

    const prevLivePrice = document.getElementById('st-prev-live-price');
    const prevStPrice = document.getElementById('st-prev-st-price');
    const prevTriggerPrice = document.getElementById('st-prev-trigger-price');
    const prevCurrentDir = document.getElementById('st-prev-current-dir');

    if (!sym) {
      if (prevLivePrice) prevLivePrice.textContent = '₹--.--';
      if (prevStPrice) prevStPrice.textContent = '₹--.--';
      if (prevTriggerPrice) prevTriggerPrice.textContent = '₹--.--';
      if (prevCurrentDir) {
        prevCurrentDir.className = 'st-preview-status';
        prevCurrentDir.style.background = '#f1f5f9';
        prevCurrentDir.style.color = '#64748b';
        prevCurrentDir.innerHTML = '<i class="fa-solid fa-layer-group"></i> No share selected';
      }
      return;
    }

    const proxInput = document.getElementById('cfg-st-proximity');
    const earlyPts = parseFloat(proxInput ? proxInput.value : 1.0) || 1.0;

    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(sym)}/supertrend?proximity=${earlyPts}`);
      if (!res.ok) return;
      const data = await res.json();
      cachedSTPreview = data;

      const liveP = Number(data.price || 0);
      const stP = Number(data.supertrend_price || 0);
      const isBuy = data.direction === 'BUY';
      const gap = Number(data.distance_points || Math.abs(liveP - stP)).toFixed(2);

      // Update badge on tag if rendered
      const tagBadge = document.getElementById(`st-tag-badge-${sym}`);
      if (tagBadge) {
        tagBadge.className = `st-tag-badge ${isBuy ? 'buy' : 'sell'}`;
        tagBadge.textContent = isBuy ? 'BUY' : 'SELL';
      }

      if (prevLivePrice) prevLivePrice.textContent = `₹${liveP.toFixed(2)}`;
      if (prevStPrice) prevStPrice.textContent = `₹${stP.toFixed(2)}`;

      if (prevCurrentDir) {
        prevCurrentDir.style.background = '';
        prevCurrentDir.style.color = '';
        prevCurrentDir.className = `st-preview-status ${isBuy ? 'buy' : 'sell'}`;
        prevCurrentDir.innerHTML = isBuy
          ? `<i class="fa-solid fa-arrow-trend-up"></i> ${sym}: BUY (BULLISH)`
          : `<i class="fa-solid fa-arrow-trend-down"></i> ${sym}: SELL (BEARISH)`;
      }

      const earlyAlertPrice = isBuy
        ? Math.max(0, stP + earlyPts)
        : Math.max(0, stP - earlyPts);

      if (prevTriggerPrice) {
        prevTriggerPrice.textContent = `₹${earlyAlertPrice.toFixed(2)}`;
      }
    } catch (e) {
      console.warn('[Monitoring] ST preview error:', e);
    }
  }

  // --- INIT ---
  let pollStockSnapshotTimer = null;
  function initMonitoring() {
    initForm();
    setupStockSearch();
    setupSuperTrendSection();
    fetchActiveSessions();
    if (pollSessionsTimer) clearInterval(pollSessionsTimer);
    pollSessionsTimer = setInterval(fetchActiveSessions, 4000);
    if (pollStockSnapshotTimer) clearInterval(pollStockSnapshotTimer);
    pollStockSnapshotTimer = setInterval(() => {
      refreshCurrentStockSnapshot();
      refreshSuperTrendPreview();
    }, 4000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMonitoring);
  } else {
    initMonitoring();
  }

  // Handle browser navigation back/forward (bfcache) & page re-entry
  window.addEventListener('pageshow', () => {
    fetchActiveSessions();
    refreshCurrentStockSnapshot();
    if (!pollSessionsTimer) {
      pollSessionsTimer = setInterval(fetchActiveSessions, 4000);
    }
    if (!pollStockSnapshotTimer) {
      pollStockSnapshotTimer = setInterval(refreshCurrentStockSnapshot, 4000);
    }
  });

  // Re-sync when user tabs back into the monitoring window
  window.addEventListener('focus', () => {
    fetchActiveSessions();
    refreshCurrentStockSnapshot();
  });

})();
