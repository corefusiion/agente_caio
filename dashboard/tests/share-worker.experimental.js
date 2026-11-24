// Share contents live only in this worker's volatile memory, indexed by the
// unforgeable navigation client ID. Never persist, redirect with data, or log.
const scope = new URL(self.registration.scope);
const receivePath = new URL('share-receive', scope).pathname;
const pending = new Map();
const TTL = 30000;
const MAX_BODY = 32768;
const MAX_TEXT = 8192;
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

async function parseShare(request) {
  const type = (request.headers.get('content-type') || '').toLowerCase();
  if (!/^application\/x-www-form-urlencoded(?:\s*;|$)/.test(type) && !/^multipart\/form-data\s*;\s*boundary=/.test(type)) throw Error('type');
  const reader = request.body?.getReader();
  if (!reader) throw Error('body');
  const chunks = []; let size = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY) { await reader.cancel(); throw Error('size'); }
    chunks.push(value);
  }
  const form = await new Request(request.url, {method:'POST', headers:{'Content-Type': request.headers.get('content-type')}, body:new Blob(chunks)}).formData();
  const values = [];
  for (const [name, value] of form.entries()) {
    if (!['title', 'text', 'url'].includes(name) || typeof value !== 'string' || form.getAll(name).length !== 1) throw Error('field');
  }
  for (const name of ['title', 'text', 'url']) {
    const value = form.get(name);
    if (typeof value === 'string' && value.trim()) values.push(value.trim());
  }
  const text = values.join('\n');
  if (!text || text.length > MAX_TEXT) throw Error('text');
  return text;
}
async function handleShare(event) {
  const clientId = event.resultingClientId;
  // Reject subresource requests and navigations whose target ID is unavailable.
  if (event.request.mode !== 'navigate' || !clientId || new URL(event.request.url).search) return new Response('Compartilhamento indisponível. Copie e cole o texto.', {status:400, headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}});
  pending.delete(clientId);
  try {
    const text = await parseShare(event.request);
    // Bound memory even if many tabs navigate without ever collecting.
    for (const [id, old] of pending) if (Date.now() >= old.expires) pending.delete(id);
    if (pending.size >= 32) throw Error('capacity');
    const item = {text, expires: Date.now() + TTL};
    pending.set(clientId, item);
    setTimeout(() => { if (pending.get(clientId) === item) pending.delete(clientId); }, TTL);
  } catch { /* The page displays a generic loss/rejection notice. */ }
  try {
    // A normal GET without the shared data, not a server POST or redirect.
    const page = await fetch(new URL('index.html', scope), {cache:'no-store'});
    if (!page.ok || page.redirected || new URL(page.url).origin !== scope.origin) throw Error('page');
    const headers = new Headers(page.headers);
    headers.set('Content-Type', 'text/html; charset=utf-8');
    headers.set('Cache-Control', 'no-store');
    headers.set('X-Content-Type-Options', 'nosniff');
    headers.delete('Content-Length');
    headers.delete('Content-Encoding');
    headers.delete('Transfer-Encoding');
    return new Response(page.body, {status:200, headers});
  } catch {
    pending.delete(clientId);
    return new Response('Não foi possível receber o compartilhamento. Copie e cole o texto.', {status:503, headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}});
  }
}
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin === scope.origin && url.pathname === receivePath && event.request.method === 'POST') event.respondWith(handleShare(event));
});
self.addEventListener('message', event => {
  if (event.data?.type !== 'CAIO_SHARE_TAKE' || !event.ports[0] || !event.source?.id) return;
  const item = pending.get(event.source.id);
  // Deleting before delivery enforces at most one collection, even on retry.
  pending.delete(event.source.id);
  const source = new URL(event.source.url);
  const valid = source.origin === scope.origin && source.pathname === receivePath && !source.search && item && Date.now() < item.expires;
  event.ports[0].postMessage(valid ? {text:item.text} : {error:true});
});
