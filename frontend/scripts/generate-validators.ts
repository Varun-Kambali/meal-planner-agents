// Writes src/generated/validators.js: the import checks for schemas/export.json and
// schemas/plan-rules.json, compiled to plain functions ahead of time with Ajv's standalone code
// generation. Compiling in the browser would need `new Function`, which a Content-Security-Policy
// without 'unsafe-eval' refuses on every page load. Run `npm run validators` after the schemas
// change; `npm run validators:check` fails when the file is out of date, and CI runs it.
//
// Three validators: `exported`, for a file a person loads, `stored`, for what this browser
// saved, which is the same format with no plan rules until they are saved, and `rulesFile`, for a
// plan-rules file (schemas/plan-rules.json), the format `meal-planner week` reads and a coach
// can write.

import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import ajv2020, { _ } from 'ajv/dist/2020.js'
import standalone from 'ajv/dist/standalone/index.js'

import { formats } from '../src/schema-runtime.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const SCHEMA = path.resolve(here, '../../schemas/export.json')
const RULES_SCHEMA = path.resolve(here, '../../schemas/plan-rules.json')
const OUT = path.resolve(here, '../src/generated/validators.js')

const BANNER = `// Generated from schemas/export.json and schemas/plan-rules.json by frontend/scripts/generate-validators.ts. Do not edit
// by hand: run \`npm run types\` and \`npm run validators\` in frontend/ after the schemas change.
/* eslint-disable */
`

// Both are CommonJS modules, so Node hands over the whole module; the class and the function
// are its `default`.
const Ajv2020 = ajv2020.default
const standaloneCode = standalone.default

const STORED = {
  $id: 'stored',
  type: 'object',
  additionalProperties: false,
  required: ['version', 'rules', 'week', 'log'],
  properties: {
    version: { const: 1 },
    rules: { anyOf: [{ $ref: 'export.json#/$defs/PlanRules' }, { type: 'null' }] },
    week: { $ref: 'export.json#/properties/week' },
    log: { $ref: 'export.json#/$defs/MealLog' },
  },
}

// What the generated code would `require`, and the name it is imported under instead, since the
// module runs in the browser as an ES module.
const RUNTIME: Record<string, string> = {
  'require("ajv/dist/runtime/ucs2length").default': 'ucs2length',
  'require("./schema-runtime").formats': 'formats',
}

async function generate(): Promise<string> {
  const ajv = new Ajv2020({
    allErrors: true,
    // Fills in a field the server has a default for, so the data matches its type.
    useDefaults: true,
    strict: true,
    // validate.ts words each problem itself from its keyword and parameters.
    messages: false,
    formats,
    code: { source: true, esm: true, formats: _`require("./schema-runtime").formats` },
  })
  ajv.addSchema(JSON.parse(await readFile(SCHEMA, 'utf8')), 'export.json')
  ajv.addSchema(JSON.parse(await readFile(RULES_SCHEMA, 'utf8')), 'plan-rules.json')
  ajv.addSchema(STORED)
  let code = standaloneCode(ajv, {
    exported: 'export.json',
    stored: 'stored',
    rulesFile: 'plan-rules.json',
  })
  for (const [call, name] of Object.entries(RUNTIME)) code = code.replaceAll(call, name)
  // An ES module is strict already, and after the import the directive would do nothing.
  code = code.replace(/^"use strict";/, '')
  const left = /require\([^)]*\)/.exec(code)
  if (left !== null) throw new Error(`the validators need ${left[0]}, which has no import here`)
  return `${BANNER}import { formats, ucs2length } from '../schema-runtime.ts'\n${code}\n`
}

async function main(): Promise<void> {
  const text = await generate()
  if (process.argv.includes('--check')) {
    const current = await readFile(OUT, 'utf8').catch(() => '')
    if (current !== text) {
      console.error(
        'src/generated/validators.js is out of date with schemas/: run npm run validators',
      )
      process.exit(1)
    }
    console.log('src/generated/validators.js matches schemas/')
    return
  }
  await writeFile(OUT, text)
  console.log(`wrote ${path.relative(process.cwd(), OUT)}`)
}

await main()
