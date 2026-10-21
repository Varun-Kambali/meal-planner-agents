import os

# LiteLLM fetches its price list from GitHub when it is imported, unless told to use the copy
# that ships with it. This runs before any test module imports litellm, so no test needs the
# network. meal_planner.llm sets the same variable for the app.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
