/**
 * StockPulse — Stock Details & Fundamentals Page
 */

(function () {
  'use strict';

  let currentStock = (window.StockPulse && window.StockPulse.getSelectedStock) ? window.StockPulse.getSelectedStock() : 'TCS';

  const el = {
    stockSelect: document.getElementById('details-stock-select'),
    symbolBadge: document.getElementById('details-symbol'),
    companyName: document.getElementById('details-company-name'),
    exchangeBadge: document.getElementById('details-exchange'),
    tradingStatus: document.getElementById('details-trading-status'),
    lastUpdated: document.getElementById('details-last-updated'),
    currentPrice: document.getElementById('details-current-price'),
    priceChangeBadge: document.getElementById('details-price-change-badge'),
    priceChangeText: document.getElementById('details-price-change-text'),
    prevCloseRef: document.getElementById('details-prev-close-ref'),
    // Stats grid
    statOpen: document.getElementById('stat-open'),
    statPrevClose: document.getElementById('stat-prev-close'),
    statDayHigh: document.getElementById('stat-day-high'),
    statDayLow: document.getElementById('stat-day-low'),
    statDayRange: document.getElementById('stat-day-range'),
    dayRangeFill: document.getElementById('stat-day-range-fill'),
    statVolume: document.getElementById('stat-volume'),
    statAvgVol: document.getElementById('stat-avg-vol'),
    stat52High: document.getElementById('stat-52w-high'),
    stat52Low: document.getElementById('stat-52w-low'),
    stat52Range: document.getElementById('stat-52w-range'),
    stat52RangeFill: document.getElementById('stat-52w-range-fill'),
    statMarketCap: document.getElementById('stat-market-cap'),
    statExchange: document.getElementById('stat-exchange'),
    statCurrency: document.getElementById('stat-currency'),
    statDataStatus: document.getElementById('stat-data-status'),
    // Skeletons, Refreshing and Error states
    refreshingBadge: document.getElementById('details-refreshing-badge'),
    unavailableState: document.getElementById('details-unavailable-state'),
    detailsErrorText: document.getElementById('details-error-text'),
    btnRetry: document.getElementById('btn-retry-details'),
    summaryCard: document.getElementById('details-summary-card'),
    detailsSection: document.querySelector('.stock-details-section'),
    // Links
    btnGoLive: document.getElementById('btn-go-live'),
    btnGoMonitoring: document.getElementById('btn-go-monitoring')
  };

  const detailsClientCache = new Map();
  const DETAILS_CACHE_TTL_MS = 6000;
  let activeDetailsSeq = 0;
  let activeDetailsAbortController = null;

  function setDetailsLoadingState() {
    const loader = (w, h) => `<span class="buffering-loader skeleton-shimmer" style="width: ${w || '65px'}; height: ${h || '16px'};"></span>`;
    if (el.unavailableState) el.unavailableState.style.display = 'none';
    if (el.summaryCard) el.summaryCard.style.display = '';
    if (el.detailsSection) el.detailsSection.style.display = '';

    if (el.symbolBadge) el.symbolBadge.innerHTML = loader('80px', '28px');
    if (el.companyName) el.companyName.innerHTML = loader('220px', '18px');
    if (el.exchangeBadge) el.exchangeBadge.innerHTML = loader('44px', '16px');
    if (el.tradingStatus) el.tradingStatus.innerHTML = loader('75px', '16px');
    if (el.lastUpdated) el.lastUpdated.innerHTML = loader('70px', '14px');
    if (el.currentPrice) el.currentPrice.innerHTML = loader('130px', '34px');
    if (el.priceChangeText) el.priceChangeText.innerHTML = loader('100px', '18px');
    if (el.prevCloseRef) el.prevCloseRef.innerHTML = loader('120px', '15px');

    if (el.statOpen) el.statOpen.innerHTML = loader('65px', '16px');
    if (el.statPrevClose) el.statPrevClose.innerHTML = loader('65px', '16px');
    if (el.statDayHigh) el.statDayHigh.innerHTML = loader('65px', '16px');
    if (el.statDayLow) el.statDayLow.innerHTML = loader('65px', '16px');
    if (el.statDayRange) el.statDayRange.innerHTML = loader('65px', '16px');
    if (el.statVolume) el.statVolume.innerHTML = loader('65px', '16px');
    if (el.statAvgVol) el.statAvgVol.innerHTML = loader('65px', '16px');
    if (el.stat52High) el.stat52High.innerHTML = loader('65px', '16px');
    if (el.stat52Low) el.stat52Low.innerHTML = loader('65px', '16px');
    if (el.stat52Range) el.stat52Range.innerHTML = loader('65px', '16px');
    if (el.statMarketCap) el.statMarketCap.innerHTML = loader('65px', '16px');
    if (el.statExchange) el.statExchange.innerHTML = loader('65px', '16px');
    if (el.statCurrency) el.statCurrency.innerHTML = loader('65px', '16px');
    if (el.statDataStatus) el.statDataStatus.innerHTML = loader('65px', '16px');
  }

  async function loadStockDetails(symbol, isInitial = false) {
    if (!symbol) return;
    currentStock = StockPulse.setSelectedStock(symbol);
    const seq = ++activeDetailsSeq;

    // Check client cache for Stale-While-Revalidate
    const cached = detailsClientCache.get(symbol);
    const now = Date.now();
    const hasFreshCache = cached && (now - cached.timestamp < DETAILS_CACHE_TTL_MS);

    if (hasFreshCache && !isInitial) {
      // Re-use fresh cache directly
      renderDetails(cached.data);
      if (el.refreshingBadge) el.refreshingBadge.style.display = 'none';
      return;
    }

    if (cached) {
      // Stale-While-Revalidate: render cached data immediately, then background refresh
      renderDetails(cached.data);
      if (el.refreshingBadge) el.refreshingBadge.style.display = 'inline-flex';
    } else {
      // Uncached: show skeleton buffering state
      setDetailsLoadingState();
      if (el.refreshingBadge) el.refreshingBadge.style.display = 'none';
    }

    if (activeDetailsAbortController) {
      activeDetailsAbortController.abort();
    }
    activeDetailsAbortController = new AbortController();

    try {
      const res = await fetch(`/api/stocks/${currentStock}?range=1D`, {
        signal: activeDetailsAbortController.signal
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      if (seq !== activeDetailsSeq) {
        return; // Stale request, ignore response
      }

      detailsClientCache.set(symbol, { data: data, timestamp: Date.now() });
      if (el.unavailableState) el.unavailableState.style.display = 'none';
      if (el.refreshingBadge) el.refreshingBadge.style.display = 'none';
      renderDetails(data);
    } catch (err) {
      if (err.name === 'AbortError') return;
      if (seq !== activeDetailsSeq) return;

      console.warn('[StockDetails] Error loading details:', err);
      if (el.refreshingBadge) el.refreshingBadge.style.display = 'none';

      // If we don't have cached data displayed, show the error state
      if (!detailsClientCache.has(symbol)) {
        if (el.summaryCard) el.summaryCard.style.display = 'none';
        if (el.detailsSection) el.detailsSection.style.display = 'none';
        if (el.unavailableState) el.unavailableState.style.display = 'block';
        if (el.detailsErrorText) {
          el.detailsErrorText.textContent = `Could not retrieve market quotes for ${symbol}. Please check your connection or retry.`;
        }
      }
    }
  }

  function renderDetails(data) {
    if (!data) return;

    if (el.symbolBadge) el.symbolBadge.textContent = data.symbol || currentStock;
    if (el.companyName) el.companyName.textContent = data.name || `${currentStock} Ltd`;
    if (el.exchangeBadge) el.exchangeBadge.textContent = data.exchange || 'NSE';
    if (el.lastUpdated) {
      const syncTime = data.last_updated || new Date().toLocaleTimeString('en-IN');
      const isClosed = data.market_status === 'CLOSED' || data.data_status === 'CLOSED';
      if (isClosed && data.market_last_traded) {
        el.lastUpdated.textContent = `${syncTime} (Close: ${data.market_last_traded})`;
      } else {
        el.lastUpdated.textContent = syncTime;
      }
    }

    const price = data.price || 0;
    const change = data.price_change || 0;
    const changePct = data.change_pct || 0;
    const prevClose = data.previous_close || 0;
    const isUp = change >= 0;
    const sign = isUp ? '+' : '';

    if (el.currentPrice) {
      el.currentPrice.textContent = `₹${price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    if (el.priceChangeBadge) {
      el.priceChangeBadge.className = `change-badge ${isUp ? 'up' : 'down'}`;
    }
    if (el.priceChangeText) {
      el.priceChangeText.textContent = `${sign}₹${Math.abs(change).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${sign}${changePct.toFixed(2)}%)`;
    }
    if (el.prevCloseRef) {
      el.prevCloseRef.textContent = `vs prev close ₹${prevClose.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }

    // Grid stats
    const openVal = data.open || prevClose;
    const dayHigh = data.day_high || price;
    const dayLow = data.day_low || price;
    const vol = data.volume || '--';
    const avgVol = data.avg_volume || '--';
    const high52 = data.fifty_two_week_high || (price * 1.2);
    const low52 = data.fifty_two_week_low || (price * 0.8);
    const mcap = data.market_cap || '--';

    if (el.statOpen) el.statOpen.textContent = `₹${openVal.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    if (el.statPrevClose) el.statPrevClose.textContent = `₹${prevClose.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    if (el.statDayHigh) el.statDayHigh.textContent = `₹${dayHigh.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    if (el.statDayLow) el.statDayLow.textContent = `₹${dayLow.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    
    // Intraday range %
    const dayRangeDiff = dayHigh - dayLow;
    if (el.statDayRange) el.statDayRange.textContent = `₹${dayRangeDiff.toFixed(2)}`;
    if (el.dayRangeFill) {
      const dayPct = dayRangeDiff > 0 ? Math.min(Math.max(((price - dayLow) / dayRangeDiff) * 100, 2), 100) : 50;
      el.dayRangeFill.style.width = `${dayPct}%`;
    }

    if (el.statVolume) el.statVolume.textContent = vol;
    if (el.statAvgVol) el.statAvgVol.textContent = avgVol;
    if (el.stat52High) el.stat52High.textContent = `₹${high52.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;
    if (el.stat52Low) el.stat52Low.textContent = `₹${low52.toLocaleString('en-IN', { minimumFractionDigits: 2 })}`;

    // 52-week position %
    const range52Diff = high52 - low52;
    if (el.stat52Range) el.stat52Range.textContent = `₹${range52Diff.toFixed(2)}`;
    if (el.stat52RangeFill) {
      const pos52 = range52Diff > 0 ? Math.min(Math.max(((price - low52) / range52Diff) * 100, 2), 100) : 50;
      el.stat52RangeFill.style.width = `${pos52}%`;
    }

    if (el.statMarketCap) el.statMarketCap.textContent = mcap;
    if (el.statExchange) el.statExchange.textContent = data.exchange || 'NSE';
    if (el.statCurrency) el.statCurrency.textContent = `${data.exchange || 'NSE'} (${data.currency_symbol === '₹' ? 'INR ₹' : 'USD $'})`;
    if (el.statDataStatus) el.statDataStatus.textContent = data.data_status || 'LIVE';
  }

  document.addEventListener('DOMContentLoaded', () => {
    if (window.StockPulse && window.StockPulse.populateStockDropdown) {
      StockPulse.populateStockDropdown(el.stockSelect, (newSym) => {
        if (newSym === currentStock && !el.unavailableState || el.unavailableState.style.display !== 'none') {
          // If already on this stock and not in error, don't refetch unnecessarily
        }
        loadStockDetails(newSym, true);
      });
    }

    if (el.btnRetry) {
      el.btnRetry.addEventListener('click', () => {
        loadStockDetails(currentStock, true);
      });
    }

    loadStockDetails(currentStock, true);
    setInterval(() => loadStockDetails(currentStock, false), 6000);
  });

})();
