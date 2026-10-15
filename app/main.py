"""Analysis API over Modules 1, 2 and 3.

A deliberately thin layer. `research_platform.model.build()` already composes the three
engines into one object, so this service resolves a company, caches the result, and serialises
it. No financial logic lives here, which is what keeps the 5,185 lines of engine tests
meaningful: if a number is wrong, the fix belongs in the engine that computed it.

Errors never reach the client as a stack trace. The caller gets a sentence it can show a user
and the detail goes to the log, because an API that leaks tracebacks leaks its internals too.
"""

from __future__ import annotations

import logging
import os
import time
from threading import Lock

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware

from app import convert, schemas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("research-api")

# A full analysis makes five network round trips and takes roughly 24 seconds cold, so this is
# the difference between a usable API and an unusable one. Fifteen minutes matches the TTL the
# Streamlit apps already use, so a figure does not have one staleness there and another here.
CACHE_TTL_S = int(os.environ.get("CACHE_TTL_S", "900"))

_cache: dict[str, tuple[object, float]] = {}
_lock = Lock()

app = FastAPI(
    title="Institutional Research API",
    version="0.1.0",
    description=(
        "Financial statement analysis, valuation and risk for any listed company, served from "
        "the same engines behind the Streamlit apps. Every figure is computed by Module 1 "
        "(filings and health scoring), Module 2 (DCF and WACC) or Module 3 (market risk)."
    ),
)

# The frontend is served from a different origin (Cloudflare Pages), so it cannot call this
# without CORS. Origins come from the environment rather than a wildcard, because a public
# wildcard on a service that costs money per request is an invitation.
origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_model(query: str):
    """One cached analysis per company. Failures are deliberately not cached: a transient
    network error held for fifteen minutes turns a blip into an outage."""
    key = query.strip().upper()
    now = time.monotonic()

    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    from research_platform.model import build

    started = time.perf_counter()
    try:
        model = build(query)
    except ValueError as exc:
        # Module 1 raises this for an unresolvable company, which is a client error.
        log.info("unresolved query %r: %s", query, exc)
        raise HTTPException(status_code=404, detail=f"No listed company found for '{query}'.")
    except Exception as exc:  # noqa: BLE001
        log.exception("analysis failed for %r", query)
        raise HTTPException(
            status_code=502,
            detail="The analysis could not be completed. The upstream financial data source "
                   "may be unavailable. Please try again shortly.",
        ) from exc

    log.info("analysed %s in %.1fs", key, time.perf_counter() - started)
    with _lock:
        _cache[key] = (model, time.monotonic() + CACHE_TTL_S)
    return model


QueryPath = Path(..., min_length=1, max_length=64,
                 description="Company name or ticker, e.g. 'AAPL' or 'Apple'")


@app.get("/health", tags=["service"], summary="Liveness probe")
def service_health():
    """Deliberately does no analysis, so a health check never costs a data fetch."""
    return {"status": "ok", "cached_companies": len(_cache)}


@app.get("/api/company/{query}", response_model=schemas.CompanySummary, tags=["company"],
         responses={404: {"model": schemas.ErrorResponse}, 502: {"model": schemas.ErrorResponse}})
def company_summary(query: str = QueryPath):
    """Everything needed for an overview screen in one call: identity, market data,
    fundamentals, the health score with its pillar breakdown, valuation and risk."""
    return convert.summary(get_model(query))


@app.get("/api/company/{query}/valuation", response_model=schemas.Valuation, tags=["company"])
def company_valuation(query: str = QueryPath):
    return convert.valuation(get_model(query))


@app.get("/api/company/{query}/risk", response_model=schemas.Risk, tags=["company"])
def company_risk(query: str = QueryPath):
    return convert.risk(get_model(query))


@app.get("/api/company/{query}/ratios", response_model=list[schemas.RatioYear], tags=["company"],
         summary="Full ratio history, one row per fiscal year")
def company_ratios(query: str = QueryPath):
    return convert.ratios(get_model(query))


@app.get("/api/company/{query}/prices", response_model=schemas.PriceHistory, tags=["company"])
def company_prices(
    query: str = QueryPath,
    period: str = Query("5y", pattern="^(1y|3y|5y|10y)$"),
):
    """Daily closes, already converted out of minor units: a London listing quotes in pence,
    and charting that unconverted overstates the price a hundredfold."""
    from fsi.providers.yahoo import price_history

    model = get_model(query)
    try:
        closes, currency, notes = price_history(model.ticker, period)
    except Exception as exc:  # noqa: BLE001
        log.warning("price history failed for %s: %s", model.ticker, exc)
        raise HTTPException(status_code=502,
                            detail="Price history is unavailable for this listing.") from exc

    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    return schemas.PriceHistory(
        ticker=model.ticker, currency=currency or model.currency, period=period,
        points=[schemas.PricePoint(date=d.strftime("%Y-%m-%d"), close=float(c))
                for d, c in closes.items()],
        low=float(closes.min()), high=float(closes.max()), latest=last,
        total_return=convert.clean(last / first - 1.0) if first else None,
        notes=notes,
    )


@app.post("/api/company/{query}/revalue", response_model=schemas.RevalueResponse, tags=["company"],
          summary="What-if: re-run the DCF with overridden assumptions")
def company_revalue(body: schemas.RevalueRequest, query: str = QueryPath):
    """Re-runs Module 2's own forecast and DCF functions with changed inputs rather than
    re-deriving the arithmetic, so a what-if and the base case can never disagree about
    method."""
    from research_platform.revalue import Overrides, revalue

    model = get_model(query)
    overrides = Overrides(
        revenue_growth_delta=body.revenue_growth_delta,
        ebitda_margin_delta=body.ebitda_margin_delta,
        wacc=body.wacc, terminal_growth=body.terminal_growth, tax_rate=body.tax_rate,
    )

    base = convert.valuation(model)
    try:
        new_dcf = revalue(model, overrides)
    except ValueError as exc:
        # The usual cause is a terminal growth at or above the discount rate, which has no
        # finite perpetuity. That is a bad input, not a server fault.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    revalued = convert.valuation(model, dcf=new_dcf)
    change = None
    if base.dcf_implied_price and revalued.dcf_implied_price:
        change = convert.clean(revalued.dcf_implied_price / base.dcf_implied_price - 1.0)
    return schemas.RevalueResponse(base=base, revalued=revalued, price_change=change)
