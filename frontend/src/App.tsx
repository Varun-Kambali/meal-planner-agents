// The app shell: it reads what this browser saved, keeps it while the page is open, saves each
// change, and shows the screen the address names. A week being written belongs to the shell, so
// it goes on while the person looks at another screen, and so do the catalogue and the log's
// summary, which are asked for once and kept until they are out of date.

import { useCallback, useEffect, useRef, useState } from 'react'

import { dateWords } from './dates'
import { saveFile, today } from './download'
import type { FormError } from './planForm'
import { useRoute, type Route } from './route'
import { DataSection } from './screens/DataSection'
import { DayView } from './screens/DayView'
import { PlanForm } from './screens/PlanForm'
import { RecipePage } from './screens/RecipePage'
import { StorageError } from './screens/StorageError'
import { Today } from './screens/Today'
import { Week } from './screens/Week'
import { WeekView } from './screens/WeekView'
import type { SaveOutcome } from './session'
import {
  ValidationFailed,
  discardStoredText,
  exportFile,
  loadState,
  readStoredText,
  saveState,
  type State,
  type StateStore,
} from './storage'
import type { MealLog } from './types'
import { useCatalogue } from './useCatalogue'
import { useSummary } from './useSummary'
import { useToday } from './useToday'
import { useWeekRun } from './useWeekRun'
import { DAY_NAMES, messageOf } from './words'

const browserStore: StateStore = { loadState, saveState, readStoredText, discardStoredText }

// The log while nothing has loaded, the same each time so the summary is not asked for again.
const NO_LOG: MealLog = { version: 1, entries: [] }

type Loaded =
  { status: 'loading' } | { status: 'ready'; state: State } | { status: 'failed'; error: unknown }

const TITLES: Record<Route['name'], string> = {
  home: 'Meal planner',
  plan: 'Your plan',
  week: 'The week',
  recipe: 'Recipe',
  recipes: 'Recipes for the week',
  log: 'Log',
  summary: 'Summary',
  day: 'Day',
  missing: 'Not found',
}

function shown(route: Route, state: State | null): Route['name'] {
  if (route.name !== 'home') return route.name
  return state?.rules == null ? 'plan' : 'week'
}

export function App({ store = browserStore }: { store?: StateStore }) {
  const [loaded, setLoaded] = useState<Loaded>({ status: 'loading' })
  const [unsaved, setUnsaved] = useState<string | null>(null)
  const [refused, setRefused] = useState<FormError[]>([])
  // Bumped when a file replaces the state, so the plan form starts again from the new rules.
  const [generation, setGeneration] = useState(0)
  const { route, moves } = useRoute()

  const load = useCallback((): void => {
    store.loadState().then(
      (state) => setLoaded({ status: 'ready', state }),
      (error: unknown) => setLoaded({ status: 'failed', error }),
    )
  }, [store])
  useEffect(() => {
    load()
  }, [load])

  // Keeps the state on the page whether or not the browser saved it, except a state that would
  // not load again, which the caller is told about and nothing changes.
  const save = useCallback(
    async (next: State): Promise<SaveOutcome> => {
      try {
        await store.saveState(next)
      } catch (error) {
        if (error instanceof ValidationFailed) return { ok: false, error }
        setLoaded({ status: 'ready', state: next })
        setUnsaved(messageOf(error))
        return { ok: false, error }
      }
      setLoaded({ status: 'ready', state: next })
      setUnsaved(null)
      return { ok: true }
    },
    [store],
  )
  const savePlan = useCallback(
    async (next: State): Promise<SaveOutcome> => {
      const outcome = await save(next)
      if (outcome.ok) setRefused([])
      return outcome
    },
    [save],
  )
  const replaceAll = useCallback(
    async (next: State): Promise<SaveOutcome> => {
      const outcome = await savePlan(next)
      if (outcome.ok || !(outcome.error instanceof ValidationFailed)) {
        setGeneration((n) => n + 1)
      }
      return outcome
    },
    [savePlan],
  )

  const state = loaded.status === 'ready' ? loaded.state : null
  const run = useWeekRun(state, save, setRefused)
  // Asked for by the first screen that needs it, and kept for the others.
  const catalogue = useCatalogue()
  const summary = useSummary(state?.rules ?? null, state?.log ?? NO_LOG)
  const now = useToday()
  // What a run is doing, for the reasons other screens give for waiting on it.
  let running: string | null = null
  if (run.running?.what === 'week') running = 'A week is being written'
  if (run.running?.what === 'replace') {
    const { day, slotId } = run.running
    const slot = state?.rules?.slots.find((s) => s.id === slotId)
    running = `${DAY_NAMES[day]}'s ${slot?.name ?? slotId} is being written again`
  }
  const name = shown(route, state)

  // A recipe page is titled with the recipe, so that tabs and history tell recipes apart.
  let title = TITLES[name]
  if (route.name === 'recipe' && state !== null) {
    const recipe = state.week?.recipes.find(
      (r) => r.spec.day === route.day && r.spec.slot.id === route.slotId,
    )
    title = recipe?.draft.title ?? 'No recipe here'
  }
  if (route.name === 'log') title = dateWords(route.date ?? now, now)
  if (route.name === 'day') title = dateWords(route.date, now)
  useEffect(() => {
    document.title = title === TITLES.home ? title : `${title}: Meal planner`
  }, [title])
  // After moving to another screen, its heading takes the focus, so a screen reader starts there.
  useEffect(() => {
    if (moves > 0) document.querySelector<HTMLElement>('[data-screen-title]')?.focus()
  }, [moves])

  const main = useRef<HTMLElement>(null)
  const nav = [
    { href: '#/plan', label: 'Plan', current: name === 'plan' },
    {
      href: '#/week',
      label: 'Week',
      current: name === 'week' || name === 'recipe' || name === 'recipes',
    },
    { href: '#/log', label: 'Log', current: name === 'log' },
    { href: '#/summary', label: 'Summary', current: name === 'summary' || name === 'day' },
  ]

  return (
    <>
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault()
          main.current?.focus()
        }}
      >
        Skip to the content
      </a>
      <header className="site-header">
        <div className="site-header-inner">
          <p className="app-name">Meal planner</p>
          <nav aria-label="Screens">
            <ul>
              {nav.map((item) => (
                <li key={item.href}>
                  <a href={item.href} aria-current={item.current ? 'page' : undefined}>
                    {item.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" ref={main} tabIndex={-1} className="page">
        {loaded.status === 'loading' && <p>Reading what this browser saved.</p>}
        {loaded.status === 'failed' && (
          <StorageError
            error={loaded.error}
            store={store}
            onReload={() => {
              setLoaded({ status: 'loading' })
              load()
            }}
          />
        )}
        {state !== null && (
          <>
            {unsaved !== null && <Unsaved message={unsaved} state={state} />}
            {name === 'plan' && (
              <PlanForm
                key={generation}
                state={state}
                refused={refused}
                onSave={savePlan}
                catalogue={catalogue}
                blocked={
                  running === null
                    ? null
                    : `${running} for the saved plan; save changes once it is done.`
                }
              />
            )}
            {name === 'week' && <Week state={state} run={run} />}
            {route.name === 'recipe' && (
              <RecipePage
                state={state}
                day={route.day}
                slotId={route.slotId}
                catalogue={catalogue}
              />
            )}
            {name === 'recipes' && (
              <RecipePage state={state} day={null} slotId={null} catalogue={catalogue} />
            )}
            {route.name === 'log' && (
              <Today
                key={route.date ?? now}
                state={state}
                date={route.date ?? now}
                today={now}
                onSave={save}
              />
            )}
            {name === 'summary' && <WeekView state={state} summary={summary} />}
            {route.name === 'day' && <DayView state={state} date={route.date} summary={summary} />}
            {name === 'missing' && (
              <>
                <h1 tabIndex={-1} data-screen-title>
                  Not found
                </h1>
                <p>
                  There is no screen at this address. Go to <a href="#/plan">your plan</a> or{' '}
                  <a href="#/week">the week</a>.
                </p>
              </>
            )}
            {(name === 'plan' || name === 'week') && (
              <DataSection
                state={state}
                onReplace={replaceAll}
                blocked={
                  run.running === null
                    ? null
                    : `A file can be loaded once the ${run.running.what === 'week' ? 'week' : 'recipe'} being written is done.`
                }
              />
            )}
          </>
        )}
      </main>
    </>
  )
}

// A save the browser refused. The page still has the state, and is the only copy until the
// person exports it or a later save works.
function Unsaved({ message, state }: { message: string; state: State }) {
  const [problem, setProblem] = useState<string | null>(null)
  return (
    // Shown above the screen, so it is not a heading: the screen's own heading comes first.
    <section role="alert" className="notice warning" aria-labelledby="unsaved-title">
      <p id="unsaved-title" className="notice-title">
        Not saved in this browser
      </p>
      <p>
        The last change was not saved: {message}. It is kept on this page until the page is closed.
        Export a copy to keep it.
      </p>
      {state.rules !== null && (
        <button
          type="button"
          className="button-secondary"
          onClick={() => {
            try {
              saveFile(exportFile(state), `meal-planner-${today()}.json`)
            } catch (error) {
              setProblem(messageOf(error))
            }
          }}
        >
          Export a copy
        </button>
      )}
      {problem !== null && <p>The copy could not be made: {problem}.</p>}
    </section>
  )
}
