// What the screen tests share: the sample export as a state, and a fake API behind fetch whose
// week streams the test drives one event at a time. No test reaches a real API or model.

import { vi } from 'vitest'

import type { State } from '../storage'
import type { CatalogueRow, Export, StreamEvent, WeekResult } from '../types'
import sample from './export-sample.json'

export function sampleExport(): Export {
  return structuredClone(sample) as Export
}

export function sampleState(): State {
  const { rules, week, log } = sampleExport()
  return { rules, week, log }
}

export function sampleWeek(): WeekResult {
  return sampleExport().week!
}

export const CATALOGUE: CatalogueRow[] = [
  { id: 'brinjal', name: 'Brinjal', category: 'veg' },
  { id: 'moong_dal', name: 'Moong dal', category: 'pulse' },
  { id: 'oats', name: 'Oats', category: 'grain' },
  { id: 'paneer', name: 'Paneer', category: 'dairy' },
]

export interface Call {
  path: string
  method: string
  body: unknown
  signal: AbortSignal | undefined
}

// A server-sent event stream the test writes to.
export class FakeStream {
  private controller: ReadableStreamDefaultController<Uint8Array> | undefined
  readonly response: Response

  constructor() {
    const body = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller
      },
    })
    this.response = new Response(body, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }

  send(event: StreamEvent): void {
    const text = `event: ${event.kind}\ndata: ${JSON.stringify(event)}\n\n`
    this.controller?.enqueue(new TextEncoder().encode(text))
  }

  end(): void {
    this.controller?.close()
  }

  // As fetch's body fails when the connection drops.
  drop(): void {
    this.controller?.error(new TypeError('network error'))
  }
}

export function json(
  content: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(content), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

type Answer = (call: Call) => Response | Promise<Response>

// fetch, answering each path with the first route whose key it starts with. A path with no route
// fails the test, so a screen cannot call the API without the test saying what it gets.
export function fakeApi(routes: Record<string, Answer>): Call[] {
  const calls: Call[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const path = url.replace(/^https?:\/\/[^/]+/, '')
      const call: Call = {
        path,
        method: init.method ?? 'GET',
        body: typeof init.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined,
        signal: init.signal ?? undefined,
      }
      calls.push(call)
      init.signal?.throwIfAborted()
      const route = Object.keys(routes)
        .sort((a, b) => b.length - a.length)
        .find((prefix) => path.startsWith(prefix))
      if (route === undefined) throw new Error(`the test gave no answer for ${path}`)
      const response = await routes[route]!(call)
      // fetch's body fails with an AbortError once the call is aborted.
      init.signal?.addEventListener('abort', () => {
        void response.body?.cancel().catch(() => undefined)
      })
      return response
    }),
  )
  return calls
}

// Downloads in jsdom: the file the page hands over, and its name.
export interface Saved {
  name: string
  text: () => Promise<string>
}

export function catchDownloads(): Saved[] {
  const saved: Saved[] = []
  const blobs = new Map<string, Blob>()
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    const url = `blob:test/${blobs.size + 1}`
    blobs.set(url, blob as Blob)
    return url
  })
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    const blob = blobs.get(this.href)
    if (blob !== undefined) saved.push({ name: this.download, text: () => blob.text() })
  })
  return saved
}
