"""Typed response shapes.

These exist for two reasons beyond tidiness. First, FastAPI turns them into an OpenAPI
schema, which is what lets a frontend tool generate a correctly typed client instead of
guessing at field names. Second, they force every value crossing the wire to be a plain JSON
type: the model underneath holds pandas Series, numpy scalars and NaN, none of which
serialise, and NaN in particular becomes invalid JSON that fails silently in a browser.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Identity(BaseModel):
    ticker: str
    name: str
    sector: str | None = Field(None, description="None when the provider does not classify it")
    currency: str
    source: str = Field(description="Where the filings came from, e.g. SEC EDGAR")
    years_of_history: int
    first_year: int
    last_year: int


class Market(BaseModel):
    share_price: float | None
    market_cap: float | None = Field(None, description="Whole currency units, not millions")
    net_debt: float | None = Field(None, description="Millions, like all statement figures")
    enterprise_value: float | None = Field(None, description="Millions")


class Fundamentals(BaseModel):
    revenue: float | None
    ebitda: float | None
    ebitda_margin: float | None
    net_margin: float | None
    free_cash_flow: float | None
    roe: float | None
    roic: float | None
    net_debt_to_ebitda: float | None
    revenue_growth: float | None = Field(None, description="Latest filed year over the prior one")


class HealthPillar(BaseModel):
    name: str
    weight: float
    score: float | None = Field(None, description="None when no metric in it could be computed")


class Health(BaseModel):
    overall: float
    rating: str = Field(description="Strong | Healthy | Adequate | Weak | Distressed")
    pillars: list[HealthPillar]
    unavailable_metrics: list[str] = Field(
        default_factory=list,
        description="Dropped because the company does not report them; their weight was "
                    "redistributed inside the pillar rather than scored as zero",
    )


class Valuation(BaseModel):
    dcf_implied_price: float | None
    upside: float | None = Field(None, description="Signed fraction against the market price")
    wacc: float | None
    terminal_growth: float | None
    enterprise_value: float | None
    equity_value: float | None
    trading_ev_ebitda: float | None
    dcf_ev_ebitda: float | None
    solved: bool
    error: str | None = None
    warning: str | None = Field(
        None, description="The engine's own calibration caveat; never quote the DCF alone")


class Risk(BaseModel):
    volatility_annualised: float | None
    beta: float | None
    max_drawdown: float | None
    var_99_1day: float | None
    expected_shortfall_99: float | None
    composite_score: float | None
    composite_rating: str | None
    regime: str | None


class CompanySummary(BaseModel):
    identity: Identity
    market: Market
    fundamentals: Fundamentals
    health: Health
    valuation: Valuation
    risk: Risk
    notes: list[str] = Field(default_factory=list)


class RatioYear(BaseModel):
    fiscal_year: int
    revenue: float | None
    ebitda: float | None
    ebit: float | None
    net_income: float | None
    gross_margin: float | None
    ebitda_margin: float | None
    net_margin: float | None
    roe: float | None
    roa: float | None
    asset_turnover: float | None
    debt_to_equity: float | None
    interest_coverage: float | None
    current_ratio: float | None
    fcf: float | None
    fcf_margin: float | None


class PricePoint(BaseModel):
    date: str
    close: float


class PriceHistory(BaseModel):
    ticker: str
    currency: str
    period: str
    points: list[PricePoint]
    low: float
    high: float
    latest: float
    total_return: float | None
    notes: list[str] = Field(default_factory=list)


class RevalueRequest(BaseModel):
    revenue_growth_delta: float = Field(0.0, ge=-0.5, le=0.5,
                                        description="Shift every forecast year, e.g. 0.02 = +2pp")
    ebitda_margin_delta: float = Field(0.0, ge=-0.5, le=0.5)
    wacc: float | None = Field(None, gt=0.0, lt=0.5, description="Absolute override, e.g. 0.09")
    terminal_growth: float | None = Field(None, gt=-0.1, lt=0.1)
    tax_rate: float | None = Field(None, ge=0.0, lt=1.0)


class RevalueResponse(BaseModel):
    base: Valuation
    revalued: Valuation
    price_change: float | None = Field(
        None, description="Fractional change in implied price against the base case")


class ErrorResponse(BaseModel):
    detail: str
