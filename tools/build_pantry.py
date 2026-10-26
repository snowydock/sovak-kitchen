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

# Every known ingredient lives in products.json: tier, typical lifespan, the regex
# that recognises it (first hit wins), and the default product we'd buy.
PRODUCTS = json.loads((ROOT / "products.json").read_text())["items"]
for _p in PRODUCTS:
    _p["re"] = re.compile(_p["match"], re.I)


def match(name):
    n = name.lower()
    for p in PRODUCTS:
        if p["re"].search(n):
            return p["key"], p["name"], p["tier"], p.get("lasts_days", 365)
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


def rule(p, item):
    """What the page needs per ingredient: how to recognise it and what to buy.
    Receipts win over the default product, except the pack size of things sold by weight."""
    r = {"key": p["key"], "name": p["name"], "tier": p["tier"], "match": p["match"]}
    if p.get("buy"):
        buy = dict(p["buy"])
        learned = (item or {}).get("bought_as")
        if learned:
            buy["store"], buy["product"] = learned["store"], learned["product"]
            if not buy.get("by_weight"):
                buy["pack"] = learned["pack"]
        r["buy"] = buy
    return r


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
            e["bought_as"] = {"store": r["store"], "product": line["name"],
                              "pack": {u: round(v / line["qty"], 1) for u, v in line["amount"].items()}}
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

    for p in PRODUCTS:
        if p.get("assumed"):
            items.setdefault(p["key"], {"key": p["key"], "name": p["name"], "tier": "long", "assumed": True})

    order = {"staple": 0, "recipe": 1, "long": 2}
    out = {
        "updated": max((json.loads(p.read_text())["date"] for p in receipts), default=None),
        "receipts": [p.name for p in receipts],
        "batches": batches,
        # The page matches recipe ingredients with these, in order (first hit wins).
        "rules": [rule(p, items.get(p["key"])) for p in PRODUCTS],
        "items": sorted(items.values(), key=lambda e: (order[e["tier"]], e["name"].lower())),
    }
    (ROOT / "pantry.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"pantry.json: {len(out['items'])} items from {len(receipts)} receipts")
    if unmatched:
        print("No catalog rule (guessed as staple):", *unmatched, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
