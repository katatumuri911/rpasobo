// RP Image Chat - Service Worker for Web Push Notifications
// version: 2.1 (Step 1-2 state schema expansion)
const SW_VERSION = "2.2";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data = { title: "RP Image Chat", body: event.data.text() };
    }
  }

  const title = data.title || "RP Image Chat";
  const options = {
    body: data.body || "",
    icon: new URL(data.icon || "/icon.png", self.location.origin).href,
    badge: new URL(data.badge || "/icon.png", self.location.origin).href,
    tag: data.tag || "rpchat-notification",
    renotify: true,
    data: data.data || {}
  };

  if (data.image) {
    try {
      options.image = new URL(data.image, self.location.origin).href;
    } catch (e) {
      options.image = data.image;
    }
  }

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const sid = data.sessionId || "";
  const mid = data.messageId || "";
  const targetPath = sid ? `/?sid=${encodeURIComponent(sid)}&mid=${encodeURIComponent(mid)}` : (data.url || "/");
  const targetUrl = new URL(targetPath, self.location.origin).href;

  event.waitUntil((async () => {
    try {
      const clientList = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of clientList) {
        if ("focus" in client) {
          try {
            await client.focus();
            if (sid) {
              client.postMessage({
                type: "NAVIGATE_TO",
                sessionId: sid,
                messageId: mid
              });
            }
            return;
          } catch (focusErr) {
            console.warn("[SW] focus error:", focusErr);
          }
        }
      }
    } catch (e) {
      console.warn("[SW] matchAll error:", e);
    }
    if (clients.openWindow) {
      return clients.openWindow(targetUrl);
    }
  })());
});
