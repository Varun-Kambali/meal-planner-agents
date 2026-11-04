// The API's routes, typed from the server's models (src/types.ts). The API keeps nothing, so
// every call carries what it needs: the rules, the week or the log come from the browser's
// storage. Writing a week streams server-sent events; the rest are one JSON request each.

import type {
  Catalogue,
  ComputeRequest,
  DraftRecipe,
  FoodRules,
  FoodSearchResult,
  LogItem,
  MealLog,
  MealResult,
  PlanRules,
  Problem,
  ReadRequest,
  ReadResult,
  ReplaceRequest,
  StreamError,
  StreamEvent,
  SummaryRequest,
  WeekResult,
  WeekSummary,
} from './types'

export type DayKey = DraftRecipe['day']
export type Diet = FoodRules['diet']
export type Allergen = FoodRules['exclude_allergens'][number]
// The events of a run. The stream's error event ends the call with StreamFailed instead.
export type WeekEvent = Exclude<StreamEvent, StreamError>

export interface CallOptions {
  // Aborting a week's stream ends the request, and the server then cancels the run.
  signal?: AbortSignal
}

// The API refused the request or could not be reached (status 0). A 422 lists each problem
// with its place in the request, such as ["body", "items", 0]; other refusals have `detail`.
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly problems: Problem[]
  readonly requestId: string | null
  // Seconds to wait before trying again, from a 429's Retry-After.
  readonly retryAfter: number | null

  constructor(
    status: number,
    detail: string,
    problems: Problem[] = [],
    requestId: string | null = null,
    retryAfter: number | null = null,
  ) {
    super(problems.length > 0 ? problems.map((problem) => problem.msg).join('; ') : detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.problems = problems
    this.requestId = requestId
    this.retryAfter = retryAfter
  }
}

// A week's stream ended without the week: the server sent its error event (the run stopped
// after the status line went out), or the connection closed first.
export class StreamFailed extends Error {
  readonly step: StreamError['step']

  constructor(step: StreamError['step'], message: string) {
    super(message)
    this.name = 'StreamFailed'
    this.step = step
  }
}

// Read at each call, so that a test can set it; Vite fixes it at build time.
function url(path: string): string {
  const base = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
  return `${base}${path}`
}

async function refusal(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('X-Request-ID')
  const wait = Number.parseInt(response.headers.get('Retry-After') ?? '', 10)
  const retryAfter = Number.isNaN(wait) ? null : wait
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not JSON: a proxy or the host answered, not the API.
  }
  const detail = (body as { detail?: unknown } | null)?.detail
  if (Array.isArray(detail)) {
    return new ApiError(response.status, 'the request was refused', detail as Problem[], requestId)
  }
  const text = typeof detail === 'string' ? detail : `${response.status} ${response.statusText}`
  return new ApiError(response.status, text, [], requestId, retryAfter)
}

async function send(path: string, init: RequestInit, options: CallOptions): Promise<Response> {
  let response: Response
  try {
    response = await fetch(url(path), { ...init, signal: options.signal })
  } catch (error) {
    if (options.signal?.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
      throw error
    }
    throw new ApiError(0, 'the API could not be reached; check the connection and try again')
  }
  if (!response.ok) throw await refusal(response)
  return response
}

async function getJson<T>(path: string, options: CallOptions = {}): Promise<T> {
  const response = await send(
    path,
    { method: 'GET', headers: { Accept: 'application/json' } },
    options,
  )
  return (await response.json()) as T
}

async function postJson<T>(path: string, body: unknown, options: CallOptions = {}): Promise<T> {
  const response = await send(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    },
    options,
  )
  return (await response.json()) as T
}

interface ServerSentEvent {
  event: string
  data: string
}

// The messages of an event stream, read as the HTML standard reads them: lines end in CRLF, LF
// or CR, a line starting with ":" is a comment, a blank line ends a message, and a message left
// open when the stream ends is dropped. A chunk can end anywhere, even between CR and LF.
export async function* serverSentEvents(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<ServerSentEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = ''
  let data: string[] = []

  const read = (line: string): ServerSentEvent | null => {
    if (line === '') {
      const message = data.length > 0 ? { event: event || 'message', data: data.join('\n') } : null
      event = ''
      data = []
      return message
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    if (field === 'data') data.push(value)
    return null
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      for (let end = /\r\n|\r|\n/.exec(buffer); end !== null; end = /\r\n|\r|\n/.exec(buffer)) {
        // A CR at the very end may be the first half of a CRLF still on its way.
        if (!done && end[0] === '\r' && end.index === buffer.length - 1) break
        const message = read(buffer.slice(0, end.index))
        buffer = buffer.slice(end.index + end[0].length)
        if (message !== null) yield message
      }
      if (done) return
    }
  } finally {
    // Stopping early (the week has arrived, or the caller stopped reading) closes the
    // connection, so the server cancels a run that no one is reading.
    await reader.cancel().catch(() => undefined)
  }
}

async function streamed(
  step: StreamError['step'],
  path: string,
  body: unknown,
  onEvent: (event: WeekEvent) => void,
  options: CallOptions,
): Promise<WeekResult> {
  const response = await send(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
    },
    options,
  )
  if (response.body === null) throw new StreamFailed(step, 'the response had no stream')
  for await (const message of serverSentEvents(response.body)) {
    let event: StreamEvent
    try {
      event = JSON.parse(message.data) as StreamEvent
    } catch {
      throw new StreamFailed(step, 'the stream sent an event that could not be read')
    }
    if (event.kind === 'error') throw new StreamFailed(event.step, event.message)
    onEvent(event)
    if (event.kind === 'week_done') return event.week
  }
  throw new StreamFailed(step, 'the stream ended before the week was done')
}

// POST /v1/week: writes a week for the rules. `onEvent` gets each event as it arrives (a slot
// started, written, checked, repaired or failed); the promise resolves with the week.
export function streamWeek(
  rules: PlanRules,
  onEvent: (event: WeekEvent) => void,
  options: CallOptions = {},
): Promise<WeekResult> {
  return streamed('week', '/v1/week', rules, onEvent, options)
}

// POST /v1/week/replace: writes one day and slot again. The rest of the week goes back as the
// model wrote it, with no numbers or failures: the server works those out again itself.
export function replaceRecipe(
  rules: PlanRules,
  week: WeekResult,
  day: DayKey,
  slotId: string,
  onEvent: (event: WeekEvent) => void,
  options: CallOptions = {},
): Promise<WeekResult> {
  const body: ReplaceRequest = {
    rules,
    recipes: week.recipes.map((recipe) => ({
      day: recipe.spec.day,
      slot_id: recipe.spec.slot.id,
      draft: recipe.draft,
      attempts: recipe.attempts,
    })),
    day,
    slot_id: slotId,
  }
  return streamed('replace', '/v1/week/replace', body, onEvent, options)
}

// POST /v1/log/read: a typed meal as nutrition-mcp foods and amounts, for the person to confirm.
export function readMeal(text: string, options: CallOptions = {}): Promise<ReadResult> {
  const body: ReadRequest = { text }
  return postJson('/v1/log/read', body, options)
}

// POST /v1/summary: the log's days against the plan's targets, worked out again by the server.
export function summarise(
  rules: PlanRules,
  log: MealLog,
  options: CallOptions = {},
): Promise<WeekSummary> {
  const body: SummaryRequest = { rules, log }
  return postJson('/v1/summary', body, options)
}

// GET /v1/catalogue: the ingredients a plan form can offer for a diet and allergens to avoid.
export function catalogue(
  filters: { diet?: Diet; excludeAllergens?: Allergen[] } = {},
  options: CallOptions = {},
): Promise<Catalogue> {
  const query = new URLSearchParams()
  if (filters.diet !== undefined) query.set('diet', filters.diet)
  for (const allergen of filters.excludeAllergens ?? []) query.append('exclude_allergens', allergen)
  const search = query.size > 0 ? `?${query.toString()}` : ''
  return getJson(`/v1/catalogue${search}`, options)
}

// GET /v1/foods/search: the foods nutrition-mcp has under a name, with the units each takes.
export function searchFoods(
  q: string,
  options: CallOptions & { limit?: number } = {},
): Promise<FoodSearchResult> {
  const query = new URLSearchParams({ q })
  if (options.limit !== undefined) query.set('limit', String(options.limit))
  return getJson(`/v1/foods/search?${query.toString()}`, options)
}

// POST /v1/log/compute: each food's numbers and the meal's total, for a meal being edited.
export function computeMeal(items: LogItem[], options: CallOptions = {}): Promise<MealResult> {
  const body: ComputeRequest = { items }
  return postJson('/v1/log/compute', body, options)
}
