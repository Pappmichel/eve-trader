// A deploy replaces the frontend's hashed JS/CSS chunk filenames
// (Vite build output). A tab left open across a deploy still references the
// *old* filenames in its already-loaded index.html/module graph - the first
// dynamic import() after that (a lazy-loaded route chunk) fails because that
// file no longer exists on the server, throwing one of the messages below.
// Confirmed real 2026-09-27: a tenant on evetrader.duckdns.org hit exactly
// this navigating to /trading a few minutes after a deploy replaced
// TradingLayout's chunk hash - reported to them as "can't load shit".
//
// This is not a real bug in the page - a single reload always fixes it,
// since it fetches the current index.html with the current chunk manifest.
// ErrorBoundary uses these helpers to do that reload automatically instead
// of showing the "Something went wrong" screen for something the user did
// nothing to cause and can't act on except by reloading anyway.
const CHUNK_LOAD_ERROR_PATTERN =
  /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed/i

const RELOAD_ATTEMPTED_KEY = 'eve-trader-chunk-reload-attempted'

export function isChunkLoadError(error: Error): boolean {
  return CHUNK_LOAD_ERROR_PATTERN.test(error.message)
}

// One automatic reload per tab session - if the error recurs right after
// reloading, it's a real failure (a genuine 404/network issue), not a stale
// chunk, and looping reloads would only hide that. Returns whether this call
// is the one that should trigger the reload.
export function shouldAutoReloadForChunkError(): boolean {
  try {
    if (sessionStorage.getItem(RELOAD_ATTEMPTED_KEY) === '1') return false
    sessionStorage.setItem(RELOAD_ATTEMPTED_KEY, '1')
    return true
  } catch {
    // sessionStorage unavailable (private browsing, storage blocked) - skip
    // the auto-reload rather than risk looping with no way to remember.
    return false
  }
}

// Called once the app has been running without error for a bit, so a later,
// separate deploy gets its own auto-reload attempt instead of being silently
// blocked by an attempt flag left over from an earlier, unrelated reload.
export function clearChunkReloadAttemptFlag(): void {
  try {
    sessionStorage.removeItem(RELOAD_ATTEMPTED_KEY)
  } catch {
    // Nothing to clear if storage isn't available.
  }
}
