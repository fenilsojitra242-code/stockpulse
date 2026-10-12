/**
 * StockPulse — Shared Frontend Core & Notification Sound/Popup Engine
 * Handles persistent state, navigation pills, customized popup HUD cards,
 * Web Audio API chime synthesis, desktop OS notifications, and live status.
 */

window.StockPulse = (function () {
  'use strict';

  // Shared state across all separate pages
  const STORAGE_KEY_STOCK = 'stockpulse_selected_stock';
  const STORAGE_KEY_USER = 'stockpulse_user_profile';
  const STORAGE_KEY_TOKEN = 'stockpulse_jwt_token';
  const STORAGE_KEY_SOUND = 'stockpulse_sound_enabled';

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

  // --- AUDIO SYNTHESIZER (WEB AUDIO API) ---
  let audioCtx = null;

  function isSoundEnabled() {
    const val = localStorage.getItem(STORAGE_KEY_SOUND);
    return val === null ? true : val === 'true';
  }

  function setSoundEnabled(enabled) {
    localStorage.setItem(STORAGE_KEY_SOUND, enabled ? 'true' : 'false');
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
        // Crisp Dual Sonar Ping (C6 -> E6) for 1-point proximity
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
    const val = localStorage.getItem(STORAGE_KEY_VOICE);
    return val === null ? true : val === 'true';
  }

  function setVoiceEnabled(enabled) {
    localStorage.setItem(STORAGE_KEY_VOICE, enabled ? 'true' : 'false');
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
        textToSay = `SuperTrend hit! Buy signal for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Trend flipped bullish.`;
      } else if (ev === 'SUPERTREND_SELL') {
        textToSay = `SuperTrend hit! Sell signal for ${sym}${currP ? ' at ' + currP + ' rupees' : ''}. Trend flipped bearish.`;
      } else if (ev === 'SUPERTREND_APPROACHING') {
        textToSay = `SuperTrend alert. ${sym} is within one point of the SuperTrend line.`;
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

    if (userTriggered) {
      try {
        const perm = await Notification.requestPermission();
        updateDesktopAlertButtonUI();
        dismissDesktopAlertBanner(false);
        if (perm === 'granted') {
          playNotificationSound('SUCCESS');
          sendTestDesktopNotification(true);
        } else if (perm === 'denied') {
          showDesktopPermissionBlockedModal();
        }
        return perm;
      } catch (err) {
        console.warn('[StockPulse] Notification.requestPermission error:', err);
      }
    } else {
      updateDesktopAlertButtonUI();
      return Notification.permission;
    }
  }

  function showDesktopNotification(title, message, tag, notifData) {
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
      const notif = new Notification(alertSummary, {
        body: cleanBody || 'Real-time market movement or prediction signal triggered.',
        icon: '/static/favicon.ico',
        badge: '/static/favicon.ico',
        tag: notifTag,
        renotify: true,
        requireInteraction: true, // Keep notification pinned on Windows until user clicks or dismisses
        silent: false
      });

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
      btn.innerHTML = '<i class="fa-solid fa-bell"></i> <span>Alerts: ON</span>';
      btn.title = 'Desktop alerts active in other windows! Click to test alert.';
    } else if (perm === 'denied') {
      btn.classList.add('denied');
      btn.innerHTML = '<i class="fa-solid fa-bell-slash"></i> <span>Alerts: Blocked</span>';
      btn.title = 'Desktop alerts blocked in browser. Click for fix instructions.';
    } else {
      btn.classList.add('default');
      btn.innerHTML = '<i class="fa-solid fa-bell"></i> <span>Alerts</span>';
      btn.title = 'Click to enable desktop alerts across other windows & apps';
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

      let eventClass = 'monitoring-started';
      let iconHtml = '<i class="fa-solid fa-play"></i>';
      let badgeTitle = 'Prediction Active';
      if (isSuperTrendBuy) {
        eventClass = 'supertrend-buy';
        iconHtml = '<i class="fa-solid fa-arrow-trend-up"></i>';
        badgeTitle = 'SuperTrend HIT: BUY Signal!';
      } else if (isSuperTrendSell) {
        eventClass = 'supertrend-sell';
        iconHtml = '<i class="fa-solid fa-arrow-trend-down"></i>';
        badgeTitle = 'SuperTrend HIT: SELL Signal!';
      } else if (isSuperTrendApproaching) {
        eventClass = 'supertrend-approaching';
        iconHtml = '<i class="fa-solid fa-bolt"></i>';
        badgeTitle = 'SuperTrend Proximity (≤ 1 Pt)';
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
      if (isSuperTrendBuy) {
        moveText = 'BUY SIGNAL';
      } else if (isSuperTrendSell) {
        moveText = 'SELL SIGNAL';
      } else if (isSuperTrendApproaching) {
        moveText = 'TESTING LINE';
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
      if (targetPct && movement != null && !isSuperTrendBuy && !isSuperTrendSell && !isSuperTrendApproaching) {
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
      if (isSuperTrendBuy || isSuperTrendSell || isSuperTrendApproaching) {
        const stPriceNum = extra.supertrend_price != null ? Number(extra.supertrend_price) : null;
        const stPriceStr = stPriceNum ? `₹${stPriceNum.toFixed(2)}` : 'Line';
        const distPts = extra.distance_points != null ? Number(extra.distance_points).toFixed(2) : '0.00';
        const signalLabel = isSuperTrendBuy ? 'BUY (BULLISH)' : (isSuperTrendSell ? 'SELL (BEARISH)' : (extra.current_direction || 'TESTING'));
        const signalColor = isSuperTrendBuy ? '#16a34a' : (isSuperTrendSell ? '#dc2626' : '#f59e0b');

        metricsGridHtml = `
          <div class="popup-metrics-grid">
            <div class="popup-metric-item">
              <span class="popup-metric-label">Live Price</span>
              <span class="popup-metric-value">${currentPrice}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">SuperTrend Line</span>
              <span class="popup-metric-value" style="color: ${signalColor}; font-weight: 700;">${stPriceStr}</span>
            </div>
            <div class="popup-metric-item">
              <span class="popup-metric-label">${isSuperTrendApproaching ? 'Gap to Line' : 'Signal'}</span>
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
          <div class="popup-stock-row">
            <div class="popup-symbol-badge">
              ${trendIcon} ${escapeHtml(symbol)}
            </div>
            <span class="popup-change-pill ${moveClass}">
              ${arrowIcon}
              ${moveText}
            </span>
          </div>
          ${metricsGridHtml}
          ${gaugeHtml}
          <p class="popup-message-text">${escapeHtml(notifData.short_summary || notifData.message || 'Alert threshold reached.')}</p>
        </div>
        <div class="popup-footer">
          <a href="/live?symbol=${encodeURIComponent(symbol)}" class="popup-action-btn primary"><i class="fa-solid fa-chart-line"></i> View Chart</a>
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
    let s = String(str).trim();
    if (!s.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(s)) {
      s += 'Z';
    }
    const t = new Date(s).getTime();
    return isNaN(t) ? 0 : t;
  }

  // --- REAL-TIME NOTIFICATION POLLER ---
  async function checkRealtimeNotifications() {
    try {
      const res = await fetch('/api/notifications?limit=10');
      if (!res.ok) return;
      const data = await res.json();
      const notifs = data.notifications || [];

      if (isFirstNotifCheck) {
        isFirstNotifCheck = false;
        const now = Date.now();
        // On initial page load or navigation between pages, mark only OLD notifications (>45s) as known.
        // Brand new notifications arriving within the last 45s will still alert the user!
        for (const n of notifs) {
          const createdAt = parseServerDate(n.created_at);
          if (createdAt && (now - createdAt > 45000)) {
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

  // --- STOCK SELECTOR PERSISTENCE ---
  function getSelectedStock() {
    return localStorage.getItem(STORAGE_KEY_STOCK) || 'TCS';
  }

  function setSelectedStock(symbol) {
    if (!symbol) return;
    const clean = symbol.toUpperCase().replace(/\.(NS|BO)$/, '');
    localStorage.setItem(STORAGE_KEY_STOCK, clean);
    return clean;
  }

  function getUser() {
    try {
      const data = localStorage.getItem(STORAGE_KEY_USER);
      return data ? JSON.parse(data) : null;
    } catch (e) {
      return null;
    }
  }

  function getAuthToken() {
    return localStorage.getItem(STORAGE_KEY_TOKEN);
  }

  function setAuthToken(token) {
    if (!token) {
      localStorage.removeItem(STORAGE_KEY_TOKEN);
    } else {
      localStorage.setItem(STORAGE_KEY_TOKEN, token);
    }
  }

  function authFetch(url, options = {}) {
    const token = getAuthToken();
    const headers = Object.assign({}, options.headers || {});
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return fetch(url, Object.assign({}, options, { headers }));
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

  // --- MARKET STATUS & NOTIFICATION BADGE POLLING ---
  async function updateSystemStatus() {
    try {
      const sym = getSelectedStock();
      const res = await fetch(`/api/stocks/${sym}/live`);
      if (res.ok) {
        const data = await res.json();
        const dot = document.getElementById('market-status-dot');
        const text = document.getElementById('market-status-text');
        if (dot && text) {
          const isOpen = data.market_status === 'OPEN';
          dot.className = `pulse-dot ${isOpen ? 'green' : 'amber'}`;
          text.textContent = `Market: ${data.market_status || 'OPEN'}`;
        }
      }
    } catch (e) {
      // quiet fallback
    }

    try {
      const notifRes = await fetch('/api/notifications');
      if (notifRes.ok) {
        const notifData = await notifRes.json();
        const unreadCount = notifData.unread_count || 0;
        
        const badge1 = document.getElementById('notif-unread-badge');
        if (badge1) {
          badge1.textContent = unreadCount;
          badge1.style.display = unreadCount > 0 ? 'inline-block' : 'none';
        }
      }
    } catch (e) {
      // quiet fallback
    }

    // Check for newly arriving real-time notifications to trigger sound & popup
    await checkRealtimeNotifications();
  }

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

  // --- MODERN USER AUTHENTICATION & PROFILE SYSTEM ---
  function setupAuthModal() {
    // 1. Ensure modal container exists in DOM
    let authModal = document.getElementById('auth-modal-backdrop');
    if (!authModal) {
      authModal = document.createElement('div');
      authModal.id = 'auth-modal-backdrop';
      authModal.className = 'modal-backdrop';
      authModal.style.display = 'none';
      authModal.innerHTML = `
        <div class="auth-modal-card" role="dialog" aria-modal="true">
          <!-- Auth Form View (Sign In / Register) -->
          <div id="auth-view-form">
            <div class="modal-header" style="padding: 18px 24px 14px 24px; border-bottom: none;">
              <div class="modal-icon-badge" style="background: rgba(37, 99, 235, 0.1); color: #2563eb;">
                <i class="fa-solid fa-shield-halved"></i>
              </div>
              <div class="modal-header-titles">
                <h3 class="modal-title" id="auth-modal-heading" style="font-size: 17px; margin-bottom: 2px;">StockPulse Account</h3>
                <p class="modal-subtitle" id="auth-modal-subheading" style="font-size: 12.5px;">Sign in to sync your active stock alerts and monitors</p>
              </div>
              <button type="button" class="modal-close-btn" id="btn-close-auth-modal"><i class="fa-solid fa-xmark"></i></button>
            </div>
            
            <div class="auth-tabs-header">
              <button type="button" class="auth-tab-btn active" id="tab-auth-login">Sign In</button>
              <button type="button" class="auth-tab-btn" id="tab-auth-register">Create Account</button>
            </div>

            <form id="form-auth-submit" class="auth-modal-content">
              <div id="auth-alert-message" class="auth-alert-box" style="display: none;"></div>

              <div class="auth-input-group" id="group-auth-name" style="display: none;">
                <label class="auth-input-label" for="auth-input-name">Full Name</label>
                <div class="auth-input-wrapper">
                  <i class="fa-solid fa-user auth-input-icon"></i>
                  <input type="text" id="auth-input-name" class="auth-input-field" placeholder="e.g. Fenil Sojitra" autocomplete="name">
                </div>
              </div>

              <div class="auth-input-group">
                <label class="auth-input-label" for="auth-input-email">Email Address</label>
                <div class="auth-input-wrapper">
                  <i class="fa-solid fa-envelope auth-input-icon"></i>
                  <input type="email" id="auth-input-email" class="auth-input-field" placeholder="you@example.com" required autocomplete="email">
                </div>
              </div>

              <div class="auth-input-group">
                <label class="auth-input-label" for="auth-input-password">Password</label>
                <div class="auth-input-wrapper">
                  <i class="fa-solid fa-lock auth-input-icon"></i>
                  <input type="password" id="auth-input-password" class="auth-input-field" placeholder="At least 6 characters" required autocomplete="current-password">
                  <button type="button" class="auth-toggle-pwd" id="btn-toggle-pwd" title="Show/Hide Password">
                    <i class="fa-solid fa-eye" id="icon-toggle-pwd"></i>
                  </button>
                </div>
              </div>

              <button type="submit" class="auth-submit-btn" id="btn-submit-auth">
                <span id="auth-btn-text">Sign In</span>
                <i class="fa-solid fa-arrow-right" id="auth-btn-icon"></i>
              </button>
            </form>
          </div>

          <!-- Profile View (When Logged In) -->
          <div id="auth-view-profile" style="display: none;">
            <div class="modal-header" style="padding: 18px 24px 14px 24px;">
              <div class="modal-icon-badge" style="background: rgba(16, 185, 129, 0.1); color: #10b981;">
                <i class="fa-solid fa-circle-check"></i>
              </div>
              <div class="modal-header-titles">
                <h3 class="modal-title" style="font-size: 17px; margin-bottom: 2px;">Your Account</h3>
                <p class="modal-subtitle" style="font-size: 12.5px;">Manage active monitors and alerts</p>
              </div>
              <button type="button" class="modal-close-btn" id="btn-close-profile-modal"><i class="fa-solid fa-xmark"></i></button>
            </div>

            <div class="auth-modal-content">
              <div class="auth-profile-badge">
                <div class="auth-profile-avatar" id="auth-profile-avatar">U</div>
                <div class="auth-profile-details">
                  <div class="auth-profile-name" id="auth-profile-name">User</div>
                  <div class="auth-profile-email" id="auth-profile-email">user@example.com</div>
                </div>
              </div>

              <div class="auth-stats-grid">
                <div class="auth-stat-card">
                  <div class="auth-stat-value" id="auth-stat-alerts">0</div>
                  <div class="auth-stat-label">Active Monitors</div>
                </div>
                <div class="auth-stat-card">
                  <div class="auth-stat-value" style="color: #16a34a;"><i class="fa-solid fa-shield-check"></i></div>
                  <div class="auth-stat-label">Verified Session</div>
                </div>
              </div>

              <div style="margin-bottom: 16px;">
                <button type="button" onclick="StockPulse.testNotificationSound()" class="btn-primary-action"
                  style="width: 100%; justify-content: center; height: 38px; font-size: 13px;">
                  <i class="fa-solid fa-play"></i> Play Test Audio Chime &amp; Popup
                </button>
              </div>

              <button type="button" class="auth-logout-btn" id="btn-logout-auth">
                <i class="fa-solid fa-arrow-right-from-bracket"></i>
                <span>Sign Out</span>
              </button>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(authModal);
    }

    // 2. Inject Nav Auth button into .nav-actions if not present
    const navActions = document.querySelector('.nav-actions');
    let navAuthBtn = document.getElementById('btn-open-auth-modal');
    if (navActions && !navAuthBtn) {
      navAuthBtn = document.createElement('button');
      navAuthBtn.type = 'button';
      navAuthBtn.id = 'btn-open-auth-modal';
      navAuthBtn.className = 'nav-auth-btn';
      navAuthBtn.title = 'Account & Authentication';
      navAuthBtn.innerHTML = `
        <i class="fa-solid fa-circle-user"></i>
        <span id="nav-auth-label">Sign In</span>
      `;
      const mobileToggle = document.getElementById('btn-mobile-toggle');
      if (mobileToggle) {
        navActions.insertBefore(navAuthBtn, mobileToggle);
      } else {
        navActions.appendChild(navAuthBtn);
      }
    }

    // 3. UI State Elements
    let currentAuthMode = 'login'; // 'login' | 'register'
    const viewForm = document.getElementById('auth-view-form');
    const viewProfile = document.getElementById('auth-view-profile');
    const tabLogin = document.getElementById('tab-auth-login');
    const tabRegister = document.getElementById('tab-auth-register');
    const groupName = document.getElementById('group-auth-name');
    const inputName = document.getElementById('auth-input-name');
    const inputEmail = document.getElementById('auth-input-email');
    const inputPwd = document.getElementById('auth-input-password');
    const btnTogglePwd = document.getElementById('btn-toggle-pwd');
    const iconTogglePwd = document.getElementById('icon-toggle-pwd');
    const formSubmit = document.getElementById('form-auth-submit');
    const btnSubmit = document.getElementById('btn-submit-auth');
    const btnSubmitText = document.getElementById('auth-btn-text');
    const alertBox = document.getElementById('auth-alert-message');
    const btnCloseAuth = document.getElementById('btn-close-auth-modal');
    const btnCloseProfile = document.getElementById('btn-close-profile-modal');
    const btnLogout = document.getElementById('btn-logout-auth');

    const profileAvatar = document.getElementById('auth-profile-avatar');
    const profileName = document.getElementById('auth-profile-name');
    const profileEmail = document.getElementById('auth-profile-email');
    const statAlerts = document.getElementById('auth-stat-alerts');
    const navAuthLabel = document.getElementById('nav-auth-label');

    function showAlert(msg, isError = true) {
      if (!alertBox) return;
      alertBox.className = 'auth-alert-box ' + (isError ? 'error' : 'success');
      alertBox.innerHTML = `
        <i class="fa-solid ${isError ? 'fa-triangle-exclamation' : 'fa-circle-check'}"></i>
        <span>${msg}</span>
      `;
      alertBox.style.display = 'flex';
    }

    function hideAlert() {
      if (alertBox) alertBox.style.display = 'none';
    }

    function setAuthMode(mode) {
      currentAuthMode = mode;
      hideAlert();
      if (mode === 'login') {
        if (tabLogin) tabLogin.classList.add('active');
        if (tabRegister) tabRegister.classList.remove('active');
        if (groupName) groupName.style.display = 'none';
        if (inputName) inputName.required = false;
        if (btnSubmitText) btnSubmitText.textContent = 'Sign In';
        if (inputPwd) inputPwd.placeholder = 'Your password';
      } else {
        if (tabLogin) tabLogin.classList.remove('active');
        if (tabRegister) tabRegister.classList.add('active');
        if (groupName) groupName.style.display = 'block';
        if (inputName) inputName.required = true;
        if (btnSubmitText) btnSubmitText.textContent = 'Create Account';
        if (inputPwd) inputPwd.placeholder = 'At least 6 characters';
      }
    }

    function updateNavAndModalViews() {
      const token = getAuthToken();
      const user = getUser();

      if (token && user && user.email) {
        // User is authenticated
        const displayName = user.name || user.email.split('@')[0];
        const initial = displayName.charAt(0).toUpperCase();

        if (navAuthBtn) {
          navAuthBtn.classList.add('logged-in');
          navAuthBtn.innerHTML = `
            <span class="nav-user-initials">${initial}</span>
            <span id="nav-auth-label">${displayName}</span>
          `;
        }

        if (viewForm) viewForm.style.display = 'none';
        if (viewProfile) viewProfile.style.display = 'block';
        if (profileAvatar) profileAvatar.textContent = initial;
        if (profileName) profileName.textContent = displayName;
        if (profileEmail) profileEmail.textContent = user.email;
        if (statAlerts) statAlerts.textContent = user.active_alerts_count || '0';
      } else {
        // Guest mode
        if (navAuthBtn) {
          navAuthBtn.classList.remove('logged-in');
          navAuthBtn.innerHTML = `
            <i class="fa-solid fa-circle-user"></i>
            <span id="nav-auth-label">Sign In</span>
          `;
        }

        if (viewForm) viewForm.style.display = 'block';
        if (viewProfile) viewProfile.style.display = 'none';
      }
    }

    function isUserAuthenticated() {
      const token = getAuthToken();
      const user = getUser();
      return !!(token && user && user.email);
    }

    function lockApplication() {
      document.body.classList.add('auth-locked');
      authModal.classList.add('mandatory-auth-gate');
      authModal.style.display = 'flex';
      setAuthMode('login');
      const heading = document.getElementById('auth-modal-heading');
      const subheading = document.getElementById('auth-modal-subheading');
      if (heading) heading.textContent = 'Sign In to StockPulse';
      if (subheading) subheading.textContent = 'Authentication is required to view live market data, charts, and alerts.';
      updateNavAndModalViews();
      if (inputEmail) setTimeout(() => inputEmail.focus(), 150);
    }

    function unlockApplication() {
      document.body.classList.remove('auth-locked');
      authModal.classList.remove('mandatory-auth-gate');
      authModal.style.display = 'none';
      const heading = document.getElementById('auth-modal-heading');
      const subheading = document.getElementById('auth-modal-subheading');
      if (heading) heading.textContent = 'StockPulse Account';
      if (subheading) subheading.textContent = 'Manage active monitors and profile settings';
      updateNavAndModalViews();
    }

    // Tab buttons
    if (tabLogin) tabLogin.addEventListener('click', () => setAuthMode('login'));
    if (tabRegister) tabRegister.addEventListener('click', () => setAuthMode('register'));

    // Password visibility toggle
    if (btnTogglePwd && inputPwd && iconTogglePwd) {
      btnTogglePwd.addEventListener('click', () => {
        if (inputPwd.type === 'password') {
          inputPwd.type = 'text';
          iconTogglePwd.className = 'fa-solid fa-eye-slash';
        } else {
          inputPwd.type = 'password';
          iconTogglePwd.className = 'fa-solid fa-eye';
        }
      });
    }

    // Open Modal
    function openModal() {
      updateNavAndModalViews();
      hideAlert();
      authModal.style.display = 'flex';
      if (!isUserAuthenticated() && inputEmail) {
        setTimeout(() => inputEmail.focus(), 150);
      }
    }

    function closeModal() {
      // If user is not logged in, enforce the mandatory gate - cannot close!
      if (!isUserAuthenticated()) {
        const card = authModal.querySelector('.auth-modal-card');
        if (card) {
          card.classList.add('auth-shake');
          setTimeout(() => card.classList.remove('auth-shake'), 400);
        }
        return;
      }
      authModal.style.display = 'none';
      hideAlert();
    }

    if (navAuthBtn) navAuthBtn.addEventListener('click', openModal);
    if (btnCloseAuth) btnCloseAuth.addEventListener('click', closeModal);
    if (btnCloseProfile) btnCloseProfile.addEventListener('click', closeModal);

    // Also support any legacy trigger buttons (#btn-open-login)
    const legacyOpenBtn = document.getElementById('btn-open-login');
    if (legacyOpenBtn) legacyOpenBtn.addEventListener('click', openModal);

    // Close on backdrop click (blocked if not authenticated)
    authModal.addEventListener('click', (e) => {
      if (e.target === authModal) {
        if (!isUserAuthenticated()) {
          const card = authModal.querySelector('.auth-modal-card');
          if (card) {
            card.classList.add('auth-shake');
            setTimeout(() => card.classList.remove('auth-shake'), 400);
          }
          return;
        }
        closeModal();
      }
    });

    // Form submission (Login / Register)
    if (formSubmit) {
      formSubmit.addEventListener('submit', async (e) => {
        e.preventDefault();
        hideAlert();

        const email = inputEmail ? inputEmail.value.trim().toLowerCase() : '';
        const password = inputPwd ? inputPwd.value : '';
        const name = inputName ? inputName.value.trim() : '';

        if (!email || !password) {
          showAlert('Please provide both email and password.');
          return;
        }

        if (currentAuthMode === 'register' && password.length < 6) {
          showAlert('Password must be at least 6 characters.');
          return;
        }

        if (btnSubmit) {
          btnSubmit.disabled = true;
          if (btnSubmitText) btnSubmitText.textContent = currentAuthMode === 'login' ? 'Signing In...' : 'Creating Account...';
        }

        try {
          const endpoint = currentAuthMode === 'login' ? '/api/auth/login' : '/api/auth/register';
          const payload = currentAuthMode === 'login' 
            ? { email, password }
            : { name: name || email.split('@')[0], email, password };

          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
          });

          const data = await res.json();

          if (!res.ok) {
            showAlert(data.detail || data.message || 'Authentication failed. Please verify your details.');
            return;
          }

          // Successful authentication!
          if (data.token || data.access_token) {
            setAuthToken(data.token || data.access_token);
          }
          if (data.user) {
            setUser(data.user);
          }

          showAlert(data.message || 'Success! Welcome to StockPulse.', false);
          unlockApplication();

          setTimeout(() => {
            if (inputPwd) inputPwd.value = '';
          }, 800);

        } catch (err) {
          console.error('[StockPulse Auth Error]:', err);
          showAlert('Network connection error. Please ensure the server is reachable.');
        } finally {
          if (btnSubmit) {
            btnSubmit.disabled = false;
            setAuthMode(currentAuthMode);
          }
        }
      });
    }

    // Logout - immediately re-locks application
    if (btnLogout) {
      btnLogout.addEventListener('click', async () => {
        try {
          await authFetch('/api/auth/logout', { method: 'POST' });
        } catch (e) {}
        setAuthToken(null);
        setUser(null);
        lockApplication();
      });
    }

    // Verify session on page load
    async function verifySession() {
      const token = getAuthToken();
      if (!token) {
        lockApplication();
        return;
      }
      try {
        const res = await fetch('/api/auth/me', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.ok) {
          const data = await res.json();
          if (data.logged_in && data.user) {
            setUser(data.user);
            unlockApplication();
          } else {
            setAuthToken(null);
            setUser(null);
            lockApplication();
          }
        } else {
          setAuthToken(null);
          setUser(null);
          lockApplication();
        }
      } catch (e) {
        console.warn('[StockPulse] Session verification check:', e);
        if (getUser()) {
          unlockApplication();
        } else {
          lockApplication();
        }
      }
    }

    // Immediate check on load
    if (!getAuthToken()) {
      lockApplication();
    }
    verifySession();
  }

  // --- SETTINGS MODAL LOGIC ---
  function setupSettingsModal() {
    const btnOpenSettings = document.getElementById('btn-open-settings');
    const settingsModal = document.getElementById('settings-modal-backdrop');
    const btnCloseSettings = document.getElementById('btn-close-settings');
    const btnDismissSettings = document.getElementById('btn-dismiss-settings');
    const btnSaveSettings = document.getElementById('btn-save-settings');

    if (btnOpenSettings && settingsModal) {
      btnOpenSettings.addEventListener('click', () => {
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
        settingsModal.style.display = 'none';
      });
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

  // --- AUTO INITIALIZE ON DOM READY ---
  document.addEventListener('DOMContentLoaded', () => {
    highlightCurrentNav();
    setupMobileMenu();
    setupAuthModal();
    setupSettingsModal();
    setupAudioUnlock();
    setupDesktopAlertUI();
    updateSystemStatus();
    updateVoiceUIButtons();
    document.querySelectorAll('.btn-voice-toggle').forEach(btn => {
      btn.addEventListener('click', toggleVoiceEnabled);
    });
    setInterval(updateSystemStatus, 2500);
    // Dedicated fast notification poller (every 1.2 seconds, independent of stock quote fetching)
    checkRealtimeNotifications();
    setInterval(checkRealtimeNotifications, 1200);
  });

  return {
    getSelectedStock,
    setSelectedStock,
    getUser,
    setUser,
    getAuthToken,
    setAuthToken,
    authFetch,
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
    isSoundEnabled,
    setSoundEnabled,
    formatIST
  };
})();
