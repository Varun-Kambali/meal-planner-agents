# 0007. The log holds foods and amounts, and the server works out the numbers
Date: 2026-10-26 · Status: accepted

## Context
Tracking keeps a meal log in the browser ([0004](0004-user-data-in-the-browser.md)): for each planned meal, how it went and what was eaten. A day view then has to show what that came to against the day's targets. There are two ways to get there. The log can hold the numbers, taken from the reader's result when the meal was logged, so that a summary is only addition. Or it can hold the foods and amounts and have the numbers worked out each time.

The first is quicker, but the log is a file the person can save, edit and load again, and it can be old. A number in it was right for the food data of the day it was written, and nothing checks it later. Every other number in this project comes from nutrition-mcp when it is shown ([0002](0002-model-writes-code-checks.md)), and a day eaten should be judged the way a day written is.

## Decision
**The log holds no number.** A log (`MealLog`, version 1) has an entry for each planned meal that was logged: its date, its slot, its status (`as_planned`, `different_amount`, `swapped` or `skipped`) and the foods eaten, up to 20, each a nutrition-mcp food id, an amount above 0 and a unit. The models refuse fields they do not know, so a calorie count in an edited file is refused at the field. A skipped meal lists no foods and any other lists at least one. The log covers at most 7 consecutive days, and a slot appears once for a date.

**The server works everything out again** (`summarise`, behind `POST /v1/summary` and `meal-planner summary`). It takes the plan rules and the log, because the API keeps nothing:

1. Each meal that was eaten goes to nutrition-mcp's `compute_meal`, one at a time. A skipped meal is not sent. Units go through `Catalogue.meal_item`, the function the reader uses ([0006](0006-reading-a-typed-meal.md)), so a meal the reader matched is a log the summary accepts.
2. The meals of a date are added up with `add_up_meals`, the function the day checks use for a written week. A nutrient that no eaten food has a value for is unknown (null), never 0. A date on which every meal was skipped totals 0, because nothing was eaten, which is known.
3. Each date is held to the targets of its own day type, the one the plan gives its weekday, by `check_day_totals` with the day tolerance from the settings. The failures read the way a written day's do.
4. The week carries the share of planned meals eaten as planned and the number of days on target. The planned meals are the plan's slots on every date from the log's first to its last, so a slot with no entry counts as not as planned.

**A log that does not fit is refused, never partly summarised.** An entry whose slot the plan lacks, a food nutrition-mcp does not know, and an amount or unit it refuses each refuse the log, and every such entry is named by its date and slot at once. The API answers 422 and the command line exits 2. Nothing is dropped quietly, and no summary is built from the entries that did fit.

## Alternatives considered
- **Store the numbers in the log.** A summary would need no nutrition-mcp calls. But it would trust numbers in a file that can be edited or old, and the day would be judged on figures nobody recomputed. The saving is the nutrition-mcp calls, which cost no model quota.
- **Judge every day against one set of targets.** It is simpler, but the plan has training days and rest days with different targets, and a day eaten on a rest day should not fail a training day's calories.
- **Summarise what fits and skip the rest.** The user would get a view, but a total that leaves a meal out looks like a smaller day, with nothing to say a meal is missing. Refusing with the entry named lets the person fix it.

## Consequences
Every summary costs nutrition-mcp calls, one for each meal eaten, and no model call. `meal-planner summary` needs no model key. The meals are sent one at a time, so a week with all its slots logged is that many calls.

A food removed from nutrition-mcp's data later, or a unit it stops reading, makes an old log fail with the entry named, and the person fixes the entry. The same goes for a log whose slot the plan has since dropped.

The export schema (`schemas/export.json`) cannot say the rules that span fields or entries: a skipped meal lists no foods and any other lists some, a slot appears once for a date, the dates run to at most 7 days, and every slot is one the plan has. The browser's import check, which is not built yet, has to run those as well as the schema, so that a bad file fails at import with the entry named instead of at the first summary.

A slot with no entry counts as not as planned, also on a date with no entries between two logged dates, so the share does not rise because meals went unlogged. Dates after the last entry are not counted. A date with no entries gets no day summary, since nothing is known about what was eaten on it.
