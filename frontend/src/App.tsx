import { useEffect, useState } from 'react'

import { loadState, type State } from './storage'

// The shell the screens go into (plan, week, log and summary come next). For now it says what
// this browser has saved and where that data lives.

type Saved =
  { status: 'loading' } | { status: 'ready'; state: State } | { status: 'failed'; message: string }

function described(saved: Saved): string {
  if (saved.status === 'loading') return 'Reading what this browser saved.'
  if (saved.status === 'failed')
    return `What this browser saved could not be read: ${saved.message}`
  const { rules, week, log } = saved.state
  if (rules === null) return 'No plan is saved in this browser yet.'
  const recipes = week?.recipes.length ?? 0
  const meals = log.entries.length
  return `Plan saved in this browser: ${rules.name}, with ${recipes} ${recipes === 1 ? 'recipe' : 'recipes'} and ${meals} logged ${meals === 1 ? 'meal' : 'meals'}.`
}

export function App() {
  const [saved, setSaved] = useState<Saved>({ status: 'loading' })

  useEffect(() => {
    let shown = true
    loadState().then(
      (state) => {
        if (shown) setSaved({ status: 'ready', state })
      },
      (error: unknown) => {
        const message = error instanceof Error ? error.message : String(error)
        if (shown) setSaved({ status: 'failed', message })
      },
    )
    return () => {
      shown = false
    }
  }, [])

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to the content
      </a>
      <main id="main" className="page">
        <h1>Meal planner</h1>
        <p aria-live="polite">{described(saved)}</p>
        <section className="notice" aria-labelledby="storage-heading">
          <h2 id="storage-heading">Where your data is kept</h2>
          <p>
            Your plan, its week of recipes and your meal log are saved in this browser only.
            Clearing this browser&apos;s data deletes them, so export a copy to keep one.
          </p>
        </section>
      </main>
    </>
  )
}
