/**
 * StockPulse — Live Market & Interactive Chart Page
 */

(function () {
  'use strict';

  const _urlParams = new URLSearchParams(window.location.search);
  const _paramStock = _urlParams.get('symbol') || _urlParams.get('stock');
  let currentSymbol = (_paramStock && _paramStock.trim().toUpperCase().replace(/\.(NS|BO)$/, '')) ||
    (window.StockPulse && window.StockPulse.getSelectedStock && window.StockPulse.getSelectedStock()) ||
    (typeof localStorage !== 'undefined' && localStorage.getItem('stockpulse_selected_stock')) ||
    'TCS';

  if (_paramStock) {
    if (window.StockPulse && window.StockPulse.setSelectedStock) {
      StockPulse.setSelectedStock(currentSymbol);
    } else if (typeof localStorage !== 'undefined') {
      localStorage.setItem('stockpulse_selected_stock', currentSymbol);
    }
  }

  function syncPopularChips(sym) {
    let targetSym = sym;
    if (!targetSym) {
      try {
        const _params = new URLSearchParams(window.location.search);
        targetSym = _params.get('symbol') || _params.get('stock');
      } catch (e) {}
    }
    if (!targetSym && window.StockPulse && window.StockPulse.getSelectedStock) {
      targetSym = window.StockPulse.getSelectedStock();
    }
    if (!targetSym && typeof localStorage !== 'undefined') {
      try {
        const uStr = localStorage.getItem('stockpulse_user_profile');
        if (uStr) {
          const u = JSON.parse(uStr);
          if (u && u.id) targetSym = localStorage.getItem('stockpulse_selected_stock_u' + u.id);
        }
      } catch (e) {}
      if (!targetSym) targetSym = localStorage.getItem('stockpulse_selected_stock');
    }
    if (!targetSym) targetSym = currentSymbol || 'TCS';
    targetSym = targetSym.toUpperCase().replace(/\.(NS|BO)$/, '');

    const chipBtns = document.querySelectorAll('.ticker-chip-btn');
    if (!chipBtns || !chipBtns.length) return;
    chipBtns.forEach(btn => {
      const bSym = btn.getAttribute('data-symbol');
      const cleanBSym = bSym ? bSym.toUpperCase().replace(/\.(NS|BO)$/, '') : '';
      if (cleanBSym && cleanBSym === targetSym) {
        btn.classList.add('active');
        btn.style.setProperty('background', '#2563eb', 'important');
        btn.style.setProperty('color', '#ffffff', 'important');
        btn.style.setProperty('border-color', '#2563eb', 'important');
      } else {
        btn.classList.remove('active');
        btn.style.removeProperty('background');
        btn.style.removeProperty('color');
        btn.style.removeProperty('border-color');
      }
    });
  }

  // Immediately synchronize popular chips upon script parse
  try {
    if (document.readyState !== 'loading') {
      syncPopularChips(currentSymbol);
    } else {
      document.addEventListener('DOMContentLoaded', () => syncPopularChips(currentSymbol));
    }
  } catch (e) {}

  let currentTimeRange = '1D';
  let currentChartMode = 'candles'; // Sole premier chart mode: Candlestick
  let cachedCandles = [];
  let hoveredCandleIdx = -1;
  let pollingTimer = null;
  let isFetching = false;
  let currentMarketStatus = (window.StockPulse && window.StockPulse.getCachedMarketStatus && window.StockPulse.getCachedMarketStatus()) || (sessionStorage.getItem('stockpulse_cached_market_status') || 'UNKNOWN');
  let lastFetchTimestamp = 0;
  let currentPriceVal = 0;
  let supertrendOverlayEnabled = (window.StockPulse && window.StockPulse.getSupertrendOverlay) ? window.StockPulse.getSupertrendOverlay() : true;
  let cachedSuperTrendData = null;
  let cachedSuperTrendSeries = [];
  let supertrendDirections = [];

  // Candlestick Zoom & Pan State (like TradingView)
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

  // Active fetch abort controller and monotonic request sequence
  let activeFetchController = null;
  let activeFetchSeq = 0;

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
    const refreshingBadge = getEl('stock-refreshing-badge');

    // Header & Price Skeletons
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
    if (refreshingBadge) refreshingBadge.style.display = 'none';

    // SuperTrend Strip Skeletons
    const stPill = getEl('live-st-pill');
    const stPrice = getEl('live-st-price');
    const stGap = getEl('live-st-gap');
    const stHitPill = getEl('live-st-hit-pill');
    if (stPill) stPill.innerHTML = '<span class="buffering-loader" style="width: 110px; height: 20px;"></span>';
    if (stPrice) stPrice.innerHTML = '<span class="buffering-loader" style="width: 60px; height: 15px;"></span>';
    if (stGap) stGap.innerHTML = '<span class="buffering-loader" style="width: 50px; height: 15px;"></span>';
    if (stHitPill) stHitPill.style.display = 'none';

    // OHLC HUD Skeletons
    ['live-hud-time', 'live-hud-open', 'live-hud-high', 'live-hud-low', 'live-hud-close', 'live-hud-change', 'live-hud-vol', 'live-hud-st'].forEach(id => {
      const el = getEl(id);
      if (el) el.innerHTML = '<span class="buffering-loader" style="width: 44px; height: 13px;"></span>';
    });

    // Chart Skeleton State
    const skelChart = getEl('skeleton-chart-wrapper');
    const canvasWrap = getEl('live-candle-wrapper');
    const unavail = getEl('unavailable-state');
    if (skelChart) skelChart.style.display = 'flex';
    if (canvasWrap) canvasWrap.style.display = 'none';
    if (unavail) unavail.style.display = 'none';
  }

  function clearLiveBufferingState() {
    const skelChart = getEl('skeleton-chart-wrapper');
    const canvasWrap = getEl('live-candle-wrapper');
    const refreshingBadge = getEl('stock-refreshing-badge');
    const overlay = getEl('chart-loading-overlay');
    if (skelChart) skelChart.style.display = 'none';
    if (canvasWrap) canvasWrap.style.display = 'block';
    if (refreshingBadge) refreshingBadge.style.display = 'none';
    if (overlay) {
      overlay.style.display = 'none';
      overlay.classList.remove('show');
    }
  }

  // Client-side cache for instantaneous display on stock switch & range toggle
  // cacheKey -> { data: Object, timestamp: number }
  const clientStockCache = new Map();

  // --- FETCH STOCK DATA ---
  async function fetchStockData(showLoading = false) {
    const cacheKey = `${currentSymbol}_${currentTimeRange}`;
    const cachedEntry = clientStockCache.get(cacheKey);
    const ttl = currentTimeRange === '1D' ? 10000 : 180000;
    const isCachedUsable = cachedEntry && (Date.now() - cachedEntry.timestamp < ttl);

    if (showLoading) {
      // User explicitly switched stock or range:
      // 1. Cancel previous in-flight fetch so old requests cannot overwrite the selection
      if (activeFetchController) {
        try { activeFetchController.abort(); } catch (e) {}
      }

      if (cachedEntry) {
        // Instantaneous render from valid client cache (Stale-While-Revalidate)
        renderDashboard(cachedEntry.data);
        if (cachedEntry.data.supertrend) {
          cachedSuperTrendData = cachedEntry.data.supertrend;
          cachedSuperTrendSeries = cachedEntry.data.supertrend.series || [];
          renderLiveSuperTrendHUD(cachedEntry.data.supertrend);
        }
        clearLiveBufferingState();

        // Show subtle refreshing badge if cache is approaching expiration
        const refBadge = getEl('stock-refreshing-badge');
        if (refBadge && !isCachedUsable) refBadge.style.display = 'inline-flex';
      } else {
        // Cold uncached stock: show matching skeleton immediately
        setLiveBufferingState();
      }
    } else {
      // Automatic background poll: skip if an active user fetch is already in flight
      if (isFetching) return;
    }

    const thisSeq = ++activeFetchSeq;
    activeFetchController = new AbortController();
    isFetching = true;
    const reqSymbol = currentSymbol;
    const reqRange = currentTimeRange;
    const reqUrl = `/api/stocks/${encodeURIComponent(reqSymbol)}?range=${encodeURIComponent(reqRange)}`;
    const startTime = Date.now();

    try {
      const resStock = await fetch(reqUrl, {
        signal: activeFetchController.signal,
        cache: 'default'
      });

      const durationMs = Date.now() - startTime;

      if (!resStock.ok) {
        console.warn(`[StockPulse][LiveMarket] Symbol=${reqSymbol} endpoint=${reqUrl} HTTP error=${resStock.status} duration=${durationMs}ms`);
        throw new Error(`HTTP ${resStock.status}`);
      }

      const data = await resStock.json();

      // Guard against race conditions: ignore response if a newer selection occurred
      if (thisSeq !== activeFetchSeq) return;

      if (data.status === 'DATA_UNAVAILABLE' || data.status === 'UNAVAILABLE' || !data.price) {
        console.warn(`[StockPulse][LiveMarket] Symbol=${reqSymbol} endpoint=${reqUrl} returned DATA_UNAVAILABLE duration=${durationMs}ms`);
        if (!cachedEntry) {
          showUnavailableState(data.symbol || currentSymbol, data.error || 'Provider data is currently unavailable.');
        }
        return;
      }

      console.log(`[StockPulse][LiveMarket] Symbol=${reqSymbol} endpoint=${reqUrl} status=${resStock.status} duration=${durationMs}ms`);

      // Update client cache with fresh payload
      clientStockCache.set(cacheKey, { data, timestamp: Date.now() });

      // Process pre-bundled SuperTrend indicator directly (zero duplicate roundtrips)
      if (data.supertrend) {
        cachedSuperTrendData = data.supertrend;
        cachedSuperTrendSeries = data.supertrend.series || [];
        renderLiveSuperTrendHUD(data.supertrend);
      } else {
        fetchLiveSuperTrend(currentSymbol);
      }

      renderDashboard(data);
      clearLiveBufferingState();

      lastFetchTimestamp = Date.now();
      if (data && data.market_status) {
        currentMarketStatus = data.market_status.toUpperCase();
        if (window.StockPulse && window.StockPulse.applyMarketStatus) {
          const ts = data.data_received_at ? new Date(data.data_received_at).getTime() : Date.now();
          window.StockPulse.applyMarketStatus(currentMarketStatus, ts);
        }
      }

    } catch (err) {
      if (err.name === 'AbortError' || thisSeq !== activeFetchSeq) {
        // Obsolete request cleanly cancelled; nothing to report
        return;
      }
      const durationMs = Date.now() - startTime;
      console.warn(`[StockPulse][LiveMarket] Symbol=${reqSymbol} endpoint=${reqUrl} failed duration=${durationMs}ms errorType=${err.name || 'Error'}: ${err.message}`);

      if (!cachedEntry) {
        showUnavailableState(currentSymbol, 'Network connection error or market provider timeout. Please check your connection and retry.');
      }
    } finally {
      if (thisSeq === activeFetchSeq) {
        isFetching = false;
        const refBadge = getEl('stock-refreshing-badge');
        if (refBadge) refBadge.style.display = 'none';
        scheduleNextPoll();
      }
    }
  }

  function scheduleNextPoll(delayMs) {
    if (pollingTimer) {
      clearTimeout(pollingTimer);
      pollingTimer = null;
    }
    if (document.hidden) return; // Tab in background: paused

    let delay = delayMs;
    if (delay === undefined) {
      delay = (currentMarketStatus === 'OPEN') ? 3000 : 30000;
    }
    pollingTimer = setTimeout(() => {
      fetchStockData(false);
    }, delay);
  }

  function showUnavailableState(sym, message) {
    clearLiveBufferingState();
    const unavail = getEl('unavailable-state');
    const cContainer = getEl('live-candle-wrapper');
    const errText = getEl('unavailable-error-text');
    if (errText) errText.textContent = message || `Market data for ${sym} is currently unavailable. Please retry.`;
    if (unavail) unavail.style.display = 'block';
    if (cContainer) cContainer.style.display = 'none';

    // Clear Header & Price Skeletons so they don't remain stuck indefinitely on failure
    const symEl = getEl('stock-symbol');
    const exchEl = getEl('stock-exchange');
    const nameEl = getEl('company-name');
    const curPrice = getEl('current-price');
    const priceChangeAbs = getEl('price-change-abs');
    const priceChangePct = getEl('price-change-pct');
    const prevCloseRef = getEl('prev-close-ref');
    const liveLabel = getEl('live-status-label');
    const updatedEl = getEl('last-updated-time');
    const livePill = getEl('live-status-pill');

    if (symEl) symEl.textContent = sym || currentSymbol;
    if (exchEl) exchEl.textContent = 'NSE';
    if (nameEl) nameEl.textContent = `${sym || currentSymbol} — Data Unavailable`;
    if (curPrice) curPrice.textContent = '₹--.--';
    if (priceChangeAbs) priceChangeAbs.textContent = '';
    if (priceChangePct) priceChangePct.textContent = '';
    if (prevCloseRef) prevCloseRef.textContent = 'Quotes currently unavailable';
    if (livePill) livePill.className = 'live-pill closed';
    if (liveLabel) liveLabel.textContent = 'UNAVAILABLE';
    if (updatedEl) updatedEl.textContent = new Date().toLocaleTimeString('en-IN');

    // SuperTrend Strip Skeletons clear
    const stPill = getEl('live-st-pill');
    const stPrice = getEl('live-st-price');
    const stGap = getEl('live-st-gap');
    const stHitPill = getEl('live-st-hit-pill');
    if (stPill) {
      stPill.className = 'supertrend-badge-pill';
      stPill.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i> UNAVAILABLE';
    }
    if (stPrice) stPrice.textContent = '₹--.--';
    if (stGap) stGap.textContent = '--';
    if (stHitPill) stHitPill.style.display = 'none';

    // OHLC HUD Skeletons clear
    ['live-hud-time', 'live-hud-open', 'live-hud-high', 'live-hud-low', 'live-hud-close', 'live-hud-change', 'live-hud-vol', 'live-hud-st'].forEach(id => {
      const el = getEl(id);
      if (el) el.textContent = '--';
    });
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
      renderCandleChart();
    } catch (e) {
      console.warn('[Live] SuperTrend fetch error:', e);
    }
  }

  function renderLiveSuperTrendHUD(data) {
    const pill = getEl('live-st-pill');
    const priceEl = getEl('live-st-price');
    const gapEl = getEl('live-st-gap');
    const hitPill = getEl('live-st-hit-pill');
    const iconBox = getEl('live-st-icon-box');

    if (!data) {
      if (pill) {
        pill.className = 'supertrend-badge-pill neutral';
        pill.innerHTML = '<i class="fa-solid fa-circle-info"></i> STATUS UNAVAILABLE';
      }
      if (priceEl) priceEl.textContent = '₹--.--';
      if (gapEl) gapEl.textContent = '--';
      if (hitPill) hitPill.style.display = 'none';
      if (iconBox) iconBox.style.background = 'var(--bg-card)';
      return;
    }

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
        } else if (typeof chartInstance !== 'undefined' && chartInstance) {
          if (typeof updateChartSuperTrendDataset === 'function') updateChartSuperTrendDataset();
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
            const evType = sig === 'APPROACHING' ? 'SUPERTREND_APPROACHING' : (sig === 'BUY' ? 'SUPERTREND_BUY' : 'SUPERTREND_SELL');
            const liveP = (currentQuote && currentQuote.price) ? currentQuote.price : 4120.0;
            const notifItem = {
              id: Date.now(),
              symbol: currentSymbol,
              event_type: evType,
              priority: sig === 'APPROACHING' ? 'WARNING' : 'SUCCESS',
              title: `SuperTrend ${sig}: ${currentSymbol}`,
              message: `SuperIndicator test signal triggered for ${currentSymbol} (${sig}).`,
              short_summary: `SuperIndicator test signal ${sig} for ${currentSymbol}.`,
              extra_data: JSON.stringify({
                symbol: currentSymbol,
                current_price: liveP,
                supertrend_price: sig === 'BUY' ? liveP - 1.2 : liveP + 1.2,
                direction: sig === 'BUY' ? 'UP' : 'DOWN'
              })
            };
            if (window.StockPulse && StockPulse.showCustomizedPopup) {
              StockPulse.showCustomizedPopup(notifItem);
            }
            if (window.StockPulse && StockPulse.playNotificationSound) {
              StockPulse.playNotificationSound(evType);
            }
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

  // --- OHLC HUD UPDATERS ---
  function updateOHLCHUD(activeCandle) {
    if (!activeCandle) {
      resetOHLCHUD();
      return;
    }
    const hTime = getEl('live-hud-time');
    const hOpen = getEl('live-hud-open');
    const hHigh = getEl('live-hud-high');
    const hLow = getEl('live-hud-low');
    const hClose = getEl('live-hud-close');
    const hChange = getEl('live-hud-change');
    const hVol = getEl('live-hud-vol');

    const closeVal = Number(activeCandle.close != null ? activeCandle.close : (activeCandle.price != null ? activeCandle.price : (currentPriceVal || 0)));
    const openVal = Number(activeCandle.open != null ? activeCandle.open : closeVal);
    const highVal = Number(activeCandle.high != null ? activeCandle.high : Math.max(openVal, closeVal));
    const lowVal = Number(activeCandle.low != null ? activeCandle.low : Math.min(openVal, closeVal));

    const diffVal = closeVal - openVal;
    const pctVal = openVal > 0 ? (diffVal / openVal) * 100 : 0;
    const sign = diffVal >= 0 ? '+' : '';
    const isBull = closeVal >= openVal;

    if (hTime) hTime.textContent = activeCandle.time || '--:--';
    if (hOpen) hOpen.textContent = `₹${openVal.toFixed(2)}`;
    if (hHigh) hHigh.textContent = `₹${highVal.toFixed(2)}`;
    if (hLow) hLow.textContent = `₹${lowVal.toFixed(2)}`;
    if (hClose) {
      hClose.textContent = `₹${closeVal.toFixed(2)}`;
      hClose.style.color = isBull ? '#089981' : '#f23645';
    }
    if (hChange) {
      hChange.textContent = `${sign}₹${diffVal.toFixed(2)} (${sign}${pctVal.toFixed(2)}%)`;
      hChange.style.color = isBull ? '#089981' : '#f23645';
    }
    if (hVol) hVol.textContent = activeCandle.volume ? Number(activeCandle.volume).toLocaleString('en-IN') : '--';

    const hSt = getEl('live-hud-st');
    const hStItem = getEl('live-hud-st-item');
    if (hSt && supertrendOverlayEnabled && cachedSuperTrendData && cachedSuperTrendData.supertrend_price) {
      if (hStItem) hStItem.style.display = 'flex';
      const isBuy = cachedSuperTrendData.direction === 'BUY';
      hSt.innerHTML = `<span style="color: ${isBuy ? '#089981' : '#f23645'}; font-weight: 800;">₹${Number(cachedSuperTrendData.supertrend_price).toFixed(2)} <span style="font-size: 10px; padding: 1px 4px; border-radius: 4px; background: ${isBuy ? 'rgba(8, 153, 129, 0.15)' : 'rgba(242, 54, 69, 0.15)'};">${cachedSuperTrendData.direction}</span></span>`;
    } else if (hSt) {
      hSt.textContent = '₹--.--';
    }
  }

  function resetOHLCHUD() {
    ['live-hud-time', 'live-hud-open', 'live-hud-high', 'live-hud-low', 'live-hud-close', 'live-hud-change', 'live-hud-vol', 'live-hud-st'].forEach(id => {
      const el = getEl(id);
      if (el) {
        el.textContent = id.endsWith('open') || id.endsWith('high') || id.endsWith('low') || id.endsWith('close') || id.endsWith('st') ? '₹--.--' : '--';
        el.style.color = '';
      }
    });
  }

  // --- RENDER DASHBOARD & CHART ---
  function renderDashboard(data) {
    if (!data) return;

    const unavail = getEl('unavailable-state');
    if (unavail) unavail.style.display = 'none';

    // Ensure candlestick canvas and HUD are active
    ensureCandleElementsActive();

    // Header info with targeted updates (only mutate if changed)
    const symEl = getEl('stock-symbol');
    const exchEl = getEl('stock-exchange');
    const nameEl = getEl('company-name');
    const updatedEl = getEl('last-updated-time');

    const nextSym = data.symbol || currentSymbol;
    const nextExch = data.exchange || 'NSE';
    const nextName = data.name || `${currentSymbol} Ltd`;
    if (symEl && symEl.textContent !== nextSym) symEl.textContent = nextSym;
    if (exchEl && exchEl.textContent !== nextExch) exchEl.textContent = nextExch;
    if (nameEl && nameEl.textContent !== nextName) nameEl.textContent = nextName;
    if (nextSym) {
      currentSymbol = nextSym.toUpperCase().replace(/\.(NS|BO)$/, '');
      syncPopularChips(currentSymbol);
    }

    // Market status and closure state based on authoritative exchange session
    const isClosed = data.market_status === 'CLOSED';
    const isDataUnavailable = data.status === 'DATA_UNAVAILABLE' || data.status === 'UNAVAILABLE' || data.data_status === 'DATA_UNAVAILABLE';
    const marketTradeTime = data.market_last_traded;
    const syncTime = data.last_updated || new Date().toLocaleTimeString('en-IN');
    const targetUpdated = (isClosed && marketTradeTime) ? `${syncTime} (Close: ${marketTradeTime})` : syncTime;
    if (updatedEl && updatedEl.textContent !== targetUpdated) {
      updatedEl.textContent = targetUpdated;
    }

    // Market status in navigation (delegate to shared coordinator to eliminate flicker and race conditions)
    if (window.StockPulse && window.StockPulse.applyMarketStatus) {
      const ts = data.data_received_at ? new Date(data.data_received_at).getTime() : Date.now();
      window.StockPulse.applyMarketStatus(data.market_status || (isClosed ? 'CLOSED' : 'OPEN'), ts);
    } else {
      const mStatusText = getEl('market-status-text');
      const mStatusDot = getEl('market-status-dot');
      const targetMText = `Market: ${data.market_status || (isClosed ? 'CLOSED' : 'OPEN')}`;
      const targetMDot = `pulse-dot ${isClosed ? 'closed' : 'green'}`;
      if (mStatusText && mStatusText.textContent !== targetMText) mStatusText.textContent = targetMText;
      if (mStatusDot && mStatusDot.className !== targetMDot) mStatusDot.className = targetMDot;
    }

    // Live / Delayed / Closed / Unavailable status badge
    const livePill = getEl('live-status-pill');
    const liveDot = getEl('live-status-dot');
    const liveLabel = getEl('live-status-label');
    const globalDot = getEl('global-status-dot');
    const globalText = getEl('global-status-text');

    let targetPillClass = 'live-pill';
    let targetDotClass = 'pulse-dot green';
    let targetLabelText = 'LIVE';
    let targetGlobalDot = 'status-indicator-dot live';
    let targetGlobalText = 'LIVE';

    if (isClosed) {
      targetPillClass = 'live-pill closed';
      targetDotClass = 'pulse-dot closed';
      targetLabelText = 'MARKET CLOSED';
      targetGlobalDot = 'status-indicator-dot closed';
      targetGlobalText = 'CLOSED';
    } else if (isDataUnavailable) {
      targetPillClass = 'live-pill closed';
      targetDotClass = 'pulse-dot neutral';
      targetLabelText = 'DATA UNAVAILABLE';
      targetGlobalDot = 'status-indicator-dot unavailable';
      targetGlobalText = 'UNAVAILABLE';
    } else if (data.data_status === 'LIVE' || data.status === 'LIVE') {
      targetPillClass = 'live-pill';
      targetDotClass = 'pulse-dot green';
      targetLabelText = 'LIVE';
      targetGlobalDot = 'status-indicator-dot live';
      targetGlobalText = 'LIVE';
    } else if (data.data_status === 'DELAYED') {
      targetPillClass = 'live-pill delayed';
      targetDotClass = 'pulse-dot amber';
      targetLabelText = 'DELAYED (15m)';
      targetGlobalDot = 'status-indicator-dot delayed';
      targetGlobalText = 'DELAYED';
    } else {
      targetPillClass = 'live-pill closed';
      targetDotClass = 'pulse-dot amber';
      targetLabelText = 'LAST CLOSE / STALE';
      targetGlobalDot = 'status-indicator-dot closed';
      targetGlobalText = 'STALE';
    }

    if (livePill && livePill.className !== targetPillClass) livePill.className = targetPillClass;
    if (liveDot && liveDot.className !== targetDotClass) liveDot.className = targetDotClass;
    if (liveLabel && liveLabel.textContent !== targetLabelText) liveLabel.textContent = targetLabelText;
    if (globalDot && globalDot.className !== targetGlobalDot) globalDot.className = targetGlobalDot;
    if (globalText && globalText.textContent !== targetGlobalText) globalText.textContent = targetGlobalText;

    // Price and Change
    const priceVal = data.price || 0;
    const prevPrice = currentPriceVal;
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

    const formattedPrice = `₹${priceVal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (priceEl && priceEl.textContent !== formattedPrice) {
      priceEl.textContent = formattedPrice;
      // Visual tick pulse animation ONLY when market is open and live price moves
      if (!isClosed && prevPrice > 0 && Math.abs(priceVal - prevPrice) >= 0.01) {
        priceEl.classList.remove('price-pulse-up', 'price-pulse-down');
        void priceEl.offsetWidth; // Force CSS reflow
        priceEl.classList.add(priceVal >= prevPrice ? 'price-pulse-up' : 'price-pulse-down');
      }
    }
    const prevRefText = `vs prev close ₹${prevClose.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (prevRefEl && prevRefEl.textContent !== prevRefText) {
      prevRefEl.textContent = prevRefText;
    }

    const badgeClass = `change-badge ${isUp ? 'up' : 'down'}`;
    if (changeBadge && changeBadge.className !== badgeClass) {
      changeBadge.className = badgeClass;
    }
    if (changeIcon) {
      changeIcon.style.display = 'inline-block';
      const iconClass = `fa-solid ${isUp ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down'}`;
      if (changeIcon.className !== iconClass) changeIcon.className = iconClass;
    }
    const absText = `${sign}₹${Math.abs(changeVal).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (changeAbs && changeAbs.textContent !== absText) {
      changeAbs.textContent = absText;
    }
    const pctText = `(${sign}${changePct.toFixed(2)}%)`;
    if (changePctEl && changePctEl.textContent !== pctText) {
      changePctEl.textContent = pctText;
    }

    // Update Chart with candles or chart_points
    const prevLen = cachedCandles ? cachedCandles.length : 0;
    const points = (data.candles && data.candles.length > 0) ? data.candles : (data.chart_points || []);
    cachedCandles = points;
    const newLen = points.length;

    // Immediately update OHLC HUD with the latest candle (removes HUD skeletons!)
    if (points.length > 0) {
      updateOHLCHUD(points[points.length - 1]);
    } else {
      resetOHLCHUD();
    }

    // Real-time anchor: follow newest incoming candle if user was viewing latest edge
    if (zoomVisibleEnd != null && prevLen > 0 && zoomVisibleEnd >= prevLen - 1) {
      const diff = newLen - prevLen;
      if (diff > 0) {
        zoomVisibleStart = Math.min(newLen - 1, zoomVisibleStart + diff);
        zoomVisibleEnd = newLen - 1;
      }
    }

    renderCandleChart();
    updateZoomHint();
  }

  // --- CHANGE STOCK ---
  function changeStock(sym) {
    if (!sym) return;
    const clean = sym.toUpperCase().replace(/\.(NS|BO)$/, '');
    const isUnavail = getEl('unavailable-state') && getEl('unavailable-state').style.display !== 'none';
    const isBuffering = getEl('skeleton-chart-wrapper') && getEl('skeleton-chart-wrapper').style.display !== 'none';
    if (clean === currentSymbol && !isUnavail && !isBuffering) {
      syncPopularChips(currentSymbol);
      return; // Prevent duplicate requests for already loaded stock
    }
    currentSymbol = clean;
    zoomVisibleStart = null;
    zoomVisibleEnd = null;

    // Highlight active popular chip immediately
    syncPopularChips(currentSymbol);

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
    syncPopularChips(currentSymbol);
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
  // Ensure Candlestick Elements are Active
  function ensureCandleElementsActive() {
    const candleWrap = getEl('live-candle-wrapper');
    const ohlcHud = getEl('live-ohlc-hud');
    const candleFooter = getEl('live-candle-footer');
    const zoomControls = getEl('chart-zoom-controls');

    if (candleWrap) candleWrap.style.display = 'block';
    if (ohlcHud) ohlcHud.style.display = 'flex';
    if (candleFooter) candleFooter.style.display = 'flex';
    if (zoomControls) zoomControls.style.display = 'inline-flex';
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
    const height = Math.floor(rect.height || 420);
    if (width <= 0 || height <= 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);

    const ctx = canvas.getContext('2d');
    if (ctx.resetTransform) ctx.resetTransform();
    else ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);

    // Pixel snap helper for crisp 1px lines
    const px = (v) => Math.round(v) + 0.5;

    const isDarkMode = document.body.classList.contains('dark-mode');
    const bgSurface = isDarkMode ? '#0f172a' : '#ffffff';
    const gridColor = isDarkMode ? 'rgba(51, 65, 85, 0.45)' : 'rgba(226, 232, 240, 0.85)';
    const textMuted = isDarkMode ? '#94a3b8' : '#64748b';
    const axisBorderColor = isDarkMode ? '#334155' : '#e2e8f0';

    // Premium TradingView-style candlestick palette
    const greenBull = '#089981';
    const greenBullBorder = '#057a67';
    const redBear = '#f23645';
    const redBearBorder = '#c92534';
    const dojiColor = '#64748b';

    // Clear background
    ctx.fillStyle = bgSurface;
    ctx.fillRect(0, 0, width, height);

    if (!cachedCandles || cachedCandles.length === 0) {
      ctx.fillStyle = textMuted;
      ctx.font = '600 13px "Plus Jakarta Sans", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const mStatus = (window.StockPulse && window.StockPulse.getCachedMarketStatus && window.StockPulse.getCachedMarketStatus()) || (sessionStorage.getItem('stockpulse_cached_market_status') || 'UNKNOWN');
      const msg = mStatus === 'CLOSED'
        ? 'Market Closed — No Active Intraday Trades In Session'
        : 'Loading candlestick market data...';
      ctx.fillText(msg, width / 2, height / 2);
      return;
    }

    const range = getVisibleCandleRange(cachedCandles.length);
    const visibleCandles = cachedCandles.slice(range.start, range.end + 1);
    const nCandles = visibleCandles.length;
    if (nCandles === 0) return;

    const livePrice = currentPriceVal || (cachedCandles[cachedCandles.length - 1].close != null ? cachedCandles[cachedCandles.length - 1].close : (cachedCandles[cachedCandles.length - 1].price || 0));

    const padTop = 20;
    const padBottom = 28;
    const padLeft = 16;
    const padRight = 102;

    const chartW = width - padLeft - padRight;
    const chartTotalH = height - padTop - padBottom;
    const priceAreaH = chartTotalH * 0.77;
    const volAreaH = chartTotalH * 0.20;
    const volAreaTop = padTop + priceAreaH + (chartTotalH * 0.03);

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

      if (cachedSuperTrendSeries && cachedSuperTrendSeries[absIdx]) {
        const s = cachedSuperTrendSeries[absIdx];
        if (s.supertrend != null) {
          matchedSt = { price: Number(s.supertrend), dir: s.direction || 'BUY' };
        }
      }

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
      const cClose = c.close != null ? c.close : (c.price != null ? c.price : 0);
      const low = c.low != null ? c.low : cClose;
      const high = c.high != null ? c.high : cClose;
      if (low < minP) minP = low;
      if (high > maxP) maxP = high;
      if (c.volume && c.volume > maxV) maxV = c.volume;

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

    if (minP === maxP || !isFinite(minP) || !isFinite(maxP)) {
      minP = (livePrice || 100) - 1.0;
      maxP = (livePrice || 100) + 1.0;
    }

    const pSpan = maxP - minP;
    const pPad = Math.max(0.25, pSpan * 0.06);
    const scaleMin = minP - pPad;
    const scaleMax = maxP + pPad;
    const scaleSpan = scaleMax - scaleMin;

    function priceToY(p) {
      return padTop + (1.0 - (p - scaleMin) / scaleSpan) * priceAreaH;
    }

    const candleSlotW = chartW / nCandles;
    const candleW = Math.max(2.5, Math.min(26, candleSlotW * 0.74));

    // 1. Grid Lines & Price Ticks
    const numTicks = 6;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textMuted;
    ctx.font = '600 10.5px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';

    for (let t = 0; t <= numTicks; t++) {
      const pVal = scaleMin + (scaleSpan * t) / numTicks;
      const y = px(priceToY(pVal));

      ctx.beginPath();
      ctx.moveTo(padLeft, y);
      ctx.lineTo(width - padRight, y);
      ctx.stroke();

      ctx.fillText(`₹${pVal.toFixed(2)}`, width - padRight + 8, y);
    }
    ctx.setLineDash([]); // Reset dashed

    // Right Y-axis vertical divider
    ctx.strokeStyle = axisBorderColor;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(px(width - padRight), padTop);
    ctx.lineTo(px(width - padRight), height - padBottom);
    ctx.stroke();

    // Volume Area Divider
    ctx.save();
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(padLeft, px(volAreaTop - 4));
    ctx.lineTo(width - padRight, px(volAreaTop - 4));
    ctx.stroke();
    ctx.restore();

    // Volume label
    ctx.fillStyle = textMuted;
    ctx.font = '600 9.5px "JetBrains Mono", monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    const volMaxStr = maxV >= 1e6 ? `${(maxV / 1e6).toFixed(1)}M` : (maxV >= 1e3 ? `${(maxV / 1e3).toFixed(0)}K` : `${maxV}`);
    ctx.fillText(`VOL (MAX: ${volMaxStr})`, padLeft + 2, volAreaTop - 6);

    // 2. Volume Bars
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const cClose = c.close != null ? c.close : (c.price != null ? c.price : (c.open || 0));
      const open = c.open != null ? c.open : cClose;
      const vH = c.volume ? Math.max(2, (c.volume / maxV) * volAreaH) : 0;
      const vy = volAreaTop + volAreaH - vH;
      const isBull = cClose >= open;

      ctx.fillStyle = isBull ? 'rgba(8, 153, 129, 0.40)' : 'rgba(242, 54, 69, 0.40)';
      ctx.fillRect(Math.round(cx - candleW / 2), Math.round(vy), Math.round(candleW), Math.round(vH));
    }

    // Pre-calculate SuperTrend series & coordinates for visible candles
    const stPoints = [];
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = padLeft + (i + 0.5) * candleSlotW;
      const stItem = visibleStValues[i];
      if (stItem && stItem.price > 0) {
        const cy = priceToY(stItem.price);
        const cClose = c.close != null ? c.close : (c.price != null ? c.price : 0);
        const cHigh = c.high != null ? c.high : cClose;
        const cLow = c.low != null ? c.low : cClose;
        stPoints.push({
          x: cx,
          y: cy,
          price: stItem.price,
          dir: stItem.dir,
          time: c.time,
          cHighY: priceToY(cHigh),
          cLowY: priceToY(cLow)
        });
      }
    }

    // 2b. SuperTrend Shaded Ribbon Fill (behind candlesticks)
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
      if (currSeg.length > 0) segments.push(currSeg);

      ctx.save();
      for (const seg of segments) {
        if (seg.length < 1) continue;
        const isBuy = seg[0].dir === 'BUY';

        ctx.beginPath();
        ctx.moveTo(seg[0].x, seg[0].y);
        for (let k = 1; k < seg.length; k++) {
          ctx.lineTo(seg[k].x, seg[k].y);
        }
        for (let k = seg.length - 1; k >= 0; k--) {
          const pt = seg[k];
          const candleY = isBuy ? Math.min(pt.y, pt.cLowY) : Math.max(pt.y, pt.cHighY);
          ctx.lineTo(pt.x, candleY);
        }
        ctx.closePath();

        ctx.fillStyle = isBuy
          ? (isDarkMode ? 'rgba(8, 153, 129, 0.16)' : 'rgba(8, 153, 129, 0.12)')
          : (isDarkMode ? 'rgba(242, 54, 69, 0.16)' : 'rgba(242, 54, 69, 0.12)');
        ctx.fill();
      }
      ctx.restore();
    }

    // 3. Candlesticks (Wick & Body)
    for (let i = 0; i < nCandles; i++) {
      const c = visibleCandles[i];
      const cx = px(padLeft + (i + 0.5) * candleSlotW);
      const close = c.close != null ? c.close : (c.price != null ? c.price : (c.open || 0));
      const open = c.open != null ? c.open : close;
      const high = c.high != null ? c.high : Math.max(open, close);
      const low = c.low != null ? c.low : Math.min(open, close);

      const isBull = close > open;
      const isBear = close < open;
      const bodyColor = isBull ? greenBull : (isBear ? redBear : dojiColor);
      const borderColor = isBull ? greenBullBorder : (isBear ? redBearBorder : dojiColor);

      const yOpen = priceToY(open);
      const yClose = priceToY(close);
      const yHigh = priceToY(high);
      const yLow = priceToY(low);

      const bodyTop = Math.min(yOpen, yClose);
      const bodyH = Math.max(1.8, Math.abs(yClose - yOpen));

      // Wick (Crisp high-contrast center line)
      ctx.strokeStyle = bodyColor;
      ctx.lineWidth = Math.max(1.2, Math.min(2.5, candleW * 0.16));
      ctx.beginPath();
      ctx.moveTo(cx, yHigh);
      ctx.lineTo(cx, yLow);
      ctx.stroke();

      // Body (Rectangle with border)
      const bx = Math.round(cx - candleW / 2);
      const by = Math.round(bodyTop);
      const bw = Math.round(candleW);
      const bh = Math.round(bodyH);

      ctx.fillStyle = bodyColor;
      ctx.fillRect(bx, by, bw, bh);

      ctx.strokeStyle = borderColor;
      ctx.lineWidth = 1;
      ctx.strokeRect(bx, by, bw, bh);
    }

    // 4. Time Axis Labels & Ticks
    const maxTimeLabels = Math.min(8, nCandles);
    const step = Math.max(1, Math.floor(nCandles / maxTimeLabels));
    ctx.fillStyle = textMuted;
    ctx.font = '600 10px "Plus Jakarta Sans", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    for (let i = 0; i < nCandles; i += step) {
      const cx = px(padLeft + (i + 0.5) * candleSlotW);
      const tLabel = visibleCandles[i].time || '';

      // Subtle bottom axis tick
      ctx.strokeStyle = axisBorderColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, height - padBottom);
      ctx.lineTo(cx, height - padBottom + 4);
      ctx.stroke();

      ctx.fillText(tLabel, cx, height - padBottom + 7);
    }

    // Bottom X-axis horizontal divider
    ctx.strokeStyle = axisBorderColor;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, px(height - padBottom));
    ctx.lineTo(width - padRight, px(height - padBottom));
    ctx.stroke();

    // 5. Live Price Horizontal Line & Glowing Marker
    if (livePrice > 0 && livePrice >= scaleMin && livePrice <= scaleMax) {
      const lpY = px(priceToY(livePrice));

      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = '#2563eb';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(padLeft, lpY);
      ctx.lineTo(width - padRight, lpY);
      ctx.stroke();
      ctx.restore();

      // Right axis live price pill badge
      drawAxisPill(ctx, width - padRight + 4, lpY, 'LIVE', `₹${livePrice.toFixed(2)}`, '#2563eb', '#ffffff');

      // Glowing pulsing dot on newest candle close
      if (range.end === cachedCandles.length - 1) {
        const lastCandleX = padLeft + (nCandles - 1 + 0.5) * candleSlotW;
        ctx.save();
        ctx.fillStyle = 'rgba(37, 99, 235, 0.25)';
        ctx.beginPath();
        ctx.arc(lastCandleX, lpY, 7.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = '#2563eb';
        ctx.beginPath();
        ctx.arc(lastCandleX, lpY, 3.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.restore();
      }
    }

    // 5b. SuperTrend Multi-Colored Solid Line & Reversal Badges
    if (supertrendOverlayEnabled && cachedSuperTrendData && stPoints.length > 0) {
      ctx.save();
      ctx.lineWidth = 2.8;
      ctx.setLineDash([]);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';

      for (let i = 1; i < stPoints.length; i++) {
        const pPrev = stPoints[i - 1];
        const pCurr = stPoints[i];

        // Draw trend flip signal badge
        if (pPrev.dir !== pCurr.dir) {
          const isBuyReversal = pCurr.dir === 'BUY';
          const badgeY = isBuyReversal ? pCurr.cLowY + 16 : pCurr.cHighY - 16;
          drawReversalBadge(ctx, pCurr.x, badgeY, isBuyReversal ? '▲ BUY' : '▼ SELL', isBuyReversal ? greenBull : redBear);
          continue;
        }

        ctx.strokeStyle = pCurr.dir === 'BUY' ? greenBull : redBear;
        ctx.beginPath();
        ctx.moveTo(pPrev.x, pPrev.y);
        ctx.lineTo(pCurr.x, pCurr.y);
        ctx.stroke();
      }
      ctx.restore();

      // Latest SuperTrend Right Axis Pill
      const latestSt = stPoints[stPoints.length - 1];
      if (latestSt && latestSt.price > 0) {
        const rawStY = priceToY(latestSt.price);
        const stY = Math.max(padTop + 10, Math.min(padTop + priceAreaH - 10, rawStY));
        const isBuy = latestSt.dir === 'BUY';
        const stColor = isBuy ? greenBull : redBear;
        drawAxisPill(ctx, width - padRight + 4, stY, isBuy ? 'ST-BUY' : 'ST-SELL', `₹${latestSt.price.toFixed(2)}`, stColor, '#ffffff');
      }
    }

    // 6. Crosshair & Dynamic Cursor Price/Time Tags
    let activeCandle = (hoveredCandleIdx >= 0 && hoveredCandleIdx < nCandles) ? visibleCandles[hoveredCandleIdx] : visibleCandles[nCandles - 1];

    if (hoveredCandleIdx >= 0 && hoveredCandleIdx < nCandles && mouseX != null && mouseY != null && !isPanning) {
      const cx = px(padLeft + (hoveredCandleIdx + 0.5) * candleSlotW);
      const activeClose = activeCandle.close != null ? activeCandle.close : (activeCandle.price || 0);
      const cy = px(priceToY(activeClose));

      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = isDarkMode ? 'rgba(148, 163, 184, 0.7)' : 'rgba(71, 85, 105, 0.7)';
      ctx.lineWidth = 1;

      // Vertical crosshair
      ctx.beginPath();
      ctx.moveTo(cx, padTop);
      ctx.lineTo(cx, height - padBottom);
      ctx.stroke();

      // Horizontal crosshair
      const clampedMouseY = px(Math.max(padTop, Math.min(height - padBottom, mouseY)));
      ctx.beginPath();
      ctx.moveTo(padLeft, clampedMouseY);
      ctx.lineTo(width - padRight, clampedMouseY);
      ctx.stroke();

      // Point circle on active candle close
      const activeOpen = activeCandle.open != null ? activeCandle.open : activeClose;
      ctx.fillStyle = activeClose >= activeOpen ? greenBull : redBear;
      ctx.beginPath();
      ctx.arc(cx, cy, 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
      ctx.restore();

      // Dynamic cursor price pill on right Y-axis
      const cursorPriceVal = scaleMin + (1.0 - (mouseY - padTop) / priceAreaH) * scaleSpan;
      if (mouseY >= padTop && mouseY <= padTop + priceAreaH) {
        drawAxisPill(ctx, width - padRight + 4, clampedMouseY, '', `₹${cursorPriceVal.toFixed(2)}`, isDarkMode ? '#334155' : '#0f172a', '#ffffff');
      }

      // Dynamic cursor time pill on bottom X-axis
      if (activeCandle.time) {
        drawTimePill(ctx, cx, height - padBottom + 13, activeCandle.time, isDarkMode ? '#334155' : '#0f172a', '#ffffff');
      }
    }

    // 7. Update Real-Time OHLC & Change HUD Bar
    if (activeCandle) {
      const hTime = getEl('live-hud-time');
      const hOpen = getEl('live-hud-open');
      const hHigh = getEl('live-hud-high');
      const hLow = getEl('live-hud-low');
      const hClose = getEl('live-hud-close');
      const hChange = getEl('live-hud-change');
      const hVol = getEl('live-hud-vol');

      const closeVal = Number(activeCandle.close != null ? activeCandle.close : (activeCandle.price != null ? activeCandle.price : (currentPriceVal || 0)));
      const openVal = Number(activeCandle.open != null ? activeCandle.open : closeVal);
      const highVal = Number(activeCandle.high != null ? activeCandle.high : Math.max(openVal, closeVal));
      const lowVal = Number(activeCandle.low != null ? activeCandle.low : Math.min(openVal, closeVal));

      const diffVal = closeVal - openVal;
      const pctVal = openVal > 0 ? (diffVal / openVal) * 100 : 0;
      const sign = diffVal >= 0 ? '+' : '';

      if (hTime) hTime.textContent = activeCandle.time || '--:--';
      if (hOpen) hOpen.textContent = `₹${openVal.toFixed(2)}`;
      if (hHigh) hHigh.textContent = `₹${highVal.toFixed(2)}`;
      if (hLow) hLow.textContent = `₹${lowVal.toFixed(2)}`;
      if (hClose) {
        hClose.textContent = `₹${closeVal.toFixed(2)}`;
        hClose.style.color = closeVal >= openVal ? greenBull : redBear;
      }
      if (hChange) {
        hChange.textContent = `${sign}₹${diffVal.toFixed(2)} (${sign}${pctVal.toFixed(2)}%)`;
        hChange.style.color = diffVal >= 0 ? greenBull : redBear;
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
          hSt.innerHTML = `<span style="color: ${isBuy ? greenBull : redBear}; font-weight: 800;">₹${stItem.price.toFixed(2)} <span style="font-size: 10px; padding: 1px 4px; border-radius: 4px; background: ${isBuy ? 'rgba(8, 153, 129, 0.15)' : 'rgba(242, 54, 69, 0.15)'};">${stItem.dir}</span></span>`;
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

    // Zoom badge in corner
    if (range.isZoomed) {
      ctx.save();
      ctx.fillStyle = isDarkMode ? 'rgba(30, 41, 59, 0.85)' : 'rgba(241, 245, 249, 0.90)';
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

  // --- AXIS PILL BADGE HELPERS ---
  function drawAxisPill(ctx, x, y, tagText, priceText, bgColor, textColor) {
    ctx.save();

    // 1. Explicitly reset canvas text alignment & smoothing state
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.setLineDash([]);

    const hasTag = Boolean(tagText && tagText.trim().length > 0);
    const cleanTag = hasTag ? tagText.trim() : '';

    // 2. Measure tag badge and price text with dedicated fonts
    ctx.font = '800 8.5px "JetBrains Mono", monospace';
    const tagTextW = hasTag ? ctx.measureText(cleanTag).width : 0;
    const tagBadgeW = hasTag ? Math.round(tagTextW + 8) : 0;

    ctx.font = '800 11px "JetBrains Mono", monospace';
    const priceTextW = ctx.measureText(priceText).width;

    // 3. Compute clean pill geometry
    const padX = 7;
    const tagGap = hasTag ? 6 : 0;
    const totalContentW = (hasTag ? tagBadgeW + tagGap : 0) + priceTextW;
    const pillW = Math.max(72, Math.round(totalContentW + padX * 2));
    const pillH = 22; // Comfortable height for 11px font
    const pillY = Math.round(y - pillH / 2);

    // 4. Ensure pill never bleeds off canvas edges
    const canvasLogicalW = ctx.canvas.width / (window.devicePixelRatio || 1);
    const canvasLogicalH = ctx.canvas.height / (window.devicePixelRatio || 1);
    const startX = Math.max(4, Math.min(x, canvasLogicalW - pillW - 2));
    const startY = Math.max(2, Math.min(canvasLogicalH - pillH - 2, pillY));
    const centerY = Math.round(startY + pillH / 2);

    // 5. Draw subtle drop shadow for depth and contrast
    ctx.shadowColor = 'rgba(0, 0, 0, 0.22)';
    ctx.shadowBlur = 4;
    ctx.shadowOffsetX = 1;
    ctx.shadowOffsetY = 1;

    // 6. Draw outer rounded pill
    ctx.fillStyle = bgColor;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(startX, startY, pillW, pillH, 5);
    } else {
      ctx.rect(startX, startY, pillW, pillH);
    }
    ctx.fill();

    // 7. Reset shadow before rendering text and inner elements
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;

    // Crisp high-contrast pill outline
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
    ctx.lineWidth = 1;
    ctx.stroke();

    let curX = startX + padX;

    // 8. Draw inner Tag badge (e.g. "LIVE", "ST-BUY", "ST-SELL")
    if (hasTag) {
      const tagBadgeH = 14;
      const tagBadgeY = Math.round(centerY - tagBadgeH / 2);

      ctx.fillStyle = 'rgba(255, 255, 255, 0.22)';
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(curX, tagBadgeY, tagBadgeW, tagBadgeH, 3);
      } else {
        ctx.rect(curX, tagBadgeY, tagBadgeW, tagBadgeH);
      }
      ctx.fill();

      ctx.fillStyle = '#ffffff';
      ctx.font = '800 8.5px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(cleanTag, Math.round(curX + tagBadgeW / 2), centerY + 0.5);

      curX += tagBadgeW + tagGap;
    }

    // 9. Draw Price text with perfect left alignment
    ctx.textAlign = 'left';
    ctx.fillStyle = textColor;
    ctx.font = '800 11px "JetBrains Mono", monospace';
    ctx.fillText(priceText, Math.round(curX), centerY + 0.5);

    ctx.restore();
  }

  function drawTimePill(ctx, x, y, timeText, bgColor, textColor) {
    ctx.save();
    ctx.font = '700 10px "JetBrains Mono", monospace';
    const textW = ctx.measureText(timeText).width;
    const boxW = textW + 12;
    const boxH = 18;
    const boxX = x - boxW / 2;
    const boxY = y - boxH / 2;

    ctx.fillStyle = bgColor;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(boxX, boxY, boxW, boxH, 4);
    else ctx.rect(boxX, boxY, boxW, boxH);
    ctx.fill();

    ctx.fillStyle = textColor;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(timeText, x, y);
    ctx.restore();
  }

  function drawReversalBadge(ctx, x, y, text, color) {
    ctx.save();
    ctx.font = '800 9.5px "JetBrains Mono", monospace';
    const tW = ctx.measureText(text).width;
    const bW = tW + 10;
    const bH = 16;
    const bX = x - bW / 2;
    const bY = y - bH / 2;

    ctx.fillStyle = color;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(bX, bY, bW, bH, 4);
    else ctx.rect(bX, bY, bW, bH);
    ctx.fill();

    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y);
    ctx.restore();
  }

  // --- INITIALIZATION ---
  function init() {
    syncPopularChips(currentSymbol);
    ensureCandleElementsActive();
    setupCandleChartEvents();
    setupSuperTrendControls();
    setupSearch();
    setupTimeRanges();

    const btnRetry = getEl('btn-retry-live');
    if (btnRetry) {
      btnRetry.addEventListener('click', () => {
        fetchStockData(true);
      });
    }

    fetchStockData(true);

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        const elapsed = Date.now() - lastFetchTimestamp;
        const threshold = (currentMarketStatus === 'OPEN') ? 3000 : 30000;
        if (elapsed >= threshold) {
          fetchStockData(false);
        } else {
          scheduleNextPoll(threshold - elapsed);
        }
      } else {
        if (pollingTimer) {
          clearTimeout(pollingTimer);
          pollingTimer = null;
        }
      }
    });

    window.addEventListener('beforeunload', () => {
      if (pollingTimer) {
        clearTimeout(pollingTimer);
        pollingTimer = null;
      }
      if (activeFetchController) {
        try { activeFetchController.abort(); } catch (e) {}
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  if (window.StockPulse) {
    window.StockPulse.changeStock = changeStock;
    window.StockPulse.fetchStockData = fetchStockData;
  }
  window.changeStock = changeStock;

})();
