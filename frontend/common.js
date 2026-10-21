/**
 * StockPulse — Shared Frontend Core & Notification Sound/Popup Engine
 * Handles persistent state, navigation pills, customized popup HUD cards,
 * Web Audio API chime synthesis, desktop OS notifications, and live status.
 */

window.getEl = function (id) {
  return document.getElementById(id);
};

window.StockPulse = (function () {
  'use strict';

  // Shared state across all separate pages
  const STORAGE_KEY_STOCK = 'stockpulse_selected_stock';
  const STORAGE_KEY_USER = 'stockpulse_user_profile';
  const STORAGE_KEY_TOKEN = 'stockpulse_jwt_token';
  const STORAGE_KEY_SOUND = 'stockpulse_sound_enabled';

  // Determine if current page is the login / register page
  const currentPath = window.location.pathname.toLowerCase();
  const isAuthPage = currentPath.startsWith('/login') ||
                     currentPath.startsWith('/register') ||
                     currentPath.startsWith('/signin');

  // Helper to read cookies safely
  function getCookie(name) {
    try {
      const value = `; ${document.cookie}`;
      const parts = value.split(`; ${name}=`);
      if (parts.length === 2) return parts.pop().split(';').shift();
    } catch (e) {}
    return null;
  }

  // --- AUTOMATIC SESSION TIMEOUT & AUTO LOGOUT ---
  const STORAGE_KEY_SESSION_TIMEOUT = 'stockpulse_session_timeout_minutes';
  const STORAGE_KEY_LAST_ACTIVE = 'stockpulse_last_active_ts';
  const DEFAULT_SESSION_TIMEOUT_MINUTES = 1440; // 24 hours inactivity timeout before auto-logout

  function getSessionTimeoutMinutes() {
    const val = localStorage.getItem(STORAGE_KEY_SESSION_TIMEOUT);
    if (!val) return DEFAULT_SESSION_TIMEOUT_MINUTES;
    const num = parseInt(val, 10);
    return isNaN(num) || num <= 0 ? DEFAULT_SESSION_TIMEOUT_MINUTES : num;
  }

  function setSessionTimeoutMinutes(mins) {
    const m = Math.max(1, parseInt(mins, 10) || DEFAULT_SESSION_TIMEOUT_MINUTES);
    localStorage.setItem(STORAGE_KEY_SESSION_TIMEOUT, m.toString());
  }

  function recordUserActivity() {
    localStorage.setItem(STORAGE_KEY_LAST_ACTIVE, Date.now().toString());
  }

  function performAutoLogout(reason = 'session_timeout') {
    localStorage.removeItem(STORAGE_KEY_TOKEN);
    localStorage.removeItem(STORAGE_KEY_USER);
    localStorage.removeItem(STORAGE_KEY_LAST_ACTIVE);
    // Invalidate cookie
    document.cookie = 'stockpulse_jwt_token=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; SameSite=Lax';

    // Best-effort notify backend
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/auth/logout');
      } else {
        fetch('/api/auth/logout', { method: 'POST', keepalive: true }).catch(() => {});
      }
    } catch (e) {}

    if (reason === 'manual_logout') {
      window.location.replace('/login?logout=true');
    } else {
      renderNavbarUserPill();
    }
  }

  function checkSessionTimeout() {
    if (isAuthPage) return false;
    const token = getStoredToken();
    if (!token) return false;

    const lastActiveStr = localStorage.getItem(STORAGE_KEY_LAST_ACTIVE);
    if (!lastActiveStr) {
      // First visit: initialize last active timestamp
      recordUserActivity();
      return false;
    }

    const lastActive = parseInt(lastActiveStr, 10);
    if (isNaN(lastActive) || lastActive <= 0) {
      recordUserActivity();
      return false;
    }

    const timeoutMinutes = getSessionTimeoutMinutes();
    const timeoutMs = timeoutMinutes * 60 * 1000;
    const elapsed = Date.now() - lastActive;

    if (elapsed > timeoutMs) {
      console.warn(`[StockPulse Session] Closed/inactive for ${(elapsed / 60000).toFixed(1)}m > ${timeoutMinutes}m timeout. Auto logging out.`);
      performAutoLogout('session_timeout');
      return true;
    }

    // Refresh active timestamp
    recordUserActivity();
    return false;
  }

  // Retrieve JWT token from either localStorage or HTTP cookie (bidirectional sync)
  function getStoredToken() {
    let t = localStorage.getItem(STORAGE_KEY_TOKEN);
    if (!t) {
      t = getCookie(STORAGE_KEY_TOKEN);
      if (t) {
        localStorage.setItem(STORAGE_KEY_TOKEN, t);
      }
    }
    return t;
  }

  // 1. Check session timeout quietly upon opening site
  checkSessionTimeout();

  // 2. Open access: authentication session requirement removed
  // Users can freely browse /live, /monitoring, /details, /history, /notifications without logging in.

  // 3. User activity & background timeout watcher
  if (!isAuthPage) {
    let lastRecord = 0;
    const onUserActive = () => {
      const now = Date.now();
      if (now - lastRecord > 10000) {
        lastRecord = now;
        recordUserActivity();
      }
    };

    ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'].forEach(evt => {
      window.addEventListener(evt, onUserActive, { passive: true });
    });

    // Record exact closing time when site / tab is closed or hidden
    window.addEventListener('beforeunload', recordUserActivity);
    window.addEventListener('pagehide', recordUserActivity);

    // When tab becomes visible again or regains focus, check session timeout immediately
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        checkSessionTimeout();
      } else {
        recordUserActivity();
      }
    });

    window.addEventListener('focus', () => {
      checkSessionTimeout();
    });

    // Background interval check every 15s while tab is open
    setInterval(() => {
      checkSessionTimeout();
    }, 15000);
  }

  // Intercept window.fetch to automatically include JWT Authorization header and handle session expiry
  if (!window._stockpulseFetchWrapped) {
    window._stockpulseFetchWrapped = true;
    const originalFetch = window.fetch;
    window.fetch = async function (resource, init = {}) {
      const token = getStoredToken();
      if (token) {
        init = init || {};
        init.headers = init.headers || {};
        if (init.headers instanceof Headers) {
          if (!init.headers.has('Authorization')) {
            init.headers.set('Authorization', 'Bearer ' + token);
          }
        } else if (Array.isArray(init.headers)) {
          if (!init.headers.some(([k]) => k.toLowerCase() === 'authorization')) {
            init.headers.push(['Authorization', 'Bearer ' + token]);
          }
        } else {
          if (!init.headers['Authorization']) {
            init.headers['Authorization'] = 'Bearer ' + token;
          }
        }
      }

      const response = await originalFetch(resource, init);

      // Handle 401 Unauthorized quietly without disruptive full-page redirects
      if (response.status === 401 && !isAuthPage) {
        const urlStr = typeof resource === 'string' ? resource : (resource.url || '');
        if (urlStr.includes('/api/auth/me') || urlStr.includes('/api/alerts') || urlStr.includes('/api/notifications')) {
          localStorage.removeItem(STORAGE_KEY_TOKEN);
          localStorage.removeItem(STORAGE_KEY_USER);
          renderNavbarUserPill();
        }
      }
      return response;
    };
  }

  // IST timezone offset: UTC+5:30
  const IST_LOCALE = 'en-IN';
  const IST_TZ = 'Asia/Kolkata';

  /**
   * formatIST — Converts any backend UTC datetime string to IST display string.
   * Backend stores datetimes with datetime.utcnow() — no 'Z' suffix.
   * We append 'Z' to force correct UTC parsing before converting to IST.
   *
   * @param {string|null} utcStr  - ISO datetime string from backend (naive UTC)
   * @param {'time'|'datetime'|'date'|'short'} mode - display format
   * @returns {string} Formatted IST time string
   */
  function formatIST(utcStr, mode) {
    if (!utcStr) return '--:--:--';
    try {
      // Append 'Z' only if no timezone info is present
      const normalized = /[Zz]$|[+-]\d{2}:\d{2}$/.test(utcStr) ? utcStr : utcStr + 'Z';
      const d = new Date(normalized);
      if (isNaN(d.getTime())) return '--:--:--';

      const opts = { timeZone: IST_TZ };
      if (mode === 'date') {
        return d.toLocaleDateString(IST_LOCALE, { ...opts, day: '2-digit', month: 'short', year: 'numeric' });
      } else if (mode === 'datetime') {
        return d.toLocaleString(IST_LOCALE, { ...opts, day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
      } else if (mode === 'short') {
        return d.toLocaleString(IST_LOCALE, { ...opts, day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
      } else {
        // default: 'time'
        return d.toLocaleTimeString(IST_LOCALE, { ...opts, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
      }
    } catch (e) {
      return '--:--:--';
    }
  }

  // Persistent tracking of known notification IDs across page changes and refreshes
  const SESSION_SEEN_KEY = 'stockpulse_seen_notif_ids';
  function initKnownNotificationIds() {
    const s = new Set();
    try {
      const stored = sessionStorage.getItem(SESSION_SEEN_KEY);
      if (stored) {
        const arr = JSON.parse(stored);
        if (Array.isArray(arr)) {
          arr.forEach(id => s.add(id));
        }
      }
    } catch (e) { }
    return s;
  }
  const knownNotificationIds = initKnownNotificationIds();
  let isFirstNotifCheck = true;

  function markNotificationAsKnown(id) {
    knownNotificationIds.add(id);
    try {
      const arr = Array.from(knownNotificationIds).slice(-300);
      sessionStorage.setItem(SESSION_SEEN_KEY, JSON.stringify(arr));
    } catch (e) { }
  }

  // Deduplicate popup events to ensure only one popup appears per event (key -> timestamp)
  const recentPopups = new Map();

  // --- USER SESSION & ISOLATED PREFERENCES ---
  function getUser() {
    try {
      const data = localStorage.getItem(STORAGE_KEY_USER);
      return data ? JSON.parse(data) : null;
    } catch (e) {
      return null;
    }
  }

  function setUser(user) {
    if (!user) {
      localStorage.removeItem(STORAGE_KEY_USER);
    } else {
      localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user));
    }
  }

  function getUserPrefKey(baseKey) {
    const user = getUser();
    if (user && user.id) {
      return `${baseKey}_u${user.id}`;
    }
    return baseKey;
  }

  // --- AUDIO SYNTHESIZER (WEB AUDIO API) ---
  let audioCtx = null;

  function isSoundEnabled() {
    const key = getUserPrefKey(STORAGE_KEY_SOUND);
    const val = localStorage.getItem(key);
    if (val !== null) return val === 'true';
    const legacyVal = localStorage.getItem(STORAGE_KEY_SOUND);
    return legacyVal === null ? true : legacyVal === 'true';
  }

  function setSoundEnabled(enabled) {
    localStorage.setItem(getUserPrefKey(STORAGE_KEY_SOUND), enabled ? 'true' : 'false');
  }

  function getAudioContext() {
    if (!audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        audioCtx = new AudioContextClass();
      }
    }
    if (audioCtx && audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {});
    }
    return audioCtx;
  }

  // Auto-unlock audio context on first user click or keypress
  function setupAudioUnlock() {
    const unlock = () => {
      const ctx = getAudioContext();
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      document.removeEventListener('click', unlock);
      document.removeEventListener('keydown', unlock);
      document.removeEventListener('touchstart', unlock);
    };
    document.addEventListener('click', unlock, { once: true });
    document.addEventListener('keydown', unlock, { once: true });
    document.addEventListener('touchstart', unlock, { once: true });
  }

  function playNotificationSound(eventType) {
    if (!isSoundEnabled()) return;
    try {
      const ctx = getAudioContext();
      if (!ctx) return;
      if (ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }

      const now = ctx.currentTime;
      const ev = (eventType || 'INFO').toUpperCase();
      const isTargetHit = ev === 'TARGET_TRIGGERED' || ev === 'SUCCESS';
      const isEarlyWarning = ev === 'EARLY_WARNING' || ev === 'MONITORING_STARTED';
      const isPredictionFailed = ev === 'PREDICTION_FAILED' || ev === 'MONITORING_EXPIRED';
      const isStopLoss = ev === 'STOP_LOSS_TRIGGERED';
      const isMilestone = ev === 'HALFWAY_MILESTONE';
      const isSuperTrendBuy = ev === 'SUPERTREND_BUY';
      const isSuperTrendSell = ev === 'SUPERTREND_SELL';
      const isSuperTrendApproaching = ev === 'SUPERTREND_APPROACHING';
      const isSuperTrendFalse = ev === 'SUPERTREND_FALSE';
      const isSuperTrendChanged = ev === 'SUPERTREND_CHANGED';

      if (isSuperTrendBuy) {
        // High-Energy Ascending Harmonic Quad (F5 -> A5 -> C6 -> E6) for BUY signal
        const notes = [698.46, 880.00, 1046.50, 1318.51];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.setValueAtTime(freq, now + idx * 0.09);
          gain.gain.setValueAtTime(0.0001, now + idx * 0.09);
          gain.gain.exponentialRampToValueAtTime(0.38, now + idx * 0.09 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.09 + 0.45);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + idx * 0.09);
          osc.stop(now + idx * 0.09 + 0.50);
        });
      } else if (isSuperTrendSell) {
        // Dramatic Descending Warning Staccato (A5 -> F5 -> D5 -> Bb4) for SELL signal
        const notes = [880.00, 698.46, 587.33, 466.16];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sawtooth';
          osc.frequency.setValueAtTime(freq, now + idx * 0.11);
          gain.gain.setValueAtTime(0.0001, now + idx * 0.11);
          gain.gain.exponentialRampToValueAtTime(0.32, now + idx * 0.11 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.11 + 0.35);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + idx * 0.11);
          osc.stop(now + idx * 0.11 + 0.38);
        });
      } else if (isSuperTrendApproaching) {
        // Crisp Dual Sonar Ping (C6 -> E6) for proximity warning
        const notes = [1046.50, 1318.51];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.setValueAtTime(freq, now + idx * 0.13);
          gain.gain.setValueAtTime(0.0001, now + idx * 0.13);
          gain.gain.exponentialRampToValueAtTime(0.30, now + idx * 0.13 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.13 + 0.30);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + idx * 0.13);
          osc.stop(now + idx * 0.13 + 0.35);
        });
      } else if (isSuperTrendFalse) {
        // Low-Tone Descending Triple Ping (G5 -> E5 -> C5) for Model Prediction False
        const notes = [783.99, 659.25, 523.25];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sawtooth';
          osc.frequency.setValueAtTime(freq, now + idx * 0.12);
          gain.gain.setValueAtTime(0.0001, now + idx * 0.12);
          gain.gain.exponentialRampToValueAtTime(0.26, now + idx * 0.12 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.12 + 0.32);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + idx * 0.12);
          osc.stop(now + idx * 0.12 + 0.35);
        });
      } else if (isSuperTrendChanged) {
        // Dual-tone Shift Chime (E5 -> A5)
        const notes = [659.25, 880.00];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'triangle';
          osc.frequency.setValueAtTime(freq, now + idx * 0.12);
          gain.gain.setValueAtTime(0.0001, now + idx * 0.12);
          gain.gain.exponentialRampToValueAtTime(0.32, now + idx * 0.12 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.12 + 0.35);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + idx * 0.12);
          osc.stop(now + idx * 0.12 + 0.38);
        });
      } else if (isStopLoss) {
        // High-Urgency Triple Siren Pulse (B5 -> G5 -> E5)
        const notes = [987.77, 783.99, 659.25, 523.25];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sawtooth';
          osc.frequency.setValueAtTime(freq, now + idx * 0.12);

          gain.gain.setValueAtTime(0.0001, now + idx * 0.12);
          gain.gain.exponentialRampToValueAtTime(0.35, now + idx * 0.12 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.12 + 0.35);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + idx * 0.12);
          osc.stop(now + idx * 0.12 + 0.38);
        });
      } else if (isMilestone) {
        // Double Bright Chime for 50% Milestone (E5 -> B5)
        const notes = [659.25, 987.77];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.setValueAtTime(freq, now + idx * 0.14);

          gain.gain.setValueAtTime(0.0001, now + idx * 0.14);
          gain.gain.exponentialRampToValueAtTime(0.32, now + idx * 0.14 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.14 + 0.5);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + idx * 0.14);
          osc.stop(now + idx * 0.14 + 0.55);
        });
      } else if (isTargetHit) {
        // 4-Note Ascending Harmonic Bell Chime (C5, E5, G5, C6)
        const notes = [523.25, 659.25, 783.99, 1046.50];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.setValueAtTime(freq, now + idx * 0.11);

          gain.gain.setValueAtTime(0.0001, now + idx * 0.11);
          gain.gain.exponentialRampToValueAtTime(0.35, now + idx * 0.11 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.11 + 0.55);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + idx * 0.11);
          osc.stop(now + idx * 0.11 + 0.6);
        });
      } else if (isPredictionFailed) {
        // 3-Note Descending Horizon Elapsed Chime (F5 -> D5 -> B4)
        const notes = [698.46, 587.33, 493.88];
        notes.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sawtooth';
          osc.frequency.setValueAtTime(freq, now + idx * 0.13);

          gain.gain.setValueAtTime(0.0001, now + idx * 0.13);
          gain.gain.exponentialRampToValueAtTime(0.22, now + idx * 0.13 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.13 + 0.35);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + idx * 0.13);
          osc.stop(now + idx * 0.13 + 0.38);
        });
      } else if (isEarlyWarning) {
        // Dual-Pulse Attention Radar Alert (A5 -> D6)
        const pulses = [880.0, 1174.66];
        pulses.forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'triangle';
          osc.frequency.setValueAtTime(freq, now + idx * 0.15);

          gain.gain.setValueAtTime(0.0001, now + idx * 0.15);
          gain.gain.exponentialRampToValueAtTime(0.32, now + idx * 0.15 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.15 + 0.4);

          osc.connect(gain);
          gain.connect(ctx.destination);

          osc.start(now + idx * 0.15);
          osc.stop(now + idx * 0.15 + 0.45);
        });
      } else {
        // Gentle Notification Ping (E5 -> A5)
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(659.25, now);

        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.28, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.5);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now);
        osc.stop(now + 0.55);
      }
    } catch (err) {
      console.warn('[StockPulse Audio] Error generating notification sound:', err);
    }
  }

  // --- AI VOICE ANNOUNCER (WEB SPEECH API TTS) ---
  const STORAGE_KEY_VOICE = 'stockpulse_voice_enabled';

  function isVoiceEnabled() {
    const key = getUserPrefKey(STORAGE_KEY_VOICE);
    const val = localStorage.getItem(key);
    if (val !== null) return val === 'true';
    const legacyVal = localStorage.getItem(STORAGE_KEY_VOICE);
    return legacyVal === null ? true : legacyVal === 'true';
  }

  function setVoiceEnabled(enabled) {
    localStorage.setItem(getUserPrefKey(STORAGE_KEY_VOICE), enabled ? 'true' : 'false');
    updateVoiceUIButtons();
  }

  function toggleVoiceEnabled() {
    const cur = isVoiceEnabled();
    setVoiceEnabled(!cur);
    return !cur;
  }

  function updateVoiceUIButtons() {
    const enabled = isVoiceEnabled();
    document.querySelectorAll('.btn-voice-toggle').forEach(btn => {
      btn.innerHTML = enabled
        ? '<i class="fa-solid fa-volume-high"></i> AI Voice: ON'
        : '<i class="fa-solid fa-volume-xmark"></i> AI Voice: OFF';
      btn.classList.toggle('voice-active', enabled);
    });
  }

  function speakVoiceAlert(notifData) {
    if (!isVoiceEnabled() || !('speechSynthesis' in window)) return;
    try {
      window.speechSynthesis.cancel(); // cancel previous active speech

      let extra = {};
      if (typeof notifData.extra_data === 'string') {
        try { extra = JSON.parse(notifData.extra_data); } catch (e) {}
      } else if (typeof notifData.extra_data === 'object' && notifData.extra_data) {
        extra = notifData.extra_data;
      }

      const sym = (notifData.symbol || extra.symbol || 'Stock').toUpperCase();
      const ev = (notifData.event_type || 'INFO').toUpperCase();
      const currP = extra.current_price ? Math.round(Number(extra.current_price)) : '';

      let textToSay = '';
      if (ev === 'SUPERTREND_BUY') {
        textToSay = `SuperIndicator Hit! Buy confirmed for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Model prediction is true.`;
      } else if (ev === 'SUPERTREND_SELL') {
        textToSay = `SuperIndicator Hit! Sell confirmed for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Model prediction is true.`;
      } else if (ev === 'SUPERTREND_APPROACHING') {
        const tgt = extra.target_direction || 'next trend';
        textToSay = `SuperIndicator Early Warning for ${sym}. Model predicts hit to ${tgt}.`;
      } else if (ev === 'SUPERTREND_FALSE') {
        const curSit = extra.current_direction || 'active';
        textToSay = `SuperIndicator model prediction is false for ${sym}. Current situation remains ${curSit}.`;
      } else if (ev === 'SUPERTREND_CHANGED') {
        const curCol = extra.current_color || 'shifted';
        const curPts = extra.current_points != null ? extra.current_points : '';
        textToSay = `SuperIndicator shift alert for ${sym}. Candle turned ${curCol} with ${curPts} points.`;
      } else if (ev === 'TARGET_TRIGGERED' || ev === 'SUCCESS') {
        textToSay = `Target reached for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Success!`;
      } else if (ev === 'EARLY_WARNING') {
        textToSay = `Early warning alert for ${sym}. Target movement predicted.`;
      } else if (ev === 'STOP_LOSS_TRIGGERED') {
        textToSay = `Stop loss triggered for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Capital protected.`;
      } else if (ev === 'HALFWAY_MILESTONE') {
        textToSay = `Halfway milestone reached for ${sym}. Fifty percent of target achieved.`;
      } else if (ev === 'PREDICTION_FAILED') {
        textToSay = `Warning window elapsed for ${sym}. Continuous monitoring restarted.`;
      } else if (ev === 'MONITORING_STARTED') {
        textToSay = `Continuous monitoring started for ${sym}.`;
      } else if (ev.includes('SYSTEM')) {
        textToSay = `System notification: ${notifData.title || notifData.short_summary || 'StockPulse system update'}.`;
      } else {
        textToSay = `${notifData.title || 'StockPulse alert'} for ${sym}.`;
      }

      const utterance = new SpeechSynthesisUtterance(textToSay);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      utterance.lang = 'en-US';
      window._activeUtterance = utterance; // Prevent garbage collection in background tab
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      console.warn('[StockPulse Voice] Speech synthesis error:', err);
    }
  }

  // --- DESKTOP OS NATIVE & MULTI-WINDOW NOTIFICATIONS ---
  let titleFlashTimer = null;
  let normalDocumentTitle = '';

  function flashTabTitle(alertText) {
    if (!normalDocumentTitle) {
      normalDocumentTitle = document.title || 'StockPulse';
    }
    // Only flash if window is in the background or not focused
    if (!document.hasFocus() || document.hidden) {
      if (titleFlashTimer) clearInterval(titleFlashTimer);
      let isFlashingAlert = false;
      titleFlashTimer = setInterval(() => {
        document.title = isFlashingAlert ? `🚨 ${alertText}` : normalDocumentTitle;
        isFlashingAlert = !isFlashingAlert;
      }, 750);
    }
  }

  function clearTabTitleFlash() {
    if (titleFlashTimer) {
      clearInterval(titleFlashTimer);
      titleFlashTimer = null;
      if (normalDocumentTitle) {
        document.title = normalDocumentTitle;
      }
    }
  }

  window.addEventListener('focus', clearTabTitleFlash);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && document.hasFocus()) {
      clearTabTitleFlash();
    }
  });

  // Register Service Worker for reliable multi-window desktop notifications
  let swRegistration = null;
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      swRegistration = reg;
      console.log('[StockPulse] Multi-window notification Service Worker ready.');
    }).catch((err) => {
      console.warn('[StockPulse] Service Worker registration note:', err);
    });
  }

  async function requestDesktopNotificationPermission(userTriggered = false) {
    if (!('Notification' in window)) {
      if (userTriggered) {
        alert('Desktop notifications are not supported in this browser.');
      }
      return 'unsupported';
    }

    if (Notification.permission === 'granted') {
      updateDesktopAlertButtonUI();
      return 'granted';
    }

    try {
      const perm = await Notification.requestPermission();
      updateDesktopAlertButtonUI();
      dismissDesktopAlertBanner(false);
      if (perm === 'granted') {
        playNotificationSound('SUCCESS');
        sendTestDesktopNotification(true);
      } else if (perm === 'denied' && userTriggered) {
        showDesktopPermissionBlockedModal();
      }
      return perm;
    } catch (err) {
      console.warn('[StockPulse] Notification.requestPermission error:', err);
    }
    return Notification.permission;
  }

  async function showDesktopNotification(title, message, tag, notifData) {
    const alertSummary = (title || 'StockPulse Alert').replace(/<[^>]*>/g, '').trim();
    flashTabTitle(alertSummary);

    if (!('Notification' in window) || Notification.permission !== 'granted') {
      return;
    }

    try {
      let cleanBody = '';
      if (notifData && notifData.short_summary) {
        cleanBody = notifData.short_summary;
      } else if (message) {
        cleanBody = message;
      }
      cleanBody = String(cleanBody)
        .replace(/<br\s*[\/]?>/gi, ' ')
        .replace(/<[^>]*>/g, '')
        .replace(/\s+/g, ' ')
        .trim();
      if (cleanBody.length > 220) {
        cleanBody = cleanBody.slice(0, 217) + '...';
      }

      const notifTag = tag || ('stockpulse-alert-' + Date.now());
      const notifOptions = {
        body: cleanBody || 'Real-time market movement or prediction signal triggered.',
        icon: '/static/favicon.png',
        badge: '/static/favicon.png',
        tag: notifTag,
        renotify: true,
        requireInteraction: true, // Keep notification pinned on Windows until user clicks or dismisses
        silent: false
      };

      // 1. Try Service Worker registration (native Windows Action Center toast over all windows)
      if (swRegistration && 'showNotification' in swRegistration) {
        await swRegistration.showNotification(alertSummary, notifOptions);
        return;
      }
      if ('serviceWorker' in navigator) {
        const reg = await navigator.serviceWorker.getRegistration();
        if (reg && 'showNotification' in reg) {
          await reg.showNotification(alertSummary, notifOptions);
          return;
        }
      }

      // 2. Direct Window Notification fallback
      const notif = new Notification(alertSummary, notifOptions);
      notif.onclick = () => {
        try {
          window.focus();
        } catch (e) {}
        clearTabTitleFlash();
        notif.close();
      };
    } catch (e) {
      console.warn('[StockPulse] showDesktopNotification error:', e);
    }
  }

  function sendTestDesktopNotification(isFirstWelcome = false) {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
      requestDesktopNotificationPermission(true);
      return;
    }

    const sym = getSelectedStock() || 'TCS';
    const title = isFirstWelcome
      ? '🔔 StockPulse: Multi-Window Alerts Active!'
      : `🤖 StockPulse Active Window Test (${sym})`;
    const body = isFirstWelcome
      ? 'Notifications will now pop up over your current window (VS Code, Excel, etc.) even when StockPulse is in the background.'
      : 'This notification confirms that alerts appear on top of other applications in your active window!';

    showDesktopNotification(title, body, 'stockpulse-test-' + Date.now(), {
      symbol: sym,
      short_summary: body,
      event_type: 'INFO'
    });
  }

  function testDesktopAlertWithDelay(delaySeconds = 3) {
    if (!('Notification' in window)) {
      alert('Desktop notifications are not supported in this browser.');
      return;
    }
    if (Notification.permission !== 'granted') {
      requestDesktopNotificationPermission(true);
      return;
    }

    showTemporaryToast(`⏱️ Alert in ${delaySeconds}s! Switch to your other window now...`, 2800);
    setTimeout(() => {
      sendTestDesktopNotification(false);
    }, delaySeconds * 1000);
  }

  function showTemporaryToast(message, durationMs = 3000) {
    const existing = document.getElementById('sp-temporary-hud-toast');
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

    const toast = document.createElement('div');
    toast.id = 'sp-temporary-hud-toast';
    toast.style.cssText = `
      position: fixed;
      top: 80px;
      right: 24px;
      background: rgba(15, 23, 42, 0.95);
      color: #ffffff;
      padding: 10px 18px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 8px 24px rgba(0,0,0,0.25);
      z-index: 10002;
      display: flex;
      align-items: center;
      gap: 10px;
      border: 1px solid rgba(255,255,255,0.15);
      backdrop-filter: blur(8px);
    `;
    toast.innerHTML = `<i class="fa-solid fa-bell" style="color: #60a5fa;"></i> <span>${escapeHtml(message)}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
      }
    }, durationMs);
  }

  function showDesktopPermissionBlockedModal() {
    const existing = document.getElementById('desktop-blocked-modal');
    if (existing) {
      existing.style.display = 'flex';
      return;
    }
    const modal = document.createElement('div');
    modal.id = 'desktop-blocked-modal';
    modal.className = 'desktop-help-modal-backdrop';
    modal.innerHTML = `
      <div class="desktop-help-modal">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
          <h3 style="font-size: 16px; font-weight: 700; color: #dc2626; display: flex; align-items: center; gap: 8px;">
            <i class="fa-solid fa-triangle-exclamation"></i> Notifications Blocked in Browser
          </h3>
          <button type="button" class="desktop-alert-banner-close" onclick="document.getElementById('desktop-blocked-modal').style.display='none'">&times;</button>
        </div>
        <p style="font-size: 13px; color: #475569; line-height: 1.5; margin-bottom: 14px;">
          To receive alerts on top of your other active windows (such as VS Code or Excel), browser notification permissions must be allowed for this site.
        </p>
        <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; font-size: 12.5px; color: #334155; margin-bottom: 16px; line-height: 1.6;">
          <strong>How to enable:</strong><br>
          1. Look at the browser address bar at the top (next to <code>http://127.0.0.1:8880</code>).<br>
          2. Click the <strong>lock / site tune icon 🔒</strong>.<br>
          3. Change <strong>Notifications</strong> to <strong>"Allow"</strong>.<br>
          4. Reload this page to activate.
        </div>
        <div style="text-align: right;">
          <button type="button" class="banner-btn-later" onclick="document.getElementById('desktop-blocked-modal').style.display='none'">Got it</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  function updateDesktopAlertButtonUI() {
    const btn = document.getElementById('btn-desktop-alerts');
    if (!btn) return;

    if (!('Notification' in window)) {
      btn.style.display = 'none';
      return;
    }

    const perm = Notification.permission;
    btn.classList.remove('granted', 'default', 'denied');

    if (perm === 'granted') {
      btn.classList.add('granted');
      btn.innerHTML = '<i class="fa-solid fa-bell" style="color: #10b981;"></i> <span>Multi-Window Alerts: ON</span>';
      btn.title = 'Desktop alerts active in other windows! Click to test 3s multi-window countdown.';
    } else if (perm === 'denied') {
      btn.classList.add('denied');
      btn.innerHTML = '<i class="fa-solid fa-bell-slash" style="color: #ef4444;"></i> <span>Alerts: Blocked</span>';
      btn.title = 'Desktop alerts blocked in browser. Click for instructions to unblock.';
    } else {
      btn.classList.add('default');
      btn.innerHTML = '<i class="fa-solid fa-bell" style="color: #f59e0b;"></i> <span style="font-weight: 700;">Enable Multi-Window Alerts</span>';
      btn.title = 'Click to enable instant notifications on top of your other desktop windows';
    }
  }

  const STORAGE_KEY_BANNER_DISMISSED = 'stockpulse_desktop_banner_dismissed';

  function dismissDesktopAlertBanner(userDismissed = true) {
    if (userDismissed) {
      sessionStorage.setItem(STORAGE_KEY_BANNER_DISMISSED, 'true');
    }
    const banner = document.getElementById('desktop-alert-banner');
    if (banner && banner.parentNode) {
      banner.parentNode.removeChild(banner);
    }
  }

  function setupDesktopAlertUI() {
    const navActions = document.querySelector('.nav-actions');
    let btn = document.getElementById('btn-desktop-alerts');

    if (navActions && !btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'btn-desktop-alerts';
      btn.className = 'nav-desktop-alerts-btn';
      const mobileToggle = document.getElementById('btn-mobile-toggle');
      if (mobileToggle) {
        navActions.insertBefore(btn, mobileToggle);
      } else {
        navActions.appendChild(btn);
      }
    }

    if (btn) {
      btn.addEventListener('click', () => {
        if (Notification.permission === 'granted') {
          testDesktopAlertWithDelay(3);
        } else {
          requestDesktopNotificationPermission(true);
        }
      });
    }

    updateDesktopAlertButtonUI();

    if ('Notification' in window && Notification.permission === 'default') {
      const dismissed = sessionStorage.getItem(STORAGE_KEY_BANNER_DISMISSED);
      if (!dismissed && !document.getElementById('desktop-alert-banner')) {
        const banner = document.createElement('div');
        banner.id = 'desktop-alert-banner';
        banner.className = 'desktop-alert-banner';
        banner.innerHTML = `
          <div class="desktop-alert-banner-header">
            <span class="desktop-alert-banner-title">
              <i class="fa-solid fa-desktop"></i> Multi-Window Notifications
            </span>
            <button type="button" class="desktop-alert-banner-close" title="Dismiss" id="btn-banner-close-alerts">&times;</button>
          </div>
          <p class="desktop-alert-banner-body">
            Get instant ML model predictions and SuperTrend reversal alerts on top of your screen while working in <strong>VS Code</strong>, <strong>Excel</strong>, or other windows.
          </p>
          <div class="desktop-alert-banner-footer">
            <button type="button" class="banner-btn-enable" id="btn-banner-allow-alerts">
              <i class="fa-solid fa-bell"></i> Enable Desktop Alerts
            </button>
            <button type="button" class="banner-btn-later" id="btn-banner-later-alerts">
              Later
            </button>
          </div>
        `;
        document.body.appendChild(banner);

        const btnAllow = document.getElementById('btn-banner-allow-alerts');
        const btnClose = document.getElementById('btn-banner-close-alerts');
        const btnLater = document.getElementById('btn-banner-later-alerts');

        if (btnAllow) {
          btnAllow.addEventListener('click', () => {
            requestDesktopNotificationPermission(true);
          });
        }
        if (btnClose) {
          btnClose.addEventListener('click', () => dismissDesktopAlertBanner(true));
        }
        if (btnLater) {
          btnLater.addEventListener('click', () => dismissDesktopAlertBanner(true));
        }
      }
    }
  }

  // --- CUSTOMIZED IN-BROWSER POPUP HUD MODAL ---
  function ensurePopupContainer() {
    let container = document.getElementById('stockpulse-popup-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'stockpulse-popup-container';
      document.body.appendChild(container);
    }
    return container;
  }

  function showCustomizedPopup(notifData) {
    if (!notifData) return;
    try {
      // Parse extra_data if available
      let extra = {};
      if (typeof notifData.extra_data === 'string') {
        try { extra = JSON.parse(notifData.extra_data); } catch (e) {}
      } else if (typeof notifData.extra_data === 'object' && notifData.extra_data) {
        extra = notifData.extra_data;
      }

      const symbol = (notifData.symbol || extra.symbol || 'STOCK').toUpperCase();
      const eventType = (notifData.event_type || 'INFO').toUpperCase();
      const notifId = notifData.id || notifData.notification_id || Math.floor(Math.random() * 100000);
      const popupKey = `${symbol}_${eventType}_${notifId}`;

      const container = ensurePopupContainer();

      // Check if this notification is already displayed on screen
      if (container.querySelector(`[data-notif-id="${notifId}"]`)) {
        return;
      }

      const card = document.createElement('div');
      card.setAttribute('data-popup-key', popupKey);
      card.setAttribute('data-notif-id', notifId);
      const isTargetHit = eventType === 'TARGET_TRIGGERED' || eventType === 'SUCCESS';
      const isEarlyWarning = eventType === 'EARLY_WARNING';
      const isPredictionFailed = eventType === 'PREDICTION_FAILED' || eventType === 'MONITORING_EXPIRED';
      const isStopLoss = eventType === 'STOP_LOSS_TRIGGERED';
      const isMilestone = eventType === 'HALFWAY_MILESTONE';
      const isSuperTrendBuy = eventType === 'SUPERTREND_BUY';
      const isSuperTrendSell = eventType === 'SUPERTREND_SELL';
      const isSuperTrendApproaching = eventType === 'SUPERTREND_APPROACHING';
      const isSuperTrendFalse = eventType === 'SUPERTREND_FALSE';
      const isSuperTrendChanged = eventType === 'SUPERTREND_CHANGED';
      const isSystem = eventType.includes('SYSTEM') || symbol === 'SYSTEM';

      let eventClass = 'monitoring-started';
      let iconHtml = '<i class="fa-solid fa-play"></i>';
      let badgeTitle = 'Prediction Active';
      if (isSystem) {
        eventClass = 'system-status';
        iconHtml = '<i class="fa-solid fa-server"></i>';
        badgeTitle = notifData.title || 'System Notification';
      } else if (isSuperTrendBuy) {
        eventClass = 'supertrend-buy';
        iconHtml = '<i class="fa-solid fa-arrow-trend-up"></i>';
        badgeTitle = 'SuperIndicator HIT: BUY (Prediction TRUE!)';
      } else if (isSuperTrendSell) {
        eventClass = 'supertrend-sell';
        iconHtml = '<i class="fa-solid fa-arrow-trend-down"></i>';
        badgeTitle = 'SuperIndicator HIT: SELL (Prediction TRUE!)';
      } else if (isSuperTrendApproaching) {
        eventClass = 'supertrend-approaching';
        iconHtml = '<i class="fa-solid fa-bolt"></i>';
        badgeTitle = 'SuperIndicator Early Warning: Hit Predicted';
      } else if (isSuperTrendFalse) {
        eventClass = 'supertrend-false';
        iconHtml = '<i class="fa-solid fa-circle-xmark"></i>';
        badgeTitle = 'SuperIndicator: Model Prediction FALSE';
      } else if (isSuperTrendChanged) {
        eventClass = 'supertrend-changed';
        iconHtml = '<i class="fa-solid fa-arrows-rotate"></i>';
        badgeTitle = 'SuperIndicator: Candle Shift Alert';
      } else if (isStopLoss) {
        eventClass = 'stop-loss-triggered';
        iconHtml = '<i class="fa-solid fa-shield-halved"></i>';
        badgeTitle = 'Stop-Loss Executed';
      } else if (isMilestone) {
        eventClass = 'halfway-milestone';
        iconHtml = '<i class="fa-solid fa-flag-checkered"></i>';
        badgeTitle = '50% Target Milestone!';
      } else if (isTargetHit) {
        eventClass = 'target-triggered';
        iconHtml = '<i class="fa-solid fa-bullseye"></i>';
        badgeTitle = 'Target Reached!';
      } else if (isEarlyWarning) {
        eventClass = 'early-warning';
        iconHtml = '<i class="fa-solid fa-triangle-exclamation"></i>';
        badgeTitle = 'Prediction Alert';
      } else if (isPredictionFailed) {
        eventClass = 'prediction-failed';
        iconHtml = '<i class="fa-solid fa-clock-rotate-left"></i>';
        badgeTitle = 'Target Not Reached';
      }

      card.className = `stockpulse-popup-card ${eventClass}`;

      const targetPct = extra.target_percent != null ? parseFloat(extra.target_percent) : null;
      const currentPriceNum = extra.current_price != null ? parseFloat(extra.current_price) : null;
      const targetPriceNum = extra.target_price != null ? parseFloat(extra.target_price) : null;
      const currentPrice = currentPriceNum != null ? `₹${currentPriceNum.toFixed(2)}` : 'Live';
      const startPrice = extra.start_price != null ? `₹${parseFloat(extra.start_price).toFixed(2)}` : 'Entry';
      const movement = extra.current_movement != null ? parseFloat(extra.current_movement) : (extra.current_change != null ? parseFloat(extra.current_change) : (extra.movement != null ? parseFloat(extra.movement) : null));

      // Resolve direction accurately: Green for UP (+), Red for DOWN (-)
      const rawDir = String(extra.direction || notifData.direction || (movement != null ? (movement >= 0 ? 'UP' : 'DOWN') : (targetPct != null && targetPct < 0 ? 'DOWN' : 'UP'))).toUpperCase();
      const isDown = rawDir === 'DOWN' || (movement != null && movement < 0 && rawDir !== 'UP');
      const isUp = !isDown;

      const moveClass = isDown ? 'negative' : 'positive';
      const moveSign = isDown ? '-' : '+';
      const arrowIcon = isDown ? '<i class="fa-solid fa-arrow-down"></i>' : '<i class="fa-solid fa-arrow-up"></i>';
      const trendIcon = isDown ? '<i class="fa-solid fa-arrow-trend-down" style="color: #ef4444;"></i>' : '<i class="fa-solid fa-arrow-trend-up" style="color: #22c55e;"></i>';

      let moveText = '';
      if (isSystem) {
        moveText = 'SYSTEM ACTIVE';
      } else if (isSuperTrendBuy) {
        moveText = 'HIT: BUY CONFIRMED';
      } else if (isSuperTrendSell) {
        moveText = 'HIT: SELL CONFIRMED';
      } else if (isSuperTrendApproaching) {
        moveText = 'HIT PREDICTED';
      } else if (isSuperTrendFalse) {
        moveText = `PREDICTION FALSE (${extra.current_direction || 'ACTIVE'})`;
      } else if (movement != null) {
        const signPrefix = movement >= 0 ? '+' : '';
        moveText = `${signPrefix}${movement.toFixed(2)}%`;
      } else if (targetPct != null) {
        moveText = `Target: ${moveSign}${Math.abs(targetPct).toFixed(2)}%`;
      } else {
        moveText = isDown ? 'DOWN (-)' : 'UP (+)';
      }

      const targetValFormatted = targetPct != null ? `${moveSign}${Math.abs(targetPct).toFixed(2)}%` : 'Boundary';
      const targetColor = isDown ? '#dc2626' : '#16a34a';

      // Real-time Progress Gauge calculation (for target alerts)
      let gaugeHtml = '';
      if (targetPct && movement != null && !isSuperTrendBuy && !isSuperTrendSell && !isSuperTrendApproaching && !isSuperTrendFalse && !isSystem) {
        const progressPct = Math.min(100, Math.max(0, (Math.abs(movement) / Math.abs(targetPct)) * 100));
        let distText = '';
        if (targetPriceNum && currentPriceNum) {
          const distRs = Math.abs(targetPriceNum - currentPriceNum).toFixed(2);
          distText = `Dist: ₹${distRs}`;
        }
        gaugeHtml = `
          <div class="session-gauge-wrapper" style="margin: 8px 0;">
            <div class="session-gauge-header">
              <span style="color: #64748b; font-size: 10.5px;"><i class="fa-solid fa-gauge-high"></i> Target Progress</span>
              <span style="color: ${targetColor}; font-weight: 700;">${progressPct.toFixed(0)}%</span>
            </div>
            <div class="session-gauge-track">
              <div class="session-gauge-fill" style="width: ${progressPct}%; background: ${targetColor};"></div>
            </div>
            <div class="session-gauge-footer" style="font-size: 10px;">
              <span>${distText}</span>
              <span>Target: ${targetValFormatted}</span>
            </div>
          </div>
        `;
      }

      let metricsGridHtml = '';
      if (isSystem) {
        metricsGridHtml = `
          <div class="popup-metrics-grid">
            <div class="popup-metric-item">
              <span class="popup-metric-label">Telemetry</span>
              <span class="popup-metric-value" style="color: #0284c7; font-weight: 700;">StockPulse Engine</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">Status</span>
              <span class="popup-metric-value" style="color: #16a34a; font-weight: 700;">Operational</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">Channel</span>
              <span class="popup-metric-value" style="color: #64748b;">In-App Popup</span>
            </div>
          </div>
        `;
      } else if (isSuperTrendBuy || isSuperTrendSell || isSuperTrendApproaching || isSuperTrendFalse || isSuperTrendChanged) {
        const stPriceNum = extra.supertrend_price != null ? Number(extra.supertrend_price) : null;
        const stPriceStr = stPriceNum ? `₹${stPriceNum.toFixed(2)}` : 'Line';
        const distPts = extra.distance_points != null ? Number(extra.distance_points).toFixed(2) : '0.00';
        let signalLabel = '';
        let signalColor = '#f59e0b';
        if (isSuperTrendBuy) {
          signalLabel = 'BUY (TRUE)';
          signalColor = '#16a34a';
        } else if (isSuperTrendSell) {
          signalLabel = 'SELL (TRUE)';
          signalColor = '#dc2626';
        } else if (isSuperTrendFalse) {
          signalLabel = `FALSE (${extra.current_direction || 'ACTIVE'})`;
          signalColor = '#dc2626';
        } else if (isSuperTrendChanged) {
          const col = extra.current_color || 'SHIFTED';
          const pts = extra.current_points != null ? extra.current_points : '0.00';
          signalLabel = `${col} (${pts > 0 ? '+' : ''}${pts} pts)`;
          signalColor = col === 'GREEN' ? '#16a34a' : '#dc2626';
        } else {
          signalLabel = `${extra.target_direction || 'HIT'} PREDICTED`;
          signalColor = '#f59e0b';
        }

        metricsGridHtml = `
          <div class="popup-metrics-grid">
            <div class="popup-metric-item">
              <span class="popup-metric-label">Live Price</span>
              <span class="popup-metric-value">${currentPrice}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">SuperIndicator Line</span>
              <span class="popup-metric-value" style="color: ${signalColor}; font-weight: 700;">${stPriceStr}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">${isSuperTrendApproaching ? 'Gap to Line' : 'Status'}</span>
              <span class="popup-metric-value" style="color: ${signalColor}; font-weight: 700;">${isSuperTrendApproaching ? `${distPts} pts` : signalLabel}</span>
            </div>
          </div>
        `;
      } else {
        metricsGridHtml = `
          <div class="popup-metrics-grid">
            <div class="popup-metric-item">
              <span class="popup-metric-label">Live Price</span>
              <span class="popup-metric-value">${currentPrice}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">Start Price</span>
              <span class="popup-metric-value">${startPrice}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">Target %</span>
              <span class="popup-metric-value" style="color: ${targetColor}; font-weight: 700;">${targetValFormatted}</span>
            </div>
          </div>
        `;
      }

      card.innerHTML = `
        <div class="popup-header">
          <div class="popup-title-group">
            <div class="popup-icon-badge ${eventClass}">${iconHtml}</div>
            <div>
              <h4 class="popup-title-text">${escapeHtml(notifData.title || badgeTitle)}</h4>
            </div>
            <span class="popup-audio-badge"><i class="fa-solid fa-volume-high"></i> Sound &amp; Voice</span>
          </div>
          <button type="button" class="popup-close-btn" title="Dismiss">&times;</button>
        </div>
        <div class="popup-body">
          ${isSystem ? `
            <div class="popup-stock-row">
              <div class="popup-symbol-badge" style="background: rgba(14, 165, 233, 0.12); color: #0284c7; border-color: rgba(14, 165, 233, 0.3);">
                <i class="fa-solid fa-shield-halved" style="color: #0284c7;"></i> SYSTEM TELEMETRY
              </div>
              <span class="popup-change-pill positive" style="background: rgba(16, 185, 129, 0.12); color: #16a34a; border-color: rgba(16, 185, 129, 0.3);">
                <i class="fa-solid fa-circle-check"></i> OPERATIONAL
              </span>
            </div>
          ` : `
            <div class="popup-stock-row">
              <div class="popup-symbol-badge">
                ${trendIcon} ${escapeHtml(symbol)}
              </div>
              <span class="popup-change-pill ${moveClass}">
                ${arrowIcon}
                ${moveText}
              </span>
            </div>
          `}
          ${metricsGridHtml}
          ${gaugeHtml}
          <p class="popup-message-text">${escapeHtml(notifData.short_summary || notifData.message || 'Alert threshold reached.')}</p>
        </div>
        <div class="popup-footer">
          <a href="/live?symbol=${encodeURIComponent(symbol === 'SYSTEM' ? 'TCS' : symbol)}" class="popup-action-btn primary"><i class="fa-solid fa-chart-line"></i> View Chart</a>
          <a href="/monitoring" class="popup-action-btn primary" style="background: rgba(255,255,255,0.08);"><i class="fa-solid fa-sliders"></i> Monitoring</a>
          <button type="button" class="popup-action-btn secondary btn-popup-dismiss">Dismiss</button>
        </div>
        <div class="popup-progress-container">
          <div class="popup-progress-bar" style="width: 100%;"></div>
        </div>
      `;

      container.appendChild(card);

      // Close handlers
      const closeCard = () => {
        card.classList.add('popup-closing');
        setTimeout(() => {
          if (card.parentNode) card.parentNode.removeChild(card);
        }, 280);
      };

      const closeBtn = card.querySelector('.popup-close-btn');
      const dismissBtn = card.querySelector('.btn-popup-dismiss');
      if (closeBtn) closeBtn.addEventListener('click', closeCard);
      if (dismissBtn) dismissBtn.addEventListener('click', closeCard);

      // Auto-dismiss in 10 seconds with smooth progress bar
      const progressBar = card.querySelector('.popup-progress-bar');
      if (progressBar) {
        progressBar.style.transition = 'width 10s linear';
        requestAnimationFrame(() => {
          progressBar.style.width = '0%';
        });
      }
      setTimeout(closeCard, 10000);
    } catch (err) {
      console.warn('[StockPulse] showCustomizedPopup error:', err);
    }
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function parseServerDate(str) {
    if (!str) return 0;
    let s = String(str).trim().replace(' ', 'T');
    if (!s.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(s)) {
      s += 'Z';
    }
    const t = new Date(s).getTime();
    return isNaN(t) ? 0 : t;
  }

  // --- REAL-TIME NOTIFICATION POLLER ---
  let isNotifPollInFlight = false;
  async function checkRealtimeNotifications() {
    if (isNotifPollInFlight) return;
    isNotifPollInFlight = true;
    try {
      const res = await fetch('/api/notifications?limit=10');
      if (!res.ok) return;
      const data = await res.json();
      const notifs = data.notifications || [];

      if (typeof data.unread_count !== 'undefined') {
        const badge1 = document.getElementById('notif-unread-badge');
        if (badge1) {
          badge1.textContent = data.unread_count;
          badge1.style.display = data.unread_count > 0 ? 'inline-block' : 'none';
        }
      }


      if (isFirstNotifCheck) {
        isFirstNotifCheck = false;
        const now = Date.now();
        // On initial page load or navigation between pages, mark only OLD notifications (>5m) as known.
        // Fresh unread notifications arriving within the last 5 minutes will still alert the user!
        for (const n of notifs) {
          const createdAt = parseServerDate(n.created_at);
          if (createdAt && (now - createdAt > 300000)) {
            markNotificationAsKnown(n.id);
          }
        }
      }

      // Detect ONLY newly created unread notifications while the user is actively on the page
      for (let i = notifs.length - 1; i >= 0; i--) {
        const n = notifs[i];
        if (!knownNotificationIds.has(n.id)) {
          markNotificationAsKnown(n.id);
          if (!n.is_read) {
            const sym = (n.symbol || '').toUpperCase();
            const ev = (n.event_type || 'INFO').toUpperCase();
            const eventKey = `${sym}_${ev}`;
            const lastShown = recentPopups.get(eventKey);
            const now = Date.now();

            // Suppress duplicate popup if already shown on screen within 4 seconds
            if (!lastShown || (now - lastShown >= 4000)) {
              recentPopups.set(eventKey, now);
              playNotificationSound(n.event_type);
              speakVoiceAlert(n);
              showCustomizedPopup(n);
              // Multi-window native desktop notification (appears over all other windows!)
              showDesktopNotification(n.title, n.message, `notif-${n.id}`, n);
            }
          }
        }
      }
    } catch (e) {
      // quiet fallback
    } finally {
      isNotifPollInFlight = false;
    }
  }

  function testNotificationSound() {
    playNotificationSound('TARGET_TRIGGERED');
    showCustomizedPopup({
      id: 999999,
      symbol: getSelectedStock() || 'TCS',
      title: 'StockPulse Target Reached (Audio Test)',
      event_type: 'TARGET_TRIGGERED',
      message: 'Sound alert, customized popup, and multi-window notifications are active!',
      short_summary: 'TCS reached +0.5% movement target. Multi-window notifications dispatched.',
      extra_data: JSON.stringify({
        symbol: getSelectedStock() || 'TCS',
        direction: 'UP',
        target_percent: 0.5,
        current_price: 4122.50,
        start_price: 4100.00,
        current_movement: 0.55
      })
    });
    // Multi-window test notification
    if ('Notification' in window && Notification.permission === 'granted') {
      showDesktopNotification('StockPulse Alert Test', 'Customized audio chime and multi-window notifications are active!', 'stockpulse-test');
    } else {
      requestDesktopNotificationPermission(true);
    }
  }

  // --- STOCK SELECTOR PERSISTENCE (PER-USER ISOLATED) ---
  function getSelectedStock() {
    const key = getUserPrefKey(STORAGE_KEY_STOCK);
    const val = localStorage.getItem(key);
    if (val) return val;
    const legacy = localStorage.getItem(STORAGE_KEY_STOCK);
    return legacy || 'TCS';
  }

  function setSelectedStock(symbol) {
    if (!symbol) return;
    const clean = symbol.toUpperCase().replace(/\.(NS|BO)$/, '');
    localStorage.setItem(getUserPrefKey(STORAGE_KEY_STOCK), clean);
    return clean;
  }

  function getChartMode() {
    const key = getUserPrefKey('stockpulse_chart_mode');
    const val = localStorage.getItem(key);
    if (val) return val;
    return localStorage.getItem('stockpulse_chart_mode') || 'line';
  }

  function setChartMode(mode) {
    if (!mode) return;
    localStorage.setItem(getUserPrefKey('stockpulse_chart_mode'), mode);
  }

  function getSupertrendOverlay() {
    const key = getUserPrefKey('stockpulse_supertrend_overlay');
    const val = localStorage.getItem(key);
    if (val !== null) return val !== 'false';
    const legacy = localStorage.getItem('stockpulse_supertrend_overlay');
    return legacy !== 'false';
  }

  function setSupertrendOverlay(enabled) {
    localStorage.setItem(getUserPrefKey('stockpulse_supertrend_overlay'), enabled ? 'true' : 'false');
  }

  // --- POPULATE STOCK SELECT DROPDOWNS ---
  async function populateStockDropdown(selectEl, onChangeCallback) {
    if (!selectEl) return;
    try {
      const res = await fetch('/api/stocks');
      if (!res.ok) return;
      const stocks = await res.json();
      const currentSym = getSelectedStock();
      
      selectEl.innerHTML = '';
      stocks.forEach(stk => {
        const opt = document.createElement('option');
        opt.value = stk.symbol;
        opt.textContent = `${stk.symbol} — ${stk.name}`;
        if (stk.symbol === currentSym) {
          opt.selected = true;
        }
        selectEl.appendChild(opt);
      });

      selectEl.addEventListener('change', (e) => {
        const newSym = setSelectedStock(e.target.value);
        if (typeof onChangeCallback === 'function') {
          onChangeCallback(newSym);
        }
      });
    } catch (err) {
      console.warn('[StockPulse] Error loading stock list:', err);
    }
  }

  // --- MARKET STATUS CACHING & TARGETED NAVBAR UPDATES ---
  const STORAGE_KEY_MARKET_STATUS = 'stockpulse_cached_market_status';
  const STORAGE_KEY_MARKET_STATUS_TS = 'stockpulse_cached_market_status_ts';
  let isSystemStatusInFlight = false;
  let systemStatusPollTimer = null;
  let lastAppliedMarketStatusTs = 0;

  function getCachedMarketStatus() {
    try {
      const status = sessionStorage.getItem(STORAGE_KEY_MARKET_STATUS);
      const ts = parseInt(sessionStorage.getItem(STORAGE_KEY_MARKET_STATUS_TS) || '0', 10);
      if (status && (Date.now() - ts < 30000)) { // 30 seconds validity for consistent page navigation
        return status;
      }
    } catch (e) {}
    return null;
  }

  function applyMarketStatus(status, updateTs = 0) {
    if (!status) return;
    const norm = String(status).toUpperCase();

    // Prevent race conditions: ignore older out-of-order API responses
    if (updateTs && lastAppliedMarketStatusTs && updateTs < lastAppliedMarketStatusTs) {
      return;
    }
    if (updateTs) {
      lastAppliedMarketStatusTs = updateTs;
    } else if (!lastAppliedMarketStatusTs) {
      lastAppliedMarketStatusTs = Date.now();
    }

    const dot = document.getElementById('market-status-dot');
    const text = document.getElementById('market-status-text');
    const globalDot = document.getElementById('global-status-dot');
    const globalText = document.getElementById('global-status-text');

    let targetText = 'Market: CHECKING...';
    let targetClass = 'pulse-dot neutral';
    let targetGlobalDot = 'status-indicator-dot neutral';
    let targetGlobalText = 'UNKNOWN';

    if (norm === 'OPEN' || norm === 'LIVE') {
      targetText = 'Market: OPEN';
      targetClass = 'pulse-dot green';
      targetGlobalDot = 'status-indicator-dot live';
      targetGlobalText = 'LIVE';
    } else if (norm === 'CLOSED') {
      targetText = 'Market: CLOSED';
      targetClass = 'pulse-dot closed';
      targetGlobalDot = 'status-indicator-dot closed';
      targetGlobalText = 'CLOSED';
    } else if (norm === 'STATUS UNKNOWN' || norm === 'UNKNOWN') {
      targetText = 'Market: UNKNOWN';
      targetClass = 'pulse-dot neutral';
      targetGlobalDot = 'status-indicator-dot neutral';
      targetGlobalText = 'UNKNOWN';
    } else if (norm === 'DATA_UNAVAILABLE' || norm === 'UNAVAILABLE') {
      targetText = 'Market: DATA UNAVAILABLE';
      targetClass = 'pulse-dot neutral';
      targetGlobalDot = 'status-indicator-dot unavailable';
      targetGlobalText = 'UNAVAILABLE';
    } else if (norm === 'DELAYED') {
      targetText = 'Market: DELAYED';
      targetClass = 'pulse-dot amber';
      targetGlobalDot = 'status-indicator-dot delayed';
      targetGlobalText = 'DELAYED';
    } else if (norm === 'STALE') {
      targetText = 'Market: STALE';
      targetClass = 'pulse-dot amber';
      targetGlobalDot = 'status-indicator-dot closed';
      targetGlobalText = 'STALE';
    } else {
      targetText = `Market: ${status}`;
      targetClass = 'pulse-dot amber';
      targetGlobalDot = 'status-indicator-dot neutral';
      targetGlobalText = status;
    }

    // Targeted DOM update: ONLY update if changed to avoid unnecessary re-rendering
    if (text && text.textContent !== targetText) {
      text.textContent = targetText;
    }
    if (dot && dot.className !== targetClass) {
      dot.className = targetClass;
    }
    if (globalText && globalText.textContent !== targetGlobalText) {
      globalText.textContent = targetGlobalText;
    }
    if (globalDot && globalDot.className !== targetGlobalDot) {
      globalDot.className = targetGlobalDot;
    }

    try {
      sessionStorage.setItem(STORAGE_KEY_MARKET_STATUS, norm === 'LIVE' ? 'OPEN' : norm);
      sessionStorage.setItem(STORAGE_KEY_MARKET_STATUS_TS, (updateTs || Date.now()).toString());
    } catch (e) {}
  }

  function initNavbarMarketStatus() {
    const cached = getCachedMarketStatus();
    if (cached) {
      applyMarketStatus(cached);
    } else {
      // Neutral initial state; never flash "Market Live" or "Market: OPEN"!
      const dot = document.getElementById('market-status-dot');
      const text = document.getElementById('market-status-text');
      if (dot && dot.className !== 'pulse-dot neutral') {
        dot.className = 'pulse-dot neutral';
      }
      if (text && text.textContent !== 'Checking market status...') {
        text.textContent = 'Checking market status...';
      }
    }
  }

  function scheduleNextSystemStatusPoll(delayMs) {
    if (systemStatusPollTimer) clearTimeout(systemStatusPollTimer);
    systemStatusPollTimer = setTimeout(updateSystemStatus, delayMs);
  }

  async function updateSystemStatus() {
    if (isSystemStatusInFlight) return;
    if (document.hidden) return; // Pause polling when tab is not visible

    isSystemStatusInFlight = true;
    const reqTs = Date.now();
    let currentStatus = getCachedMarketStatus();
    try {
      const res = await fetch('/api/stocks/market-status');
      if (res.ok) {
        const data = await res.json();
        if (data && data.market_status) {
          currentStatus = data.market_status;
          applyMarketStatus(data.market_status, reqTs);
        } else {
          currentStatus = 'STATUS UNKNOWN';
          applyMarketStatus('STATUS UNKNOWN', reqTs);
        }
      } else {
        // API response inconclusive/unavailable: show UNKNOWN / DATA_UNAVAILABLE state instead of incorrectly declaring market CLOSED
        currentStatus = 'DATA_UNAVAILABLE';
        applyMarketStatus('DATA_UNAVAILABLE', reqTs);
      }
    } catch (e) {
      // Network error: show DATA_UNAVAILABLE state instead of incorrectly declaring market CLOSED
      currentStatus = 'DATA_UNAVAILABLE';
      applyMarketStatus('DATA_UNAVAILABLE', reqTs);
    } finally {
      isSystemStatusInFlight = false;
    }

    // Check for newly arriving real-time notifications
    await checkRealtimeNotifications();

    // Dynamically adjust polling interval: 15s if open, 60s if closed, 15s if unknown/unavailable
    const nextInterval = (currentStatus === 'OPEN') ? 15000 : ((currentStatus === 'CLOSED') ? 60000 : 15000);
    scheduleNextSystemStatusPoll(nextInterval);
  }

  // Resume status refresh immediately when tab becomes visible
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      updateSystemStatus();
    }
  });

  // --- INIT ACTIVE PAGE HIGHLIGHT ---
  function highlightCurrentNav() {
    const path = window.location.pathname.toLowerCase();
    const navLinks = document.querySelectorAll('.nav-link');
    navLinks.forEach(link => link.classList.remove('active'));

    if (path.includes('monitoring')) {
      const el = document.getElementById('nav-monitoring');
      if (el) el.classList.add('active');
    } else if (path.includes('details') || path.includes('stock-info')) {
      const el = document.getElementById('nav-details');
      if (el) el.classList.add('active');
    } else if (path.includes('history') || path.includes('alert-history')) {
      const el = document.getElementById('nav-history');
      if (el) el.classList.add('active');
    } else if (path.includes('notifications')) {
      const el = document.getElementById('nav-notifications');
      if (el) el.classList.add('active');
    } else {
      // default: Live Market
      const el = document.getElementById('nav-live');
      if (el) el.classList.add('active');
    }
  }

  // --- USER AUTHENTICATION / PREFERENCES MODAL LOGIC ---
  function setupAuthModal() {
    const btnOpenLogin = document.getElementById('btn-open-login');
    const loginModal = document.getElementById('login-modal-backdrop');
    const btnCloseLogin = document.getElementById('btn-close-login-modal');
    const btnDismissLogin = document.getElementById('btn-dismiss-login-modal');
    const formLogin = document.getElementById('form-user-login');
    const btnLogout = document.getElementById('btn-logout-user');
    const nameInput = document.getElementById('login-name-input');
    const emailInput = document.getElementById('login-email-input');
    const feedbackMsg = document.getElementById('login-feedback-msg');
    const activeCard = document.getElementById('login-active-card');
    const activeName = document.getElementById('active-user-name');
    const activeEmail = document.getElementById('active-user-email');
    const navUserLabel = document.getElementById('nav-user-label');
    const navUserIcon = document.getElementById('nav-user-icon');

    function renderUserUI() {
      const user = getUser();
      if (user && (user.name || user.email)) {
        if (navUserLabel) navUserLabel.textContent = user.name || (user.email ? user.email.split('@')[0] : 'Trader');
        if (navUserIcon) navUserIcon.className = 'fa-solid fa-user-check text-green';
        if (activeCard) activeCard.style.display = 'flex';
        if (activeName) activeName.textContent = user.name || 'Trader';
        if (activeEmail) activeEmail.textContent = user.email || 'Popup Alerts Active';
        if (emailInput) emailInput.value = user.email || '';
        if (nameInput) nameInput.value = user.name || '';
        if (btnLogout) btnLogout.style.display = 'inline-flex';
      } else {
        if (navUserLabel) navUserLabel.textContent = 'Alert Settings';
        if (navUserIcon) navUserIcon.className = 'fa-solid fa-sliders';
        if (activeCard) activeCard.style.display = 'none';
        if (btnLogout) btnLogout.style.display = 'none';
      }
    }

    if (btnOpenLogin && loginModal) {
      btnOpenLogin.addEventListener('click', () => {
        renderUserUI();
        if (feedbackMsg) feedbackMsg.style.display = 'none';
        loginModal.style.display = 'flex';
        requestDesktopNotificationPermission();
      });
    }

    if (btnCloseLogin && loginModal) {
      btnCloseLogin.addEventListener('click', () => {
        loginModal.style.display = 'none';
      });
    }

    if (btnDismissLogin && loginModal) {
      btnDismissLogin.addEventListener('click', () => {
        loginModal.style.display = 'none';
      });
    }

    if (btnLogout) {
      btnLogout.addEventListener('click', async () => {
        performAutoLogout('manual_logout');
      });
    }

    if (formLogin) {
      formLogin.addEventListener('submit', async (e) => {
        e.preventDefault();
        const existing = getUser() || {};
        const email = emailInput ? emailInput.value.trim() : '';
        const name = nameInput ? nameInput.value.trim() : '';

        const updated = {
          ...existing,
          name: name || existing.name || 'Trader',
          email: email || existing.email || 'user@stockpulse.io'
        };
        setUser(updated);
        renderUserUI();
        renderNavbarUserPill();
        if (feedbackMsg) {
          feedbackMsg.className = 'login-feedback-msg success';
          feedbackMsg.textContent = 'Preferences saved! Popup alerts are ready.';
          feedbackMsg.style.display = 'block';
        }
        setTimeout(() => {
          if (loginModal) loginModal.style.display = 'none';
        }, 900);
      });
    }

    renderUserUI();
  }

  // --- SETTINGS MODAL LOGIC ---
  function setupSettingsModal() {
    const btnOpenSettings = document.getElementById('btn-open-settings');
    const settingsModal = document.getElementById('settings-modal-backdrop');
    const btnCloseSettings = document.getElementById('btn-close-settings');
    const btnDismissSettings = document.getElementById('btn-dismiss-settings');
    const btnSaveSettings = document.getElementById('btn-save-settings');

    // Dynamically inject Session Timeout control if not already present
    if (settingsModal) {
      const modalBody = settingsModal.querySelector('.modal-body');
      if (modalBody && !document.getElementById('setting-session-timeout')) {
        const timeoutGroup = document.createElement('div');
        timeoutGroup.className = 'settings-group';
        timeoutGroup.style.marginTop = '16px';
        timeoutGroup.innerHTML = `
          <label class="settings-label" style="display: flex; align-items: center; gap: 6px;"><i class="fa-solid fa-clock-rotate-left text-blue"></i> Session Timeout (Auto-Logout)</label>
          <p class="settings-desc">Automatically logs out your account if the site is closed or inactive.</p>
          <div class="select-wrapper">
            <select id="setting-session-timeout" class="control-select">
              <option value="5">5 Minutes (Strict Security)</option>
              <option value="15">15 Minutes (Default)</option>
              <option value="30">30 Minutes</option>
              <option value="60">1 Hour</option>
              <option value="240">4 Hours</option>
            </select>
          </div>
        `;
        modalBody.appendChild(timeoutGroup);
      }
    }

    function syncSettingsUI() {
      const timeoutSel = document.getElementById('setting-session-timeout');
      if (timeoutSel) {
        timeoutSel.value = getSessionTimeoutMinutes().toString();
      }
    }

    if (btnOpenSettings && settingsModal) {
      btnOpenSettings.addEventListener('click', () => {
        syncSettingsUI();
        settingsModal.style.display = 'flex';
      });
    }

    if (btnCloseSettings && settingsModal) {
      btnCloseSettings.addEventListener('click', () => {
        settingsModal.style.display = 'none';
      });
    }

    if (btnDismissSettings && settingsModal) {
      btnDismissSettings.addEventListener('click', () => {
        settingsModal.style.display = 'none';
      });
    }

    if (btnSaveSettings && settingsModal) {
      btnSaveSettings.addEventListener('click', () => {
        const timeoutSel = document.getElementById('setting-session-timeout');
        if (timeoutSel) {
          setSessionTimeoutMinutes(timeoutSel.value);
        }
        settingsModal.style.display = 'none';
      });
    }
  }

  // --- RENDER AUTHENTICATED USER CIRCLE / SIGN IN BUTTON IN NAVBAR ---
  function renderNavbarUserPill() {
    const navActions = document.querySelector('.nav-actions');
    if (!navActions) return;

    let userWrapper = document.getElementById('nav-user-circle-wrapper');
    let signinBtn = document.getElementById('nav-signin-btn');
    const user = getUser();
    const token = getStoredToken();
    const isAuthenticated = Boolean(token && user && (user.name || user.username || user.email));

    if (!isAuthenticated) {
      if (userWrapper) userWrapper.style.display = 'none';
      if (!signinBtn) {
        signinBtn = document.createElement('a');
        signinBtn.id = 'nav-signin-btn';
        signinBtn.href = '/login';
        signinBtn.title = 'Sign in or create an account';
        signinBtn.className = 'nav-signin-btn';
        signinBtn.style.cssText = 'display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; font-size: 13px; font-weight: 700; color: #ffffff; background: linear-gradient(135deg, #2563eb, #1d4ed8); border-radius: 9px; text-decoration: none; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.25); transition: opacity 0.15s, transform 0.15s; margin-left: 8px;';
        signinBtn.innerHTML = '<i class="fa-solid fa-right-to-bracket"></i> Sign In';
        navActions.insertBefore(signinBtn, navActions.firstChild);
      } else {
        signinBtn.style.display = 'inline-flex';
      }
      return;
    }

    if (signinBtn) {
      signinBtn.style.display = 'none';
    }

    const displayName = (user && (user.name || user.username || user.email)) ? (user.name || user.username) : 'User';
    const emailStr = user && user.email ? user.email : '';
    const initial = displayName ? displayName.trim().charAt(0).toUpperCase() : 'U';

    if (!userWrapper) {
      userWrapper = document.createElement('div');
      userWrapper.id = 'nav-user-circle-wrapper';
      userWrapper.className = 'nav-user-circle-wrapper';
      userWrapper.style.position = 'relative';
      userWrapper.style.display = 'inline-flex';
      userWrapper.style.alignItems = 'center';
      userWrapper.style.marginLeft = '10px';

      userWrapper.innerHTML = `
        <button type="button" id="nav-user-circle-btn" title="Account profile & sign out" style="width: 33px; height: 33px; border-radius: 50%; background: linear-gradient(135deg, #2563eb, #3b82f6); color: #ffffff; font-size: 14px; font-weight: 800; border: 2px solid rgba(255, 255, 255, 0.4); display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 2px 10px rgba(37, 99, 235, 0.35); transition: transform 0.15s ease, box-shadow 0.15s ease; padding: 0; outline: none;">
          ${initial}
        </button>
        <div id="nav-user-dropdown-card" style="display: none; position: absolute; right: 0; top: 42px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; box-shadow: 0 16px 36px -4px rgba(15, 23, 42, 0.18), 0 0 1px rgba(0, 0, 0, 0.1); width: 220px; z-index: 10000; padding: 14px; text-align: left;">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px solid #f1f5f9;">
            <div style="width: 36px; height: 36px; border-radius: 50%; background: linear-gradient(135deg, #2563eb, #3b82f6); color: #ffffff; font-weight: 800; font-size: 15px; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">${initial}</div>
            <div style="overflow: hidden; flex: 1;">
              <div id="nav-dropdown-name" style="font-size: 13px; font-weight: 700; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${displayName}</div>
              <div id="nav-dropdown-email" style="font-size: 11px; color: #64748b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${emailStr}</div>
            </div>
          </div>
          <button type="button" id="nav-dropdown-logout-btn" style="width: 100%; height: 34px; background: #fef2f2; border: 1px solid #fee2e2; border-radius: 8px; color: #dc2626; font-size: 13px; font-weight: 600; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; transition: background 0.2s;">
            <i class="fa-solid fa-right-from-bracket"></i> Sign Out
          </button>
        </div>
      `;

      navActions.insertBefore(userWrapper, navActions.firstChild);

      const circleBtn = document.getElementById('nav-user-circle-btn');
      const dropdownCard = document.getElementById('nav-user-dropdown-card');
      const logoutBtn = document.getElementById('nav-dropdown-logout-btn');

      if (circleBtn && dropdownCard) {
        circleBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const isShown = dropdownCard.style.display === 'block';
          dropdownCard.style.display = isShown ? 'none' : 'block';
        });

        document.addEventListener('click', (e) => {
          if (!userWrapper.contains(e.target)) {
            dropdownCard.style.display = 'none';
          }
        });
      }

      if (logoutBtn) {
        logoutBtn.addEventListener('mouseenter', () => logoutBtn.style.background = '#fee2e2');
        logoutBtn.addEventListener('mouseleave', () => logoutBtn.style.background = '#fef2f2');
        logoutBtn.addEventListener('click', () => {
          performAutoLogout('manual_logout');
        });
      }

    } else {
      userWrapper.style.display = 'inline-flex';
      const circleBtn = document.getElementById('nav-user-circle-btn');
      const nameEl = document.getElementById('nav-dropdown-name');
      const emailEl = document.getElementById('nav-dropdown-email');
      if (circleBtn) circleBtn.textContent = initial;
      if (nameEl) nameEl.textContent = displayName;
      if (emailEl) emailEl.textContent = emailStr;
    }
  }

  // --- MOBILE TOGGLE ---
  function setupMobileMenu() {
    const btnToggle = document.getElementById('btn-mobile-toggle');
    const navLinks = document.getElementById('nav-links');
    if (btnToggle && navLinks) {
      btnToggle.addEventListener('click', () => {
        navLinks.classList.toggle('mobile-open');
      });
    }
  }

  // Sync current user profile from /api/auth/me to keep navbar and profile perfectly up-to-date
  async function syncCurrentUserProfile() {
    const token = getStoredToken();
    if (!token) return;
    try {
      const res = await fetch('/api/auth/me', {
        headers: { 'Authorization': 'Bearer ' + token }
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.user) {
          setUser(data.user);
          renderNavbarUserPill();
        }
      }
    } catch (e) {}
  }

  // --- AUTO INITIALIZE ON DOM READY ---
  document.addEventListener('DOMContentLoaded', () => {
    if (isAuthPage) return;
    initNavbarMarketStatus();
    highlightCurrentNav();
    setupMobileMenu();
    renderNavbarUserPill();
    syncCurrentUserProfile();
    setupAuthModal();
    setupSettingsModal();
    setupAudioUnlock();
    setupDesktopAlertUI();
    updateSystemStatus();
    updateVoiceUIButtons();
    document.querySelectorAll('.btn-voice-toggle').forEach(btn => {
      btn.addEventListener('click', toggleVoiceEnabled);
    });
    // Dedicated fast notification poller backed by Web Worker
    // (immune to browser background tab throttling when working in another window)
    checkRealtimeNotifications();
    startBackgroundPoller();
  });

  // Web Worker timer to evade browser background-tab timer throttling when in different windows
  let bgPollWorker = null;
  function startBackgroundPoller() {
    try {
      if (typeof Worker !== 'undefined') {
        const workerBlob = new Blob([
          `let timer = null;
           self.onmessage = function(e) {
             if (e.data === 'start') {
               if (timer) clearInterval(timer);
               timer = setInterval(() => { self.postMessage('tick'); }, 3000);
             } else if (e.data === 'stop') {
               if (timer) clearInterval(timer);
             }
           };`
        ], { type: 'application/javascript' });
        bgPollWorker = new Worker(URL.createObjectURL(workerBlob));
        bgPollWorker.onmessage = () => {
          checkRealtimeNotifications();
        };
        bgPollWorker.postMessage('start');
        return;
      }
    } catch (e) {
      console.warn('[StockPulse] Web Worker timer fallback note:', e);
    }
    setInterval(checkRealtimeNotifications, 3000);
  }

  return {
    getSelectedStock,
    setSelectedStock,
    getUser,
    setUser,
    populateStockDropdown,
    updateSystemStatus,
    playNotificationSound,
    speakVoiceAlert,
    isVoiceEnabled,
    setVoiceEnabled,
    toggleVoiceEnabled,
    showCustomizedPopup,
    showDesktopNotification,
    requestDesktopNotificationPermission,
    sendTestDesktopNotification,
    testDesktopAlertWithDelay,
    testNotificationSound,
    flashTabTitle,
    clearTabTitleFlash,
    updateDesktopAlertButtonUI,
    isSoundEnabled,
    setSoundEnabled,
    getChartMode,
    setChartMode,
    getSupertrendOverlay,
    setSupertrendOverlay,
    formatIST,
    applyMarketStatus,
    initNavbarMarketStatus,
    getCachedMarketStatus
  };
})();
