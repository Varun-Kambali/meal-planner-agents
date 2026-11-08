// What the app shell hands the screens.

import type { State } from './storage'

// How saving the state went. A failed save keeps the state on the page; the shell says what
// happened and offers the export, since the page is then the only copy.
export type SaveOutcome = { ok: true } | { ok: false; error: unknown }

// What a screen asks the shell to save: the change to make, not the state it will make it to.
// The shell applies it to the state as it is when the save's turn comes, in the order the saves
// were asked for, so two saves that overlap (a meal logged while a week is being written) both
// survive. An update that returns the state it was given changes nothing and writes nothing.
export type Update = (current: State) => State
export type Save = (update: Update) => Promise<SaveOutcome>
