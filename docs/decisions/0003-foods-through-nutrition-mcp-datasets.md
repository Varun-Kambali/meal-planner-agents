# 0003. New foods reach nutrition-mcp through its custom-dataset folder
Date: 2026-10-21 · Status: accepted

## Context
nutrition-mcp v1.0.0 bundles 41 Indian dishes and 66 raw ingredients. The recipes this project writes need foods it doesn't have: oats, most fruit, seeds, olive oil, whey, chicken, tofu, quinoa. nutrition-mcp can load extra dataset folders named in `NUTRITION_MCP_DATA_DIRS`, in the same format as its bundled data, with no change to its tools.

## Decision
This project keeps its own food file in `data/foods/`, in nutrition-mcp's custom-dataset format. The rows come from USDA FoodData Central (public domain), each with its record id, mapped with nutrition-mcp's own `usda_to_nutrients` so the units and carbohydrate definition match the bundled data. Every match is reviewed by hand. nutrition-mcp is a pinned dependency at `v1.0.0` and loads the folder at startup.

A separate catalogue (`data/catalogue.yaml`) lists the ingredients the model may use, with allergen groups, pack size, shelf life once opened and grams per household measure. It holds no nutrition values.

## Alternatives considered
- **Add the foods to nutrition-mcp itself.** It would mix this project's needs into nutrition-mcp's release and data review, and every addition would need a new nutrition-mcp version.
- **A second nutrition source inside this project.** Numbers would come from two places with two mappings, and the rule that every number comes from nutrition-mcp would no longer hold.
- **Let the model supply numbers for foods nutrition-mcp lacks.** That is the guessing this project exists to avoid.

## Consequences
nutrition-mcp stays the only source of numbers, and the custom-dataset feature gets a real user. A clash between an id here and a bundled id stops startup, which the tests check. USDA has no glycemic index, so meals with these foods have no glycemic load. The build needs a free USDA key and reuses a nutrition-mcp module that isn't part of its tool interface, which is acceptable while the version is pinned.
