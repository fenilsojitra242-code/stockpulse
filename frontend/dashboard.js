/**
 * StockPulse — Live Stock Visualization with Floating Menus
 * Clean, lightweight, professional dashboard JavaScript
 */

(function () {
  'use strict';

  // Application State
  const state = {
    symbol: 'TCS',
    timeRange: '1D',
    pollingIntervalMs: 5000,
    pollTimer: null,
    isFetching: false,
    chart: null,
    currencySymbol: '₹',
    lastPrice: 0,
    availableStocks: [],

    // Dynamic Monitoring Configuration State
    targetPercent: 0.05,
    warningMinutes: 2,
    direction: 'UP',
    updateIntervalSec: 60,
    notificationMethod: 'Popup',
    isMonitoringActive: false,

    // User Authentication & Email Alert State
    user: null,
    userEmail: '',
    userName: ''
  };

  // DOM Elements Cache
  const elements = {
    // 1. Floating Upper Navigation
    navLinks: document.getElementById('nav-links'),
    btnMobileToggle: document.getElementById('btn-mobile-toggle'),
    marketStatusDot: document.getElementById('market-status-dot'),
    marketStatusText: document.getElementById('market-status-text'),
    globalStatusDot: document.getElementById('global-status-dot'),
    globalStatusText: document.getElementById('global-status-text'),
    btnOpenSettings: document.getElementById('btn-open-settings'),

    // 2. Dynamic Control Menu
    stockInput: document.getElementById('cfg-stock-input'),
    btnToggleStockDropdown: document.getElementById('btn-toggle-stock-dropdown'),
    stockDropdown: document.getElementById('stock-control-dropdown'),
    targetPctInput: document.getElementById('cfg-target-pct'),
    targetPriceInput: document.getElementById('cfg-target-price'),
    previewPredictionLabel: document.getElementById('preview-prediction-label'),
    previewProbPct: document.getElementById('preview-prob-pct'),
    previewScenarioText: document.getElementById('preview-scenario-text'),
    previewProbProgress: document.getElementById('preview-prob-progress'),
    previewExplanationText: document.getElementById('preview-explanation-text'),
    pctPresets: document.getElementById('pct-presets'),
    warningWindowSelect: document.getElementById('cfg-warning-window') || document.getElementById('cfg-warning-min'),
    directionSelect: document.getElementById('cfg-direction'),
    btnToggleMoreOptions: document.getElementById('btn-toggle-more-options'),
    secondaryControlsRow: document.getElementById('secondary-controls-row'),
    intervalSelect: document.getElementById('cfg-interval'),
    notificationSelect: document.getElementById('cfg-notification'),
    btnResetConfig: document.getElementById('btn-reset-config'),
    btnStartMonitoring: document.getElementById('btn-start-monitoring') || document.getElementById('btn-submit-monitor'),
    btnStartLabel: document.getElementById('btn-start-label') || (document.getElementById('btn-submit-monitor') ? document.getElementById('btn-submit-monitor').querySelector('span') : null),

    // Active Monitoring Indicators & Banners
    activeConfigPill: document.getElementById('active-config-pill'),
    indicatorSummaryText: document.getElementById('indicator-summary-text'),
    activeConfigBanner: document.getElementById('active-config-banner'),
    bannerSymbol: document.getElementById('banner-symbol'),
    bannerDirection: document.getElementById('banner-direction'),
    bannerTarget: document.getElementById('banner-target'),
    bannerWindow: document.getElementById('banner-window'),
    bannerExtras: document.getElementById('banner-extras'),

    // Active Predictive Monitoring Sessions (Sections 21 & 22)
    activeSessionsSection: document.getElementById('active-sessions-section') || document.querySelector('.side-monitoring-panel'),
    activeCardsGrid: document.getElementById('active-cards-grid') || document.getElementById('side-monitor-cards'),

    // 3. Live Stock Visualization Header & Price
    stockSymbol: document.getElementById('stock-symbol'),
    stockExchange: document.getElementById('stock-exchange'),
    companyName: document.getElementById('company-name'),
    currentPrice: document.getElementById('current-price'),
    priceChangeBadge: document.getElementById('price-change-badge'),
    priceChangeIcon: document.getElementById('price-change-icon'),
    priceChangeAbs: document.getElementById('price-change-abs'),
    priceChangePct: document.getElementById('price-change-pct'),
    prevCloseRef: document.getElementById('prev-close-ref'),
    liveStatusPill: document.getElementById('live-status-pill'),
    liveStatusDot: document.getElementById('live-status-dot'),
    liveStatusLabel: document.getElementById('live-status-label'),
    lastUpdatedTime: document.getElementById('last-updated-time'),

    // Time Range Controls
    rangeButtons: document.querySelectorAll('.range-btn'),
    rangeInfo: document.getElementById('range-info'),

    // Chart Area
    chartCanvas: document.getElementById('live-price-chart'),
    chartLoading: document.getElementById('chart-loading-overlay'),
    unavailableState: document.getElementById('unavailable-state'),
    canvasContainer: document.getElementById('canvas-container'),

    // 4. Stock Details / Statistics
    statOpen: document.getElementById('stat-open'),
    statPrevClose: document.getElementById('stat-prev-close'),
    statDayHigh: document.getElementById('stat-day-high'),
    statDayLow: document.getElementById('stat-day-low'),
    statVolume: document.getElementById('stat-volume'),
    stat52wHigh: document.getElementById('stat-52w-high'),
    stat52wLow: document.getElementById('stat-52w-low'),
    statAvgVol: document.getElementById('stat-avg-vol'),
    statMarketCap: document.getElementById('stat-market-cap'),
    statExchange: document.getElementById('stat-exchange'),
    statTradingStatus: document.getElementById('stat-trading-status'),

    // 5. Confirmation Modal
    configModalBackdrop: document.getElementById('config-modal-backdrop'),
    modalSummaryStock: document.getElementById('modal-summary-stock'),
    modalSummaryTarget: document.getElementById('modal-summary-target'),
    modalSummaryWindow: document.getElementById('modal-summary-window'),
    modalSummaryDirection: document.getElementById('modal-summary-direction'),
    modalSummaryInterval: document.getElementById('modal-summary-interval'),
    modalSummaryNotif: document.getElementById('modal-summary-notif'),
    btnCloseModal: document.getElementById('btn-close-modal'),
    btnDismissModal: document.getElementById('btn-dismiss-modal'),
    btnAcknowledgeModal: document.getElementById('btn-acknowledge-modal'),

    // 6. Settings Modal
    settingsModalBackdrop: document.getElementById('settings-modal-backdrop'),
    settingPollingRate: document.getElementById('setting-polling-rate'),
    btnCloseSettings: document.getElementById('btn-close-settings'),
    btnDismissSettings: document.getElementById('btn-dismiss-settings'),
    btnSaveSettings: document.getElementById('btn-save-settings'),

    // 7. System Notifications Feed
    btnToggleNotifications: document.getElementById('btn-toggle-notifications'),
    notifUnreadBadge: document.getElementById('notif-unread-badge'),
    notificationsSection: document.getElementById('notifications-section'),
    notificationsList: document.getElementById('notifications-list'),
    btnRefreshNotifs: document.getElementById('btn-refresh-notifs'),
    btnMarkAllRead: document.getElementById('btn-mark-all-read'),
    btnClearNotifs: document.getElementById('btn-clear-notifs'),
    notifEmptyPlaceholder: document.getElementById('notif-empty-placeholder'),

    // 8. Notification Detail Modal
    notifDetailModal: document.getElementById('notif-detail-modal-backdrop'),
    notifDetailTitle: document.getElementById('notif-detail-title'),
    notifDetailSubtitle: document.getElementById('notif-detail-subtitle'),
    notifDetailPriorityTag: document.getElementById('notif-detail-priority-tag'),
    notifDetailChannelTag: document.getElementById('notif-detail-channel-tag'),
    notifDetailTimeTag: document.getElementById('notif-detail-time-tag'),
    notifDetailMessageBody: document.getElementById('notif-detail-message-body'),
    btnCloseNotifDetail: document.getElementById('btn-close-notif-detail'),
    btnDismissNotifDetail: document.getElementById('btn-dismiss-notif-detail'),

    // 9. User Login & Email Alert Modal
    btnOpenLogin: document.getElementById('btn-open-login'),
    navUserLabel: document.getElementById('nav-user-label'),
    navUserIcon: document.getElementById('nav-user-icon'),
    loginModalBackdrop: document.getElementById('login-modal-backdrop'),
    btnCloseLoginModal: document.getElementById('btn-close-login-modal'),
    btnDismissLoginModal: document.getElementById('btn-dismiss-login-modal'),
    formUserLogin: document.getElementById('form-user-login'),
    loginNameInput: document.getElementById('login-name-input'),
    loginEmailInput: document.getElementById('login-email-input'),
    loginFeedbackMsg: document.getElementById('login-feedback-msg'),
    btnSubmitLogin: document.getElementById('btn-submit-login'),
    btnLogoutUser: document.getElementById('btn-logout-user'),
    loginActiveCard: document.getElementById('login-active-card'),
    activeUserName: document.getElementById('active-user-name'),
    activeUserEmail: document.getElementById('active-user-email'),
    userAvatarCircle: document.getElementById('user-avatar-circle'),
    emailNotifHint: document.getElementById('email-notif-hint'),
    emailDestAddr: document.getElementById('email-dest-addr'),
    linkSetupEmail: document.getElementById('link-setup-email')
  };

  const RANGE_METADATA = {
    '1D': 'Intraday 1-Minute Interval',
    '5D': '5-Day History (5-Min Interval)',
    '1M': '1-Month Historical Trend',
    '6M': '6-Month Historical Trend',
    '1Y': '1-Year Historical Performance'
  };

  // --- INITIALIZATION ---
  document.addEventListener('DOMContentLoaded', () => {
    initChart();
    setupEventHandlers();
    loadAvailableStocks();
    fetchStockData(state.symbol, state.timeRange, true);
    startLivePolling();
    fetchNotifications();
    setInterval(fetchNotifications, 10000);
    fetchActiveSessions();
    setInterval(fetchActiveSessions, 4000);
    initUserSession();
  });

  // --- CHART SETUP WITH CUSTOM CURRENT-PRICE INDICATOR ---
  function initChart() {
    const ctx = elements.chartCanvas.getContext('2d');

    // Custom Plugin: Horizontal Current Price Line and Label on Price Axis
    // Custom Plugin: Horizontal Current Price Line and Alert Overlays on Chart
    const currentPriceLinePlugin = {
      id: 'currentPriceLine',
      afterDraw: (chart) => {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea || !scales.y) return;

        // 1. Current Price Line
        if (typeof state.lastPrice === 'number' && state.lastPrice > 0) {
          const yPos = scales.y.getPixelForValue(state.lastPrice);
          if (yPos >= chartArea.top && yPos <= chartArea.bottom) {
            ctx.save();
            ctx.beginPath();
            ctx.setLineDash([4, 4]);
            ctx.strokeStyle = '#64748b';
            ctx.lineWidth = 1;
            ctx.moveTo(chartArea.left, yPos);
            ctx.lineTo(chartArea.right, yPos);
            ctx.stroke();

            const labelText = `${state.currencySymbol}${state.lastPrice.toFixed(2)}`;
            ctx.font = '600 11px JetBrains Mono, monospace';
            const textWidth = ctx.measureText(labelText).width;
            const boxWidth = textWidth + 12;
            const boxHeight = 18;
            const boxX = chartArea.right - boxWidth;
            const boxY = yPos - boxHeight / 2;

            ctx.fillStyle = '#0f172a';
            ctx.beginPath();
            if (ctx.roundRect) {
              ctx.roundRect(boxX, boxY, boxWidth, boxHeight, 4);
            } else {
              ctx.rect(boxX, boxY, boxWidth, boxHeight);
            }
            ctx.fill();

            ctx.fillStyle = '#ffffff';
            ctx.textBaseline = 'middle';
            ctx.fillText(labelText, boxX + 6, yPos);
            ctx.restore();
          }
        }

        // 2. Active Alert Overlays (Target, Reference, and Stop-Loss)
        const curSym = (state.symbol || '').toUpperCase();
        const activeAlert = (state.activeAlerts || []).find(a => (a.symbol || '').toUpperCase() === curSym);

        if (activeAlert) {
          // Reference Price Line (Blue)
          if (typeof activeAlert.reference_price === 'number' && activeAlert.reference_price > 0) {
            const refY = scales.y.getPixelForValue(activeAlert.reference_price);
            if (refY >= chartArea.top && refY <= chartArea.bottom) {
              ctx.save();
              ctx.beginPath();
              ctx.setLineDash([3, 3]);
              ctx.strokeStyle = '#2563eb';
              ctx.lineWidth = 1.2;
              ctx.moveTo(chartArea.left, refY);
              ctx.lineTo(chartArea.right, refY);
              ctx.stroke();

              const refText = `REF: ₹${activeAlert.reference_price.toFixed(2)}`;
              ctx.font = '600 10px JetBrains Mono, monospace';
              const rWidth = ctx.measureText(refText).width + 10;
              ctx.fillStyle = '#2563eb';
              ctx.beginPath();
              if (ctx.roundRect) ctx.roundRect(chartArea.left + 8, refY - 8, rWidth, 16, 4);
              else ctx.rect(chartArea.left + 8, refY - 8, rWidth, 16);
              ctx.fill();
              ctx.fillStyle = '#ffffff';
              ctx.textBaseline = 'middle';
              ctx.fillText(refText, chartArea.left + 13, refY);
              ctx.restore();
            }
          }

          // Target Price Line (Green for UP, Red for DOWN)
          if (typeof activeAlert.target_price === 'number' && activeAlert.target_price > 0) {
            const tY = scales.y.getPixelForValue(activeAlert.target_price);
            if (tY >= chartArea.top && tY <= chartArea.bottom) {
              const tColor = activeAlert.direction === 'DOWN' ? '#dc2626' : '#16a34a';
              ctx.save();
              ctx.beginPath();
              ctx.setLineDash([6, 3]);
              ctx.strokeStyle = tColor;
              ctx.lineWidth = 1.8;
              ctx.moveTo(chartArea.left, tY);
              ctx.lineTo(chartArea.right, tY);
              ctx.stroke();

              const tSign = activeAlert.direction === 'DOWN' ? '-' : '+';
              const tText = `🎯 TARGET: ₹${activeAlert.target_price.toFixed(2)} (${tSign}${Math.abs(activeAlert.target_percent).toFixed(2)}%)`;
              ctx.font = '700 10.5px JetBrains Mono, monospace';
              const tWidth = ctx.measureText(tText).width + 12;
              ctx.fillStyle = tColor;
              ctx.beginPath();
              if (ctx.roundRect) ctx.roundRect(chartArea.right - tWidth - 8, tY - 9, tWidth, 18, 4);
              else ctx.rect(chartArea.right - tWidth - 8, tY - 9, tWidth, 18);
              ctx.fill();
              ctx.fillStyle = '#ffffff';
              ctx.textBaseline = 'middle';
              ctx.fillText(tText, chartArea.right - tWidth - 2, tY);
              ctx.restore();
            }
          }


        }
      }
    };

    state.chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Price',
          data: [],
          borderColor: '#16a34a',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: '#16a34a',
          pointHoverBorderColor: '#ffffff',
          pointHoverBorderWidth: 2,
          tension: 0.15,
          fill: true,
          backgroundColor: 'rgba(22, 163, 74, 0.06)'
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            enabled: true,
            backgroundColor: 'rgba(15, 23, 42, 0.95)',
            titleFont: { family: 'Plus Jakarta Sans', size: 12, weight: '600' },
            bodyFont: { family: 'JetBrains Mono', size: 13, weight: '700' },
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
            callbacks: {
              title: (items) => {
                if (!items.length) return '';
                return items[0].label || '';
              },
              label: (context) => {
                const val = context.parsed.y;
                return `Price: ${state.currencySymbol}${val.toFixed(2)}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: {
              display: false,
              drawBorder: false
            },
            ticks: {
              color: '#94a3b8',
              font: { family: 'JetBrains Mono', size: 11 },
              maxTicksLimit: 8,
              autoSkip: true
            }
          },
          y: {
            position: 'right',
            grid: {
              color: '#f1f5f9',
              drawBorder: false
            },
            ticks: {
              color: '#64748b',
              font: { family: 'JetBrains Mono', size: 11 },
              callback: (value) => `${state.currencySymbol}${value.toFixed(0)}`
            }
          }
        }
      },
      plugins: [currentPriceLinePlugin]
    });
  }

  // --- SETUP EVENT HANDLERS ---
  function setupEventHandlers() {
    // 1. Mobile Navigation Menu Toggle
    elements.btnMobileToggle.addEventListener('click', () => {
      elements.navLinks.classList.toggle('mobile-open');
    });

    // 2. Settings Modal Triggers
    elements.btnOpenSettings.addEventListener('click', () => {
      elements.settingsModalBackdrop.style.display = 'flex';
    });
    elements.btnCloseSettings.addEventListener('click', () => {
      elements.settingsModalBackdrop.style.display = 'none';
    });
    elements.btnDismissSettings.addEventListener('click', () => {
      elements.settingsModalBackdrop.style.display = 'none';
    });
    elements.btnSaveSettings.addEventListener('click', () => {
      const rate = parseInt(elements.settingPollingRate.value, 10) || 5000;
      state.pollingIntervalMs = rate;
      startLivePolling();
      elements.settingsModalBackdrop.style.display = 'none';
    });

    // 2b. Email Authentication Modal Triggers
    if (elements.btnOpenEmailConfig) {
      elements.btnOpenEmailConfig.addEventListener('click', () => {
        openEmailModal();
      });
    }
    if (elements.btnCloseEmailModal) {
      elements.btnCloseEmailModal.addEventListener('click', () => {
        closeEmailModal();
      });
    }
    if (elements.btnDismissEmailModal) {
      elements.btnDismissEmailModal.addEventListener('click', () => {
        closeEmailModal();
      });
    }
    if (elements.linkSetupEmail) {
      elements.linkSetupEmail.addEventListener('click', (e) => {
        e.preventDefault();
        openEmailModal();
      });
    }
    if (elements.btnToggleEmailPwd) {
      elements.btnToggleEmailPwd.addEventListener('click', () => {
        const type = elements.cfgEmailPass.type === 'password' ? 'text' : 'password';
        elements.cfgEmailPass.type = type;
        elements.btnToggleEmailPwd.innerHTML = type === 'password' ? '<i class="fa-regular fa-eye"></i>' : '<i class="fa-regular fa-eye-slash"></i>';
      });
    }
    if (elements.emailPresetBtns) {
      elements.emailPresetBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          const presetKey = btn.getAttribute('data-preset');
          applyEmailPreset(presetKey);
        });
      });
    }
    if (elements.btnTestEmailAuth) {
      elements.btnTestEmailAuth.addEventListener('click', () => {
        testEmailAuthentication();
      });
    }
    if (elements.btnSaveEmailConfig) {
      elements.btnSaveEmailConfig.addEventListener('click', () => {
        saveEmailConfiguration();
      });
    }

    // 3. Dynamic Stock Input & Dropdown
    elements.stockInput.addEventListener('input', (e) => {
      const q = e.target.value.trim().toUpperCase();
      renderStockDropdown(q);
    });

    elements.stockInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        elements.stockDropdown.classList.remove('show');
        const raw = elements.stockInput.value.trim().toUpperCase();
        if (raw) selectStock(raw.replace('.NS', '').replace('.BO', ''));
      }
    });

    elements.btnToggleStockDropdown.addEventListener('click', (e) => {
      e.stopPropagation();
      if (elements.stockDropdown.classList.contains('show')) {
        elements.stockDropdown.classList.remove('show');
      } else {
        renderStockDropdown('');
      }
    });

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
      if (!elements.stockInput.contains(e.target) && !elements.stockDropdown.contains(e.target) && !elements.btnToggleStockDropdown.contains(e.target)) {
        elements.stockDropdown.classList.remove('show');
      }
    });

    // 4. Target % Input, Target Price, & Live Prediction Preview
    let predictDebounceTimer = null;
    function triggerPredictionPreview() {
      clearTimeout(predictDebounceTimer);
      predictDebounceTimer = setTimeout(fetchPredictionPreview, 300);
    }

    async function fetchPredictionPreview() {
      if (!state.symbol || !state.targetPercent || !state.warningMinutes) return;
      try {
        if (elements.previewPredictionLabel) {
          elements.previewPredictionLabel.textContent = 'EVALUATING';
          elements.previewPredictionLabel.className = 'badge';
        }
        const resp = await fetch('/api/alerts/predict', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: state.symbol,
            direction: state.direction,
            target_percent: state.targetPercent,
            warning_minutes: state.warningMinutes
          })
        });
        if (resp.ok) {
          const data = await resp.json();
          const prob = data.probability || 0;
          const probPct = Math.round(prob * 100);
          if (elements.previewProbPct) elements.previewProbPct.textContent = `${probPct}%`;
          if (elements.previewProbProgress) elements.previewProbProgress.style.width = `${Math.min(100, Math.max(0, probPct))}%`;
          if (elements.previewPredictionLabel) {
            elements.previewPredictionLabel.textContent = data.prediction_label || 'MONITORING';
            elements.previewPredictionLabel.className = `badge ${data.is_alert_worthy ? 'badge-success' : (probPct >= 50 ? 'badge-warning' : 'badge-info')}`;
          }
          if (elements.previewScenarioText) {
            const dirSign = data.direction === 'DOWN' ? '-' : (data.direction === 'BOTH' ? '±' : '+');
            elements.previewScenarioText.textContent = `${data.symbol} ${dirSign}${data.target_percent.toFixed(2)}% in ${data.warning_minutes}m`;
          }
          if (elements.previewExplanationText) {
            elements.previewExplanationText.textContent = data.explanation || '';
          }
        }
      } catch (err) {
        console.warn('[StockPulse] Prediction preview fetch error:', err);
      }
    }

    function updateDashboardTargetStyling(updateTargetPriceInput = true) {
      if (!elements.targetPctInput) return;
      const rawVal = elements.targetPctInput.value.trim();
      const cleanedNum = parseFloat(rawVal.replace(/[^\d.]/g, ''));
      const absVal = (!isNaN(cleanedNum) && cleanedNum > 0) ? cleanedNum : (state.targetPercent || 0.05);

      const hasMinus = rawVal.startsWith('-');
      const hasPlus = rawVal.startsWith('+');

      let isDown = false;
      if (hasMinus) {
        isDown = true;
        if (elements.directionSelect && elements.directionSelect.value !== 'DOWN') {
          elements.directionSelect.value = 'DOWN';
          state.direction = 'DOWN';
        }
      } else if (hasPlus) {
        isDown = false;
        if (elements.directionSelect && elements.directionSelect.value !== 'UP') {
          elements.directionSelect.value = 'UP';
          state.direction = 'UP';
        }
      } else {
        isDown = elements.directionSelect ? elements.directionSelect.value === 'DOWN' : (state.direction === 'DOWN');
      }

      state.targetPercent = absVal;

      const suffixEl = document.getElementById('cfg-affix-tag');
      const tgtPctValEl = document.getElementById('tgt-pct-val');

      if (isDown) {
        elements.targetPctInput.classList.remove('dir-positive');
        elements.targetPctInput.classList.add('dir-negative');
        if (suffixEl) {
          suffixEl.classList.remove('dir-positive');
          suffixEl.classList.add('dir-negative');
        }
        if (elements.directionSelect) {
          elements.directionSelect.style.color = '#dc2626';
          elements.directionSelect.style.fontWeight = '700';
        }
        if (tgtPctValEl) {
          tgtPctValEl.textContent = `-${absVal.toFixed(2)}%`;
          tgtPctValEl.style.color = '#dc2626';
        }
      } else {
        elements.targetPctInput.classList.remove('dir-negative');
        elements.targetPctInput.classList.add('dir-positive');
        if (suffixEl) {
          suffixEl.classList.remove('dir-negative');
          suffixEl.classList.add('dir-positive');
        }
        if (elements.directionSelect) {
          elements.directionSelect.style.color = '#16a34a';
          elements.directionSelect.style.fontWeight = '700';
        }
        if (tgtPctValEl) {
          tgtPctValEl.textContent = `+${absVal.toFixed(2)}%`;
          tgtPctValEl.style.color = '#16a34a';
        }
      }

      // Auto-calculate exact target price based on current stock price
      if (updateTargetPriceInput && elements.targetPriceInput && state.lastPrice && state.lastPrice > 0) {
        const calculatedTargetPrice = isDown
          ? state.lastPrice * (1 - absVal / 100)
          : state.lastPrice * (1 + absVal / 100);
        elements.targetPriceInput.value = calculatedTargetPrice.toFixed(2);
      }

      // Update chips text & styling
      const chips = document.querySelectorAll('.chip-row .form-chip, .pct-presets .preset-chip');
      chips.forEach(chip => {
        const val = parseFloat(chip.getAttribute('data-pct') || chip.getAttribute('data-val'));
        if (!isNaN(val)) {
          chip.textContent = isDown ? `-${val.toFixed(2)}%` : `+${val.toFixed(2)}%`;
          chip.classList.toggle('dir-negative', isDown);
          chip.classList.toggle('active', Math.abs(val - absVal) < 0.01);
        }
      });

      if (state.isMonitoringActive) updateActiveSummaryDisplay();
      triggerPredictionPreview();
    }

    elements.targetPctInput.addEventListener('input', () => {
      updateDashboardTargetStyling();
    });

    const formatCleanDashboardTarget = () => {
      if (!elements.targetPctInput) return;
      const cleanNum = parseFloat(elements.targetPctInput.value.replace(/[^\d.]/g, '')) || state.targetPercent || 0.05;
      state.targetPercent = cleanNum;
      const isDown = (elements.directionSelect ? elements.directionSelect.value : state.direction) === 'DOWN';
      elements.targetPctInput.value = (isDown ? '-' : '+') + cleanNum.toFixed(2);
      updateDashboardTargetStyling();
    };
    elements.targetPctInput.addEventListener('change', formatCleanDashboardTarget);
    elements.targetPctInput.addEventListener('blur', formatCleanDashboardTarget);

    if (elements.targetPriceInput) {
      elements.targetPriceInput.addEventListener('input', () => {
        const customPrice = parseFloat(elements.targetPriceInput.value);
        if (!isNaN(customPrice) && customPrice > 0 && state.lastPrice && state.lastPrice > 0) {
          const isDown = customPrice < state.lastPrice;
          const diffPct = Math.abs((customPrice - state.lastPrice) / state.lastPrice) * 100;
          state.targetPercent = diffPct;
          state.direction = isDown ? 'DOWN' : 'UP';
          if (elements.directionSelect) elements.directionSelect.value = state.direction;
          if (elements.targetPctInput) elements.targetPctInput.value = (isDown ? '-' : '+') + diffPct.toFixed(2);
          updateDashboardTargetStyling(false);
        }
      });
    }

    const chipButtons = document.querySelectorAll('.chip-row .form-chip, .pct-presets .preset-chip');
    chipButtons.forEach(chip => {
      chip.addEventListener('click', () => {
        const val = parseFloat(chip.getAttribute('data-pct') || chip.getAttribute('data-val'));
        if (!isNaN(val)) {
          state.targetPercent = val;
          const isDown = state.direction === 'DOWN';
          elements.targetPctInput.value = (isDown ? '-' : '+') + val.toFixed(2);
          updateDashboardTargetStyling();
        }
      });
    });

    // 5. Warning Window Input
    if (elements.warningWindowSelect) {
      const handleWindowChange = (e) => {
        const val = Math.max(1, parseInt(e.target.value, 10) || 1);
        state.warningMinutes = val;
        const tgtMinVal = document.getElementById('tgt-min-val');
        if (tgtMinVal) tgtMinVal.textContent = `${val} min`;
        if (state.isMonitoringActive) updateActiveSummaryDisplay();
        triggerPredictionPreview();
      };
      elements.warningWindowSelect.addEventListener('input', handleWindowChange);
      elements.warningWindowSelect.addEventListener('change', handleWindowChange);
    }

    // 6. Direction Select
    elements.directionSelect.addEventListener('change', (e) => {
      state.direction = e.target.value || 'UP';
      const isDown = state.direction === 'DOWN';
      const cleanNum = parseFloat(elements.targetPctInput.value.replace(/[^\d.]/g, '')) || state.targetPercent || 0.05;
      state.targetPercent = cleanNum;
      elements.targetPctInput.value = (isDown ? '-' : '+') + cleanNum.toFixed(2);
      updateDashboardTargetStyling();
    });

    // 7. More Options Toggle
    elements.btnToggleMoreOptions.addEventListener('click', () => {
      elements.secondaryControlsRow.classList.toggle('show');
    });

    // 8. Secondary Parameters
    if (elements.intervalSelect) {
      elements.intervalSelect.addEventListener('change', (e) => {
        state.updateIntervalSec = parseInt(e.target.value, 10) || 60;
        if (state.isMonitoringActive) updateActiveSummaryDisplay();
      });
    }

    const intervalButtons = document.querySelectorAll('#interval-selector .interval-btn');
    intervalButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        intervalButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        state.updateIntervalSec = (parseInt(btn.getAttribute('data-interval'), 10) || 1) * 60;
        if (state.isMonitoringActive) updateActiveSummaryDisplay();
      });
    });

    state.notificationMethod = 'Popup';

    // Reset Defaults
    elements.btnResetConfig.addEventListener('click', () => {
      resetConfigurationDefaults();
    });

    // 9. Start / Stop Monitoring Action Button
    if (elements.btnStartMonitoring) {
      elements.btnStartMonitoring.addEventListener('click', (e) => {
        if (e.target.closest('form')) e.preventDefault();
        startMonitoringConfiguration();
      });
    }

    const alertConfigForm = document.getElementById('alert-config-form');
    if (alertConfigForm) {
      alertConfigForm.addEventListener('submit', (e) => {
        e.preventDefault();
        startMonitoringConfiguration();
      });
    }

    // 10. Confirmation Modal Handlers
    elements.btnCloseModal.addEventListener('click', () => {
      elements.configModalBackdrop.style.display = 'none';
    });
    elements.btnDismissModal.addEventListener('click', () => {
      elements.configModalBackdrop.style.display = 'none';
    });
    elements.btnAcknowledgeModal.addEventListener('click', () => {
      elements.configModalBackdrop.style.display = 'none';
    });

    // 11. Time Range Buttons (1D, 5D, 1M, 6M, 1Y)
    elements.rangeButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        const range = btn.getAttribute('data-range');
        if (range && range !== state.timeRange) {
          elements.rangeButtons.forEach((b) => {
            b.classList.remove('active');
            b.setAttribute('aria-selected', 'false');
          });
          btn.classList.add('active');
          btn.setAttribute('aria-selected', 'true');

          state.timeRange = range;
          elements.rangeInfo.textContent = RANGE_METADATA[range] || range;
          fetchStockData(state.symbol, state.timeRange, true);
        }
      });
    });

    // 12. Notification Center & Feed Event Listeners
    if (elements.btnToggleNotifications) {
      elements.btnToggleNotifications.addEventListener('click', () => {
        if (elements.notificationsSection) {
          elements.notificationsSection.scrollIntoView({ behavior: 'smooth' });
        }
      });
    }

    if (elements.btnRefreshNotifs) {
      elements.btnRefreshNotifs.addEventListener('click', () => {
        fetchNotifications();
      });
    }

    if (elements.btnMarkAllRead) {
      elements.btnMarkAllRead.addEventListener('click', () => {
        markAllNotificationsRead();
      });
    }

    if (elements.btnClearNotifs) {
      elements.btnClearNotifs.addEventListener('click', () => {
        clearAllNotifications();
      });
    }

    if (elements.btnCloseNotifDetail) {
      elements.btnCloseNotifDetail.addEventListener('click', () => {
        if (elements.notifDetailModal) elements.notifDetailModal.style.display = 'none';
      });
    }

    if (elements.btnDismissNotifDetail) {
      elements.btnDismissNotifDetail.addEventListener('click', () => {
        if (elements.notifDetailModal) elements.notifDetailModal.style.display = 'none';
      });
    }

    if (elements.btnOpenLogin) {
      elements.btnOpenLogin.addEventListener('click', () => {
        if (!state.user || !state.userEmail) {
          window.location.href = '/login';
        } else {
          openLoginModal();
        }
      });
    }
    if (elements.btnCloseLoginModal) {
      elements.btnCloseLoginModal.addEventListener('click', closeLoginModal);
    }
    if (elements.btnDismissLoginModal) {
      elements.btnDismissLoginModal.addEventListener('click', closeLoginModal);
    }
    if (elements.loginModalBackdrop) {
      elements.loginModalBackdrop.addEventListener('click', (e) => {
        if (e.target === elements.loginModalBackdrop) closeLoginModal();
      });
    }
    if (elements.formUserLogin) {
      elements.formUserLogin.addEventListener('submit', handleLoginSubmit);
    }
    if (elements.btnLogoutUser) {
      elements.btnLogoutUser.addEventListener('click', handleLogout);
    }
    if (elements.linkSetupEmail) {
      elements.linkSetupEmail.addEventListener('click', (e) => {
        e.preventDefault();
        openLoginModal();
      });
    }
  }

  // --- DYNAMIC STOCK SELECTION ---
  function selectStock(symbol) {
    if (!symbol) return;
    state.symbol = symbol.toUpperCase();
    elements.stockInput.value = state.symbol;
    elements.stockDropdown.classList.remove('show');

    // Update active banners if monitoring is active
    if (state.isMonitoringActive) {
      updateActiveSummaryDisplay();
    }

    // Immediately fetch live visualization for the selected stock
    fetchStockData(state.symbol, state.timeRange, true);
  }

  async function loadAvailableStocks() {
    try {
      const res = await fetch('/api/stocks');
      if (res.ok) {
        const data = await res.json();
        state.availableStocks = Array.isArray(data) ? data : [];
      }
    } catch (e) {
      console.warn('[StockPulse] Could not pre-fetch stocks list:', e);
    }
  }

  function renderStockDropdown(query) {
    const list = state.availableStocks.length ? state.availableStocks : [
      { symbol: 'TCS', name: 'Tata Consultancy Services', exchange: 'NSE' },
      { symbol: 'INFY', name: 'Infosys Ltd', exchange: 'NSE' },
      { symbol: 'RELIANCE', name: 'Reliance Industries', exchange: 'NSE' },
      { symbol: 'HDFCBANK', name: 'HDFC Bank Ltd', exchange: 'NSE' },
      { symbol: 'ICICIBANK', name: 'ICICI Bank Ltd', exchange: 'NSE' },
      { symbol: 'SBIN', name: 'State Bank of India', exchange: 'NSE' },
      { symbol: 'BHARTIARTL', name: 'Bharti Airtel Ltd', exchange: 'NSE' },
      { symbol: 'WIPRO', name: 'Wipro Ltd', exchange: 'NSE' }
    ];

    let matches = list.filter((s) => {
      if (!query) return true;
      return s.symbol.toUpperCase().includes(query) || (s.name && s.name.toUpperCase().includes(query));
    });

    if (query && !matches.some((m) => m.symbol.toUpperCase() === query)) {
      matches.unshift({ symbol: query, name: `Search symbol '${query}'`, exchange: 'NSE' });
    }

    if (matches.length === 0) {
      elements.stockDropdown.innerHTML = `<div class="dropdown-item" style="color: #94a3b8; font-size: 12px;">No matching stock found.</div>`;
      elements.stockDropdown.classList.add('show');
      return;
    }

    elements.stockDropdown.innerHTML = matches.slice(0, 8).map((m) => `
      <div class="dropdown-item" data-sym="${m.symbol}">
        <div class="item-left">
          <span class="item-symbol">${m.symbol}</span>
          <span class="item-name">${m.name || ''}</span>
        </div>
        <span class="item-exchange">${m.exchange || 'NSE'}</span>
      </div>
    `).join('');

    elements.stockDropdown.querySelectorAll('.dropdown-item').forEach((item) => {
      item.addEventListener('click', () => {
        const sym = item.getAttribute('data-sym');
        if (sym) selectStock(sym);
      });
    });

    elements.stockDropdown.classList.add('show');
  }

  function updatePresetChips(val) {
    elements.pctPresets.querySelectorAll('.preset-chip').forEach((chip) => {
      const chipVal = parseFloat(chip.getAttribute('data-val'));
      if (Math.abs(chipVal - val) < 0.05) {
        chip.classList.add('active');
      } else {
        chip.classList.remove('active');
      }
    });
  }

  function resetConfigurationDefaults() {
    state.targetPercent = 5.0;
    state.warningMinutes = 10;
    state.direction = 'UP';
    state.updateIntervalSec = 60;
    state.notificationMethod = 'Popup';

    elements.targetPctInput.value = '5.0';
    updatePresetChips(5.0);
    elements.warningWindowSelect.value = '10';
    elements.directionSelect.value = 'UP';
    elements.intervalSelect.value = '60';
    elements.notificationSelect.value = 'Popup';

    if (state.isMonitoringActive) updateActiveSummaryDisplay();
  }

  // --- START & STOP MONITORING CONFIGURATION LOGIC ---
  async function startMonitoringConfiguration() {
    state.isMonitoringActive = true;

    // 1. Update Button State
    elements.btnStartMonitoring.classList.add('active-state');
    elements.btnStartLabel.textContent = 'START MONITORING';
    elements.btnStartMonitoring.querySelector('i').className = 'fa-solid fa-play';

    // 2. Show Active Pill & Banner
    updateActiveSummaryDisplay();
    elements.activeConfigPill.style.display = 'inline-flex';
    elements.activeConfigBanner.style.display = 'flex';

    // 3. Register with Backend Monitoring & Automated Messaging Engine
    try {
      const resp = await fetch('/api/alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: state.symbol,
          direction: state.direction,
          target_percent: state.targetPercent,
          warning_minutes: state.warningMinutes,
          monitoring_interval: 1, // System-controlled 1-min check interval
          notification_method: 'popup'
        })
      });
      if (resp.ok) {
        const data = await resp.json();
        state.activeAlertId = data.id;
        // Immediate fetch of active monitoring sessions grid
        await fetchActiveSessions();
        setTimeout(fetchNotifications, 500);
      }
    } catch (e) {
      console.warn('[StockPulse] Could not register monitoring session with backend:', e);
    }
  }

  async function stopMonitoringConfiguration() {
    // 1. Notify Backend to Stop current alert if active
    if (state.activeAlertId) {
      try {
        await fetch(`/api/alerts/${state.activeAlertId}/stop`, { method: 'POST' });
        state.activeAlertId = null;
        await fetchActiveSessions();
        setTimeout(fetchNotifications, 500);
      } catch (e) {
        console.warn('[StockPulse] Error notifying backend stop:', e);
      }
    }
  }

  function updateActiveSummaryDisplay() {
    const dirSign = state.direction === 'DOWN' ? '-' : (state.direction === 'UP' ? '+' : '±');
    const targetStr = `${dirSign}${state.targetPercent.toFixed(2)}%`;
    const summaryShort = `${state.symbol} · ${state.direction} · ${targetStr} · ${state.warningMinutes} min`;

    // Update Pill Text
    elements.indicatorSummaryText.textContent = summaryShort;

    // Update Banner Details
    elements.bannerSymbol.textContent = state.symbol;
    elements.bannerDirection.textContent = state.direction;
    elements.bannerTarget.textContent = targetStr;
    elements.bannerWindow.textContent = `${state.warningMinutes} min`;
    elements.bannerExtras.textContent = `1m Check | Live Early-Warning`;
  }

  function populateConfirmationModal() {
    const dirSign = state.direction === 'DOWN' ? '-' : (state.direction === 'UP' ? '+' : '±');
    const targetStr = `${dirSign}${state.targetPercent.toFixed(2)}%`;

    elements.modalSummaryStock.textContent = state.symbol;
    elements.modalSummaryTarget.textContent = targetStr;
    elements.modalSummaryWindow.textContent = `${state.warningMinutes} minutes`;
    elements.modalSummaryDirection.textContent = state.direction;
    elements.modalSummaryInterval.textContent = '1 min (System)';
    elements.modalSummaryNotif.textContent = state.notificationMethod;
  }

  function formatInterval(sec) {
    if (sec < 60) return `${sec} sec`;
    return `${Math.round(sec / 60)} min`;
  }

  // --- ACTIVE PREDICTIVE MONITORING SESSIONS (Sections 21 & 22) ---
  async function fetchActiveSessions() {
    if (!elements.activeCardsGrid) return;
    try {
      const res = await fetch('/api/alerts/active');
      if (!res.ok) return;
      const alerts = await res.json();
      renderActiveSessions(alerts);
    } catch (e) {
      console.warn('[StockPulse] Could not fetch active sessions:', e);
    }
  }

  function renderActiveSessions(alerts) {
    if (!elements.activeSessionsSection || !elements.activeCardsGrid) return;
    state.activeAlerts = alerts || [];
    if (state.chart) {
      try { state.chart.draw(); } catch (e) {}
    }

    if (!Array.isArray(alerts) || alerts.length === 0) {
      elements.activeSessionsSection.style.display = 'none';
      elements.activeConfigBanner.style.display = 'none';
      elements.activeConfigPill.style.display = 'none';
      state.isMonitoringActive = false;
      elements.btnStartMonitoring.classList.remove('active-state');
      elements.btnStartLabel.textContent = 'START MONITORING';
      elements.btnStartMonitoring.querySelector('i').className = 'fa-solid fa-play';
      elements.activeCardsGrid.innerHTML = '';
      return;
    }

    elements.activeSessionsSection.style.display = 'block';

    const cardsHtml = alerts.map((a) => {
      const isUp = a.direction === 'UP';
      const isDown = a.direction === 'DOWN';
      const dirSign = isDown ? '-' : (isUp ? '+' : '±');
      const dirLabel = isDown ? 'DOWN (-)' : (isUp ? 'UP (+)' : 'BOTH (±)');
      const dirColor = isDown ? '#dc2626' : (isUp ? '#16a34a' : '#2563eb');
      const targetStr = `${dirSign}${a.target_percent.toFixed(2)}%`;
      const currentPriceStr = a.current_price ? `₹${a.current_price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
      const targetPriceStr = a.target_price ? `₹${a.target_price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
      const refPriceStr = a.reference_price ? `₹${a.reference_price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
      const stopLossStr = a.stop_loss_price ? `₹${Number(a.stop_loss_price).toFixed(2)}` : (a.stop_loss_percent ? `${a.stop_loss_percent}%` : '-0.03%');
      
      const currentMoveVal = (a.current_movement != null && !isNaN(a.current_movement)) ? Number(a.current_movement) : null;
      const moveSign = (currentMoveVal != null && currentMoveVal > 0) ? '+' : '';
      const moveColor = (currentMoveVal != null && currentMoveVal > 0) ? '#16a34a' : (currentMoveVal != null && currentMoveVal < 0 ? '#dc2626' : '#64748b');
      const moveStr = currentMoveVal != null ? `${moveSign}${currentMoveVal.toFixed(2)}%` : '--';

      const remVal = (a.remaining_percent != null && !isNaN(a.remaining_percent)) ? Number(a.remaining_percent) : null;
      const remStr = remVal != null ? `${remVal.toFixed(2)}%` : '--';

      const progressVal = a.progress_percent != null ? Number(a.progress_percent) : 0;
      const distRupees = a.target_distance_rupees != null ? `₹${Number(a.target_distance_rupees).toFixed(2)}` : '--';
      const milestoneBadge = a.milestone_50_sent ? `<span style="background: #ede9fe; color: #7c3aed; font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-flag-checkered"></i> 50% Milestone Hit</span>` : '';

      const dirClass = isDown ? 'dir-down' : (isUp ? 'dir-up' : 'dir-both');
      const dirIcon = isDown ? 'fa-arrow-trend-down' : (isUp ? 'fa-arrow-trend-up' : 'fa-arrows-up-down');
      let dotColor = 'green';
      let statusClass = 'monitoring';
      let statusLabel = 'MONITORING';
      if (a.status === 'EARLY_WARNING') {
        statusClass = 'early-warning';
        statusLabel = 'EARLY WARNING';
        dotColor = 'amber';
      } else if (a.status === 'TARGET_TRIGGERED' || a.status === 'TRIGGERED' || a.status === 'SUCCESS') {
        statusClass = 'target-triggered';
        statusLabel = 'TARGET TRIGGERED';
        dotColor = 'green';
      } else if (a.status === 'EXPIRED') {
        statusClass = 'expired';
        statusLabel = 'EXPIRED';
        dotColor = 'gray';
      }

      const fmt = (window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST : (s) => s ? new Date(s + 'Z').toLocaleTimeString('en-IN', { hour12: false }) : '--:--:--';
      const updatedTimeStr = fmt(a.last_checked_at || a.started_at, 'time');

      return `
        <div class="active-session-card status-${statusClass}" id="session-card-${a.id}">
          <div class="session-card-head">
            <div class="session-symbol-group">
              <span class="session-card-symbol">${a.symbol}</span>
              <span class="session-dir-badge ${dirClass}">
                <i class="fa-solid ${dirIcon}"></i>
                <span>${dirLabel}</span>
              </span>
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
              <span class="session-metric-value" style="color: ${dirColor}; font-weight: 700;">${targetPriceStr} (${targetStr})</span>
            </div>
            <div class="session-metric-cell">
              <span class="session-metric-label">Live Price</span>
              <span class="session-metric-value">${currentPriceStr}</span>
            </div>
            <div class="session-metric-cell">
              <span class="session-metric-label">Distance</span>
              <span class="session-metric-value" style="color: #2563eb; font-weight: 700;">${distRupees} (${remStr})</span>
            </div>
            <div class="session-metric-cell">
              <span class="session-metric-label">Window</span>
              <span class="session-metric-value">${a.warning_minutes} min</span>
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
            <span style="font-size: 11px; color: #64748b;">Updated ${updatedTimeStr}</span>
            <div style="display: flex; align-items: center; gap: 6px;">
              <button type="button" class="btn-extend-session" data-alert-id="${a.id}" title="Extend warning window by 1 minute" style="font-size: 11px; font-weight: 700; padding: 4px 8px; border-radius: 6px; border: 1px solid #cbd5e1; background: #ffffff; cursor: pointer; display: inline-flex; align-items: center; gap: 3px;">
                <i class="fa-solid fa-clock"></i> +1m
              </button>
              <button type="button" class="btn-reset-session" data-alert-id="${a.id}" title="Reset reference price to live quote" style="font-size: 11px; font-weight: 700; padding: 4px 8px; border-radius: 6px; border: 1px solid #cbd5e1; background: #ffffff; cursor: pointer; display: inline-flex; align-items: center; gap: 3px;">
                <i class="fa-solid fa-arrow-rotate-left"></i> Reset
              </button>
              <button type="button" class="btn-stop-session" data-alert-id="${a.id}">
                <i class="fa-solid fa-stop"></i> Stop
              </button>
            </div>
          </div>
        </div>
      `;
    }).join('');

    elements.activeCardsGrid.innerHTML = cardsHtml;

    // Attach Action Handlers
    elements.activeCardsGrid.querySelectorAll('.btn-extend-session').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const alertId = btn.getAttribute('data-alert-id');
        if (!alertId) return;
        btn.disabled = true;
        try {
          await fetch(`/api/alerts/${alertId}/extend?minutes=1`, { method: 'POST' });
          await fetchActiveSessions();
        } catch (e) { }
      });
    });

    elements.activeCardsGrid.querySelectorAll('.btn-reset-session').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const alertId = btn.getAttribute('data-alert-id');
        if (!alertId) return;
        btn.disabled = true;
        try {
          await fetch(`/api/alerts/${alertId}/reset_ref`, { method: 'POST' });
          await fetchActiveSessions();
        } catch (e) { }
      });
    });

    elements.activeCardsGrid.querySelectorAll('.btn-stop-session').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const alertId = btn.getAttribute('data-alert-id');
        if (!alertId) return;
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Stopping...`;
        try {
          await fetch(`/api/alerts/${alertId}/stop`, { method: 'POST' });
          await fetchActiveSessions();
          fetchNotifications();
        } catch (err) {
          console.warn('[StockPulse] Error stopping alert session:', err);
        }
      });
    });
  }

  // --- REAL-TIME SYSTEM NOTIFICATIONS LOGIC ---
  async function fetchNotifications() {
    try {
      const resp = await fetch('/api/notifications?limit=25');
      if (!resp.ok) return;
      const data = await resp.json();
      state.notifications = data.notifications || [];
      renderNotificationsList(data);
    } catch (e) {
      console.warn('[StockPulse] Could not fetch notifications:', e);
    }
  }

  function renderNotificationsList(data) {
    const listEl = elements.notificationsList;
    if (!listEl) return;

    // Update unread badge in top navbar
    const unread = data.unread_count || 0;
    if (elements.notifUnreadBadge) {
      if (unread > 0) {
        elements.notifUnreadBadge.textContent = unread > 99 ? '99+' : unread;
        elements.notifUnreadBadge.style.display = 'inline-block';
      } else {
        elements.notifUnreadBadge.style.display = 'none';
      }
    }

    const items = data.notifications || [];
    if (items.length === 0) {
      listEl.innerHTML = `
        <div class="notif-empty-state" id="notif-empty-placeholder">
          <i class="fa-regular fa-bell-slash notif-empty-icon"></i>
          <span>No notifications recorded yet. Monitoring events and early warnings will appear here.</span>
        </div>`;
      return;
    }

    listEl.innerHTML = '';
    items.forEach((notif) => {
      const itemEl = document.createElement('div');
      const isUnread = !notif.is_read;
      itemEl.className = `notif-item ${isUnread ? 'unread' : ''}`;

      const priority = (notif.priority || 'INFO').toLowerCase();
      const dotClass = `notif-dot-${priority}`;
      const tagPriorityClass = `notif-tag-priority-${priority}`;

      // Format time
      const fmt2 = (window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST : (s) => s ? new Date(s + 'Z').toLocaleTimeString('en-IN') : '--:--:--';
      const timeStr = fmt2(notif.created_at, 'short');

      // Parse short summary or use second line of message
      let summaryText = '';
      if (notif.extra_data) {
        try {
          const parsed = JSON.parse(notif.extra_data);
          summaryText = parsed.short_summary || '';
        } catch (_) {}
      }
      if (!summaryText && notif.message) {
        const lines = notif.message.split('\n').filter(l => l.trim().length > 0);
        summaryText = lines[1] || lines[0] || '';
      }

      itemEl.innerHTML = `
        <div class="notif-icon-col">
          <span class="notif-status-dot ${dotClass}"></span>
        </div>
        <div class="notif-content-col">
          <div class="notif-header-line">
            <span class="notif-item-title">${escapeHtml(notif.title)}</span>
            <span class="notif-item-time">${timeStr}</span>
          </div>
          <div class="notif-item-summary">${escapeHtml(summaryText)}</div>
          <div class="notif-meta-tags">
            <span class="notif-tag ${tagPriorityClass}">${notif.priority}</span>
            <span class="notif-tag notif-tag-channel"><i class="fa-solid fa-paper-plane"></i> ${notif.channel}</span>
            <span class="notif-tag notif-tag-channel">${notif.event_type}</span>
          </div>
        </div>
      `;

      itemEl.addEventListener('click', () => {
        openNotificationDetailModal(notif);
        if (isUnread) {
          markNotificationRead(notif.id);
        }
      });

      listEl.appendChild(itemEl);
    });
  }

  function openNotificationDetailModal(notif) {
    if (!elements.notifDetailModal) return;
    elements.notifDetailTitle.textContent = notif.title || 'Notification Notice';
    elements.notifDetailSubtitle.textContent = `Event: ${notif.event_type} • Stock: ${notif.symbol}`;
    elements.notifDetailPriorityTag.textContent = notif.priority || 'INFO';
    elements.notifDetailPriorityTag.className = `notif-tag notif-tag-priority-${(notif.priority || 'INFO').toLowerCase()}`;
    elements.notifDetailChannelTag.textContent = (notif.channel || 'dashboard').toUpperCase();
    elements.notifDetailTimeTag.textContent = notif.created_at ? ((window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST(notif.created_at, 'datetime') : new Date(notif.created_at + 'Z').toLocaleString('en-IN')) : '';
    elements.notifDetailMessageBody.textContent = notif.message || '';
    elements.notifDetailModal.style.display = 'flex';
  }

  async function markNotificationRead(notifId) {
    try {
      await fetch(`/api/notifications/${notifId}/read`, { method: 'POST' });
      fetchNotifications();
    } catch (e) {
      console.warn('[StockPulse] Could not mark notification as read:', e);
    }
  }

  async function markAllNotificationsRead() {
    try {
      await fetch('/api/notifications/read-all', { method: 'POST' });
      fetchNotifications();
    } catch (e) {
      console.warn('[StockPulse] Could not mark all notifications as read:', e);
    }
  }

  async function clearAllNotifications() {
    try {
      await fetch('/api/notifications/clear', { method: 'POST' });
      fetchNotifications();
    } catch (e) {
      console.warn('[StockPulse] Could not clear all notifications:', e);
    }
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // --- USER AUTHENTICATION & EMAIL ALERT SYSTEM ---
  function openLoginModal(hintMessage = '') {
    if (!elements.loginModalBackdrop) return;
    elements.loginModalBackdrop.style.display = 'flex';

    if (elements.loginFeedbackMsg) {
      if (hintMessage) {
        elements.loginFeedbackMsg.textContent = hintMessage;
        elements.loginFeedbackMsg.className = 'login-feedback-msg';
        elements.loginFeedbackMsg.style.display = 'block';
      } else {
        elements.loginFeedbackMsg.style.display = 'none';
      }
    }

    if (state.user) {
      if (elements.loginActiveCard) elements.loginActiveCard.style.display = 'flex';
      if (elements.activeUserName) elements.activeUserName.textContent = state.userName || 'Trader';
      if (elements.activeUserEmail) elements.activeUserEmail.textContent = state.userEmail || '';
      if (elements.userAvatarCircle) elements.userAvatarCircle.textContent = (state.userName || state.userEmail || 'U').charAt(0).toUpperCase();
      if (elements.btnLogoutUser) elements.btnLogoutUser.style.display = 'inline-flex';
      if (elements.loginNameInput) elements.loginNameInput.value = state.userName;
      if (elements.loginEmailInput) elements.loginEmailInput.value = state.userEmail;
    } else {
      if (elements.loginActiveCard) elements.loginActiveCard.style.display = 'none';
      if (elements.btnLogoutUser) elements.btnLogoutUser.style.display = 'none';
    }

    if (elements.loginEmailInput) {
      setTimeout(() => elements.loginEmailInput.focus(), 100);
    }
  }

  function closeLoginModal() {
    if (!elements.loginModalBackdrop) return;
    elements.loginModalBackdrop.style.display = 'none';
  }

  async function handleLoginSubmit(e) {
    if (e) e.preventDefault();
    if (!state.user) {
      window.location.href = '/login';
      return;
    }
    // If user is already logged in, update display handle
    const name = elements.loginNameInput ? elements.loginNameInput.value.trim() : '';
    if (name) {
      state.userName = name;
      if (state.user) state.user.name = name;
      localStorage.setItem('stockpulse_user', JSON.stringify(state.user));
      renderUserUI();
      showLoginFeedback('Trader handle updated.', true);
      setTimeout(() => closeLoginModal(), 800);
    }
  }

  async function handleLogout() {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (_) {}
    localStorage.removeItem('stockpulse_user');
    localStorage.removeItem('stockpulse_jwt_token');
    localStorage.removeItem('stockpulse_user_profile');
    document.cookie = 'stockpulse_jwt_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax';
    state.user = null;
    state.userEmail = '';
    state.userName = '';
    if (elements.loginEmailInput) elements.loginEmailInput.value = '';
    if (elements.loginNameInput) elements.loginNameInput.value = '';
    renderUserUI();
    showLoginFeedback('Signed out successfully.', true);
    setTimeout(() => {
      closeLoginModal();
      window.location.href = '/login?logout=true';
    }, 600);
  }

  function applyUserSession(user) {
    state.user = user;
    state.userEmail = user.email || '';
    state.userName = user.name || '';
    localStorage.setItem('stockpulse_user', JSON.stringify(user));
    renderUserUI();
  }

  function renderUserUI() {
    if (state.user && state.userEmail) {
      if (elements.navUserLabel) elements.navUserLabel.textContent = state.userName || state.userEmail;
      if (elements.btnOpenLogin) {
        elements.btnOpenLogin.classList.add('logged-in');
        elements.btnOpenLogin.title = `Signed in as ${state.userEmail} (Click to manage)`;
      }
      if (elements.emailDestAddr) {
        elements.emailDestAddr.textContent = state.userEmail;
      }
      if (elements.loginActiveCard) elements.loginActiveCard.style.display = 'flex';
      if (elements.activeUserName) elements.activeUserName.textContent = state.userName || 'Trader';
      if (elements.activeUserEmail) elements.activeUserEmail.textContent = state.userEmail;
      if (elements.userAvatarCircle) elements.userAvatarCircle.textContent = (state.userName || state.userEmail).charAt(0).toUpperCase();
      if (elements.btnLogoutUser) elements.btnLogoutUser.style.display = 'inline-flex';
    } else {
      if (elements.navUserLabel) elements.navUserLabel.textContent = 'Sign In';
      if (elements.btnOpenLogin) {
        elements.btnOpenLogin.classList.remove('logged-in');
        elements.btnOpenLogin.title = 'Sign In with Email';
      }
      if (elements.emailDestAddr) {
        elements.emailDestAddr.textContent = 'No email set';
      }
      if (elements.loginActiveCard) elements.loginActiveCard.style.display = 'none';
      if (elements.btnLogoutUser) elements.btnLogoutUser.style.display = 'none';
    }
  }

  function showLoginFeedback(msg, isSuccess) {
    if (!elements.loginFeedbackMsg) return;
    elements.loginFeedbackMsg.innerHTML = msg;
    elements.loginFeedbackMsg.className = `login-feedback-msg ${isSuccess ? 'success' : 'error'}`;
    elements.loginFeedbackMsg.style.display = 'block';
  }

  async function initUserSession() {
    // 1. Check local storage
    const cached = localStorage.getItem('stockpulse_user');
    if (cached) {
      try {
        const u = JSON.parse(cached);
        if (u && u.email) {
          applyUserSession(u);
        }
      } catch (_) {}
    }

    // 2. Fetch server-side active user profile
    try {
      const resp = await fetch('/api/auth/me' + (state.userEmail ? `?email=${encodeURIComponent(state.userEmail)}` : ''));
      if (resp.ok) {
        const data = await resp.json();
        if (data.logged_in && data.user) {
          applyUserSession(data.user);
        }
      }
    } catch (e) {
      console.warn('[StockPulse] Could not check auth session:', e);
    }
  }

  // Client-side cache for instantaneous 0ms display on stock switch & range toggle
  const clientDashboardCache = new Map();

  // --- DATA FETCHING & LIVE STOCK VIEW UPDATES ---
  async function fetchStockData(symbol, timeRange, showLoadingOverlay = false) {
    if (state.isFetching) return;

    const cacheKey = `${symbol}_${timeRange}`;
    const cachedEntry = clientDashboardCache.get(cacheKey);

    // Instantaneous 0ms render if cached in client memory (Stale-While-Revalidate)
    if (cachedEntry) {
      renderStockDashboard(cachedEntry);
      showLoadingOverlay = false;
    }

    state.isFetching = true;

    if (showLoadingOverlay) {
      setConnectionStatus('updating', '● Updating');
      elements.chartLoading.classList.add('show');
      elements.unavailableState.style.display = 'none';
    }

    try {
      const response = await fetch(`/api/stocks/${encodeURIComponent(symbol)}?range=${encodeURIComponent(timeRange)}`);
      if (!response.ok) {
        throw new Error(`HTTP error ${response.status}`);
      }
      const data = await response.json();

      if (data.status === 'UNAVAILABLE' || !data.price) {
        if (!cachedEntry) renderUnavailableState();
        return;
      }

      // Valid data received — update client cache
      clientDashboardCache.set(cacheKey, data);
      renderStockDashboard(data);
    } catch (err) {
      console.error('[StockPulse] Data fetch error:', err);
      if (!cachedEntry) renderUnavailableState();
    } finally {
      state.isFetching = false;
      if (showLoadingOverlay) {
        elements.chartLoading.classList.remove('show');
      }
    }
  }

  function renderStockDashboard(data) {
    elements.unavailableState.style.display = 'none';
    elements.canvasContainer.style.display = 'block';

    const curr = data.currency_symbol || '₹';
    state.currencySymbol = curr;
    state.lastPrice = typeof data.price === 'number' ? data.price : 0;

    // 1. Stock Identity & Header
    elements.stockSymbol.textContent = data.symbol || state.symbol;
    elements.stockExchange.textContent = data.exchange || 'NSE';
    elements.companyName.textContent = data.name || `${data.symbol} Ltd`;

    // 2. Current Price
    elements.currentPrice.textContent = `${curr}${data.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

    // 3. Price Change & Percentage
    const change = data.price_change || 0;
    const changePct = data.change_pct || 0;
    const isUp = change > 0;
    const isDown = change < 0;

    elements.priceChangeBadge.className = 'change-badge ' + (isUp ? 'positive' : (isDown ? 'negative' : 'neutral'));
    elements.priceChangeIcon.className = 'fa-solid ' + (isUp ? 'fa-arrow-trend-up' : (isDown ? 'fa-arrow-trend-down' : 'fa-minus'));

    const sign = isUp ? '+' : (isDown ? '-' : '');
    const absVal = Math.abs(change).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const absPct = Math.abs(changePct).toFixed(2);

    elements.priceChangeAbs.textContent = `${sign}${curr}${absVal}`;
    elements.priceChangePct.textContent = `(${sign}${absPct}%)`;

    if (data.previous_close) {
      elements.prevCloseRef.textContent = `vs prev close ${curr}${data.previous_close.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      elements.prevCloseRef.style.display = 'inline';
    } else {
      elements.prevCloseRef.style.display = 'none';
    }

    // 4. Status Indicator & Timestamp
    const isMarketClosed = data.market_status === 'CLOSED' || data.data_status === 'CLOSED';
    if (isMarketClosed) {
      setConnectionStatus('closed', '● MARKET CLOSED');
    } else if (data.data_status === 'DELAYED') {
      setConnectionStatus('delayed', '● DELAYED (15m)');
    } else {
      setConnectionStatus('live', '● LIVE');
    }

    const syncTime = data.last_updated || new Date().toLocaleTimeString('en-IN');
    if (isMarketClosed && data.market_last_traded) {
      elements.lastUpdatedTime.textContent = `${syncTime} (Close: ${data.market_last_traded})`;
    } else {
      elements.lastUpdatedTime.textContent = syncTime;
    }

    // Market status in floating nav
    if (data.market_status) {
      if (window.StockPulse && window.StockPulse.applyMarketStatus) {
        window.StockPulse.applyMarketStatus(data.market_status);
      } else if (elements.marketStatusText && elements.marketStatusDot) {
        const isOp = data.market_status === 'OPEN';
        const isCl = data.market_status === 'CLOSED';
        elements.marketStatusText.textContent = `Market: ${data.market_status}`;
        elements.marketStatusDot.className = `pulse-dot ${isOp ? 'green' : (isCl ? 'closed' : 'neutral')}`;
      }
    }

    // 5. Update Statistics Section
    elements.statOpen.textContent = data.open ? `${curr}${data.open.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.statPrevClose.textContent = data.previous_close ? `${curr}${data.previous_close.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.statDayHigh.textContent = data.day_high ? `${curr}${data.day_high.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.statDayLow.textContent = data.day_low ? `${curr}${data.day_low.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.statVolume.textContent = data.volume || '--';

    elements.stat52wHigh.textContent = data.fifty_two_week_high ? `${curr}${data.fifty_two_week_high.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.stat52wLow.textContent = data.fifty_two_week_low ? `${curr}${data.fifty_two_week_low.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '--';
    elements.statAvgVol.textContent = data.avg_volume || '--';
    elements.statMarketCap.textContent = data.market_cap || '--';
    elements.statExchange.textContent = data.exchange || 'NSE';
    elements.statTradingStatus.textContent = data.trading_status || 'Normal Trading';

    // 6. Update Chart
    updateChart(data.candles || [], isUp, isDown);

    // 7. Update Target Price & Prediction Preview with fresh live quote
    updateDashboardTargetStyling();
  }

  function updateChart(candles, isUp, isDown) {
    if (!state.chart) return;

    if (!candles || candles.length === 0) {
      state.chart.data.labels = [];
      state.chart.data.datasets[0].data = [];
      state.chart.update();
      return;
    }

    const labels = candles.map((c) => c.time);
    const prices = candles.map((c) => c.price);

    const lineColor = isDown ? '#dc2626' : '#16a34a';
    const areaColor = isDown ? 'rgba(220, 38, 38, 0.06)' : 'rgba(22, 163, 74, 0.06)';

    state.chart.data.labels = labels;
    state.chart.data.datasets[0].data = prices;
    state.chart.data.datasets[0].borderColor = lineColor;
    state.chart.data.datasets[0].backgroundColor = areaColor;
    state.chart.data.datasets[0].pointHoverBackgroundColor = lineColor;

    // Auto-calculate bounds
    const minVal = Math.min(...prices);
    const maxVal = Math.max(...prices);
    const padding = (maxVal - minVal) * 0.08 || 2;

    state.chart.options.scales.y.suggestedMin = minVal - padding;
    state.chart.options.scales.y.suggestedMax = maxVal + padding;

    state.chart.update('none'); // Update without flickering animations
  }

  function renderUnavailableState() {
    setConnectionStatus('unavailable', '● Data unavailable');
    elements.unavailableState.style.display = 'flex';
    elements.canvasContainer.style.display = 'none';
    elements.chartLoading.classList.remove('show');

    // Mute statistics values
    elements.statOpen.textContent = '--';
    elements.statPrevClose.textContent = '--';
    elements.statDayHigh.textContent = '--';
    elements.statDayLow.textContent = '--';
    elements.statVolume.textContent = '--';
    elements.stat52wHigh.textContent = '--';
    elements.stat52wLow.textContent = '--';
    elements.statAvgVol.textContent = '--';
    elements.statMarketCap.textContent = '--';
    elements.statExchange.textContent = '--';
    elements.statTradingStatus.textContent = 'Data Unavailable';
  }

  function setConnectionStatus(type, label) {
    // type: 'live' | 'updating' | 'closed' | 'delayed' | 'unavailable'
    elements.liveStatusPill.className = `live-pill ${type}`;
    elements.liveStatusDot.className = `pulse-dot ${type === 'live' ? 'green' : (type === 'unavailable' ? 'red' : 'amber')}`;
    elements.liveStatusLabel.textContent = label;

    elements.globalStatusDot.className = `status-indicator-dot ${type}`;
    if (type === 'live') {
      elements.globalStatusText.textContent = 'LIVE';
    } else if (type === 'updating') {
      elements.globalStatusText.textContent = 'UPDATING';
    } else if (type === 'closed') {
      elements.globalStatusText.textContent = 'CLOSED';
    } else if (type === 'delayed') {
      elements.globalStatusText.textContent = 'DELAYED';
    } else {
      elements.globalStatusText.textContent = 'OFFLINE';
    }
  }

  // --- AUTOMATIC LIVE POLLING (EVERY 5 SECONDS) ---
  function startLivePolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);

    state.pollTimer = setInterval(() => {
      if (!state.isFetching) {
        fetchStockData(state.symbol, state.timeRange, false);
      }
    }, state.pollingIntervalMs);
  }

})();
