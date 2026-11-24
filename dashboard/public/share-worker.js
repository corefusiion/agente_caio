// Fail closed for old installations; do not accept or persist shared content.
const base = new URL(self.registration.scope);
const target = new URL('share-receive', base).pathname;
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin === base.origin && url.pathname === target && event.request.method === 'POST') {
    event.respondWith(new Response('Compartilhamento desativado por segurança.', {status:410, headers:{'Content-Type':'text/plain; charset=utf-8', 'Cache-Control':'no-store'}}));
  }
});
