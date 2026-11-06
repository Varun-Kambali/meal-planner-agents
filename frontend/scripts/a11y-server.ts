// The built site (dist/) on 127.0.0.1:4173 for the accessibility check, with what its screens
// need from a browser and an API that are not there:
//
// - /__seed/<name> puts a saved state into this browser's storage, as the app saves it: `week` is
//   src/test/recorded-week.json (scripts/record_week_fixture.py), `tracking` the same with the
//   meal log of src/test/recorded-tracking.json (scripts/record_tracking_fixture.py), and
//   `unreadable` a stored copy that does not fit the file format. A check then moves on to the
//   screen to test.
// - GET /v1/catalogue lists the foods of that week, so the plan form's food lists load.
// - POST /v1/week refuses the plan with a 422, so the week screen's refusal can be checked.
// - POST /v1/log/read, GET /v1/foods/search, POST /v1/log/compute and POST /v1/summary answer
//   with the API's recorded replies, whatever they are asked, so the log and summary screens can
//   be checked with real data.
//
// Nothing here calls a model or the real API.

import { readFile } from 'node:fs/promises'
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const DIST = path.resolve(here, '../dist')
const FIXTURE = path.resolve(here, '../src/test/recorded-week.json')
const TRACKING = path.resolve(here, '../src/test/recorded-tracking.json')
export const ORIGIN = 'http://127.0.0.1:4173'
// The key storage.ts keeps a copy of the state under; the app reads it before IndexedDB.
const STORAGE_KEY = 'meal-planner-state'

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.json': 'application/json',
}

interface RecordedState {
  week: { recipes: { nutrition: { items: { food: { id: string; name: string } }[] } | null }[] }
}

// The replies scripts/record_tracking_fixture.py recorded, and the log they were recorded for.
interface RecordedTracking {
  log: unknown
  read: { reply: unknown }
  search: { reply: unknown }
  compute: { reply: unknown }
  summary: unknown
}

// The foods of the recorded week, as the catalogue route lists them.
function foodsOf(state: RecordedState): { id: string; name: string; category: string }[] {
  const foods = new Map<string, string>()
  for (const recipe of state.week.recipes) {
    for (const item of recipe.nutrition?.items ?? []) foods.set(item.food.id, item.food.name)
  }
  return [...foods].map(([id, name]) => ({ id, name, category: 'other' }))
}

// A page that saves `text` as the stored state. The text goes into a script, so "<" is escaped.
function seedPage(text: string): string {
  const literal = JSON.stringify(text).replaceAll('<', '\\u003c')
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Saved state</title></head><body><main><h1>Saved state</h1></main><script>localStorage.setItem(${JSON.stringify(STORAGE_KEY)}, ${literal})</script></body></html>`
}

function send(response: ServerResponse, status: number, type: string, body: string | Buffer): void {
  response.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  response.end(body)
}

export async function serve(): Promise<Server> {
  const week = await readFile(FIXTURE, 'utf8')
  const tracking = JSON.parse(await readFile(TRACKING, 'utf8')) as RecordedTracking
  const seeds: Record<string, string> = {
    week,
    tracking: JSON.stringify({ ...(JSON.parse(week) as object), log: tracking.log }),
    unreadable: '{"version": 1, "rules": "not a plan"}',
  }
  // Each recorded reply by the method and path it answers.
  const replies: Record<string, string> = {
    'POST /v1/log/read': JSON.stringify(tracking.read.reply),
    'GET /v1/foods/search': JSON.stringify(tracking.search.reply),
    'POST /v1/log/compute': JSON.stringify(tracking.compute.reply),
    'POST /v1/summary': JSON.stringify(tracking.summary),
  }
  const catalogue = JSON.stringify(foodsOf(JSON.parse(week) as RecordedState))
  const refusal = JSON.stringify({
    detail: [
      { loc: ['body', 'food', 'liked_foods'], msg: "'jackfruit_seed' is not in the catalogue" },
    ],
  })

  async function answer(request: IncomingMessage, response: ServerResponse): Promise<void> {
    const { pathname } = new URL(request.url ?? '/', ORIGIN)
    const seed = /^\/__seed\/(\w+)$/.exec(pathname)?.[1]
    if (seed !== undefined && seeds[seed] !== undefined) {
      send(response, 200, TYPES['.html']!, seedPage(seeds[seed]))
      return
    }
    if (pathname === '/v1/catalogue') return send(response, 200, TYPES['.json']!, catalogue)
    if (pathname === '/v1/week' && request.method === 'POST') {
      return send(response, 422, TYPES['.json']!, refusal)
    }
    const reply = replies[`${request.method ?? 'GET'} ${pathname}`]
    if (reply !== undefined) return send(response, 200, TYPES['.json']!, reply)
    const file = path.resolve(DIST, `.${pathname === '/' ? '/index.html' : pathname}`)
    if (!file.startsWith(`${DIST}${path.sep}`))
      return send(response, 404, 'text/plain', 'not found')
    try {
      const body = await readFile(file)
      send(response, 200, TYPES[path.extname(file)] ?? 'application/octet-stream', body)
    } catch {
      send(response, 404, 'text/plain', 'not found')
    }
  }

  const server = createServer((request, response) => {
    answer(request, response).catch((error: unknown) => {
      send(response, 500, 'text/plain', String(error))
    })
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(Number(new URL(ORIGIN).port), new URL(ORIGIN).hostname, resolve)
  })
  return server
}

// Run on its own, it serves until stopped, for running pa11y-ci by hand.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await serve()
  console.log(`serving dist/ at ${ORIGIN}`)
}
