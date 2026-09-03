/* Browser-local state, kept apart per signed-in account.
 *
 * localStorage belongs to an origin, not to a person, so a single key means
 * two people sharing a machine share the value behind it. That was correct
 * when this was a single-user program. It is not now: sign out, sign in as
 * somebody else, and you inherit their mailbox selections, their scan window,
 * their row density - state that reads as your own and is not.
 *
 * Nothing here is financial data; that all lives server-side behind
 * row-level security. These are choices. But a choice that silently arrives
 * from another account is still confusing, and "which statements are ticked
 * for import" is a choice with consequences.
 *
 * The theme is deliberately NOT scoped. Dark mode belongs to the screen and
 * the person looking at it, not to the account they happen to be signed into.
 */

let currentUser = null;

/** Called by AuthProvider whenever the signed-in user resolves or changes. */
export function setStorageUser(userId) {
  currentUser = userId || null;
}

/** `base` namespaced to the current account, or bare when signed out. */
export function scopedKey(base) {
  return currentUser ? `${base}:${currentUser}` : base;
}

export function readScoped(base, fallback = {}) {
  try {
    const raw = localStorage.getItem(scopedKey(base));
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function writeScoped(base, value) {
  try {
    localStorage.setItem(scopedKey(base), JSON.stringify(value));
  } catch {
    /* Private mode, quota. The server holds anything that matters. */
  }
}
