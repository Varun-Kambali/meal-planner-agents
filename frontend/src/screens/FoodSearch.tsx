// "Change food" in the swap flow: nutrition-mcp's foods under a name, asked for when the form is
// sent and never as the person types, since every /v1/ route shares one rate limit.

import { useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { searchFoods } from '../api'
import type { FoodOption } from '../types'
import { isAbort, useCalls } from '../useCalls'
import { RequestProblem } from './parts'

type Search =
  | { status: 'idle' }
  | { status: 'searching' }
  | { status: 'found'; options: FoodOption[] }
  | { status: 'failed'; error: unknown }

// nutrition-mcp's foods under a name the person types, asked for when the form is sent.
export function FoodSearch({
  initial,
  onChoose,
}: {
  initial: string
  onChoose: (option: FoodOption) => void
}) {
  const [query, setQuery] = useState(initial)
  const [search, setSearch] = useState<Search>({ status: 'idle' })
  const [problem, setProblem] = useState<string | null>(null)
  const call = useCalls()
  const id = useId()
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => input.current?.focus(), [])

  // Never rejects: a search that fails is said to, with a way to search again.
  async function find(): Promise<void> {
    if (query.trim() === '') {
      setProblem('Type a food name first.')
      // As the swap flow's own blank check does, so the person can start typing.
      input.current?.focus()
      return
    }
    setProblem(null)
    setSearch({ status: 'searching' })
    try {
      const options = await call((signal) => searchFoods(query.trim(), { signal }))
      setSearch({ status: 'found', options })
    } catch (error) {
      if (!isAbort(error)) setSearch({ status: 'failed', error })
    }
  }

  return (
    <div className="food-search">
      <form
        noValidate
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          if (search.status !== 'searching') void find()
        }}
      >
        <label htmlFor={`${id}-q`}>Food name</label>
        <div className="inline">
          <input
            id={`${id}-q`}
            ref={input}
            className="input"
            autoComplete="off"
            maxLength={100}
            value={query}
            aria-invalid={problem === null ? undefined : true}
            aria-describedby={problem === null ? undefined : `${id}-problem`}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button
            type="submit"
            className="button-secondary"
            aria-disabled={search.status === 'searching'}
          >
            Search
          </button>
        </div>
        {problem !== null && (
          <p id={`${id}-problem`} className="field-error">
            {problem}
          </p>
        )}
        <output>
          {search.status === 'searching' && 'Searching.'}
          {search.status === 'found' &&
            (search.options.length === 0
              ? 'nutrition-mcp has no food under that name.'
              : `${search.options.length} ${search.options.length === 1 ? 'food' : 'foods'} found.`)}
        </output>
      </form>
      {search.status === 'failed' && (
        <RequestProblem
          what="The search did not run."
          error={search.error}
          onRetry={() => void find()}
        />
      )}
      {search.status === 'found' && search.options.length > 0 && (
        <ul className="choices-found" aria-label="Foods found">
          {search.options.map((option) => (
            <li key={option.id}>
              {option.name} <span className="meta">{option.kind}</span>{' '}
              <button
                type="button"
                className="button-text"
                aria-label={`Use ${option.name}`}
                onClick={() => onChoose(option)}
              >
                Use
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
