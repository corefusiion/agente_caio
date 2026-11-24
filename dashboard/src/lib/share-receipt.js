// Fail closed pending successful end-to-end security validation.
// No service worker is registered and no shared content is collected.
export function startShareReceipt() {
  return Promise.resolve(location.pathname.endsWith('/share-receive') ? {error:true} : null);
}
