# 0008. A replay of the smoke slice gates every PR, held to its own numbers
Date: 2026-10-27 · Status: accepted

## Context
The full eval writes a week for each of the 20 plans with both systems and reads all 53 typed meals, calling the model with a key. Running it on every PR would spend the free tier's daily quota, put a key in CI, and give different numbers each time, because the model does not answer the same way twice. Thresholds on a live run would need margins wide enough to hide a real regression.

The runner can record a run's model replies and replay them (`RecordingModel` and `ReplayModel`, which find a reply by a hash of its prompts). The smoke slice, fixed in `evals/cases.py`, is three plans (`bengali_fish_home`, `hard_vegan_jar_breakfast`, one of the two hard plans, and `kerala_egg_no_dairy`) and ten meals chosen to cover every label status, foods the data lacks and three of the expected misses. Its recording holds 53 model replies. A replay sends them through the real portion fitting, nutrition-mcp and checks, so the same code gives the same numbers every time, with no key and no model call. The replay at commit 51b7e0a matched the recorded run on every metric; only the timings differed.

The plan said to set the thresholds a few points below the first full run. That does not fit this gate. The slice is 3 of the 20 plans, picked to be hard, and its numbers sit well below the full run's: 46 of 70 recipes pass every hard check in the slice, 451 of 553 in the full run. A threshold taken from the full run would fail every replay, or would have to be so low that it checks nothing.

## Decision
**CI's `eval-smoke` job runs `make eval-smoke` on every PR and every push to main.** It replays `evals/recordings/smoke.jsonl` and holds the result to `evals/thresholds.toml`. A metric outside its threshold fails the job, and so does a model call the recording has no reply for. The "main via PR" ruleset requires it, as it requires `check` and `secrets-scan`.

**The thresholds come from the replay itself.** Each minimum is the replayed value rounded down to two decimal places, and each maximum rounded up. At the slice's counts that is less than one item away from the replayed number, so losing one recipe or one food fails the gate. A margin of a few points is there to absorb noise between runs, and a replay has none.

**What is held:**

| Metric | Replayed | Threshold |
|---|---|---|
| Pipeline: recipes passing every hard check | 46 of 70 | at least 0.65 |
| Pipeline: recipes passing at their first attempt | 27 of 70 | at least 0.38 |
| Pipeline: recipes passing the variety checks | 66 of 70 | at least 0.94 |
| Baseline: recipes passing every hard check | 7 of 70 | at least 0.10 |
| Reader: food precision | 15 of 16 | at least 0.93 |
| Reader: food recall | 15 of 17 | at least 0.88 |
| Reader: status accuracy | 14 of 17 | at least 0.82 |
| Reader: asked when it should | 5 of 6 | at least 0.83 |
| Reader: foods not in the data reported missing | 4 of 4 | at least 1.0 |
| Reader: calorie error against the label (MAPE) | 0.125 over 8 meals | at most 0.13 |
| Reader: reads that failed | 0 of 10 | at most 0 |

**The baseline has a minimum too,** so a change cannot make the headline look better by making the comparison worse.

**Not held, and why.** No day and no week passes in the slice for either system. The pipeline's days fail the saturated-fat check most (17 of its 21 days), and 13 of the 21 fail only because a food in them, such as rohu or curry leaves, has no saturated-fat or fibre value in the food data, so the check cannot show the day is within its limit. The hard plan's days miss protein by design. A minimum of 0 checks nothing, so days and weeks get thresholds once those missing values are dealt with. Tokens, cost and time are not held: a replay times nothing real, and cost comes from LiteLLM's price list, which changes with its version.

**Moving a threshold** happens in the PR whose change moves the number, with the reason in the PR. A change to anything the model is sent (a prompt, the catalogue, the food data, the portion fitting or a check, whose failures go back to the model in a repair) makes the replay miss, since the recording no longer stands for the code. That PR records the slice again with a key, which needs Varun's yes, and sets the thresholds from the new replay.

## Alternatives considered
- **A live smoke eval in CI with a key in the repository's secrets.** It would test the model as well as the code. But it spends quota on every PR, a PR from a fork gets no secrets, and its numbers change from run to run, so the thresholds would need margins that let a real regression through.
- **Thresholds from the full run, a few points below it.** The slice's numbers are lower than the full run's, as above, so these would fail every replay or check nothing.
- **A margin of a few points below the replay.** With 70 recipes in the slice, a 5-point margin lets 3 recipes go without the gate noticing, and there is no noise for the margin to absorb.
- **Hold every metric in the results file.** Counts, tokens and time would fail on changes that are fine, and a gate that fails for no reason gets ignored.

## Consequences
The gate catches regressions in what runs after the model: portion fitting, nutrition-mcp's numbers, the checks, the reader's amount and unit checks, and the metrics. It says nothing about the model, whose replies are fixed. Only a full run measures that, and the full run's numbers are the ones the README shows.

A check that gets looser raises the numbers and still passes a minimum. The tests at the edge of each tolerance in `tests/unit/test_checks.py` catch that, not this gate.

Most work on the recipes changes what the model is sent, so it comes with a new recording: 53 calls on the free tier, after Varun's yes.

The thresholds are as strict as the counts allow, so a change that moves a number by one item on purpose still fails the gate. Its PR sets the new value and says why, which leaves a record of every number that moved.
