# 0009. A day check that cannot tell is unsettled, and saturated fat is bounded by total fat
Date: 2026-10-27 · Status: accepted

## Context
A day's totals are added up from what nutrition-mcp says each recipe holds. When a food has no value for a nutrient, the day's total for it is only what the other foods hold: a lower bound ([0007](0007-the-log-holds-foods-and-amounts.md)). The day check failed such a day whenever the lower bound could not settle it: a cap with the known part under it, or a floor with the known part under it. The failure was then blamed on one of the day's recipes and sent back to the writer.

The first full eval showed what that costs. The data nutrition-mcp uses (CoFID 2021) has no saturated fat for curry leaves, rohu, garam masala or methi leaves, and no fibre for moong, toor, chana and urad dal, moth beans or rohu. Of the pipeline's 50 days failing on saturated fat, 48 had a food like that. On 37 of them the part known was under the cap, so the day could have been within it or over it; on the other 11 the part known was already over the cap, and 2 more had a measured total over it. Of its 44 days failing on fibre, 38 had a food like that, and on all 38 the part known was under the floor. Across all checks, 38 of its 88 failing days failed only on checks the data could not settle (`evals/results/runs/2026-10-27_gemini-gemini-3.1-flash-lite_b593c44/cases.jsonl`). Repair rewrote recipes for these days, and a rewrite cannot supply a missing value: it can only drop curry leaves from a South Indian dish or rohu from a Bengali fish plan.

## Decision
**A day check has three outcomes: it passes, it fails, or it is unsettled.** It fails only when what is known already breaks the limit: a lower bound over a cap, or an upper bound under a floor. It passes when the total, or a bound on it, is within the limit. Otherwise it is unsettled: the day can be shown neither to meet the limit nor to miss it, because a food lacks a value. An unsettled check is reported on the day (`DayReport.unsettled`, `DaySummary.unsettled`) with the foods that lack the value, never counted as a failure, and never sent to repair. For calories, and for a cap with no ceiling, no rewrite settles it short of dropping the food. For a floor, or a cap whose ceiling is over it, a rewrite could settle it by raising the known part or lowering the ceiling, and the check is still not sent: the missing value may settle it by itself (a dal with no fibre value still has fibre), and repair would spend model calls chasing what the data cannot show.

**Saturated fat has an upper bound from total fat.** Saturated fat is part of a food's total fat, so it can never be more than it. For a food with no saturated-fat value but a total-fat value, the total fat is its most. The day's ceiling is the known saturated fat plus the total fat of the foods that lack it; when that ceiling is within the cap, the day passes the cap. Every number in the bound is nutrition-mcp's, and no value is estimated: the bound proves the day is within the cap, or it settles nothing. A food with neither value gives no ceiling.

**The eval counts unsettled days apart.** A day passes only when nothing fails and nothing is unsettled. summary.md shows the days the checks could not settle and the checks they could not settle, and a week with no failing recipe or day alongside the strict week count. The meal-log summary counts a day as on target only when nothing is unsettled.

**Fibre for the dals is a data question,** left to the review of the food data. No bound from the data helps a floor pass: a missing fibre value can only add fibre, so the known part is already the most that can be shown.

## Alternatives considered
- **Treat a missing value as 0.** Days would pass on numbers nobody measured, against the rule that a missing value is never 0.
- **Estimate the missing value** from a similar food or another table. That is a number nutrition-mcp did not give, shown next to ones it did. A second source for a food is a data change, made through nutrition-mcp's custom datasets ([0003](0003-foods-through-nutrition-mcp-datasets.md)) after a review, not in a check.
- **Keep failing the day, but skip repair for it.** It saves the calls, but a day that cannot be judged would still be published as a failing day, which is the error the eval found.
- **Keep the foods out of the catalogue.** It would remove curry leaves, rohu and the dals from Indian cooking to make a check pass.

## Consequences
A day with curry leaves or rohu and little other saturated fat now passes the cap, and repair no longer spends model calls on what it cannot fix. The day and week numbers change meaning from this commit: a results file written before it counts unsettled days as failing, and its commit says which rule it used.

A user sees "not known" with the foods named on an unsettled day, instead of a failure. Whether a fibre floor was met stays unknown until the dals have fibre values. A day that is under a floor as far as is known, with a food lacking the value, is left as it is, where before repair might have added more of the nutrient. A day that falls short only by a pinch of spice with no value is left unsettled too, though the spice's own weight could show it cannot make up the difference; using a food's grams as a bound on every nutrient it holds would settle those days, and is not built yet.

A day whose recipes all lack numbers, because nutrition-mcp could not work them out, is unsettled too. Those recipes still fail on their own, so the week does not pass, and they are what repair rewrites.

What the model is sent changes, because unsettled checks leave the repair prompts, so the smoke recording is made again and the thresholds are set from its replay ([0008](0008-smoke-replay-gates-every-pr.md)). Days now pass in the slice, so the gate holds the share of days passing and of weeks with no failure, and a maximum on the share of days left unsettled.
