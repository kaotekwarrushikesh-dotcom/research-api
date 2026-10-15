"""Turn a CompanyModel into plain JSON-safe values.

The single trap this module exists for: NaN. Pandas produces it constantly for ratios whose
denominator is missing, Python happily carries it, and `json.dumps` emits a bare `NaN` token
that is not valid JSON. Browsers reject it, and the failure surfaces far from its cause. So
every float crosses through `clean()` and becomes `null` instead, which a frontend can
actually branch on.

Nothing here computes a financial figure. Every value is read from the model that Modules 1,
2 and 3 already produced.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app import schemas


def clean(value: Any) -> float | None:
    """Any numeric to a JSON-safe float, or None. NaN and infinity both become None."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(out) or math.isinf(out)) else out


def _latest(model) -> pd.Series:
    return model.fundamentals.ratios.iloc[-1]


def identity(model) -> schemas.Identity:
    f = model.fundamentals
    return schemas.Identity(
        ticker=model.ticker, name=model.name, sector=model.sector, currency=model.currency,
        source=f.source, years_of_history=int(f.years),
        first_year=int(f.first_year), last_year=int(f.last_year),
    )


def market(model) -> schemas.Market:
    return schemas.Market(
        share_price=clean(model.share_price),
        market_cap=clean(model.market_cap),
        net_debt=clean(model.net_debt),
        enterprise_value=clean(model.market_enterprise_value),
    )


def fundamentals(model) -> schemas.Fundamentals:
    latest = _latest(model)
    ratios = model.fundamentals.ratios
    growth = None
    if len(ratios) > 1 and ratios["revenue"].iloc[-2]:
        growth = clean(ratios["revenue"].iloc[-1] / ratios["revenue"].iloc[-2] - 1.0)
    return schemas.Fundamentals(
        revenue=clean(latest.get("revenue")), ebitda=clean(latest.get("ebitda")),
        ebitda_margin=clean(latest.get("ebitda_margin")),
        net_margin=clean(latest.get("net_margin")),
        free_cash_flow=clean(latest.get("fcf")), roe=clean(latest.get("roe")),
        roic=clean(model.roic), net_debt_to_ebitda=clean(model.net_debt_to_ebitda),
        revenue_growth=growth,
    )


def health(model) -> schemas.Health:
    h = model.health
    from fsi.financial_health import PILLARS

    return schemas.Health(
        overall=clean(h["overall"]) or 0.0, rating=h["rating"],
        pillars=[schemas.HealthPillar(name=p.name, weight=p.weight,
                                      score=clean(h["pillars"].get(p.name)))
                 for p in PILLARS],
        unavailable_metrics=sorted(set(h.get("unavailable_metrics") or [])),
    )


def valuation(model, dcf=None) -> schemas.Valuation:
    """`dcf` lets a what-if result be rendered through the same shape as the base case, so the
    frontend has one valuation type rather than two that drift apart."""
    v = model.valuation
    result = dcf if dcf is not None else model.dcf
    assumptions = v.get("assumptions")
    wacc_obj = v.get("wacc")

    if result is None:
        return schemas.Valuation(
            dcf_implied_price=None, upside=None,
            wacc=clean(getattr(wacc_obj, "wacc", None)),
            terminal_growth=clean(getattr(assumptions, "terminal_growth", None)),
            enterprise_value=None, equity_value=None,
            trading_ev_ebitda=clean(model.trading_ev_ebitda), dcf_ev_ebitda=None,
            solved=False, error=v.get("dcf_error") or "The DCF did not solve.",
        )

    price = clean(result.implied_share_price_floored)
    upside = clean(price / model.share_price - 1.0) if price and model.share_price else None
    ebitda = model.ebitda
    return schemas.Valuation(
        dcf_implied_price=price, upside=upside,
        wacc=clean(getattr(result, "wacc", None)),
        terminal_growth=clean(getattr(result, "terminal_growth", None)),
        enterprise_value=clean(result.enterprise_value),
        equity_value=clean(result.equity_value),
        trading_ev_ebitda=clean(model.trading_ev_ebitda),
        dcf_ev_ebitda=clean(result.enterprise_value / ebitda) if ebitda else None,
        solved=True,
        warning="This engine's DCF reads systematically below market across its tested "
                "universe, a documented limitation of its terminal value rather than a data "
                "bug. Do not present it on its own.",
    )


def risk(model) -> schemas.Risk:
    mr = model.market_risk
    composite = model.composite_risk
    return schemas.Risk(
        volatility_annualised=clean(mr.volatility.annualised),
        beta=clean(getattr(mr.beta, "beta_regression", None)) if mr.beta else None,
        max_drawdown=clean(mr.drawdown.max_drawdown),
        var_99_1day=clean(mr.var_historical.var_return),
        expected_shortfall_99=clean(mr.expected_shortfall.es_return),
        composite_score=clean(getattr(composite, "overall", None)),
        composite_rating=getattr(composite, "rating", None),
        regime=mr.regime,
    )


def summary(model) -> schemas.CompanySummary:
    return schemas.CompanySummary(
        identity=identity(model), market=market(model), fundamentals=fundamentals(model),
        health=health(model), valuation=valuation(model), risk=risk(model),
        notes=list(model.notes or []),
    )


RATIO_FIELDS = [
    "revenue", "ebitda", "ebit", "net_income", "gross_margin", "ebitda_margin", "net_margin",
    "roe", "roa", "asset_turnover", "debt_to_equity", "interest_coverage", "current_ratio",
    "fcf", "fcf_margin",
]


def ratios(model) -> list[schemas.RatioYear]:
    out = []
    for _, row in model.fundamentals.ratios.iterrows():
        out.append(schemas.RatioYear(
            fiscal_year=int(row["fiscal_year"]),
            **{f: clean(row.get(f)) for f in RATIO_FIELDS},
        ))
    return out
