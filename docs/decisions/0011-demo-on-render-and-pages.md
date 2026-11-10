# 0011. The demo runs on a Render free web service in Ohio and on GitHub Pages
Date: 2026-11-07 · Status: accepted; the API moved from Singapore to Ohio on 2026-11-08

## Context
The requirements ask for a public demo on free platforms, with rate limits and a spend cap, deployed only after Varun's yes. The plan put the API in a free Hugging Face Docker Space. When the image was ready (2026-11-07), Hugging Face's docs said Docker and Gradio Spaces need a paid plan to create; a free account gets static Spaces and up to two ZeroGPU Gradio Spaces. So the planned host was gone.

The API is not a static site. It runs nutrition-mcp as a subprocess and keeps it open, and it streams a week over server-sent events for up to a few minutes. Measured on a Mac after startup, the catalogue and a search, the API process held 280 MB and nutrition-mcp 72 MB. The browser app is static files with hash routing, so any static host serves it.

The free options checked on 2026-11-07:
- **Render free web service:** 0.1 CPU, 512 MB, stops after 15 minutes without a request and takes about a minute to start again, 750 hours a month, no card.
- **Koyeb free:** 512 MB, 0.1 vCPU, Frankfurt or Washington, stops after an hour without a request.
- **GitHub Pages:** a 1 GB site and a soft limit of 100 GB a month, for static files only.

## Decision
The API runs as a Render free web service built from the Dockerfile, as `render.yaml` describes, with `/healthz` as its health check and `autoDeployTrigger: checksPass`, so a push to `main` deploys only once CI is green. The browser app is built by `.github/workflows/pages.yml` and served from GitHub Pages under `/meal-planner-agents/`, with the API's address from the repository variable `MEAL_PLANNER_API_BASE`. Varun chose this pair on 2026-11-07.

Both are inert until Varun acts. `render.yaml` does nothing until the repository is connected to Render as a Blueprint, and Render asks for `GEMINI_API_KEY` then (`sync: false`), from a Google AI Studio project with no billing account. The Pages build job runs only when `MEAL_PLANNER_API_BASE` is set. So merging the config deployed nothing, and the deploy stayed his steps: the account, the Blueprint, the key and turning Pages on.

**Ohio, not Singapore.** The first service, in Singapore, went live on 2026-11-08 and answered `/healthz` in 0.2 s. After its first sleep it never woke: from about 10:19 to 11:32 IST every request hung with no answer, and Render's dashboard showed no event and no log line between the 9:55 deploy and the next one at 11:32, which brought it back. A timed test reproduced it: after 16 idle minutes, `/healthz` at 11:51 IST got 0 bytes in 240 s, and a second call timed out too, while Render answered `/robots.txt` itself, as it does for a service that is asleep. Render's community forum has the same report for free services in Oregon and Frankfurt (requests that hang, no logs, a new deploy clears it), and one user fixed it by creating the service again in Ohio. Varun chose that on 2026-11-08 ("Recreate in Ohio"): `render.yaml` names `region: ohio` with the reason in a comment, and the service was created again there on 2026-11-09, at the same address. From India it answered `/healthz` in 0.29 to 0.37 s, against 0.1 to 0.2 s from Singapore. Its wake was timed once: idle from 09:28 IST on 2026-11-09, it answered `/healthz` with a 200 after 71.4 s, then in 0.33 s once warm.

## Alternatives considered
- **A paid Hugging Face Space.** The plan's host, at a cost the project's free-only rule does not allow.
- **Koyeb free.** The same CPU and memory as Render and a longer idle window, in Frankfurt or Washington. It was the other free option when Varun chose Render on 2026-11-07, and it stays the fallback.
- **Serving the app from the API's container.** One host and no CORS. A visitor would then wait for the server to wake before seeing any page at all, where Pages shows the app at once and the app says the server is starting.
- **A keep-alive that calls the service every few minutes, and redeploys it when it stops answering.** Put to Varun with the region move on 2026-11-08; he chose the move. A service kept up all month uses nearly all of the 750 free hours, and the calls would hide whether the host can wake at all.

## Consequences
The demo costs nothing, and the key's project has no billing account, so the free quota is the most it can spend. A visitor after 15 idle minutes waits while the server starts, about 70 seconds in the one Ohio test. The page says the server is starting (`useServerWake.ts`), asks again every 3 seconds for two minutes and every 30 seconds after that, so a slow wake still ends with the page working once the server answers. Every visitor shares one free quota and one 512 MB instance; [0012](0012-count-visitors-by-cf-connecting-ip.md) is how the rate limit tells visitors apart.

The Singapore failure means a free host's wake has to be timed, not assumed. One Ohio wake shows that the service can wake, not that it always will: Render's forum reports the hang in more than one region. If a wake hangs again, with no logs until a deploy, the next options are Koyeb or the keep-alive above.

A copy of the demo needs its own Render service, its own Pages origin in `MEAL_PLANNER_CORS_ORIGINS`, and the variable set in its repository. The README's Deployment section lists the steps.
