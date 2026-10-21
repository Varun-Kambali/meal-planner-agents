# 0001. Follow the plan the user already has
Date: 2026-10-21 · Status: accepted

## Context
The project started as a planner that builds a whole week from a profile: targets, meals, a shopping list, a prep calendar, rotation across weeks, and later supplements and habits. Each of those is a feature with its own data, checks and UI. Building them together would leave none of them finished and tested properly.

The person this is for already has a plan, written from their own research or by a dietitian. The plan is a set of rules: day types with targets, a style and time limit for each meal, the gear in the kitchen, food rules, shopping days. Writing the rules was quick. Writing a week of recipes that keep to them, all different and all on target, took far longer, and seeing whether the meals eaten matched the plan was a separate job.

## Decision
v1 takes the plan's rules as input. It writes a week of recipes that fit them and tracks the meals eaten against them. Writing a plan or targets from scratch, shopping lists, a prep calendar, rotation across weeks, supplements, habits and health conditions are out of v1.

## Alternatives considered
- **Plan the whole week from a profile.** The original idea. It needs targets from scratch, which pulls in health judgments, and it makes the recipe step one part of a much larger product.
- **The full system: plan, prep, shop, rotate, track.** Everything the user would eventually want, and far too much to finish and test properly at once.
- **Tracking only.** Smaller, but it skips the part that took the most time by hand, and it shows less of what a model pipeline with checks can do.

## Consequences
The user has to enter rules before anything happens, so the plan form has to be quick to fill in, and import and export of the rules file matter. A dietitian can write the rules file for a client. The excluded features stay possible later: shopping and prep read the same week of recipes, and nutrition-mcp's `compute_targets` could fill in calorie and protein targets for someone who has none.
