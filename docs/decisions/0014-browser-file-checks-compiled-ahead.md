# 0014. The browser repeats the server's file checks, with validators compiled ahead of time
Date: 2026-11-04 · Status: accepted; the rules-file validator was made to share the export format's definitions on 2026-11-08

## Context
The browser holds the plan rules, the week and the meal log ([0004](0004-user-data-in-the-browser.md)). A person can export them as one file and load a file back, either an export or a plan-rules file such as the one `meal-planner week` reads or a coach writes. A file can be old, edited by hand or not this app's at all. If a bad one is accepted, the first sign is a 422 from the week or summary route later, far from the file that caused it, or a saved state that no longer loads.

The formats are `schemas/export.json` and `schemas/plan-rules.json`, written from the server's Pydantic models by `scripts/export_schemas.py`; CI fails when they are out of date with the models. Some rules the models check in code cannot be said in a JSON Schema: unique day type and slot ids, slot shares that add up to 1, one log entry per date and slot, at most 7 days, each entry's slot in the plan.

The first version of the browser's check compiled the schemas with Ajv when the page loaded. Ajv compiles a schema into a function with `new Function`, which a Content-Security-Policy without `'unsafe-eval'` refuses, so adding such a policy later would have broken loading the saved state on every page.

## Decision
1. **The browser checks every file before it uses it, and every state before it saves it,** in `src/validate.ts`: the schema first, then the rules the schema cannot say, as the server's models make them. A file that fails is refused with each problem and its JSON Pointer, such as `/log/entries/0/items/0/food_id`. `saveState` runs the check `loadState` will run and refuses a state that would not load again, so what is saved always opens.
2. **The schema validators are compiled ahead of time.** `scripts/generate-validators.ts` uses Ajv's standalone code generation to write `src/generated/validators.js`, plain functions for the export file, the stored state and a plan-rules file. The file is committed. The generated code has no `new Function` and no `eval`, Ajv itself is imported only for its types, and the generator stops if the output still needs a `require`. `npm run validators:check` regenerates it and fails when the committed file differs, and CI runs it.
3. **The checks a schema cannot say are copied by hand, and a test keeps the copy honest.** `tests/unit/test_export_schemas.py` runs the server's models over `src/test/invalid-exports.json`, the files the browser's tests refuse, and expects the server to refuse each one too.

On 2026-11-08 the plan-rules validator was made to point at the export format's `PlanRules` definition instead of compiling `plan-rules.json` a second time, and `inlineRefs: false` makes each shared definition one function. That took Vite's 500 kB warning off the build (the main bundle is 462.67 kB).

## Alternatives considered
- **Compile in the browser with Ajv.** The first version: less build machinery, and `'unsafe-eval'` needed by any Content-Security-Policy the page gets, with Ajv's compiler in the bundle.
- **Send the file to the server and let its models check it** (through `/v1/summary`, considered when the export format was written). One set of checks, no copy. Loading a file would need the API to be awake and would spend the rate limit, and the browser could not check its own saved state without a request.
- **The schema alone.** No hand-copied checks, and a file whose slot shares add up to 90% would load, then be refused by the first request that sends its rules.

## Consequences
A bad file is refused at the moment it is loaded, with the place of each problem. The page can take a Content-Security-Policy without `'unsafe-eval'`. The demo has none today, because GitHub Pages sets no headers, so this keeps the option open rather than closing a hole.

The checks live twice, in Python and in TypeScript. The test over `invalid-exports.json` keeps them in step only for the cases listed there, so a new rule in a model needs a case added for it. A change to a schema needs `npm run types` and `npm run validators` before CI passes. With `messages: false`, `validate.ts` words each problem itself, and a broken id pattern reads as "does not meet the schema's pattern rule". A plan-rules file with one mistake is tried as an export first and refused with the export format's problems, which are harder to read than the rules format's would be.
