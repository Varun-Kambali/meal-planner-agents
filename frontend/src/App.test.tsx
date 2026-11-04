import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { App } from './App'

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
})

it('renders the shell', async () => {
  const root = createRoot(container)
  await act(async () => {
    root.render(<App />)
  })

  expect(container.querySelector('h1')?.textContent).toBe('Meal planner')
  expect(container.querySelector('main')?.id).toBe('main')
  expect(container.textContent).toContain('kept in this browser only')
})
