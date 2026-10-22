"""Synthetic product-margin model used by the demo workbook.

All names and figures are invented. This module is the single source of the
demo *input data* and also an independent, plain-Python implementation of the
workbook's business logic. Tests compare LibreOffice's recalculation of the
workbook formulas against `compute()`, so a mistake in either implementation
shows up as a mismatch.

Overhead allocation (documented business rule): annual fixed overhead is
allocated to products in proportion to each product's share of total annual
manufacturing hours (volume x manufacturing hours per unit).
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Product:
    name: str
    price: float  # USD per unit
    volume: float  # units per year
    mfg_hours: float  # manufacturing hours per unit
    pkg_hours: float  # packaging hours per unit
    material_cost: float  # USD per unit


@dataclass(frozen=True)
class Inputs:
    manufacturing_hourly_wage: float = 32.0  # USD/hour
    packaging_hourly_wage: float = 22.0  # USD/hour
    payroll_burden_rate: float = 0.20  # fraction of wages
    annual_fixed_overhead: float = 420_000.0  # USD/year


PRODUCTS: tuple[Product, ...] = (
    Product("Oak Bookshelf", 189.0, 4000, 2.0, 0.30, 55.0),
    Product("Walnut Side Table", 234.0, 2500, 3.0, 0.40, 70.0),
    Product("Pine Step Stool", 72.0, 9000, 0.9, 0.15, 14.0),
    Product("Maple Cutting Board", 39.0, 12000, 0.4, 0.10, 9.0),
    Product("Birch Wall Shelf", 96.0, 6000, 1.2, 0.20, 20.0),
    Product("Cedar Planter Box", 89.0, 5000, 1.1, 0.25, 16.0),
)

BASE_INPUTS = Inputs()


def compute(inputs: Inputs = BASE_INPUTS, products: tuple[Product, ...] = PRODUCTS) -> dict:
    """Return per-product and summary outputs for the given inputs."""
    burden = 1.0 + inputs.payroll_burden_rate
    total_mfg_hours = sum(p.volume * p.mfg_hours for p in products)
    rows = []
    for p in products:
        revenue = p.price * p.volume
        mfg_labor = p.volume * p.mfg_hours * inputs.manufacturing_hourly_wage * burden
        pkg_labor = p.volume * p.pkg_hours * inputs.packaging_hourly_wage * burden
        materials = p.volume * p.material_cost
        mfg_hours_total = p.volume * p.mfg_hours
        overhead = inputs.annual_fixed_overhead * mfg_hours_total / total_mfg_hours
        total_cost = mfg_labor + pkg_labor + materials + overhead
        profit = revenue - total_cost
        margin = 0.0 if revenue == 0 else profit / revenue
        rows.append(
            {
                "product": p.name,
                "revenue": revenue,
                "manufacturing_labor": mfg_labor,
                "packaging_labor": pkg_labor,
                "materials": materials,
                "manufacturing_hours": mfg_hours_total,
                "overhead": overhead,
                "total_cost": total_cost,
                "profit": profit,
                "margin": margin,
                "loss_flag": 1 if profit < 0 else 0,
            }
        )
    total_revenue = sum(r["revenue"] for r in rows)
    total_cost = sum(r["total_cost"] for r in rows)
    total_profit = sum(r["profit"] for r in rows)
    summary = {
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "overall_margin": 0.0 if total_revenue == 0 else total_profit / total_revenue,
        "products_losing_money": sum(r["loss_flag"] for r in rows),
    }
    return {"products": rows, "summary": summary}


def scenario_inputs(manufacturing_wage_factor: float) -> Inputs:
    """Inputs with the manufacturing hourly wage scaled by a relative factor."""
    return replace(
        BASE_INPUTS,
        manufacturing_hourly_wage=BASE_INPUTS.manufacturing_hourly_wage * manufacturing_wage_factor,
    )
