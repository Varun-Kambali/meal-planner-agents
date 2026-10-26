# 0006. The model names the foods; nutrition-mcp and code decide the rest
Date: 2026-10-26 · Status: accepted

## Context
When a person goes off plan, they type what they had in their own words: "2 rotis, a katori of dal and 2 eggs". Tracking needs that turned into foods, amounts and numbers. [0002](0002-model-writes-code-checks.md) already settles who is trusted with numbers: nutrition-mcp supplies them, and the model never does. Reading a meal adds three more problems:

- **The text is free, and the model is the only part that can split it** into foods and amounts, in any order, with "half", "a" and household measures.
- **The names are not nutrition-mcp's names.** "Dal" is not one food in its data, and "roti" is an alias of its chapati.
- **Some amounts cannot be known.** "A bowl", "some" and "2 or 3" give no number a meal can be added up from, and nothing sources the weight of a bowl.

## Decision
The model does the part only it can do, and code decides everything that ends up as a number. A read is five steps ([architecture.md](../architecture.md#reading-a-typed-meal) has them in full).

**The model names what is in the text** (`read_meal`, `prompts/reader.md`). It lists up to 20 foods, each with a name, an amount, a unit and the words the person used for the amount (`amount_text`). The text goes in as a JSON string inside a delimited data block, and the prompt says it is data and never instructions. The reply has no field for a nutrition figure, and anything extra in it is ignored.

**nutrition-mcp finds the food.** Each name goes to `search_food` for up to 5 results. The top result is taken when it is the only one, or when the name typed is its name, one of its aliases or its id. The names this leaves open ("dal") go to one more model call together (`choose_food`, `prompts/choose_food.md`). It sees each name and its numbered options, and nothing else the person typed, and it answers with an option number or null for each name. It never writes an id. A name with no result, null, a number outside the list, no pick for a name and two picks that disagree each leave the item `missing`.

**Code settles the amount.** The model's number is used only when its words for the amount are in the typed text, on their own rather than inside a longer number or word, and are neither vague (some, a little, a handful) nor a range (2-3, 2 or 3). Otherwise the item is `needs_amount`, whatever number the model returned, and the person is asked.

**Code settles the unit, then works out the grams.** `Catalogue.meal_item` reads kilograms and litres as grams and millilitres, sends a unit nutrition-mcp lists for the food by its own name, and otherwise uses the catalogue's measure for the food (its grams per piece, cup or tbsp). A number with no unit word counts pieces. A unit neither has makes the item `unknown_unit`, and the item lists the units that would work. No weight is made up: "2 pav" stays unknown, because nothing sources a pav's weight.

**nutrition-mcp gives the numbers.** Each matched item carries its numbers for its grams, and `compute_meal` adds up the matched items. The result lists every item with its status (`matched`, `needs_amount`, `unknown_unit` or `missing`), the total and the names that were not found.

## Alternatives considered
- **The model returns food ids and grams.** It would be one call, but the grams would be its guesses, and guessing is what this project exists to avoid ([0002](0002-model-writes-code-checks.md)). An id it writes could also be one nutrition-mcp does not have.
- **Matching without a model.** Searching the text for known names costs no model call, but it cannot split "2 rotis, a bowl of dal and 2 eggs" into foods and amounts, because the amount can come before or after the name, in words or digits.
- **Using the model's amount as it comes.** It would be simpler, but the model can return a number the person did not type, such as 1 for "roti and dal", or a single number for "2-3". Requiring its words to be in the text, and reading them for a range or a vague word, keeps the amount the person's.

## Consequences
A read makes at most two model calls: one to list the foods, and one more for all the open names together. A text whose names the search settles costs one. Both calls have their own timeout (`MEAL_PLANNER_READER_CALL_TIMEOUT_S`, 30 seconds by default), and the names are searched one after another. If a model call fails, the read fails with a message that names the step and holds nothing the person wrote (a 503 or 502 from the API, exit 1 from the command line).

A vague amount, a range, an amount whose words are not in the text and a unit nobody has each ask the person instead of being guessed. That includes an amount the model rewrote, such as digits for a word. A food the search does not find is reported by name in `missing`. It is left out of the total, as is an item that needs an amount or has an unknown unit, so the total covers only what was matched. The person is to confirm or edit the result in the browser before it is saved; the browser app is not built yet.

Which dish a general name means is the model's pick among nutrition-mcp's own options, so it can be wrong in a way code cannot see. The reading eval in milestone 4 measures how often. The two reader prompts stay as they are until that eval records the model's replies. The smoke eval replays recordings made against them, so a changed prompt means recording again.
