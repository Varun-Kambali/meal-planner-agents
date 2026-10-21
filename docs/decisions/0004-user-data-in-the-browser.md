# 0004. The user's data stays in the browser; the API keeps no state
Date: 2026-10-21 · Status: accepted

## Context
The app handles a person's targets, food rules and a log of what they ate. A public demo would hold that for anyone who tries it. Storing it on a server means accounts, login, a database, backups and a privacy policy, none of which is the point of v1.

## Decision
The browser keeps the plan rules, the week's recipes and the meal log, with export and import as one JSON file. Every API request carries what it needs, and the server drops it after the response. Logs record ids, counts, tokens, latency and cost, never the rules or typed text, and traces mask both. The log stores food ids and amounts; the server recomputes the numbers from nutrition-mcp for each summary.

## Alternatives considered
- **Accounts and Postgres** (Neon or Supabase free tier, the roadmap's first idea). Sync across devices, at the cost of login, a schema, and personal food logs on a public demo.
- **SQLite on the server without accounts.** Simpler, but data from different users would mix on a shared demo, and the server would still hold it.

## Consequences
Nothing about a user can leak from the server. Each request is larger, because the rules travel with it. Clearing the browser's storage loses the data unless it was exported, and there is no sync between devices; the UI says so. A dietitian product would need accounts and a server-side store, which can be added behind the same API.
