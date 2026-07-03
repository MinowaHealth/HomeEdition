"""Stage 1.5 — generic meal catalog seeding per persona.

Why per-persona, not shared:
    `health_food_itemsv2` and `meals` carry `user_id` and are per-user scoped
    by (tenant_id, user_id). There is no system-user-owned global catalog
    yet, so each persona gets their own copy of the same 8 foods +
    5 meal templates. ~13 POSTs per persona × 17 personas = 221 calls,
    well under the rate-limit budget for the seed run.

Wired into `_seed_persona` at the end of stage1.py topology so that
captured `food_items` IDs are available when meals POST.
"""
from __future__ import annotations

from .sources.manual import ManualClient


# Generic foods. Calories/macros are USDA-ish ballpark; not nutrition
# advice. Names use the seeder's records-side IDs so meals can reference
# them locally.
_FOODS: list[dict] = [
    {"_rec_id": "F_EGGS",    "name": "Scrambled Eggs (2)",     "calories": 140, "protein_g": 12, "carbs_g": 1,  "fat_g": 10},
    {"_rec_id": "F_OATS",    "name": "Oatmeal (1 cup)",        "calories": 150, "protein_g": 5,  "carbs_g": 27, "fat_g": 3},
    {"_rec_id": "F_APPLE",   "name": "Apple (medium)",         "calories": 95,  "protein_g": 0.5,"carbs_g": 25, "fat_g": 0.3},
    {"_rec_id": "F_CHICKEN", "name": "Chicken Breast (4oz)",   "calories": 180, "protein_g": 35, "carbs_g": 0,  "fat_g": 4},
    {"_rec_id": "F_RICE",    "name": "Brown Rice (1 cup)",     "calories": 200, "protein_g": 4,  "carbs_g": 45, "fat_g": 0.5},
    {"_rec_id": "F_SALAD",   "name": "Mixed Salad (2 cups)",   "calories": 50,  "protein_g": 3,  "carbs_g": 9,  "fat_g": 1},
    {"_rec_id": "F_YOGURT",  "name": "Greek Yogurt (1 cup)",   "calories": 130, "protein_g": 22, "carbs_g": 9,  "fat_g": 0.5},
    {"_rec_id": "F_TOAST",   "name": "Whole Wheat Toast (2)",  "calories": 140, "protein_g": 6,  "carbs_g": 24, "fat_g": 2},
]


# Meal templates reference foods by records-side _rec_id. The seeder
# remaps to server-side UUIDs after foods POST.
_MEAL_TEMPLATES: list[dict] = [
    {"_rec_id": "M_BREAKFAST", "name": "Breakfast",
     "items": [("F_EGGS", 1), ("F_TOAST", 1), ("F_APPLE", 1)]},
    {"_rec_id": "M_LUNCH",     "name": "Lunch",
     "items": [("F_CHICKEN", 1), ("F_RICE", 1), ("F_SALAD", 1)]},
    {"_rec_id": "M_DINNER",    "name": "Dinner",
     "items": [("F_CHICKEN", 1.5), ("F_SALAD", 1), ("F_YOGURT", 0.5)]},
    {"_rec_id": "M_SNACK",     "name": "Snack",
     "items": [("F_APPLE", 1), ("F_YOGURT", 0.5)]},
    {"_rec_id": "M_LIGHT",     "name": "Light Meal",
     "items": [("F_OATS", 1), ("F_APPLE", 1)]},
]


def seed_meals_for_persona(
    client: ManualClient, email: str
) -> tuple[dict[str, str], dict[str, str]]:
    """POST foods then meals for one persona. Returns (food_ids, meal_ids)
    where keys are records-side `_rec_id` and values are server UUIDs.

    Failures during food POST cause meal POSTs to be skipped silently;
    the run continues so other personas can still seed.
    """
    food_ids: dict[str, str] = {}
    for food in _FOODS:
        body = {k: v for k, v in food.items() if not k.startswith("_")}
        resp = client.post_food_item(email, body)
        if "id" in resp:
            food_ids[food["_rec_id"]] = resp["id"]

    meal_ids: dict[str, str] = {}
    for tmpl in _MEAL_TEMPLATES:
        items: list[dict] = []
        for rec_id, servings in tmpl["items"]:
            server_food = food_ids.get(rec_id)
            if server_food:
                items.append({"food_item_id": server_food, "servings": servings})
        if not items:
            continue
        body = {"name": tmpl["name"], "items": items}
        resp = client.post_meal(email, body)
        if "id" in resp:
            meal_ids[tmpl["_rec_id"]] = resp["id"]

    return food_ids, meal_ids
