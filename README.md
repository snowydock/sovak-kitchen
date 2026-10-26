# sovak-kitchen

The Sovak Kitchen, the meal-prep cookbook Mike and Ali share. Live at https://snowydock.github.io/sovak-kitchen/

- `recipes.json`: recipes and sauces
- `receipts/`: Instacart orders, item lines only
- `products.json`: every ingredient, how to recognise it in a recipe, and the product/pack we'd buy
- `pantry.json`: built by `python3 tools/build_pantry.py` from the receipts and cooked batches in the fitness log
- `prompts/receipt.md`: how to add a receipt
