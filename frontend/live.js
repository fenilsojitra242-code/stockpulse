/**
 * StockPulse — Live Market & Interactive Chart Page
 */

(function () {
  'use strict';

  let currentSymbol = (window.StockPulse && window.StockPulse.getSelectedStock && window.StockPulse.getSelectedStock()) || 'TCS';
  let currentTimeRange = '1D';
  let currentChartMode = (window.StockPulse && window.StockPulse.getChartMode && window.StockPulse.getChartMode()) || 'line'; // 'line' | 'candles'
  let cachedCandles = [];
  let hoveredCandleIdx = -1;
  let chartInstance = null;
  let pollingTimer = null;
  let isFetching = false;
  let currentPriceVal = 0;
  let supertrendOverlayEnabled = (window.StockPulse && window.StockPulse.getSupertrendOverlay) ? window.StockPulse.getSupertrendOverlay() : true;
  let cachedSuperTrendData = null;
  let cachedSuperTrendSeries = [];
  let supertrendDirections = [];

  // Candlestick Zoom & Pan State (like TradingView / share websites)
  let zoomVisibleStart = null;
  let zoomVisibleEnd = null;
  let isPanning = false;
  let panStartX = 0;
  let panStartCandleStart = 0;
  let panStartCandleEnd = 0;
  let lastTouchDistance = null;
  let lastPinchMidX = 0;

  function getEl(id) {
    return document.getElementById(id);
  }

  // --- INIT CHART.JS ---
  function initChart() {
    const canvas = getEl('live-price-chart');
    if (!canvas) return;

    if (chartInstance) {
      try { chartInstance.destroy(); } catch (e) { }
      chartInstance = null;
    }

    const ctx = canvas.getContext('2d');

    // Custom horizontal current price line plugin
    const currentPricePlugin = {
      id: 'currentPriceLine',
      afterDraw: (chart) => {
        const { ctx, chartArea, scales } = chart;
        if (!chartArea || !scales.y || !currentPriceVal || currentPriceVal <= 0) return;

        const yPos = scales.y.getPixelForValue(currentPriceVal);
        if (yPos < chartArea.top || yPos > chartArea.bottom) return;

        ctx.save();
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = '#64748b';
        ctx.lineWidth = 1;
        ctx.moveTo(chartArea.left, yPos);
        ctx.lineTo(chartArea.right, yPos);
        ctx.stroke();

        // Right badge with current price
        const labelText = `₹${currentPriceVal.toFixed(2)}`;
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
    };

    chartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: `${currentSymbol} Price`,
          data: [],
          borderColor: '#16a34a',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 6,
          pointHoverBackgroundColor: '#16a34a',
          pointHoverBorderColor: '#ffffff',
          pointHoverBorderWidth: 2,
          fill: true,
          backgroundColor: 'rgba(22, 163, 74, 0.08)',
          tension: 0.15
        }, {
          label: 'SuperTrend',
          data: [],
          borderColor: '#10b981',
          borderWidth: 2.2,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: (ctx) => {
            const dir = supertrendDirections[ctx.dataIndex] || (cachedSuperTrendData ? cachedSuperTrendData.direction : 'BUY');
            return dir === 'BUY' ? '#10b981' : '#ef4444';
          },
          fill: {
            target: 0,
            above: 'rgba(239, 68, 68, 0.12)', // Light red shadow when SuperTrend > Price (SELL)
            below: 'rgba(16, 185, 129, 0.12)'  // Light green shadow when SuperTrend < Price (BUY)
          },
          tension: 0,
          segment: {
            borderColor: (ctx) => {
              const idx0 = ctx.p0DataIndex;
              const idx1 = ctx.p1DataIndex;
              const d0 = supertrendDirections[idx0];
              const d1 = supertrendDirections[idx1];
              // Disconnect line during reversal so it does not draw an awkward diagonal cross
              if (d0 && d1 && d0 !== d1) {
                return 'transparent';
              }
              const dir = d1 || d0 || (cachedSuperTrendData ? cachedSuperTrendData.direction : 'BUY');
              return dir === 'BUY' ? '#10b981' : '#ef4444';
            }
          },
          hidden: false
        }]
      },
      plugins: [currentPricePlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: 'index' },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(15, 23, 42, 0.94)',
            titleColor: '#94a3b8',
            titleFont: { size: 12, family: "'Plus Jakarta Sans', sans-serif" },
            bodyColor: '#ffffff',
            bodyFont: { size: 14, weight: 'bold', family: "'JetBrains Mono', monospace" },
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
            callbacks: {
              label: (ctx) => {
                if (ctx.datasetIndex === 1) {
                  const dir = supertrendDirections[ctx.dataIndex] || (cachedSuperTrendData ? cachedSuperTrendData.direction : 'BUY');
                  const dirTag = dir === 'BUY' ? 'BUY (Bullish)' : 'SELL (Bearish)';
                  return ` SuperTrend: ₹${Number(ctx.parsed.y).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} [${dirTag}]`;
                }
                return ` Price: ₹${Number(ctx.parsed.y).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: '#64748b',
              font: { size: 11, family: "'Plus Jakarta Sans', sans-serif" },
              maxTicksLimit: 8
            }
          },
          y: {
            position: 'right',
            grid: { color: 'rgba(226, 232, 240, 0.7)' },
            ticks: {
              color: '#64748b',
              font: { size: 11, family: "'JetBrains Mono', monospace" },
              callback: (val) => `₹${Number(val).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
            }
          }
        }
      }
    });
  }

  function setLiveBufferingState() {
    const symEl = getEl('stock-symbol');
    const exchEl = getEl('stock-exchange');
    const nameEl = getEl('company-name');
    const curPrice = getEl('current-price');
    const priceChangeAbs = getEl('price-change-abs');
    const priceChangePct = getEl('price-change-pct');
    const prevCloseRef = getEl('prev-close-ref');
    const liveLabel = getEl('live-status-label');
    const updatedEl = getEl('last-updated-time');
    const changeIcon = getEl('price-change-icon');

    if (symEl) symEl.innerHTML = '<span class="buffering-loader" style="width: 80px; height: 32px;"></span>';
    if (exchEl) exchEl.innerHTML = '<span class="buffering-loader" style="width: 44px; height: 18px;"></span>';
    if (nameEl) nameEl.innerHTML = '<span class="buffering-loader" style="width: 220px; height: 18px;"></span>';
    if (curPrice) curPrice.innerHTML = '<span class="buffering-loader" style="width: 140px; height: 38px;"></span>';
    if (priceChangeAbs) priceChangeAbs.innerHTML = '<span class="buffering-loader" style="width: 55px; height: 18px;"></span>';
    if (priceChangePct) priceChangePct.innerHTML = '<span class="buffering-loader" style="width: 60px; height: 18px;"></span>';
    if (prevCloseRef) prevCloseRef.innerHTML = '<span class="buffering-loader" style="width: 130px; height: 16px;"></span>';
    if (liveLabel) liveLabel.innerHTML = '<span class="buffering-loader" style="width: 50px; height: 14px;"></span>';
    if (updatedEl) updatedEl.innerHTML = '<span class="buffering-loader" style="width: 70px; height: 14px;"></span>';
    if (changeIcon) changeIcon.style.display = 'none';
  }

  // --- FETCH STOCK DATA ---
  async function fetchStockData(showLoading = false) {
    if (isFetching) return;
    isFetching = true;

    const overlay = getEl('chart-loading-overlay');
    if (showLoading) {
      if (overlay) overlay.style.display = 'flex';
      setLiveBufferingState();
    }

    try {
      // Parallel fetch for stock candles and SuperTrend indicator series
      const [resStock, resST] = await Promise.all([
        fetch(`/api/stocks/${currentSymbol}?range=${currentTimeRange}`),
        fetch(`/api/stocks/${encodeURIComponent(currentSymbol)}/supertrend?range=${currentTimeRange}`).catch(() => null)
      ]);

      if (!resStock.ok) throw new Error(`HTTP ${resStock.status}`);
      const data = await resStock.json();

      if (resST && resST.ok) {
        try {
          const stData = await resST.json();
          cachedSuperTrendData = stData;
          cachedSuperTrendSeries = stData.series || [];
          renderLiveSuperTrendHUD(stData);
        } catch (stErr) {
          console.warn('[Live] Error parsing SuperTrend response:', stErr);
        }
      }

      renderDashboard(data);
    } catch (err) {
      console.warn('[LiveMarket] Fetch error:', err);
      const unavail = getEl('unavailable-state');
      const cContainer = getEl('canvas-container');
      if (unavail && cContainer) {
        unavail.style.display = 'block';
        cContainer.style.display = 'none';
      }
    } finally {
      isFetching = false;
      if (overlay) overlay.style.display = 'none';
    }
  }

  // --- SUPERTREND ENGINE & HUD ---
  async function fetchLiveSuperTrend(symbol) {
    if (!symbol) return;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/supertrend?range=${currentTimeRange}`);
      if (!res.ok) return;
      const data = await res.json();
      cachedSuperTrendData = data;
      cachedSuperTrendSeries = data.series || [];
      renderLiveSuperTrendHUD(data);
      if (currentChartMode === 'candles') {
        renderCandleChart();
      } else if (chartInstance) {
        updateChartSuperTrendDataset();
        chartInstance.update('none');
      }
    } catch (e) {
      console.warn('[Live] SuperTrend fetch error:', e);
    }
  }

  function updateChartSuperTrendDataset(labels) {
    if (!chartInstance || !chartInstance.data || !chartInstance.data.datasets || chartInstance.data.datasets.length < 2) return;
    const lbls = labels || chartInstance.data.labels || [];
    if (!supertrendOverlayEnabled || !cachedSuperTrendData || !cachedSuperTrendData.supertrend_price || lbls.length === 0) {
      chartInstance.data.datasets[1].data = [];
      supertrendDirections = [];
      chartInstance.data.datasets[1].hidden = true;
      return;
    }

    const fallbackVal = Number(cachedSuperTrendData.supertrend_price);
    const fallbackDir = cachedSuperTrendData.direction || 'BUY';

    let stData = [];
    let stDirs = [];

    if (cachedSuperTrendSeries && cachedSuperTrendSeries.length > 0) {
      const seriesMap = new Map();
      cachedSuperTrendSeries.forEach(s => {
        if (s.time) {
          seriesMap.set(s.time, { price: Number(s.supertrend), dir: s.direction || 'BUY' });
        }
      });

      let lastSeenPrice = fallbackVal;
      let lastSeenDir = fallbackDir;

      lbls.forEach(t => {
        if (seriesMap.has(t)) {
          const item = seriesMap.get(t);
          lastSeenPrice = item.price;
          lastSeenDir = item.dir;
        }
        stData.push(lastSeenPrice);
        stDirs.push(lastSeenDir);
      });
    } else {
      stData = lbls.map(() => fallbackVal);
      stDirs = lbls.map(() => fallbackDir);
    }

    supertrendDirections = stDirs;
    chartInstance.data.datasets[1].data = stData;
    chartInstance.data.datasets[1].hidden = false;
  }

  function renderLiveSuperTrendHUD(data) {
    if (!data) return;
    const pill = getEl('live-st-pill');
    const priceEl = getEl('live-st-price');
    const gapEl = getEl('live-st-gap');
    const hitPill = getEl('live-st-hit-pill');
    const iconBox = getEl('live-st-icon-box');

    const isBuy = data.direction === 'BUY';
    const stPrice = Number(data.supertrend_price || 0).toFixed(2);
    const gap = Number(data.distance_points || 0).toFixed(2);

    if (pill) {
      pill.className = `supertrend-badge-pill ${isBuy ? 'buy' : 'sell'}`;
      pill.innerHTML = isBuy
        ? '<i class="fa-solid fa-arrow-trend-up"></i> BUY (BULLISH)'
        : '<i class="fa-solid fa-arrow-trend-down"></i> SELL (BEARISH)';
    }

    if (priceEl) priceEl.textContent = `₹${stPrice}`;
    if (gapEl) gapEl.textContent = `${gap} pts`;

    if (hitPill) {
      if (data.is_approaching) {
        hitPill.style.display = 'inline-flex';
        hitPill.innerHTML = `<i class="fa-solid fa-bolt"></i> WITHIN ${gap} PTS OF HIT!`;
      } else {
        hitPill.style.display = 'none';
      }
    }

    if (iconBox) {
      iconBox.style.background = isBuy
        ? 'linear-gradient(135deg, #10b981, #059669)'
        : 'linear-gradient(135deg, #ef4444, #dc2626)';
    }
  }

  function setupSuperTrendControls() {
    const btnToggle = getEl('btn-toggle-supertrend-overlay');
    if (btnToggle) {
      btnToggle.classList.toggle('active', supertrendOverlayEnabled);
      btnToggle.addEventListener('click', () => {
        supertrendOverlayEnabled = !supertrendOverlayEnabled;
        if (window.StockPulse && window.StockPulse.setSupertrendOverlay) {
          StockPulse.setSupertrendOverlay(supertrendOverlayEnabled);
        } else {
          localStorage.setItem('stockpulse_supertrend_overlay', supertrendOverlayEnabled ? 'true' : 'false');
        }
        btnToggle.classList.toggle('active', supertrendOverlayEnabled);

        const legStBuy = getEl('legend-chip-st-buy');
        const legStSell = getEl('legend-chip-st-sell');
        if (legStBuy) legStBuy.style.display = supertrendOverlayEnabled ? 'inline-flex' : 'none';
        if (legStSell) legStSell.style.display = supertrendOverlayEnabled ? 'inline-flex' : 'none';

        const hudStItem = getEl('live-hud-st-item');
        if (hudStItem) hudStItem.style.display = supertrendOverlayEnabled ? 'flex' : 'none';

        if (currentChartMode === 'candles') {
          renderCandleChart();
        } else if (chartInstance) {
          updateChartSuperTrendDataset();
          chartInstance.update('none');
        }
      });
    }

    let testCycle = 0;
    const btnTest = getEl('btn-test-live-supertrend');
    if (btnTest) {
      btnTest.addEventListener('click', async () => {
        const signals = ['APPROACHING', 'BUY', 'SELL'];
        const sig = signals[testCycle % signals.length];
        testCycle++;
        btnTest.disabled = true;
        const orig = btnTest.innerHTML;
        btnTest.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Triggering ${sig}...`;
        try {
          const res = await fetch(`/api/stocks/${encodeURIComponent(currentSymbol)}/supertrend/test-notify?signal_type=${sig}`, {
            method: 'POST'
          });
          if (res.ok) {
            btnTest.innerHTML = `<i class="fa-solid fa-check"></i> ${sig} Sent!`;
          } else {
            btnTest.innerHTML = `<i class="fa-solid fa-xmark"></i> Failed`;
          }
        } catch (e) {
          btnTest.innerHTML = `<i class="fa-solid fa-xmark"></i> Error`;
        } finally {
          setTimeout(() => {
            btnTest.disabled = false;
            btnTest.innerHTML = orig;
            fetchLiveSuperTrend(currentSymbol);
          }, 1800);
        }
      });
    }
  }

  // --- RENDER DASHBOARD & CHART ---
  function renderDashboard(data) {
    if (!data) return;

    const unavail = getEl('unavailable-state');
    if (unavail) unavail.style.display = 'none';

    // FIX: Respect selected chart mode! Never force line container to block on background updates
    applyChartMode(currentChartMode, false);

    // Header info
    const symEl = getEl('stock-symbol');
    const exchEl = getEl('stock-exchange');
    const nameEl = getEl('company-name');
    const updatedEl = getEl('last-updated-time');

    if (symEl) symEl.textContent = data.symbol || currentSymbol;
    if (exchEl) exchEl.textContent = data.exchange || 'NSE';
    if (nameEl) nameEl.textContent = data.name || `${currentSymbol} Ltd`;

    // Last updated timestamp & market trade time
    if (updatedEl) {
      const syncTime = data.last_updated || new Date().toLocaleTimeString('en-IN');
      const marketTradeTime = data.market_last_traded;
      const isClosed = data.market_status === 'CLOSED' || data.data_status === 'CLOSED';
      if (isClosed && marketTradeTime) {
        updatedEl.textContent = `${syncTime} (Close: ${marketTradeTime})`;
      } else {
        updatedEl.textContent = syncTime;
      }
    }

    // Market status in navigation
    const mStatusText = getEl('market-status-text');
    const mStatusDot = getEl('market-status-dot');
    const isMarketClosed = data.market_status === 'CLOSED' || data.data_status === 'CLOSED';
    if (mStatusText) mStatusText.textContent = `Market: ${data.market_status || (isMarketClosed ? 'CLOSED' : 'OPEN')}`;
    if (mStatusDot) mStatusDot.className = `pulse-dot ${isMarketClosed ? 'amber' : 'green'}`;

    // Live / Delayed / Closed status badge
    const livePill = getEl('live-status-pill');
    const liveDot = getEl('live-status-dot');
    const liveLabel = getEl('live-status-label');
    const globalDot = getEl('global-status-dot');
    const globalText = getEl('global-status-text');

    if (isMarketClosed) {
      if (livePill) livePill.className = 'live-pill closed';
      if (liveDot) liveDot.className = 'pulse-dot amber';
      if (liveLabel) liveLabel.textContent = 'MARKET CLOSED';
      if (globalDot) globalDot.className = 'status-indicator-dot closed';
      if (globalText) globalText.textContent = 'CLOSED';
    } else if (data.data_status === 'LIVE' || data.status === 'LIVE') {
      if (livePill) livePill.className = 'live-pill';
      if (liveDot) liveDot.className = 'pulse-dot green';
      if (liveLabel) liveLabel.textContent = 'LIVE';
      if (globalDot) globalDot.className = 'status-indicator-dot live';
      if (globalText) globalText.textContent = 'LIVE';
    } else if (data.data_status === 'DELAYED') {
      if (livePill) livePill.className = 'live-pill delayed';
      if (liveDot) liveDot.className = 'pulse-dot amber';
      if (liveLabel) liveLabel.textContent = 'DELAYED (15m)';
      if (globalDot) globalDot.className = 'status-indicator-dot delayed';
      if (globalText) globalText.textContent = 'DELAYED';
    }

    // Price
    const priceVal = data.price || 0;
    currentPriceVal = priceVal;
    const changeVal = data.price_change || 0;
    const changePct = data.change_pct || 0;
    const prevClose = data.previous_close || 0;

    const isUp = changeVal >= 0;
    const sign = isUp ? '+' : '';

    const priceEl = getEl('current-price');
    const prevRefEl = getEl('prev-close-ref');
    const changeBadge = getEl('price-change-badge');
    const changeIcon = getEl('price-change-icon');
    const changeAbs = getEl('price-change-abs');
    const changePctEl = getEl('price-change-pct');

    if (priceEl) {
      priceEl.textContent = `₹${priceVal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    if (prevRefEl) {
      prevRefEl.textContent = `vs prev close ₹${prevClose.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    if (changeBadge) {
      changeBadge.className = `change-badge ${isUp ? 'up' : 'down'}`;
    }
    if (changeIcon) {
      changeIcon.style.display = 'inline-block';
      changeIcon.className = `fa-solid ${isUp ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down'}`;
    }
    if (changeAbs) {
      changeAbs.textContent = `${sign}₹${Math.abs(changeVal).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    if (changePctEl) {
      changePctEl.textContent = `(${sign}${changePct.toFixed(2)}%)`;
    }

    // Update Chart with candles or chart_points
    const prevLen = cachedCandles ? cachedCandles.length : 0;
    const points = (data.candles && data.candles.length > 0) ? data.candles : (data.chart_points || []);
    cachedCandles = points;
    const newLen = points.length;

    // Real-time anchor: follow newest incoming candle if user was viewing latest edge
    if (zoomVisibleEnd != null && prevLen > 0 && zoomVisibleEnd >= prevLen - 1) {
      const diff = newLen - prevLen;
      if (diff > 0) {
        zoomVisibleStart = Math.min(newLen - 1, zoomVisibleStart + diff);
        zoomVisibleEnd = newLen - 1;
      }
    }

    if (currentChartMode === 'candles') {
      renderCandleChart();
      updateZoomHint();
    } else {
      if (!chartInstance) {
        initChart();
      }

      if (chartInstance && points.length > 0) {
        const labels = points.map(p => p.time);
        const prices = points.map(p => p.price);

        const color = isUp ? '#16a34a' : '#dc2626';
        const canvas = getEl('live-price-chart');
        let gradient = 'rgba(22, 163, 74, 0.08)';

        if (canvas) {
          const ctx = canvas.getContext('2d');
          const g = ctx.createLinearGradient(0, 0, 0, 360);
          g.addColorStop(0, isUp ? 'rgba(22, 163, 74, 0.20)' : 'rgba(220, 38, 38, 0.20)');
          g.addColorStop(1, 'rgba(255, 255, 255, 0.00)');
          gradient = g;
        }

        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = prices;
        chartInstance.data.datasets[0].borderColor = color;
        chartInstance.data.datasets[0].backgroundColor = gradient;
        chartInstance.data.datasets[0].pointHoverBackgroundColor = color;

        updateChartSuperTrendDataset(labels);

        // Dynamic Y-axis limits with padding
        let allVals = [...prices];
        if (supertrendOverlayEnabled && cachedSuperTrendData && cachedSuperTrendData.supertrend_price) {
          allVals.push(Number(cachedSuperTrendData.supertrend_price));
        }
        const minVal = Math.min(...allVals);
        const maxVal = Math.max(...allVals);
        const padding = (maxVal - minVal) * 0.08 || 2;
        chartInstance.options.scales.y.suggestedMin = minVal - padding;
        chartInstance.options.scales.y.suggestedMax = maxVal + padding;

        chartInstance.update('none');
      }
    }
  }

  // --- CHANGE STOCK ---
  function changeStock(sym) {
    if (!sym || sym === currentSymbol) return;
    currentSymbol = sym.toUpperCase().replace(/\.(NS|BO)$/, '');
    zoomVisibleStart = null;
    zoomVisibleEnd = null;
    if (window.StockPulse && window.StockPulse.setSelectedStock) {
      StockPulse.setSelectedStock(currentSymbol);
    } else {
      localStorage.setItem('stockpulse_selected_stock', currentSymbol);
    }
    fetchStockData(true);
  }

  // --- POPULAR CHIPS & SEARCH ---
  function setupSearch() {
    const searchInput = getEl('stock-search-input');
    const searchDropdown = getEl('search-dropdown');
    if (!searchInput || !searchDropdown) return;

    let searchTimeout = null;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimeout);
      const query = e.target.value.trim();
      if (!query) {
        searchDropdown.style.display = 'none';
        return;
      }
      searchTimeout = setTimeout(async () => {
        try {
          const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
          if (res.ok) {
            const results = await res.json();
            renderSearchResults(results, searchInput, searchDropdown, query);
          }
        } catch (e) {
          renderSearchResults([], searchInput, searchDropdown, query);
        }
      }, 150);
    });

    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const firstItem = searchDropdown.querySelector('.search-result-item');
        if (firstItem && searchDropdown.style.display !== 'none') {
          firstItem.click();
        } else {
          const val = searchInput.value.trim();
          if (val) {
            searchInput.value = '';
            searchDropdown.style.display = 'none';
            changeStock(val);
          }
        }
      }
    });

    document.addEventListener('click', (e) => {
      if (!searchInput.contains(e.target) && !searchDropdown.contains(e.target)) {
        searchDropdown.style.display = 'none';
      }
    });

    // Popular buttons
    const chipBtns = document.querySelectorAll('.ticker-chip-btn');
    chipBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const sym = btn.getAttribute('data-symbol');
        chipBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        changeStock(sym);
      });
    });
  }

  function renderSearchResults(results, searchInput, searchDropdown, query) {
    if (!searchDropdown) return;
    const qUpper = (query || (searchInput ? searchInput.value : '')).trim().toUpperCase();

    let items = Array.isArray(results) ? [...results] : [];
    if (qUpper && !items.some(it => it.symbol.toUpperCase() === qUpper)) {
      items.unshift({
        symbol: qUpper,
        name: qUpper === 'OLX' ? 'OLX India / CarTrade Tech Ltd' : `${qUpper} Share / Equity`,
        exchange: 'NSE'
      });
    }

    if (items.length === 0) {
      searchDropdown.innerHTML = '<div style="padding: 12px 14px; color: #64748b; font-size: 13px;">No matching stock found</div>';
      searchDropdown.style.display = 'block';
      return;
    }

    searchDropdown.innerHTML = '';
    items.slice(0, 15).forEach(item => {
      const row = document.createElement('div');
      row.className = 'search-result-item';
      row.style.cssText = 'padding: 10px 14px; cursor: pointer; border-bottom: 1px solid #f1f5f9; display: flex; justify-content: space-between; align-items: center; transition: background 0.15s ease;';
      row.onmouseenter = () => { row.style.background = '#f8fafc'; };
      row.onmouseleave = () => { row.style.background = '#ffffff'; };
      row.innerHTML = `
        <div>
          <strong style="color: #0f172a; font-size: 13px;">${item.symbol}</strong>
          <span style="color: #64748b; font-size: 12px; margin-left: 8px;">${item.name}</span>
        </div>
        <span style="font-size: 11px; background: #e2e8f0; color: #475569; padding: 2px 6px; border-radius: 4px; font-weight: 600;">${item.exchange || 'NSE'}</span>
      `;
      row.addEventListener('click', () => {
        searchInput.value = '';
        searchDropdown.style.display = 'none';
        changeStock(item.symbol);
      });
      searchDropdown.appendChild(row);
    });
    searchDropdown.style.display = 'block';
  }

  // --- TIME RANGE HANDLERS ---
  function setupTimeRanges() {
    const rangeButtons = document.querySelectorAll('.range-btn');
    const rangeInfo = getEl('range-info');

    rangeButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const range = btn.getAttribute('data-range');
        if (range === currentTimeRange) return;
        rangeButtons.forEach(b => {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        currentTimeRange = range;
        zoomVisibleStart = null;
        zoomVisibleEnd = null;

        if (rangeInfo) {
          if (range === '1D') rangeInfo.textContent = 'Intraday 1-Minute Interval';
          else if (range === '5D') rangeInfo.textContent = '5-Day 5-Minute Candles';
          else rangeInfo.textContent = `${range} Historical Daily Trend`;
        }

        fetchStockData(true);
      });
    });
  }

  // --- CHART MODE TOGGLE (LINE VS CANDLES) ---
  function applyChartMode(mode, shouldRender = true) {
    currentChartMode = mode;
    if (window.StockPulse && window.StockPulse.setChartMode) {
      StockPulse.setChartMode(mode);
    } else {
      localStorage.setItem('stockpulse_chart_mode', mode);
    }

    const btnLine = getEl('btn-chart-mode-line');
    const btnCandles = getEl('btn-chart-mode-candles');
    const lineWrap = getEl('canvas-container');
    const candleWrap = getEl('live-candle-wrapper');
    const ohlcHud = getEl('live-ohlc-hud');
    const candleFooter = getEl('live-candle-footer');
    const zoomControls = getEl('chart-zoom-controls');

    if (mode === 'candles') {
      if (btnCandles) btnCandles.classList.add('active');
      if (btnLine) btnLine.classList.remove('active');

      if (lineWrap) lineWrap.style.display = 'none';
      if (candleWrap) candleWrap.style.display = 'block';
      if (ohlcHud) ohlcHud.style.display = 'flex';
      if (candleFooter) candleFooter.style.display = 'flex';
      if (zoomControls) zoomControls.style.display = 'inline-flex';

      if (shouldRender) {
        renderCandleChart();
        updateZoomHint();
      }
    } else {
      if (btnLine) btnLine.classList.add('active');
      if (btnCandles) btnCandles.classList.remove('active');

      if (lineWrap) lineWrap.style.display = 'block';
      if (candleWrap) candleWrap.style.display = 'none';
      if (ohlcHud) ohlcHud.style.display = 'none';
      if (candleFooter) candleFooter.style.display = 'none';
      if (zoomControls) zoomControls.style.display = 'none';

      if (shouldRender && chartInstance) {
        chartInstance.resize();
        chartInstance.update('none');
      }
    }
  }

  function setupChartModeToggle() {
    const btnLine = getEl('btn-chart-mode-line');
    const btnCandles = getEl('btn-chart-mode-candles');
    if (!btnLine || !btnCandles) return;

    btnLine.addEventListener('click', () => applyChartMode('line', true));
    btnCandles.addEventListener('click', () => applyChartMode('candles', true));

    applyChartMode(currentChartMode, false);
  }

  // --- CANDLESTICK ZOOM & PAN MATH (LIKE TRADINGVIEW) ---
  function getVisibleCandleRange(totalCount) {
    if (totalCount <= 0) return { start: 0, end: 0, count: 0, isZoomed: false };

    if (zoomVisibleStart == null || zoomVisibleEnd == null) {
      zoomVisibleStart = 0;
      zoomVisibleEnd = totalCount - 1;
    }

    let start = Math.max(0, Math.min(totalCount - 1, zoomVisibleStart));
    let end = Math.max(start, Math.min(totalCount - 1, zoomVisibleEnd));
    const minCandles = Math.min(8, totalCount);

    if (end - start + 1 < minCandles) {
      start = Math.max(0, end - minCandles + 1);
    }

    zoomVisibleStart = start;
    zoomVisibleEnd = end;

    const isZoomed = (start > 0 || end < totalCount - 1);
    return { start, end, count: end - start + 1, isZoomed };
  }

  function zoomAtPoint(ratio, factor) {
    if (!cachedCandles || cachedCandles.length === 0) return;
    const total = cachedCandles.length;
    const range = getVisibleCandleRange(total);
    const curCount = range.count;

    const minCount = Math.min(8, total);
    const maxCount = total;

    let newCount = Math.round(curCount * factor);
    newCount = Math.max(minCount, Math.min(maxCount, newCount));
    if (newCount === curCount) return;

    const clampedRatio = Math.max(0, Math.min(1, ratio));
    const anchorIdx = range.start + (curCount * clampedRatio);

    let newStart = Math.round(anchorIdx - (newCount * clampedRatio));
    let newEnd = newStart + newCount - 1;

    if (newStart < 0) {
      newEnd += (0 - newStart);
      newStart = 0;
    }
    if (newEnd >= total) {
      newStart -= (newEnd - (total - 1));
      newEnd = total - 1;
      if (newStart < 0) newStart = 0;
    }

    zoomVisibleStart = newStart;
    zoomVisibleEnd = newEnd;
    renderCandleChart();
    updateZoomHint();
  }

  function resetZoom() {
    zoomVisibleStart = null;
    zoomVisibleEnd = null;
    renderCandleChart();
    updateZoomHint();
  }

  function updateZoomHint() {
    const hintEl = getEl('live-candle-zoom-hint');
    if (!hintEl || !cachedCandles || cachedCandles.length === 0) return;
    const total = cachedCandles.length;
    const range = getVisibleCandleRange(total);

    if (range.isZoomed) {
      const zoomRatio = (total / range.count).toFixed(1);
      hintEl.innerHTML = `<strong>Zoom: ${zoomRatio}x</strong> (Showing candles ${range.start + 1}–${range.end + 1} of ${total}) • Drag to pan • <a href="javascript:void(0)" id="link-reset-zoom" style="color: #2563eb; font-weight: 700; text-decoration: underline;">Reset View</a>`;
      const linkReset = getEl('link-reset-zoom');
      if (linkReset) linkReset.onclick = resetZoom;
    } else {
      hintEl.textContent = `Scroll wheel to zoom • Drag to pan • Double-click to reset`;
    }
  }

  // --- CANDLESTICK CHART INTERACTION & RENDERING ---
  function setupCandleChartEvents() {
    const wrapper = getEl('live-candle-wrapper');
    if (!wrapper) return;

    wrapper.addEventListener('mousemove', handleCandleMouseMove);
    wrapper.addEventListener('mouseleave', handleCandleMouseLeave);

    // Mouse drag to pan
    wrapper.addEventListener('mousedown', (e) => {
      if (e.button !== 0 || !cachedCandles || cachedCandles.length === 0) return;
      isPanning = true;
      panStartX = e.clientX;
      const range = getVisibleCandleRange(cachedCandles.length);
      panStartCandleStart = range.start;
      panStartCandleEnd = range.end;
      wrapper.style.cursor = 'grabbing';
    });

    window.addEventListener('mouseup', () => {
      if (isPanning) {
        isPanning = false;
        if (wrapper) wrapper.style.cursor = 'crosshair';
      }
    });

    // Double click canvas to reset zoom
    wrapper.addEventListener('dblclick', () => {
      resetZoom();
    });

    // Mouse wheel zoom in / out
    wrapper.addEventListener('wheel', (e) => {
      e.preventDefault();
      if (!cachedCandles || cachedCandles.length === 0) return;
      const rect = wrapper.getBoundingClientRect();
      const padLeft = 14;
      const padRight = 82;
      const chartW = rect.width - padLeft - padRight;
      if (chartW <= 0) return;

      const mouseX = e.clientX - rect.left - padLeft;
      const ratio = Math.max(0, Math.min(1, mouseX / chartW));
      const factor = e.deltaY < 0 ? 0.80 : 1.25;
      zoomAtPoint(ratio, factor);
    }, { passive: false });

    // Touch support (pan & pinch-to-zoom)
    wrapper.addEventListener('touchstart', (e) => {
      if (!cachedCandles || cachedCandles.length === 0) return;
      if (e.touches.length === 1) {
        isPanning = true;
        panStartX = e.touches[0].clientX;
        const range = getVisibleCandleRange(cachedCandles.length);
        panStartCandleStart = range.start;
        panStartCandleEnd = range.end;
      } else if (e.touches.length === 2) {
        isPanning = false;
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        lastTouchDistance = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
        const rect = wrapper.getBoundingClientRect();
        lastPinchMidX = ((t1.clientX + t2.clientX) / 2) - rect.left - 14;
      }
    }, { passive: true });

    wrapper.addEventListener('touchmove', (e) => {
      if (!cachedCandles || cachedCandles.length === 0) return;
      if (e.touches.length === 1 && isPanning) {
        const dx = e.touches[0].clientX - panStartX;
        const rect = wrapper.getBoundingClientRect();
        const chartW = rect.width - 14 - 82;
        const range = getVisibleCandleRange(cachedCandles.length);
        const slotW = chartW / range.count;
        const shift = Math.round(dx / slotW);

        let newStart = panStartCandleStart - shift;
        let newEnd = panStartCandleEnd - shift;
        const total = cachedCandles.length;
        if (newStart < 0) { newEnd += -newStart; newStart = 0; }
        if (newEnd >= total) { newStart -= (newEnd - (total - 1)); newEnd = total - 1; if (newStart < 0) newStart = 0; }
        zoomVisibleStart = newStart;
        zoomVisibleEnd = newEnd;
        renderCandleChart();
        updateZoomHint();
      } else if (e.touches.length === 2 && lastTouchDistance != null) {
        const t1 = e.touches[0];
        const t2 = e.touches[1];
        const dist = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
        const ratio = dist / lastTouchDistance;
        if (Math.abs(1 - ratio) > 0.04) {
          const rect = wrapper.getBoundingClientRect();
          const chartW = rect.width - 14 - 82;
          const midRatio = Math.max(0, Math.min(1, lastPinchMidX / chartW));
          zoomAtPoint(midRatio, ratio > 1 ? 0.88 : 1.14);
          lastTouchDistance = dist;
        }
      }
    }, { passive: true });

    wrapper.addEventListener('touchend', (e) => {
      if (e.touches.length === 0) {
        isPanning = false;
        lastTouchDistance = null;
        handleCandleMouseLeave();
      }
    });

    // Zoom buttons toolbar (+, -, Reset)
    const btnIn = getEl('btn-zoom-in');
    const btnOut = getEl('btn-zoom-out');
    const btnReset = getEl('btn-zoom-reset');
    if (btnIn) btnIn.addEventListener('click', () => zoomAtPoint(0.5, 0.75));
    if (btnOut) btnOut.addEventListener('click', () => zoomAtPoint(0.5, 1.33));
    if (btnReset) btnReset.addEventListener('click', () => resetZoom());

    try {
      const resizeObserver = new ResizeObserver(() => {
        if (currentChartMode === 'candles') renderCandleChart();
      });
      resizeObserver.observe(wrapper);
    } catch (e) {
      window.addEventListener('resize', () => {
        if (currentChartMode === 'candles') renderCandleChart();
      });
    }
  }

  function handleCandleMouseMove(e) {
    if (!cachedCandles || cachedCandles.length === 0) return;
    const wrapper = getEl('live-candle-wrapper');
    if (!wrapper) return;

    if (isPanning) {
      const dx = e.clientX - panStartX;
      const rect = wrapper.getBoundingClientRect();
      const padLeft = 14;
      const padRight = 82;
      const chartW = rect.width - padLeft - padRight;
      const range = getVisibleCandleRange(cachedCandles.length);
      const slotW = chartW / range.count;

      const candleShift = Math.round(dx / slotW);
      if (candleShift !== 0) {
        let newStart = panStartCandleStart - candleShift;
        let newEnd = panStartCandleEnd - candleShift;
        const total = cachedCandles.length;

        if (newStart < 0) {
          newEnd += (0 - newStart);
          newStart = 0;
        }
        if (newEnd >= total) {
          newStart -= (newEnd - (total - 1));
          newEnd = total - 1;
          if (newStart < 0) newStart = 0;
        }

        zoomVisibleStart = newStart;
        zoomVisibleEnd = newEnd;
        renderCandleChart();
        updateZoomHint();
      }
      return;
    }

    const rect = wrapper.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const padLeft = 14;
    const padRight = 82;
    const chartW = rect.width - padLeft - padRight;
    if (chartW <= 0) return;

    const range = getVisibleCandleRange(cachedCandles.length);
    const candleSlotW = chartW / range.count;
    let idx = Math.floor((mouseX - padLeft) / candleSlotW);
    idx = Math.max(0, Math.min(range.count - 1, idx));

    hoveredCandleIdx = idx;
    renderCandleChart(mouseX, mouseY);
  }

  function handleCandleMouseLeave() {
    hoveredCandleIdx = -1;
    renderCandleChart(null, null);
  }

  function renderCandleChart(mouseX, mouseY) {
    const canvas = getEl('live-candle-canvas');
    const wrapper = getEl('live-candle-wrapper');
    if (!canvas || !wrapper) return;

    const rect = wrapper.getBoundingClientRect();
    const width = Math.floor(rect.width);
    const height = Math.floor(rect.height || 380);
    if (width <= 0 || height <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;

    const ctx = canvas.getContext('2d');
    if (ctx.resetTransform) ctx.resetTransform();
    else ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);

    const isDarkMode = document.body.classList.contains('dark-mode');
    const bgSurface = isDarkMode ? '#0f172a' : '#ffffff';
    const gridColor = isDarkMode ? 'rgba(51, 65, 85, 0.4)' : 'rgba(226, 232, 240, 0.8)';
    const textMuted = isDarkMode ? '#94a3b8' : '#64748b';
    const greenBull = '#10b981';
    const redBear = '#ef4444';

    // Clear background
    ctx.fillStyle = bgSurface;
    ctx.fillRect(0, 0, width, height);

    if (!cachedCandles || cachedCandles.length === 0) {
      ctx.fillStyle = textMuted;
      ctx.font = '600 13px "Plus Jakarta Sans", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Loading candlestick market data...', width / 2, height / 2);
      return;
    }

    const range = getVisibleCandleRange(cachedCandles.length);
    const visibleCandles = cachedCandles.slice(range.start, range.end + 1);
    const nCandles = visibleCandles.length;
    if (nCandles === 0) return;

    const livePrice = currentPriceVal || (cachedCandles[cachedCandles.length - 1].price || 0);

    const padTop = 18;
    const padBottom = 26;
    const padLeft = 14;
    const padRight = 82;

    const chartW = width - padLeft - padRight;
    const chartTotalH = height - padTop - padBottom;
    const priceAreaH = chartTotalH * 0.78;
    const volAreaH = chartTotalH * 0.20;
    const volAreaTop = padTop + priceAreaH + (chartTotalH * 0.02);

    // Pre-resolve SuperTrend values for visible candles so scaling accommodates both candles & indicator
    const seriesMap = new Map();
    if (cachedSuperTrendSeries && cachedSuperTrendSeries.length > 0) {
      cachedSuperTrendSeries.forEach(s => {
        if (s.time) {
          seriesMap.set(s.time, { price: Number(s.supertrend), dir: s.direction || 'BUY' });
        }
      });
    }

    const defaultStVal = Number(cachedSuperTrendData ? (cachedSuperTrendData.supertrend_price || 0) : 0);
    const defaultStDir = cachedSuperTrendData ? (cachedSuperTrendData.direction || 'BUY') : 'BUY';

    const visibleStValues = [];
    let runningStVal = defaultStVal;
    let runningStDir = defaultStDir;

    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const absIdx = range.start + i;
      let matchedSt = null;

      // Primary: exact index alignment with series
      if (cachedSuperTrendSeries && cachedSuperTrendSeries[absIdx]) {
        const s = cachedSuperTrendSeries[absIdx];
        if (s.supertrend != null) {
          matchedSt = { price: Number(s.supertrend), dir: s.direction || 'BUY' };
        }
      }

      // Secondary: timestamp map
      if (!matchedSt && c.time && seriesMap.has(c.time)) {
        matchedSt = seriesMap.get(c.time);
      }

      if (matchedSt) {
        runningStVal = matchedSt.price;
        runningStDir = matchedSt.dir;
      }

      visibleStValues.push({
        price: runningStVal,
        dir: runningStDir,
        time: c.time
      });
    }

    // Auto-scale price & volume strictly to currently visible candles AND SuperTrend indicator
    let minP = Infinity;
    let maxP = -Infinity;
    let maxV = 1;

    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const low = c.low != null ? c.low : c.price;
      const high = c.high != null ? c.high : c.price;
      if (low < minP) minP = low;
      if (high > maxP) maxP = high;
      if (c.volume && c.volume > maxV) maxV = c.volume;

      // Include SuperTrend level in vertical scale if overlay is active
      if (supertrendOverlayEnabled && cachedSuperTrendData && visibleStValues[i] && visibleStValues[i].price > 0) {
        const stP = visibleStValues[i].price;
        if (stP < minP) minP = stP;
        if (stP > maxP) maxP = stP;
      }
    }

    if (livePrice > 0 && range.end === cachedCandles.length - 1) {
      minP = Math.min(minP, livePrice);
      maxP = Math.max(maxP, livePrice);
    }

    if (minP === maxP) {
      minP -= 1.0;
      maxP += 1.0;
    }

    const pSpan = maxP - minP;
    const pPad = Math.max(0.2, pSpan * 0.05);
    const scaleMin = minP - pPad;
    const scaleMax = maxP + pPad;
    const scaleSpan = scaleMax - scaleMin;

    function priceToY(p) {
      return padTop + (1.0 - (p - scaleMin) / scaleSpan) * priceAreaH;
    }

    const candleSlotW = chartW / nCandles;
    const candleW = Math.max(2, Math.min(26, candleSlotW * 0.72));

    // 1. Grid lines & Price tick labels
    const numTicks = 5;
    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textMuted;
    ctx.font = '600 10px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    for (let t = 0; t <= numTicks; t++) {
      const pVal = scaleMin + (scaleSpan * t) / numTicks;
      const y = priceToY(pVal);

      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();

      ctx.fillText(`₹${pVal.toFixed(2)}`, width - padRight + 8, y);
    }

    // 2. Volume Bars
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const open = c.open != null ? c.open : c.price;
      const vH = c.volume ? (c.volume / maxV) * volAreaH : 0;
      const vy = volAreaTop + volAreaH - vH;
      const isBull = c.price >= open;

      ctx.fillStyle = isBull ? 'rgba(16, 185, 129, 0.28)' : 'rgba(239, 68, 68, 0.28)';
      ctx.fillRect(cx - candleW / 2, vy, candleW, vH);
    }

    // Pre-calculate SuperTrend series & coordinates for visible candles
    const stPoints = [];
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const stItem = visibleStValues[i];
      if (stItem && stItem.price > 0) {
        const cy = priceToY(stItem.price);
        const cHigh = c.high != null ? c.high : c.price;
        const cLow = c.low != null ? c.low : c.price;
        const cHighY = priceToY(cHigh);
        const cLowY = priceToY(cLow);
        stPoints.push({
          x: cx,
          y: cy,
          price: stItem.price,
          dir: stItem.dir,
          time: c.time,
          cHighY,
          cLowY
        });
      }
    }

    // 2b. SuperTrend Shaded Shadow / Cloud Ribbon (rendered behind candlesticks)
    if (supertrendOverlayEnabled && cachedSuperTrendData && stPoints.length > 0) {
      const segments = [];
      let currSeg = [stPoints[0]];

      for (let j = 1; j < stPoints.length; j++) {
        if (stPoints[j].dir === currSeg[0].dir) {
          currSeg.push(stPoints[j]);
        } else {
          segments.push(currSeg);
          currSeg = [stPoints[j]];
        }
      }
      if (currSeg.length > 0) {
        segments.push(currSeg);
      }

      ctx.save();
      for (const seg of segments) {
        if (seg.length < 1) continue;
        const isBuy = seg[0].dir === 'BUY';

        ctx.beginPath();
        // 1. Trace forward along the solid SuperTrend line
        ctx.moveTo(seg[0].x, seg[0].y);
        for (let k = 1; k < seg.length; k++) {
          ctx.lineTo(seg[k].x, seg[k].y);
        }

        // 2. Trace backward along the candle boundary facing the SuperTrend line:
        for (let k = seg.length - 1; k >= 0; k--) {
          const pt = seg[k];
          const candleY = isBuy ? Math.min(pt.y, pt.cLowY) : Math.max(pt.y, pt.cHighY);
          ctx.lineTo(pt.x, candleY);
        }

        ctx.closePath();

        ctx.fillStyle = isBuy
          ? (isDarkMode ? 'rgba(16, 185, 129, 0.16)' : 'rgba(16, 185, 129, 0.12)')
          : (isDarkMode ? 'rgba(239, 68, 68, 0.16)' : 'rgba(239, 68, 68, 0.12)');
        ctx.fill();
      }
      ctx.restore();
    }

    // 3. Candlesticks
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const open = c.open != null ? c.open : c.price;
      const close = c.price;
      const high = c.high != null ? c.high : Math.max(open, close);
      const low = c.low != null ? c.low : Math.min(open, close);
      const isBull = close >= open;
      const color = isBull ? greenBull : redBear;

      const yOpen = priceToY(open);
      const yClose = priceToY(close);
      const yHigh = priceToY(high);
      const yLow = priceToY(low);

      const bodyTop = Math.min(yOpen, yClose);
      const bodyH = Math.max(1.5, Math.abs(yClose - yOpen));

      // Wick
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(1, Math.min(2.5, candleW * 0.18));
      ctx.beginPath();
      ctx.moveTo(cx, yHigh);
      ctx.lineTo(cx, yLow);
      ctx.stroke();

      // Body
      ctx.fillStyle = color;
      ctx.fillRect(cx - candleW / 2, bodyTop, candleW, bodyH);
    }

    // 4. Time Axis Labels
    const maxTimeLabels = Math.min(8, nCandles);
    const step = Math.max(1, Math.floor(nCandles / maxTimeLabels));
    ctx.fillStyle = textMuted;
    ctx.font = '600 10px "Plus Jakarta Sans", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    for (let i = 0; i < nCandles; i += step) {
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const tLabel = visibleCandles[i].time || '';
      ctx.fillText(tLabel, cx, height - padBottom + 8);
    }

    // 5. Live Price Reference Line
    if (livePrice > 0 && livePrice >= scaleMin && livePrice <= scaleMax) {
      const lpY = priceToY(livePrice);

      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = '#3b82f6';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(padLeft, lpY);
      ctx.lineTo(width - padRight, lpY);
      ctx.stroke();
      ctx.restore();

      drawAxisPill(ctx, width - padRight + 4, lpY, 'LIVE', `₹${livePrice.toFixed(2)}`, '#3b82f6', '#ffffff');
    }

    // 5b. SuperTrend Multi-Colored Solid Line on Candlestick Chart (Green for BUY, Red for SELL)
    if (supertrendOverlayEnabled && cachedSuperTrendData && stPoints.length > 0) {
      ctx.save();
      ctx.lineWidth = 2.6;
      ctx.setLineDash([]); // 100% Solid line
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      if (stPoints.length === 1) {
        // Single point fallback: horizontal marker
        const p = stPoints[0];
        ctx.strokeStyle = p.dir === 'BUY' ? greenBull : redBear;
        ctx.beginPath();
        ctx.moveTo(p.x - candleSlotW / 2, p.y);
        ctx.lineTo(p.x + candleSlotW / 2, p.y);
        ctx.stroke();
      } else {
        for (let i = 1; i < stPoints.length; i++) {
          const pPrev = stPoints[i - 1];
          const pCurr = stPoints[i];

          // If direction flipped between candles, don't draw diagonal cross; draw flip marker dot
          if (pPrev.dir !== pCurr.dir) {
            ctx.fillStyle = pCurr.dir === 'BUY' ? greenBull : redBear;
            ctx.beginPath();
            ctx.arc(pCurr.x, pCurr.y, 4.5, 0, Math.PI * 2);
            ctx.fill();
            continue;
          }

          ctx.strokeStyle = pCurr.dir === 'BUY' ? greenBull : redBear;
          ctx.beginPath();
          ctx.moveTo(pPrev.x, pPrev.y);
          ctx.lineTo(pCurr.x, pCurr.y);
          ctx.stroke();
        }
      }

      ctx.restore();

      // Latest SuperTrend Axis Pill on right edge - clamped to ensure always visible
      const latestSt = stPoints[stPoints.length - 1];
      if (latestSt && latestSt.price > 0) {
        const rawStY = priceToY(latestSt.price);
        const stY = Math.max(padTop + 10, Math.min(padTop + priceAreaH - 10, rawStY));
        const isBuy = latestSt.dir === 'BUY';
        const stColor = isBuy ? greenBull : redBear;
        drawAxisPill(ctx, width - padRight + 4, stY, isBuy ? 'ST-BUY' : 'ST-SELL', `₹${latestSt.price.toFixed(2)}`, stColor, '#ffffff');
      }
    }

    // 6. Crosshair & Hover
    let activeCandle = null;
    if (hoveredCandleIdx >= 0 && hoveredCandleIdx < nCandles) {
      activeCandle = visibleCandles[hoveredCandleIdx];
    } else {
      activeCandle = visibleCandles[nCandles - 1];
    }

    if (hoveredCandleIdx >= 0 && hoveredCandleIdx < nCandles && mouseX != null && mouseY != null && !isPanning) {
      const cx = padLeft + (hoveredCandleIdx + 0.5) * candleSlotW;
      const cy = priceToY(activeCandle.price);

      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = isDarkMode ? 'rgba(148, 163, 184, 0.7)' : 'rgba(71, 85, 105, 0.7)';
      ctx.lineWidth = 1;

      // Vertical line
      ctx.beginPath();
      ctx.moveTo(cx, padTop);
      ctx.lineTo(cx, padTop + priceAreaH);
      ctx.stroke();

      // Horizontal line
      const clampedMouseY = Math.max(padTop, Math.min(padTop + priceAreaH, mouseY));
      ctx.beginPath();
      ctx.moveTo(padLeft, clampedMouseY);
      ctx.lineTo(width - padRight, clampedMouseY);
      ctx.stroke();

      // Point circle
      const activeOpen = activeCandle.open != null ? activeCandle.open : activeCandle.price;
      ctx.fillStyle = activeCandle.price >= activeOpen ? greenBull : redBear;
      ctx.beginPath();
      ctx.arc(cx, cy, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
      ctx.restore();
    }

    // Update OHLC HUD
    if (activeCandle) {
      const hTime = getEl('live-hud-time');
      const hOpen = getEl('live-hud-open');
      const hHigh = getEl('live-hud-high');
      const hLow = getEl('live-hud-low');
      const hClose = getEl('live-hud-close');
      const hVol = getEl('live-hud-vol');

      const openVal = activeCandle.open != null ? activeCandle.open : activeCandle.price;
      const highVal = activeCandle.high != null ? activeCandle.high : Math.max(openVal, activeCandle.price);
      const lowVal = activeCandle.low != null ? activeCandle.low : Math.min(openVal, activeCandle.price);

      if (hTime) hTime.textContent = activeCandle.time || '--:--';
      if (hOpen) hOpen.textContent = `₹${openVal.toFixed(2)}`;
      if (hHigh) hHigh.textContent = `₹${highVal.toFixed(2)}`;
      if (hLow) hLow.textContent = `₹${lowVal.toFixed(2)}`;
      if (hClose) {
        hClose.textContent = `₹${activeCandle.price.toFixed(2)}`;
        hClose.style.color = activeCandle.price >= openVal ? greenBull : redBear;
      }
      if (hVol) hVol.textContent = activeCandle.volume ? activeCandle.volume.toLocaleString('en-IN') : '--';

      // SuperTrend value in Crosshair HUD
      const hSt = getEl('live-hud-st');
      const hStItem = getEl('live-hud-st-item');
      if (hSt && supertrendOverlayEnabled && cachedSuperTrendData) {
        if (hStItem) hStItem.style.display = 'flex';
        const activeIdx = (hoveredCandleIdx >= 0 && hoveredCandleIdx < nCandles) ? hoveredCandleIdx : (nCandles - 1);
        const stItem = visibleStValues[activeIdx];
        if (stItem && stItem.price > 0) {
          const isBuy = stItem.dir === 'BUY';
          hSt.innerHTML = `<span style="color: ${isBuy ? greenBull : redBear}; font-weight: 800;">₹${stItem.price.toFixed(2)} <span style="font-size: 10px; padding: 1px 4px; border-radius: 4px; background: ${isBuy ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)'};">${stItem.dir}</span></span>`;
        } else {
          hSt.textContent = '₹--.--';
        }
      } else if (hStItem && !supertrendOverlayEnabled) {
        hStItem.style.display = 'none';
      }

      // Legend chips visibility
      const legStBuy = getEl('legend-chip-st-buy');
      const legStSell = getEl('legend-chip-st-sell');
      if (legStBuy) legStBuy.style.display = supertrendOverlayEnabled ? 'inline-flex' : 'none';
      if (legStSell) legStSell.style.display = supertrendOverlayEnabled ? 'inline-flex' : 'none';
    }

    // Watermark badge when zoomed
    if (range.isZoomed) {
      ctx.save();
      ctx.fillStyle = isDarkMode ? 'rgba(30, 41, 59, 0.75)' : 'rgba(241, 245, 249, 0.85)';
      ctx.strokeStyle = isDarkMode ? '#334155' : '#cbd5e1';
      ctx.lineWidth = 1;
      const badgeText = `ZOOM ${(cachedCandles.length / nCandles).toFixed(1)}x (${nCandles}/${cachedCandles.length})`;
      ctx.font = '700 10px "JetBrains Mono", monospace';
      const bW = ctx.measureText(badgeText).width + 12;
      const bH = 18;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(padLeft + 6, padTop + 6, bW, bH, 4);
      else ctx.rect(padLeft + 6, padTop + 6, bW, bH);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#2563eb';
      ctx.textBaseline = 'middle';
      ctx.textAlign = 'left';
      ctx.fillText(badgeText, padLeft + 12, padTop + 6 + bH / 2);
      ctx.restore();
    }
  }

  function drawAxisPill(ctx, x, y, tagText, priceText, bgColor, textColor) {
    ctx.save();
    ctx.font = '700 9px "JetBrains Mono", monospace';
    const tagW = ctx.measureText(tagText).width;
    ctx.font = '800 10px "JetBrains Mono", monospace';
    const priceW = ctx.measureText(priceText).width;

    const boxW = Math.max(76, tagW + priceW + 14);
    const boxH = 18;
    const boxY = y - boxH / 2;

    ctx.fillStyle = bgColor;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(x, boxY, boxW, boxH, 4);
    } else {
      ctx.rect(x, boxY, boxW, boxH);
    }
    ctx.fill();

    ctx.fillStyle = textColor;
    ctx.textBaseline = 'middle';
    ctx.font = '700 9px "JetBrains Mono", monospace';
    ctx.fillText(tagText, x + 4, y);

    ctx.font = '800 10px "JetBrains Mono", monospace';
    ctx.fillText(priceText, x + tagW + 8, y);
    ctx.restore();
  }

  // --- INITIALIZATION ---
  function init() {
    initChart();
    setupChartModeToggle();
    setupCandleChartEvents();
    setupSuperTrendControls();
    setupSearch();
    setupTimeRanges();
    fetchStockData(true);

    if (pollingTimer) clearInterval(pollingTimer);
    pollingTimer = setInterval(() => {
      fetchStockData(false);
    }, 5000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
