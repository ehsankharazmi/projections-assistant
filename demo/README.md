# Synthetic demo workbook

All products, prices, volumes, and costs are **invented**. Nothing here is real business data.

- **Input data and the independent model:** `backend/app/demo/model.py`. Plain Python, no spreadsheet engine.
- **Generator:** `backend/app/demo/generate.py`. It writes three files:
  - `demo_product_margins.xlsx`: the sheets `Inputs`, `Products` and `Summary`, with real formulas. Its stored formula results are injected the way Excel would save them, so the compatibility gate can compare against them.
  - `demo_reference_outputs.json`: trusted expected outputs (baseline, +5% and +8% manufacturing wage), computed by the Python model independently of the formulas.
  - `demo_map.json`: the demo workbook map. It is labelled `source: demo` and ships confirmed.

Regenerate with:

```bash
uv run python -m app.demo.generate demo/generated
```

The app generates the files into its data directory automatically the first time you choose **Load synthetic demo workbook**.

**Business rule (overhead allocation):** annual fixed overhead is allocated to products in proportion to their share of total manufacturing hours (volume × manufacturing hours per unit).

**Designed outcome:** at baseline every product is profitable. With manufacturing wages +5%, Walnut Side Table stays slightly profitable; with +8% it loses money.
