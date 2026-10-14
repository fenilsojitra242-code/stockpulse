/**
 * StockPulse — Real-Time System Notifications Page
 */

(function () {
  'use strict';

  let allNotifications = [];
  let filterChannel = 'ALL';
  let filterPriority = 'ALL';

  const el = {
    notifList: document.getElementById('notifications-full-list'),
    emptyState: document.getElementById('notif-empty-state'),
    unreadTotalBadge: document.getElementById('unread-total-badge'),
    btnTestSystem: document.getElementById('btn-test-system-notif'),
    btnRefresh: document.getElementById('btn-refresh-feed'),
    btnMarkAllRead: document.getElementById('btn-mark-all-feed-read'),
    btnClearAll: document.getElementById('btn-clear-all-feed'),
    channelChips: document.querySelectorAll('.channel-filter-chip'),
    priorityChips: document.querySelectorAll('.priority-filter-chip'),
    // Detail Modal
    detailModal: document.getElementById('notif-detail-modal-backdrop'),
    btnCloseDetail: document.getElementById('btn-close-notif-detail'),
    btnDismissDetail: document.getElementById('btn-dismiss-notif-detail'),
    detailTitle: document.getElementById('notif-detail-title'),
    detailBody: document.getElementById('notif-detail-message-body'),
    detailTime: document.getElementById('notif-detail-time-tag'),
    detailPriority: document.getElementById('notif-detail-priority-tag'),
    detailChannel: document.getElementById('notif-detail-channel-tag')
  };

  async function loadNotifications() {
    try {
      const res = await fetch('/api/notifications');
      if (!res.ok) return;
      const data = await res.json();
      allNotifications = data.notifications || [];
      const unread = data.unread_count || 0;

      if (el.unreadTotalBadge) {
        el.unreadTotalBadge.textContent = `${unread} Unread`;
        el.unreadTotalBadge.style.display = unread > 0 ? 'inline-block' : 'none';
      }

      renderNotifications();
    } catch (e) {
      console.warn('[Notifications] Error fetching notifications:', e);
    }
  }

  function renderNotifications() {
    if (!el.notifList) return;

    let filtered = allNotifications;

    if (filterChannel !== 'ALL') {
      if (filterChannel === 'POPUP') {
        filtered = filtered.filter(n => ['POPUP', 'BROWSER', 'DASHBOARD'].includes((n.channel || '').toUpperCase()));
      } else if (filterChannel === 'SYSTEM') {
        filtered = filtered.filter(n => (n.channel || '').toUpperCase() === 'SYSTEM' || !n.user_id || (n.symbol === 'SYSTEM') || (n.event_type || '').toUpperCase().includes('SYSTEM') || (n.event_type || '').toUpperCase().includes('UNAVAILABLE'));
      } else {
        filtered = filtered.filter(n => (n.channel || '').toUpperCase() === filterChannel);
      }
    }

    if (filterPriority !== 'ALL') {
      filtered = filtered.filter(n => (n.priority || '').toUpperCase() === filterPriority);
    }

    if (!filtered || filtered.length === 0) {
      el.notifList.innerHTML = '';
      if (el.emptyState) el.emptyState.style.display = 'block';
      return;
    }

    if (el.emptyState) el.emptyState.style.display = 'none';
    el.notifList.innerHTML = '';

    filtered.forEach(notif => {
      const item = document.createElement('div');
      item.className = `notif-card-item ${notif.is_read ? 'read' : 'unread'}`;
      item.style.cssText = `
        background: #ffffff;
        border: 1px solid ${notif.is_read ? '#e2e8f0' : '#bfdbfe'};
        border-radius: 12px;
        padding: 18px 20px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        cursor: pointer;
        transition: all 0.15s ease;
        position: relative;
      `;

      const priority = (notif.priority || 'INFO').toUpperCase();
      const channel = (notif.channel || 'DASHBOARD').toUpperCase();
      const fmt = (window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST : (s) => s ? new Date(s + 'Z').toLocaleTimeString('en-IN') : '--:--:--';
      const timeStr = fmt(notif.created_at, 'short');

      let prioBg = '#f1f5f9';
      let prioColor = '#475569';
      if (priority === 'ALERT' || priority === 'CRITICAL') { prioBg = '#fee2e2'; prioColor = '#b91c1c'; }
      else if (priority === 'WARNING') { prioBg = '#fef3c7'; prioColor = '#b45309'; }
      else if (priority === 'SUCCESS') { prioBg = '#dcfce7'; prioColor = '#15803d'; }
      else if (priority === 'IMPORTANT') { prioBg = '#ede9fe'; prioColor = '#6d28d9'; }

      const evType = (notif.event_type || '').toUpperCase();
      const isSystemAlert = !notif.user_id || (notif.symbol === 'SYSTEM') || evType.includes('SYSTEM') || evType.includes('UNAVAILABLE');
      let eventBadgeHtml = '';
      if (isSystemAlert) {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #e0e7ff; color: #4338ca; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-server"></i> System Alert</span>';
      }
      if (evType === 'SUPERTREND_APPROACHING') {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #fef3c7; color: #b45309; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-bolt"></i> SuperIndicator Early Warning</span>';
      } else if (evType === 'SUPERTREND_BUY') {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #dcfce7; color: #15803d; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-arrow-trend-up"></i> SuperIndicator Hit (TRUE)</span>';
      } else if (evType === 'SUPERTREND_SELL') {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-arrow-trend-down"></i> SuperIndicator Hit (TRUE)</span>';
      } else if (evType === 'SUPERTREND_FALSE') {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #fef2f2; color: #991b1b; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px; border: 1px solid #fecaca;"><i class="fa-solid fa-circle-xmark"></i> Model Prediction FALSE</span>';
      } else if (evType === 'SUPERTREND_CHANGED') {
        eventBadgeHtml += '<span style="font-size: 11px; font-weight: 700; background: #eff6ff; color: #1d4ed8; padding: 2px 8px; border-radius: 10px; display: inline-flex; align-items: center; gap: 4px; border: 1px solid #bfdbfe;"><i class="fa-solid fa-arrows-rotate"></i> SuperIndicator Shift</span>';
      }

      item.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            ${!notif.is_read ? '<span style="width: 8px; height: 8px; border-radius: 50%; background: #2563eb; display: inline-block;"></span>' : ''}
            <span style="font-size: 11px; font-weight: 700; background: ${prioBg}; color: ${prioColor}; padding: 2px 8px; border-radius: 10px;">${priority}</span>
            ${eventBadgeHtml}
            <span style="font-size: 11px; font-weight: 600; background: #eff6ff; color: #1d4ed8; padding: 2px 8px; border-radius: 10px;"><i class="fa-solid fa-bullhorn"></i> ${channel}</span>
            <span style="font-size: 12px; color: #64748b;">${timeStr}</span>
          </div>

          <div style="display: flex; align-items: center; gap: 8px;">
            ${!notif.is_read ? `<button type="button" class="btn-mark-single-read" data-id="${notif.id}" style="background: none; border: none; font-size: 11.5px; color: #2563eb; cursor: pointer; text-decoration: underline;">Mark read</button>` : ''}
            <button type="button" class="btn-delete-single" data-id="${notif.id}" style="background: none; border: none; font-size: 12px; color: #94a3b8; cursor: pointer; padding: 2px 5px; border-radius: 4px;" title="Clear notification">
              <i class="fa-regular fa-trash-can"></i>
            </button>
          </div>
        </div>

        <h4 style="font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 4px;">${escapeHtml(notif.title || 'System Notification')}</h4>
        <p style="font-size: 13px; color: #334155; margin: 0; line-height: 1.5; white-space: pre-line;">${escapeHtml(notif.message || '')}</p>
      `;

      item.addEventListener('click', (e) => {
        if (e.target.closest('.btn-delete-single')) {
          e.stopPropagation();
          deleteSingleNotification(notif.id);
          return;
        }
        if (e.target.classList.contains('btn-mark-single-read')) {
          e.stopPropagation();
          markRead(notif.id);
          return;
        }
        openDetailModal(notif);
      });

      el.notifList.appendChild(item);
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>"']/g, function (m) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[m];
    });
  }

  function openDetailModal(notif) {
    if (!el.detailModal) return;
    if (el.detailTitle) el.detailTitle.textContent = notif.title || 'Event Details';
    if (el.detailBody) el.detailBody.textContent = notif.message || '';
    if (el.detailTime) el.detailTime.textContent = notif.created_at ? ((window.StockPulse && StockPulse.formatIST) ? StockPulse.formatIST(notif.created_at, 'datetime') : new Date(notif.created_at + 'Z').toLocaleString('en-IN')) : '';
    if (el.detailPriority) el.detailPriority.textContent = (notif.priority || 'INFO').toUpperCase();
    if (el.detailChannel) el.detailChannel.textContent = (notif.channel || 'DASHBOARD').toUpperCase();
    el.detailModal.style.display = 'flex';

    if (!notif.is_read) {
      markRead(notif.id);
    }
  }

  async function markRead(id) {
    try {
      await fetch(`/api/notifications/${id}/read`, { method: 'PATCH' });
      loadNotifications();
    } catch (e) {}
  }

  async function markAllRead() {
    try {
      await fetch('/api/notifications/read-all', { method: 'POST' });
      loadNotifications();
    } catch (e) {}
  }

  async function clearAllNotifications() {
    if (!confirm('Are you sure you want to clear all notifications?')) return;
    try {
      await fetch('/api/notifications/clear', { method: 'POST' });
      allNotifications = [];
      if (el.unreadTotalBadge) el.unreadTotalBadge.style.display = 'none';
      renderNotifications();
    } catch (e) {
      console.warn('[Notifications] Error clearing notifications:', e);
    }
  }

  async function deleteSingleNotification(id) {
    try {
      await fetch(`/api/notifications/${id}`, { method: 'DELETE' });
      allNotifications = allNotifications.filter(n => n.id !== id);
      renderNotifications();
    } catch (e) {
      console.warn('[Notifications] Error deleting notification:', e);
    }
  }

  async function testSystemNotification() {
    try {
      if ('Notification' in window && Notification.permission === 'default') {
        try {
          await Notification.requestPermission();
          if (window.StockPulse && StockPulse.updateDesktopAlertButtonUI) {
            StockPulse.updateDesktopAlertButtonUI();
          }
        } catch (e) {}
      }

      if (el.btnTestSystem) {
        el.btnTestSystem.disabled = true;
        el.btnTestSystem.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Dispatching...';
      }
      const res = await fetch('/api/notifications/test-system', { method: 'POST' });
      if (res.ok) {
        // Play local test chime & popups
        if (window.StockPulse && StockPulse.testNotificationSound) {
          StockPulse.testNotificationSound();
        }
        await loadNotifications();
      }
    } catch (e) {
      console.warn('[Notifications] Error triggering test system notification:', e);
    } finally {
      if (el.btnTestSystem) {
        el.btnTestSystem.disabled = false;
        el.btnTestSystem.innerHTML = '<i class="fa-solid fa-bolt"></i> Test System Notification';
      }
    }
  }

  // --- FILTERS ---
  if (el.channelChips) {
    el.channelChips.forEach(chip => {
      chip.addEventListener('click', () => {
        el.channelChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        filterChannel = chip.getAttribute('data-channel').toUpperCase();
        renderNotifications();
      });
    });
  }

  if (el.priorityChips) {
    el.priorityChips.forEach(chip => {
      chip.addEventListener('click', () => {
        el.priorityChips.forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        filterPriority = chip.getAttribute('data-priority').toUpperCase();
        renderNotifications();
      });
    });
  }

  if (el.btnTestSystem) el.btnTestSystem.addEventListener('click', testSystemNotification);
  if (el.btnRefresh) el.btnRefresh.addEventListener('click', loadNotifications);
  if (el.btnMarkAllRead) el.btnMarkAllRead.addEventListener('click', markAllRead);
  if (el.btnClearAll) el.btnClearAll.addEventListener('click', clearAllNotifications);

  if (el.btnCloseDetail) el.btnCloseDetail.addEventListener('click', () => el.detailModal.style.display = 'none');
  if (el.btnDismissDetail) el.btnDismissDetail.addEventListener('click', () => el.detailModal.style.display = 'none');

  document.addEventListener('DOMContentLoaded', () => {
    loadNotifications();
    setInterval(loadNotifications, 7000);
  });

})();
