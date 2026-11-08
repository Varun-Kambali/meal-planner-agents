import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  StreamFailed,
  catalogue,
  computeMeal,
  health,
  readMeal,
  replaceRecipe,
  searchFoods,
  serverSentEvents,
  streamWeek,
  summarise,
  type WeekEvent,
} from './api'
import sample from './test/export-sample.json'
import type { Export, StreamEvent } from './types'

const file = sample as Export
const rules = file.rules
const week = file.week!

interface Call {
  url: string
  method: string
  body: unknown
  headers: Headers
}

// fetch, replaced by one that records each call and answers with `respond`.
function serve(respond: (call: Call) => Response): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const call: Call = {
        url,
        method: init.method ?? 'GET',
        body: typeof init.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
        headers: new Headers(init.headers),
      }
      calls.push(call)
      init.signal?.throwIfAborted()
      return respond(call)
    }),
  )
  return calls
}

function json(content: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(content), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

// The bytes of a body delivered in the given pieces, as a network would split them.
function body(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk)
      }
      controller.close()
    },
  })
}

function stream(...chunks: string[]): Response {
  return new Response(body(...chunks), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

function message(event: StreamEvent): string {
  return `event: ${event.kind}\ndata: ${JSON.stringify(event)}\n\n`
}

const started: StreamEvent = { kind: 'slot_started', slot_id: 'lunch', days: ['mon'], attempt: 1 }
const done: StreamEvent = { kind: 'week_done', week }

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('serverSentEvents', () => {
  async function messages(...chunks: (string | Uint8Array)[]) {
    const found: { event: string; data: string }[] = []
    for await (const each of serverSentEvents(body(...chunks))) found.push(each)
    return found
  }

  it('keeps the event name when a CRLF is split between two chunks', async () => {
    await expect(messages('event: a\r', '\ndata: 1\r\n\r\n')).resolves.toEqual([
      { event: 'a', data: '1' },
    ])
  })

  it('reads CR line ends, data over several lines and the default event name', async () => {
    await expect(messages('data: x\rdata: y\r\r')).resolves.toEqual([
      { event: 'message', data: 'x\ny' },
    ])
  })

  it('takes a CR at the very end of the stream as a line end', async () => {
    await expect(messages('data: 1\r', '\r')).resolves.toEqual([{ event: 'message', data: '1' }])
  })

  it('ignores comments and other fields, and drops a message left open', async () => {
    await expect(
      messages(': ping\nid: 7\nretry: 10\nevent: a\ndata: 1\n\nevent: b\ndata: 2\n'),
    ).resolves.toEqual([{ event: 'a', data: '1' }])
  })

  it('decodes a character whose bytes are split between chunks', async () => {
    const bytes = new TextEncoder().encode('data: d\u00e9j\u00e0\n\n')
    const cut = 8 // inside the two bytes of the first accented letter

    await expect(messages(bytes.slice(0, cut), bytes.slice(cut))).resolves.toEqual([
      { event: 'message', data: 'd\u00e9j\u00e0' },
    ])
  })
})

describe('streamWeek', () => {
  it('posts the rules and hands each event over as it arrives', async () => {
    const calls = serve(() => stream(message(started), message(done)))
    const events: WeekEvent[] = []

    const result = await streamWeek(rules, (event) => events.push(event))

    expect(calls).toHaveLength(1)
    expect(calls[0]).toMatchObject({ url: '/v1/week', method: 'POST', body: rules })
    expect(calls[0]!.headers.get('Accept')).toBe('text/event-stream')
    expect(events.map((event) => event.kind)).toEqual(['slot_started', 'week_done'])
    expect(result).toEqual(week)
  })

  it('reads events split anywhere, with CRLF line ends, comments and long data', async () => {
    const text = `: keep-alive\r\n\r\n${message(started).replaceAll('\n', '\r\n')}${message(done)}`
    // Pieces of a few bytes, so a line end, a field name and a JSON value are each cut in two.
    const pieces = text.match(/[\s\S]{1,7}/g) ?? []
    serve(() => stream(...pieces))
    const events: WeekEvent[] = []

    const result = await streamWeek(rules, (event) => events.push(event))

    expect(events).toEqual([started, done])
    expect(result).toEqual(week)
  })

  it('closes the connection once the week is done, so nothing is left open', async () => {
    const cancel = vi.fn<(reason: unknown) => void>()
    const encoder = new TextEncoder()
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(message(started) + message(done)))
        // The server has not closed the stream yet.
      },
      cancel,
    })
    serve(() => new Response(open, { status: 200 }))

    await expect(streamWeek(rules, () => {})).resolves.toEqual(week)
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('closes the connection when the callback fails, so the server stops the run', async () => {
    const cancel = vi.fn<(reason: unknown) => void>()
    const encoder = new TextEncoder()
    const open = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(message(started)))
      },
      cancel,
    })
    serve(() => new Response(open, { status: 200 }))

    await expect(
      streamWeek(rules, () => {
        throw new Error('the screen went away')
      }),
    ).rejects.toThrow('the screen went away')
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('rejects with the step when the run stops after the stream started', async () => {
    const error: StreamEvent = {
      kind: 'error',
      step: 'week',
      message: 'the run stopped before it finished',
    }
    serve(() => stream(message(started), message(error)))
    const events: WeekEvent[] = []

    const failure = streamWeek(rules, (event) => events.push(event))

    await expect(failure).rejects.toBeInstanceOf(StreamFailed)
    await expect(failure).rejects.toMatchObject({
      step: 'week',
      message: 'the run stopped before it finished',
    })
    expect(events.map((event) => event.kind)).toEqual(['slot_started'])
  })

  it('rejects when the stream ends before the week is done', async () => {
    serve(() => stream(message(started)))

    await expect(streamWeek(rules, () => {})).rejects.toMatchObject({
      name: 'StreamFailed',
      step: 'week',
    })
  })

  it('rejects with each problem of a refused plan', async () => {
    const problems = [{ loc: ['body', 'slots', 0, 'kcal_share'], msg: 'must be at most 1' }]
    serve(() => json({ detail: problems }, 422, { 'X-Request-ID': 'abc123' }))

    const failure = streamWeek(rules, () => {})

    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({ status: 422, problems, requestId: 'abc123' })
  })

  it('stops when the caller cancels', async () => {
    serve(() => stream(message(started), message(done)))
    const controller = new AbortController()
    controller.abort()

    await expect(streamWeek(rules, () => {}, { signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
  })

  // A body that sends one event, then fails as fetch's body does when the connection is lost,
  // or, given a signal, when the call is aborted.
  function dropping(error: () => unknown, signal?: AbortSignal): Response {
    let pulls = 0
    const failing = new ReadableStream<Uint8Array>({
      async pull(controller) {
        if (pulls++ === 0) {
          controller.enqueue(new TextEncoder().encode(message(started)))
          return
        }
        if (signal !== undefined && !signal.aborted) {
          await new Promise((resolve) => signal.addEventListener('abort', resolve))
        }
        controller.error(error())
      },
    })
    return new Response(failing, { status: 200 })
  }

  it('rejects with StreamFailed when the connection drops mid-stream', async () => {
    serve(() => dropping(() => new TypeError('network error')))
    const events: WeekEvent[] = []

    const failure = streamWeek(rules, (event) => events.push(event))

    await expect(failure).rejects.toBeInstanceOf(StreamFailed)
    await expect(failure).rejects.toMatchObject({
      step: 'week',
      message: 'the connection closed before the week was done',
    })
    expect(events.map((event) => event.kind)).toEqual(['slot_started'])
  })

  it('names the replace step when its connection drops', async () => {
    serve(() => dropping(() => new TypeError('Load failed')))

    await expect(replaceRecipe(rules, week, 'mon', 'lunch', () => {})).rejects.toMatchObject({
      name: 'StreamFailed',
      step: 'replace',
    })
  })

  it('passes an abort mid-stream through as an abort', async () => {
    const controller = new AbortController()
    serve(() =>
      dropping(
        () => new DOMException('The user aborted a request.', 'AbortError'),
        controller.signal,
      ),
    )

    const failure = streamWeek(rules, () => controller.abort(), { signal: controller.signal })

    await expect(failure).rejects.toMatchObject({ name: 'AbortError' })
    await expect(failure).rejects.not.toBeInstanceOf(StreamFailed)
  })
})

describe('replaceRecipe', () => {
  it('sends the drafts of the week and the recipe to write again, and no numbers', async () => {
    const calls = serve(() => stream(message(done)))
    const recipe = week.recipes[1]!

    const result = await replaceRecipe(rules, week, 'mon', 'lunch', () => {})

    expect(result).toEqual(week)
    expect(calls[0]).toMatchObject({ url: '/v1/week/replace', method: 'POST' })
    expect(calls[0]!.body).toEqual({
      rules,
      day: 'mon',
      slot_id: 'lunch',
      recipes: week.recipes.map((each) => ({
        day: each.spec.day,
        slot_id: each.spec.slot.id,
        draft: each.draft,
        attempts: each.attempts,
      })),
    })
    expect(JSON.stringify(calls[0]!.body)).not.toContain('"nutrition"')
    expect(recipe.spec.slot.id).toBe('lunch')
  })
})

describe('the JSON routes', () => {
  it('reads a typed meal', async () => {
    const read = { text_length: 13, items: [], total: null, missing: ['jalebi'] }
    const calls = serve(() => json(read))

    await expect(readMeal('2 jalebi')).resolves.toEqual(read)
    expect(calls[0]).toMatchObject({
      url: '/v1/log/read',
      method: 'POST',
      body: { text: '2 jalebi' },
    })
  })

  it('summarises a log against the rules', async () => {
    const summary = {
      days: [],
      as_planned_share: 0,
      days_on_target: 0,
      days_unsettled: 0,
      planned_meals: 0,
    }
    const calls = serve(() => json(summary))

    await expect(summarise(rules, file.log)).resolves.toEqual(summary)
    expect(calls[0]).toMatchObject({
      url: '/v1/summary',
      method: 'POST',
      body: { rules, log: file.log },
    })
  })

  it('lists the catalogue for a diet and the allergens to leave out', async () => {
    const rows = [{ id: 'paneer', name: 'Paneer', category: 'dairy' }]
    const calls = serve(() => json(rows))

    await expect(catalogue({ diet: 'veg', excludeAllergens: ['peanut', 'milk'] })).resolves.toEqual(
      rows,
    )
    expect(calls[0]).toMatchObject({
      url: '/v1/catalogue?diet=veg&exclude_allergens=peanut&exclude_allergens=milk',
      method: 'GET',
    })
  })

  it('asks for the whole catalogue with no filters', async () => {
    const calls = serve(() => json([]))

    await catalogue()

    expect(calls[0]!.url).toBe('/v1/catalogue')
  })

  it('searches foods by name, with the text in the body and none in the URL', async () => {
    const found = [{ id: 'idli', name: 'Idli', kind: 'dish', units: ['g', 'piece'] }]
    const calls = serve(() => json(found))

    await expect(searchFoods('idli & sambar', { limit: 10 })).resolves.toEqual(found)
    expect(calls[0]!.url).toBe('/v1/foods/search')
    expect(calls[0]!.method).toBe('POST')
    expect(calls[0]!.body).toEqual({ q: 'idli & sambar', limit: 10 })
  })

  it('leaves the limit out of the body when none is given', async () => {
    const calls = serve(() => json([]))

    await searchFoods('dal')

    expect(calls[0]!.body).toEqual({ q: 'dal' })
  })

  it('works out a meal as it is edited', async () => {
    const result = file.week!.recipes[0]!.nutrition
    const items = [{ food_id: 'banana', amount: 1, unit: 'piece' }]
    const calls = serve(() => json(result))

    await expect(computeMeal(items)).resolves.toEqual(result)
    expect(calls[0]).toMatchObject({ url: '/v1/log/compute', method: 'POST', body: { items } })
  })

  it('passes the place of a refused food on', async () => {
    const problems = [
      { loc: ['body', 'items', 0], msg: "item 1 (banana): unit 'cup' is not known" },
    ]
    serve(() => json({ detail: problems }, 422))

    await expect(
      computeMeal([{ food_id: 'banana', amount: 1, unit: 'cup' }]),
    ).rejects.toMatchObject({ status: 422, problems })
  })
})

describe('health', () => {
  it('asks GET /healthz and resolves when it answers with JSON', async () => {
    const calls = serve(() => json({ status: 'ok' }))

    await expect(health()).resolves.toBeUndefined()

    expect(calls.map((call) => [call.method, call.url])).toEqual([['GET', '/healthz']])
  })

  it('fails on an error status, a page that is not JSON, and a lost connection', async () => {
    serve(() => json({ detail: 'down' }, 503))
    await expect(health()).rejects.toMatchObject({ name: 'ApiError', status: 503 })

    serve(() => new Response('<html>starting</html>', { status: 200 }))
    await expect(health()).rejects.toMatchObject({
      name: 'ApiError',
      detail: 'the response was not JSON',
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    await expect(health()).rejects.toMatchObject({ name: 'ApiError', status: 0 })
  })

  it('goes to the API named at build time, and stops when aborted', async () => {
    vi.stubEnv('VITE_API_BASE', 'https://api.example.org/')
    const calls = serve(() => json({ status: 'ok' }))
    await health()
    expect(calls[0]!.url).toBe('https://api.example.org/healthz')

    const controller = new AbortController()
    controller.abort()
    await expect(health({ signal: controller.signal })).rejects.toMatchObject({
      name: 'AbortError',
    })
  })
})

describe('errors', () => {
  it('says how long to wait when rate limited', async () => {
    serve(() =>
      json({ detail: 'too many requests, try again later' }, 429, { 'Retry-After': '42' }),
    )

    await expect(readMeal('rice')).rejects.toMatchObject({
      status: 429,
      detail: 'too many requests, try again later',
      problems: [],
      retryAfter: 42,
    })
  })

  it('keeps the message of a service that is down', async () => {
    const detail = 'nutrition-mcp could not be reached (step: nutrition)'
    serve(() => json({ detail }, 503))

    const failure = summarise(rules, file.log)

    await expect(failure).rejects.toMatchObject({ status: 503, detail })
    await expect(failure).rejects.toThrow(detail)
  })

  it('says so when a 200 is not JSON, such as the fallback page of a host', async () => {
    serve(() => new Response('<!doctype html><title>Meal planner</title>', { status: 200 }))

    const failure = summarise(rules, file.log)

    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({
      status: 200,
      detail: 'the response was not JSON',
    })
  })

  it('passes an abort while a JSON body is read through as an abort', async () => {
    const controller = new AbortController()
    // A body that sends the start of the JSON, then fails as fetch's body does on an abort.
    const half = new ReadableStream<Uint8Array>({
      start(source) {
        source.enqueue(new TextEncoder().encode('[{"id": "dal"'))
      },
      async pull(source) {
        if (!controller.signal.aborted) {
          await new Promise((resolve) => controller.signal.addEventListener('abort', resolve))
        }
        source.error(new DOMException('The user aborted a request.', 'AbortError'))
      },
    })
    serve(() => new Response(half, { status: 200 }))

    const failure = searchFoods('dal', { signal: controller.signal })
    controller.abort()

    await expect(failure).rejects.toMatchObject({ name: 'AbortError' })
    await expect(failure).rejects.not.toBeInstanceOf(ApiError)
  })

  it('reads a refusal that is not JSON, such as a proxy page', async () => {
    serve(
      () => new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }),
    )

    await expect(readMeal('rice')).rejects.toMatchObject({ status: 502, detail: '502 Bad Gateway' })
  })

  it('says the API could not be reached when the request does not go out', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(readMeal('rice')).rejects.toMatchObject({ name: 'ApiError', status: 0 })
  })

  it('sends requests to the API named at build time', async () => {
    vi.stubEnv('VITE_API_BASE', 'https://api.example.org/')
    const calls = serve(() => json([]))

    await searchFoods('dal')

    expect(calls[0]!.url).toBe('https://api.example.org/v1/foods/search')
  })
})
