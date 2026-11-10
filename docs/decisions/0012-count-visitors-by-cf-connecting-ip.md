# 0012. The rate limit counts the address in `CF-Connecting-IP`, not `X-Forwarded-For`
Date: 2026-11-07 · Status: accepted; checked live on 2026-11-08

## Context
The API allows each client address 10 requests a minute to the `/v1/` routes (`MEAL_PLANNER_RATE_LIMIT_PER_MINUTE`), and every visitor of the demo shares one free model quota ([0011](0011-demo-on-render-and-pages.md)). On Render the connection the API sees comes from Render's proxy, not from the visitor, so counting the connection's address would put every visitor under one allowance.

The usual fix is to trust the proxy's `X-Forwarded-For`: uvicorn does that with `FORWARDED_ALLOW_IPS='*'`, and takes the leftmost entry as the client. Render's proxy appends to an `X-Forwarded-For` the client sent instead of replacing it, so the leftmost entry is whatever the client wrote. A visitor could send a new value with each request and never reach the limit. Cloudflare sits in front of Render and sets `CF-Connecting-IP` (and `True-Client-IP`) itself, from the connection it accepted.

## Decision
`MEAL_PLANNER_CLIENT_ADDRESS_HEADER` names one header to count clients by. When it is set and the request carries that header, not blank, its value (stripped, cut to 64 characters) is the client; otherwise the connection's address is. A header the setting does not name is never read. The default is empty, so a local run or the image elsewhere counts connections and trusts no header. `render.yaml` sets it to `CF-Connecting-IP`, and `FORWARDED_ALLOW_IPS` stays unset, so uvicorn does not rewrite the client from `X-Forwarded-For` either.

The check after the first deploy, on 2026-11-08:
- A request that carries its own `CF-Connecting-IP` is refused by Cloudflare with a 403 (error 1000) before it reaches the API.
- With a different forged `X-Forwarded-For` and `True-Client-IP` on each call, 10 requests in the minute were allowed and the next two got 429. So the limit counted the real address.

The tests are in `tests/unit/test_middleware.py` (a named header counted per value, stripped, cut to 64 characters, a missing or blank one falling back to the connection, a header sent without the setting ignored) and `test_the_rate_limit_counts_the_configured_address_header` in `tests/unit/test_api_week.py`.

## Alternatives considered
- **`FORWARDED_ALLOW_IPS='*'` with uvicorn's proxy headers.** The common setup, and on Render it lets a client choose its own count, as above.
- **No forwarded address at all.** Safe, and every visitor of the demo would share one allowance of 10 requests a minute.

## Consequences
The limit is per visitor on the demo, and a visitor cannot pick the address it is counted under while Cloudflare is in front of Render. The address is never logged.

It rests on that one fact about the host. If Cloudflare were not in front, a request without the header would count under the proxy's address, so those visitors would share one allowance, and a client could send `CF-Connecting-IP` itself and choose its count again. Moving to another host means checking how it passes the visitor's address and setting the header to match, or leaving it empty. The live check above is the test to repeat.

The count is in memory and per process, so a restart or a second instance starts it again. It limits requests, not model calls, so one visitor within the limit can still start enough weeks to use a large part of the shared daily quota. The README lists that among the limitations.
