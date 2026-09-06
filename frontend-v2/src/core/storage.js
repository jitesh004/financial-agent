/* Browser-local state, namespaced per account.
 *
 * Two people on one machine must not inherit each other's ticked statements,
 * last dashboard or period - so every key written here is scoped by the signed
 * in user's id. The theme is the deliberate exception (see theme.js): dark
 * mode belongs to the screen and the person looking at it, and it has to be
 * readable before anybody is signed in at all.
 *
 * Every read and write is guarded. A private window, cleared site data, or a
 * browser configured to block storage all make these throw rather than return
 * empty, and a preference is never worth failing an interaction over.
 */

let userId = null;

export function setStorageUser(id) {
  userId = id || null;
}

export function scopedKey(key) {
  return userId ? `${key}::${userId}` : key;
}

export function read(key, fallback = null) {
  try {
    const raw = localStorage.getItem(scopedKey(key));
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function write(key, value) {
  try {
    localStorage.setItem(scopedKey(key), JSON.stringify(value));
  } catch { /* private mode, or quota */ }
}

export function remove(key) {
  try { localStorage.removeItem(scopedKey(key)); } catch { /* ignore */ }
}

/* Unscoped, for the handful of things that belong to the browser rather than
   to the account - the theme, and whether the navigation rail is collapsed. */
export function readGlobal(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeGlobal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}
