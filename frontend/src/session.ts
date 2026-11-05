// What the app shell hands the screens.

// How saving the state went. A failed save keeps the state on the page; the shell says what
// happened and offers the export, since the page is then the only copy.
export type SaveOutcome = { ok: true } | { ok: false; error: unknown }
