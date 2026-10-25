#!/usr/bin/env python3
"""Rebuild pantry.json from receipts/*.json and the fitness log.

    python3 tools/build_pantry.py

Each receipt line is matched to a catalog entry below (first regex that hits).
The catalog decides the tier and how long an item typically lasts; repeat
purchases override that with the learned interval between orders.
Batches: a recipe from recipes.json showing up in Mike's fitness log after a
gap of more than a week counts as one cooked batch on its first logged day.
Its ingredients are deducted from recipe and long-lived stock bought on or
before that day. (Staples are tracked by cadence, not deduction.)

Unmatched lines are printed so a catalog rule can be added; they still land
in pantry.json as a guessed staple so nothing is lost.

Tiers
  recipe  - bought for batch cooking; Plan deducts it by batch
  staple  - everyday repeat buys; pre-checked on the list when due
  long    - long-lived pantry items; assumed on hand unless bought again
"""
import json, re, statistics, sys, urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_URL = "https://raw.githubusercontent.com/snowydock/sovak-fitness/main/log/{}.json"
BATCH_GAP_DAYS = 7
USED_UP_SHARE = 0.15   # under this share of the last purchase left = used up

# key, display name, tier, typical days it lasts, name regex
CATALOG = [
    # --- staples ---
    ("sparkling-water", "Sparkling water", "staple", 17, r"sparkling water"),
    ("cottage-cheese", "Cottage cheese", "staple", 14, r"cottage cheese"),
    ("chobani-drinks", "Chobani protein drinks", "staple", 14, r"chobani.*protein drink"),
    ("nurri-shakes", "Nurri protein shakes", "staple", 15, r"nurri"),
    ("egg-whites", "Liquid egg whites", "staple", 21, r"egg whites"),
    ("eggs", "Eggs", "staple", 14, r"\beggs?\b.*(large|count|ct)|large eggs"),
    ("oat-milk", "Oat milk", "staple", 21, r"oat (beverage|milk)"),
    ("blueberries", "Frozen blueberries", "staple", 35, r"blueberr"),
    ("salsa", "Salsa", "staple", 21, r"salsa"),
    ("beef-sticks", "Beef sticks", "staple", 14, r"beef sticks?"),
    ("clif-bars", "Clif bars", "staple", 14, r"clif bar"),
    ("rice-cake-bites", "Rice cake bites", "staple", 10, r"rice cake"),
    ("cool-whip", "Cool Whip", "staple", 14, r"cool whip|whipped topping"),
    ("green-beans", "Frozen green beans", "staple", 60, r"green beans"),
    # --- recipe ingredients ---
    ("ground-beef", "Ground beef", "recipe", 5, r"ground beef"),
    ("ground-turkey", "Ground turkey", "recipe", 5, r"ground turkey"),
    ("chicken-thighs", "Chicken thighs", "recipe", 5, r"chicken thigh"),
    ("rotisserie-chicken", "Rotisserie chicken", "recipe", 4, r"rotisserie"),
    ("greek-yogurt", "Greek yogurt (tub)", "recipe", 14, r"greek yogurt(?!.*drink)"),
    ("tortillas", "Tortillas", "recipe", 21, r"tortilla|wraps"),
    ("chicken-broth", "Chicken broth", "recipe", 365, r"chicken broth"),
    ("egg-noodles", "Egg noodles", "recipe", 365, r"egg noodles"),
    ("cabbage", "Cabbage", "recipe", 14, r"cabbage"),
    ("cucumbers", "Cucumbers", "recipe", 7, r"cucumber"),
    ("oranges", "Oranges", "recipe", 10, r"\borange\b(?!.*pepper)"),
    ("carrots", "Carrots", "recipe", 21, r"carrot"),
    ("bell-pepper", "Bell pepper", "recipe", 7, r"bell pepper"),
    ("onions", "Onions", "recipe", 30, r"^onion|yellow onion|white onion|red onion"),
    ("green-onions", "Green onions", "recipe", 7, r"green onion|scallion"),
    ("dill", "Fresh dill", "recipe", 7, r"^(fresh )?dill\b(?! pickle)"),
    ("peas", "Frozen peas", "recipe", 90, r"green peas|\bpeas\b"),
    ("pickles", "Dill pickles", "recipe", 60, r"pickle"),
    ("milk", "Milk", "recipe", 7, r"whole milk|\bmilk\b(?!.*oat)"),
    # --- long-lived ---
    ("chicken-breast-frozen", "Frozen chicken breast", "long", 90, r"chicken breast"),
    ("bibigo-rice", "Bibigo rice bowls", "long", 120, r"bibigo"),
    ("coffee", "Coffee beans", "long", 45, r"coffee"),
    ("soy-sauce", "Soy sauce", "long", 180, r"soy sauce"),
    ("sweet-chili", "Sweet chili sauce", "long", 120, r"sweet chil+i"),
    ("tomato-paste", "Tomato paste", "long", 60, r"tomato paste"),
    ("honey", "Honey", "long", 365, r"honey"),
    ("chocolate-chips", "Chocolate chips", "long", 180, r"chocolate.*chips|baking chips"),
    ("ginger-paste", "Ginger paste", "long", 60, r"ginger.*paste"),
]

# Long-lived items assumed on hand without a receipt (spice rack, oils, sauces).
ASSUMED = [
    ("salt", "Salt"), ("black-pepper", "Black pepper"), ("garlic-powder", "Garlic powder"),
    ("onion-powder", "Onion powder"), ("smoked-paprika", "Smoked paprika"), ("cumin", "Cumin"),
    ("oregano", "Dried oregano"), ("chili-powder", "Chili powder"), ("taco-seasoning", "Taco seasoning"),
    ("red-pepper-flakes", "Red pepper flakes"), ("cinnamon", "Cinnamon"),
    ("cooking-spray", "Cooking spray"), ("olive-oil", "Olive oil"), ("sesame-oil", "Sesame oil"),
    ("rice-vinegar", "Rice vinegar"), ("sriracha", "Sriracha"), ("gochujang", "Gochujang"),
    ("minced-garlic", "Minced garlic"), ("worcestershire", "Worcestershire sauce"),
]


def match(name):
    n = name.lower()
    for key, disp, tier, lasts, rx in CATALOG:
        if re.search(rx, n):
            return key, disp, tier, lasts
    return None


QTY = re.compile(r"^\s*([\d.]+)\s*(kg|g|ml|l)?\b\s*", re.I)


def parse_ingredient(line):
    """'1100g 85/15 ground beef' -> ('85/15 ground beef', {'g': 1100})"""
    m = QTY.match(line)
    if not m:
        return line, None
    n, unit = float(m.group(1)), (m.group(2) or "").lower()
    rest = line[m.end():]
    amt = {"g": n} if unit == "g" else {"g": n * 1000} if unit == "kg" else \
          {"ml": n} if unit == "ml" else {"ml": n * 1000} if unit == "l" else {"count": n}
    return rest, amt


def fetch_log_months(start, end):
    days, d = {}, start.replace(day=1)
    while d <= end:
        try:
            with urllib.request.urlopen(LOG_URL.format(d.strftime("%Y-%m")), timeout=20) as r:
                days.update(json.load(r).get("days", {}))
        except Exception as e:
            print(f"fitness log {d:%Y-%m}: skipped ({e.__class__.__name__})", file=sys.stderr)
        d = (d + timedelta(days=32)).replace(day=1)
    return days


def find_batches(recipes, start):
    by_name = {r["name"].lower(): r for r in recipes}
    log = fetch_log_months(start, date.today())
    batches, last_seen = [], {}
    for day in sorted(log):
        if day < start.isoformat():
            continue
        for it in ((log[day] or {}).get("food") or {}).get("items", []):
            name = (it.get("name") if isinstance(it, dict) else it) or ""
            r = by_name.get(name.lower())
            if not r:
                continue
            prev = last_seen.get(r["id"])
            if prev is None or (date.fromisoformat(day) - prev).days > BATCH_GAP_DAYS:
                batches.append({"recipe": r["name"], "recipe_id": r["id"], "cooked": day})
            last_seen[r["id"]] = date.fromisoformat(day)
    return batches


def remaining_after(e, batches, recipes_by_id):
    """Left from the most recent purchase after deducting batches cooked since it."""
    last = e["purchases"][-1]
    left = dict(last["amount"])
    for b in batches:
        if b["cooked"] < last["date"]:
            continue
        for line in recipes_by_id[b["recipe_id"]]["ingredients"]:
            if not isinstance(line, str):
                continue
            rest, amt = parse_ingredient(line)
            m = match(rest)
            if not m or m[0] != e["key"]:
                continue
            unit = next(iter(amt)) if amt else None
            alias = {"g": "ml", "ml": "g"}  # liquids: 1 g ~ 1 ml
            target = unit if unit in left else alias.get(unit) if alias.get(unit) in left else None
            if target is None:          # can't compare units: the batch used it
                left = {k: 0 for k in left}
            else:
                left[target] = max(0, left[target] - amt[unit])
            e.setdefault("used_in", []).append(b["recipe"])
    used_up = any(left[k] < USED_UP_SHARE * last["amount"][k] for k in left)
    return {k: round(v) for k, v in left.items()}, used_up


def add_amount(total, amt):
    for unit, v in amt.items():
        total[unit] = total.get(unit, 0) + v


def main():
    receipts = sorted((ROOT / "receipts").glob("*.json"))
    items, unmatched = {}, []
    for path in receipts:
        r = json.loads(path.read_text())
        for line in r["items"]:
            m = match(line["name"])
            if not m:
                unmatched.append(f'{path.name}: {line["name"]}')
                key = re.sub(r"[^a-z0-9]+", "-", line["name"].lower()).strip("-")[:40]
                m = (key, line["name"], "staple", 14)
            key, disp, tier, lasts = m
            e = items.setdefault(key, {"key": key, "name": disp, "tier": tier,
                                       "lasts_days": lasts, "purchases": []})
            # two lines on the same day (e.g. two stores) merge into one purchase
            same = [p for p in e["purchases"] if p["date"] == r["date"]]
            if same:
                add_amount(same[0]["amount"], line["amount"])
            else:
                e["purchases"].append({"date": r["date"], "store": r["store"],
                                       "amount": dict(line["amount"])})

    for e in items.values():
        e["purchases"].sort(key=lambda p: p["date"])
        dates = [date.fromisoformat(p["date"]) for p in e["purchases"]]
        e["last_bought"] = dates[-1].isoformat()
        e["times_bought"] = len(dates)
        if len(dates) >= 2:
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            e["cadence_days"] = round(statistics.median(gaps))

    recipes = json.loads((ROOT / "recipes.json").read_text())["recipes"]
    recipes_by_id = {r["id"]: r for r in recipes}
    first = min((date.fromisoformat(p["date"]) for e in items.values() for p in e["purchases"]), default=date.today())
    batches = find_batches(recipes, first)
    for e in items.values():
        if e["tier"] in ("recipe", "long"):
            left, used_up = remaining_after(e, batches, recipes_by_id)
            e["on_hand"] = left
            if used_up:
                e["used_up"] = True
            if "used_in" in e:
                e["used_in"] = sorted(set(e["used_in"]))

    for key, disp in ASSUMED:
        items.setdefault(key, {"key": key, "name": disp, "tier": "long", "assumed": True})

    order = {"staple": 0, "recipe": 1, "long": 2}
    out = {
        "updated": max((json.loads(p.read_text())["date"] for p in receipts), default=None),
        "receipts": [p.name for p in receipts],
        "batches": batches,
        "items": sorted(items.values(), key=lambda e: (order[e["tier"]], e["name"].lower())),
    }
    (ROOT / "pantry.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"pantry.json: {len(out['items'])} items from {len(receipts)} receipts")
    if unmatched:
        print("No catalog rule (guessed as staple):", *unmatched, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
