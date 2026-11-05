// The accessibility check: pa11y-ci with axe over the built site (run `npm run build` first). Every
// screen pa11y.json lists, at desktop and phone width, in the light theme and then the dark one.
// Issues axe says need a person to review are reported as warnings and do not fail the check;
// every other issue does.
//
// pa11y.json is the light run as it is. The dark run is the same with Chrome told to prefer dark.
// Chrome is the system's (Google Chrome on the CI runner and on a Mac), or PUPPETEER_EXECUTABLE_PATH.

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { serve } from './a11y-server.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const CONFIG = path.resolve(here, '../pa11y.json')
const PA11Y = path.resolve(here, '../node_modules/.bin/pa11y-ci')
// Chrome's own setting for prefers-color-scheme: 1 is light, 0 is dark.
const SCHEME_FLAG = '--blink-settings=preferredColorScheme='
const SCHEMES: [string, number][] = [
  ['light', 1],
  ['dark', 0],
]
const CHROMES = [
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
]

interface Config {
  defaults: { chromeLaunchConfig: { args: string[] } } & Record<string, unknown>
  urls: unknown[]
}

function run(command: string, args: string[], env: NodeJS.ProcessEnv): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: 'inherit', env })
    child.once('error', reject)
    child.once('exit', (code) => resolve(code ?? 1))
  })
}

async function main(): Promise<number> {
  const config = JSON.parse(await readFile(CONFIG, 'utf8')) as Config
  const env = { ...process.env }
  env.PUPPETEER_EXECUTABLE_PATH ??= CHROMES.find((chrome) => existsSync(chrome))
  if (env.PUPPETEER_EXECUTABLE_PATH === undefined) {
    console.error('no Chrome found: set PUPPETEER_EXECUTABLE_PATH to a Chrome or Chromium')
    return 1
  }
  const server = await serve()
  const folder = await mkdtemp(path.join(tmpdir(), 'pa11y-'))
  let failed = false
  try {
    for (const [scheme, value] of SCHEMES) {
      const args = config.defaults.chromeLaunchConfig.args.map((arg) =>
        arg.startsWith(SCHEME_FLAG) ? `${SCHEME_FLAG}${value}` : arg,
      )
      const themed = {
        ...config,
        defaults: {
          ...config.defaults,
          chromeLaunchConfig: { ...config.defaults.chromeLaunchConfig, args },
        },
      }
      const file = path.join(folder, `pa11y-${scheme}.json`)
      await writeFile(file, JSON.stringify(themed))
      console.log(`\nThe ${scheme} theme: ${config.urls.length} screens`)
      if ((await run(PA11Y, ['--config', file], env)) !== 0) failed = true
    }
  } finally {
    server.close()
    await rm(folder, { recursive: true, force: true })
  }
  return failed ? 1 : 0
}

process.exit(await main())
