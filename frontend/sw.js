// StockPulse - Background Service Worker for Native Multi-Window Notifications
// This service worker ensures notifications appear on top of other desktop windows (VS Code, Excel, etc.)
// even when the browser tab is minimized, in the background, or obscured.

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Handle notification click: Bring existing StockPulse window to the foreground
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // If a StockPulse tab/window is already open, focus it
      for (const client of clientList) {
        if (client.url && 'focus' in client) {
          return client.focus();
        }
      }
      // If no window is currently open, open the live market page
      if (self.clients.openWindow) {
        return self.clients.openWindow('/live');
      }
    })
  );
});

// Allow main thread to post messages to Service Worker
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SHOW_NOTIFICATION') {
    const { title, options } = event.data;
    self.registration.showNotification(title, options);
  }
});
