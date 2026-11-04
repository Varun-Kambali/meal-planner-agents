import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { App } from './App'
import { STORAGE_KEY } from './storage'
import sample from './test/export-sample.json'

// React's act() warns unless the test says it is running in a test environment.
declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined
}

let container: HTMLDivElement

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  container = document.createElement('div')
  document.body.append(container)
})

afterEach(() => {
  container.remove()
  localStorage.clear()
})

async function render(): Promise<void> {
  const root = createRoot(container)
  await act(async () => {
    root.render(<App />)
  })
}

it('renders the shell with what this browser saved', async () => {
  // jsdom has no IndexedDB, so the browser's storage is localStorage here.
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sample))

  await render()

  expect(container.querySelector('h1')?.textContent).toBe('Meal planner')
  expect(container.querySelector('main')?.id).toBe('main')
  expect(container.querySelector('[aria-live]')?.textContent).toBe(
    'Plan saved in this browser: Office weekday, with 2 recipes and 3 logged meals.',
  )
  expect(container.textContent).toContain('saved in this browser only')
})
