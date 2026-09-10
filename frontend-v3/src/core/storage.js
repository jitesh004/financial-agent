/* ────────────────────────────────────────────────────────────────────────────
   Browser-local state, namespaced per account.
   ──────────────────────────────────────────────────────────────────────── */

let userId = null;

export function setStorageUser(id) {
  userId = id || null;
}

export function scopedKey(key) {
  return userId ? `prism-v3:${key}::${userId}` : `prism-v3:${key}`;
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
  } catch {}
}

export function remove(key) {
  try { localStorage.removeItem(scopedKey(key)); } catch {}
}

export function readGlobal(key, fallback = null) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeGlobal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}
