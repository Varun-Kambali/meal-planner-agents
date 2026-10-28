# 0010. Traces are made by this code, masked twice, and off without keys
Date: 2026-10-28 · Status: accepted

## Context
The requirements ask for Langfuse traces of every run: each step, the model, tokens, cost, latency and the checks that failed, with the user's rules and typed text masked, and tracing off unless its keys are set. The prompts and the replies hold exactly what must stay out: plan, slot and day type names, styles, cuisines, liked and excluded foods, recipe titles and steps written for them, and the words a person typed about a meal. Logs already keep all of that out with an allow-list of fields (`log.py`). A trace has to keep it out as well, and it leaves the machine: the plan is Langfuse Cloud's free Hobby tier.

The Langfuse Python SDK (v5) is built on OpenTelemetry. It offers integrations that trace without code: a LiteLLM callback that records every completion, a LangChain callback handler that records each LangGraph node with its input and output, and an `@observe` decorator that records a function's arguments and return value. Each records the prompt, the reply or the graph's state as it is. The SDK also has a mask hook, which it calls on every input, output and metadata before setting them on a span. When it is not given a tracer provider, the client becomes the process's global one, and its default filter sends any span with `gen_ai` attributes or from an instrumentation it knows (LiteLLM, LangChain, OpenAI and others), whoever made it.

## Decision
1. **This code makes every span.** `tracing.py` starts the trace, a span for each graph node and a generation for each model call through the SDK's `start_observation`. No LiteLLM callback, LangChain callback handler or `@observe` is used.
2. **Replies are masked like prompts.** Every string in a prompt or a reply becomes `[masked]`; ids, counts and numbers stay. Values are masked before they are handed to the SDK, and `mask` is also the client's mask hook, so a value that reaches the SDK another way is masked as well.
3. **`mask` keeps a string only under a field in one allow-list,** `ID_FIELDS` in `tracing.py`: food ids, the plan's own slot and day type ids, days of the week, a read item's status, error codes and exception class names. Every other string is masked, including one that looks like an id. Numbers, booleans and None are kept; lists, mappings and pydantic models are walked, and mapping keys are kept as field names.
4. **Only this code's spans reach Langfuse.** The client gets a tracer provider of its own, which is not the global one, so a span another library makes through OpenTelemetry never reaches it. Its `should_export_span` is the SDK's `is_langfuse_span`, so a span from any other instrumentation scope is dropped even on that provider. A test makes such a span both ways and finds neither sent.
5. **Off without keys.** `setup_tracing` returns None unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are both set and not blank. Then no client is made, no thread starts and nothing touches the network, which is how `make test` and `make eval-smoke` run in CI.

The CLI and the API get the tracer from `start_services`. It wraps the model so that each call becomes a generation in the span it runs in, and when its block ends it sends what is left: the CLI before it exits, the API when it shuts down. An error inside tracing is logged with its class only and swallowed, so tracing never fails a request.

## Alternatives considered
- **The LiteLLM callback and the LangChain callback handler.** Every node and call traced with a few lines. They send the prompts, replies and graph state as they are, and masking them after the fact would mean taking apart LangChain's message objects and this project's prompt format to find what may stay.
- **Mask the prompts and keep the replies.** A trace would show what the writer wrote. A reply carries the plan back, though: titles, cuisines and steps written for its styles and liked foods, and the reader's reply carries the typed food names.
- **Keep strings that look like ids.** A cuisine such as `south_indian`, a liked food or a slot style passes for an id, so a pattern cannot tell what is safe. Only the field a string sits under can.
- **Let the client take the global tracer provider, with the default filter.** Any library in the process that traces through OpenTelemetry would then have its spans sent when they look like model calls, and an instrumentation of LiteLLM added later would send prompts unmasked.
- **Self-hosted Langfuse.** Traces would stay on a machine the project runs, but it needs its own database services next to the API, and the demo is planned for a free Space. The requirements chose the Cloud free tier, which masking makes safe to use.

## Consequences
A trace shows the shape of a run and its numbers: which nodes ran and how many rounds, each call's model, tokens, cost and latency, whether each recount ran, the checks that failed or were left unsettled, and the food ids and grams of each recipe. It never shows a title, a step, a cuisine or anything typed, so judging the writer's words still needs the eval's local results.

Mapping keys are kept, so no caller may pass a mapping keyed by what the user wrote. Every mapping the code passes today is keyed by field names, days, nutrients, nutrition-mcp's units or check names.

A new graph node is traced only when it is added through `_traced` in `week.py`; a new model task is traced by the model wrapper with no more code. Spans other libraries make are never sent, and turning one on would mean changing the filter and showing that what it sends is masked.

The eval runner passes no tracer, so evals send no trace even with keys set. A screenshot of a real trace (`docs/media/trace.png`) waits for the first run with a Langfuse project's keys.
