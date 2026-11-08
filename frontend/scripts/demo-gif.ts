// Makes docs/media/demo.gif: the app's main path, driven in a real Chrome and recorded frame by
// frame. Run it on demand with `npm run build && npm run demo:gif` in frontend/; CI does not.
//
// What it replays: the built site (dist/) served by the accessibility check's stand-in server
// (scripts/a11y-server.ts), which hands the page a week that the pipeline wrote earlier from
// recorded model replies, with every number from nutrition-mcp, plus a meal log that goes with
// it. It calls no model and no API of ours, so the GIF comes out the same each time. The page's
// clock is set to Wednesday 4 November 2026, a day of that week, so "today" has meals to log.
//
// The path: the saved plan, the week with its checks, one recipe, logging a planned meal as
// eaten, and the day against its targets. Frames go to a temporary folder as PNG, ffmpeg
// assembles them with a two-pass palette, and the folder is removed. Chrome is the system's, as
// in a11y.ts, or PUPPETEER_EXECUTABLE_PATH; ffmpeg must be on the PATH.

import { spawn } from 'node:child_process'
import { copyFile, mkdtemp, mkdir, rm, stat } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer, { type Page } from 'puppeteer'

import { ORIGIN, serve } from './a11y-server.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const OUT = path.resolve(here, '../../docs/media/demo.gif')
const CHROMES = [
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
]
const WIDTH = 1280
const HEIGHT = 800
const FPS = 10
const GIF_WIDTH = 960
const MAX_BYTES = 5 * 1024 * 1024
// A day of the recorded week (2026-11-02 to 2026-11-08), local time.
const TODAY = '2026-11-04T10:00:00'

function run(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'ignore', 'inherit'] })
    child.once('error', reject)
    child.once('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)),
    )
  })
}

const pause = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

// Frames are numbered in the order they are shown.
class Recorder {
  count = 0
  readonly page: Page
  readonly folder: string

  constructor(page: Page, folder: string) {
    this.page = page
    this.folder = folder
  }

  private name(): string {
    this.count += 1
    return path.join(this.folder, `frame-${String(this.count).padStart(5, '0')}.png`)
  }

  // One frame of whatever the page shows now.
  async frame(): Promise<string> {
    const file = this.name()
    await this.page.screenshot({ path: file })
    return file
  }

  // The page as it is for `seconds`: one capture, shown for as many frames as that takes.
  async hold(seconds: number): Promise<void> {
    const first = await this.frame()
    for (let i = 1; i < Math.round(seconds * FPS); i++) await copyFile(first, this.name())
  }

  // Scrolls down by `distance` pixels over `steps` frames.
  async scroll(distance: number, steps: number): Promise<void> {
    for (let i = 0; i < steps; i++) {
      await this.page.evaluate(`window.scrollBy(0, ${distance / steps})`)
      await pause(60)
      await this.frame()
    }
  }

  async scrollTop(): Promise<void> {
    await this.page.evaluate('window.scrollTo(0, 0)')
    await pause(100)
  }
}

async function click(page: Page, selector: string): Promise<void> {
  const element = await page.waitForSelector(selector, { visible: true })
  await element?.click()
  await pause(500)
}

async function record(page: Page, rec: Recorder): Promise<void> {
  // Puts the recorded week and its log into this browser, then opens the app.
  await page.goto(`${ORIGIN}/__seed/tracking`)
  await page.goto(`${ORIGIN}/#/plan`)
  await page.waitForSelector('#f-food-liked_foods option + option')
  await pause(600)

  // The saved plan.
  await rec.hold(2.5)
  await rec.scroll(600, 6)
  await rec.hold(1.5)

  // The week: recipes and what each is checked against.
  await rec.scrollTop()
  await click(page, 'nav ::-p-text(Week)')
  await page.waitForSelector('#main h1', { visible: true })
  await rec.hold(3)
  await rec.scroll(560, 6)
  await rec.hold(2)

  // One recipe.
  await rec.scrollTop()
  await click(page, 'table a')
  await page.waitForSelector('#main h1', { visible: true })
  await rec.hold(2.5)
  await rec.scroll(700, 7)
  await rec.hold(2)

  // Logging a planned meal as eaten.
  await rec.scrollTop()
  await click(page, 'nav ::-p-text(Log)')
  await page.waitForSelector('section.log-meal', { visible: true })
  await rec.hold(2.5)
  await click(page, 'section.log-meal:nth-of-type(1) .actions button:first-child')
  await rec.hold(2.5)

  // The summary, and a day of it against its targets.
  await click(page, 'nav ::-p-text(Summary)')
  await page.waitForSelector('.summary-days', { visible: true })
  await rec.hold(2.5)
  await click(page, '.summary-days a')
  await page.waitForSelector('.day-table', { visible: true })
  await rec.hold(3)
  await rec.scroll(500, 5)
  await rec.hold(2.5)
}

async function main(): Promise<void> {
  const executablePath =
    process.env.PUPPETEER_EXECUTABLE_PATH ?? CHROMES.find((chrome) => existsSync(chrome))
  if (executablePath === undefined) {
    throw new Error('no Chrome found: set PUPPETEER_EXECUTABLE_PATH to a Chrome or Chromium')
  }
  if (!existsSync(path.resolve(here, '../dist/index.html'))) {
    throw new Error('dist/ is missing: run npm run build first')
  }
  const frames = await mkdtemp(path.join(tmpdir(), 'demo-gif-'))
  const server = await serve()
  const browser = await puppeteer.launch({ executablePath, args: ['--no-sandbox'] })
  try {
    const page = await browser.newPage()
    await page.setViewport({ width: WIDTH, height: HEIGHT })
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'light' }])
    // The page reads the clock when it opens, so the clock is set before any of its code runs.
    await page.evaluateOnNewDocument(`{
      const RealDate = Date
      const offset = new RealDate(${JSON.stringify(TODAY)}).getTime() - RealDate.now()
      globalThis.Date = class extends RealDate {
        constructor(...args) {
          if (args.length === 0) super(RealDate.now() + offset)
          else super(...args)
        }
        static now() {
          return RealDate.now() + offset
        }
      }
    }`)
    const rec = new Recorder(page, frames)
    await record(page, rec)
    await browser.close()

    await mkdir(path.dirname(OUT), { recursive: true })
    const palette = path.join(frames, 'palette.png')
    const input = ['-framerate', String(FPS), '-i', path.join(frames, 'frame-%05d.png')]
    const scale = `fps=${FPS},scale=${GIF_WIDTH}:-1:flags=lanczos`
    await run('ffmpeg', ['-y', ...input, '-vf', `${scale},palettegen=stats_mode=diff`, palette])
    await run('ffmpeg', [
      '-y',
      ...input,
      '-i',
      palette,
      '-lavfi',
      `${scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
      OUT,
    ])
    const { size } = await stat(OUT)
    console.log(
      `wrote ${path.relative(process.cwd(), OUT)}: ${rec.count} frames, ${(rec.count / FPS).toFixed(1)} s, ${(size / 1024 / 1024).toFixed(2)} MB`,
    )
    if (size > MAX_BYTES) throw new Error('the GIF is over 5 MB: shorten the path or the scroll')
  } finally {
    await browser.close().catch(() => undefined)
    server.close()
    if (process.env.DEMO_GIF_KEEP_FRAMES === undefined) {
      await rm(frames, { recursive: true, force: true })
    } else console.log(`frames kept in ${frames}`)
  }
}

await main()
process.exit(0)
