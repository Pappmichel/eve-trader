// In-app navigation guard for the layout editor. The app uses <BrowserRouter>
// (no data router, so no useBlocker): the editor registers a check here and the
// PI tab bar asks it before navigating away.
let guard: (() => boolean) | null = null

export function setUnsavedGuard(hasUnsaved: (() => boolean) | null): void {
  guard = hasUnsaved
}

// True when it is fine to leave (nothing unsaved, or the user confirmed).
export function confirmLeave(): boolean {
  if (!guard || !guard()) return true
  return window.confirm('You have unsaved edits in the layout editor. Leave without saving?')
}
