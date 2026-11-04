// Writes src/types.ts from the JSON Schemas in ../schemas, which scripts/export_schemas.py
// writes from the server's Pydantic models. Run `npm run types` after the schemas change;
// `npm run types:check` fails when src/types.ts is out of date, and CI runs it.
//
// The schema files share definitions (PlanRules is in several), and json-schema-to-typescript
// would declare a shared one once per file. So the definitions of every file are merged into
// one set first, and a name defined twice must be defined the same way both times.

import { readFile, readdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { isDeepStrictEqual } from 'node:util'

import { compile, type JSONSchema } from 'json-schema-to-typescript'
import * as prettier from 'prettier'

type Schema = Record<string, unknown>

const here = path.dirname(fileURLToPath(import.meta.url))
const SCHEMAS_DIR = path.resolve(here, '../../schemas')
const OUT = path.resolve(here, '../src/types.ts')
// The type the file is compiled around. Every other definition is declared beside it.
const ROOT = 'Export'

const BANNER = `// Generated from schemas/ by frontend/scripts/generate-types.ts. Do not edit by hand:
// change the server's models, run \`uv run python scripts/export_schemas.py\`, then
// \`npm run types\` in frontend/.`

async function schemaFiles(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true, recursive: true })
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith('.json'))
    .map((entry) => path.join(entry.parentPath, entry.name))
    .sort()
}

function isSchema(value: unknown): value is Schema {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// Every definition of every file, by name, and each file's own type under its title.
async function mergedDefinitions(files: string[]): Promise<Map<string, Schema>> {
  const merged = new Map<string, Schema>()
  const add = (name: string, schema: Schema, file: string): void => {
    const known = merged.get(name)
    if (known !== undefined && !isDeepStrictEqual(known, schema)) {
      throw new Error(`${name} in ${file} differs from an earlier definition of ${name}`)
    }
    merged.set(name, schema)
  }
  for (const file of files) {
    const { $defs, $schema: _dialect, ...root } = JSON.parse(await readFile(file, 'utf8')) as Schema
    const shown = path.relative(SCHEMAS_DIR, file)
    if (typeof root.title !== 'string') throw new Error(`${shown} has no title`)
    for (const [name, schema] of Object.entries(isSchema($defs) ? $defs : {})) {
      if (isSchema(schema)) add(name, schema, shown)
    }
    add(root.title, root, shown)
  }
  return merged
}

// The changes made for the types only. Validation in the browser uses the schemas as they are.
function forTypes(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(forTypes)
  if (!isSchema(value)) return value
  const schema: Schema = {}
  for (const [key, inner] of Object.entries(value)) schema[key] = forTypes(inner)
  const properties = schema.properties
  if (isSchema(properties)) {
    for (const property of Object.values(properties)) {
      // Pydantic titles every field ("Day Types"), and the compiler would make each title a
      // type of its own. A field's type needs no name.
      if (isSchema(property)) delete property.title
    }
    // A field with a default is always in what the server sends, and the import check fills it
    // in for a file without it, so the type says it is there.
    const required = new Set(Array.isArray(schema.required) ? (schema.required as string[]) : [])
    for (const [name, property] of Object.entries(properties)) {
      if (isSchema(property) && 'default' in property) required.add(name)
    }
    if (required.size > 0) schema.required = [...required]
  }
  // A map whose keys come from a list and must all be there (PlanRules.week names all seven
  // days) would become an index signature, with every value possibly missing. Its type lists
  // the keys instead.
  const names = isSchema(schema.propertyNames) ? schema.propertyNames.enum : undefined
  if (
    Array.isArray(names) &&
    isSchema(schema.additionalProperties) &&
    schema.minProperties === names.length
  ) {
    const each = schema.additionalProperties
    schema.properties = Object.fromEntries(names.map((name) => [String(name), each]))
    schema.required = names.map(String)
    schema.additionalProperties = false
    delete schema.propertyNames
    delete schema.minProperties
  }
  // A reference with a description beside it ("total": Nutrients, "the sum over the items")
  // would become a second copy of the type it refers to (Nutrients1).
  if (typeof schema.$ref === 'string') {
    delete schema.description
    delete schema.title
  }
  // Draft 2020-12 writes a tuple as prefixItems, which the compiler does not read; the older
  // form it reads is an items list with no additional items.
  if (Array.isArray(schema.prefixItems)) {
    schema.items = schema.prefixItems
    schema.additionalItems = false
    delete schema.prefixItems
    delete schema.minItems
    delete schema.maxItems
  }
  return schema
}

async function generate(): Promise<string> {
  const definitions = await mergedDefinitions(await schemaFiles(SCHEMAS_DIR))
  const root = definitions.get(ROOT)
  if (root === undefined) throw new Error(`no schema is titled ${ROOT}`)
  const others = Object.fromEntries(
    [...definitions.entries()]
      .filter(([name]) => name !== ROOT)
      .sort(([a], [b]) => a.localeCompare(b)),
  )
  const schema = forTypes({ ...root, $defs: others }) as JSONSchema
  const compiled = await compile(schema, ROOT, {
    additionalProperties: false,
    bannerComment: BANNER,
    format: false,
    // Bounded lists (at most 4 day types) would otherwise become unions of tuples.
    ignoreMinAndMaxItems: true,
    strictIndexSignatures: true,
    unreachableDefinitions: true,
  })
  // Each definition not used by the root says so in a comment, which reads as noise here. The
  // sentence goes, and then any comment it leaves empty or ending in a blank line.
  const text = compiled
    .replace(
      /\n\s*\*?\s*This interface was referenced by `\w+`'s JSON-Schema\n\s*\* via the `definition` "\w+"\./g,
      '',
    )
    .replace(/\n\s*\*\s*(\n\s*\*\/)/g, '$1')
    .replace(/\/\*\*\s*\*\/\n/g, '')
  const options = await prettier.resolveConfig(OUT)
  return prettier.format(text, { ...options, filepath: OUT })
}

async function main(): Promise<void> {
  const text = await generate()
  if (process.argv.includes('--check')) {
    const current = await readFile(OUT, 'utf8').catch(() => '')
    if (current !== text) {
      console.error('src/types.ts is out of date with schemas/: run npm run types')
      process.exit(1)
    }
    console.log('src/types.ts matches schemas/')
    return
  }
  await writeFile(OUT, text)
  console.log(`wrote ${path.relative(process.cwd(), OUT)}`)
}

await main()
