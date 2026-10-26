# Adding an Instacart receipt

When Mike pastes an Instacart order:

1. Write `receipts/YYYY-MM-DD.json` using the delivery date. If a second order lands on the same day, add the store: `YYYY-MM-DD-<store>.json`.
2. Keep item lines only: name, sku (Costco item #), category, qty, final price, and `amount` as `{"g"}`, `{"ml"}` or `{"count"}` for the whole line. **Never store** the driver, order ID, address, card, totals, tip or fees.
3. For a replacement, store what arrived and put the original in `"replaced"`. Refunded or not-found items go in `"not_received"`, not `items`.
4. Run `python3 tools/build_pantry.py`. Anything it reports as unmatched gets an entry in `products.json` (key, tier, lifespan, match regex, default product); then run it again. Once something has been bought, the receipt line becomes the product Groceries suggests, so no hand edits are needed there.
5. Commit the receipt, `pantry.json` and any catalog change together.

Tiers: `recipe` (bought for batches, deducted when a recipe is planned), `staple` (everyday repeat buys), `long` (pantry and freezer, assumed on hand). Repeat purchases teach the cadence on their own, so never ask Mike to confirm anything.
