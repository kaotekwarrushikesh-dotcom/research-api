"""Tests for the analysis API.

The offline tests pin the behaviour that has nothing to do with finance: caching, error
translation, validation bounds, and above all that nothing NaN-shaped ever reaches the wire.
The integration tests run a real company through every endpoint, because an API that has only
been tested against a fake model proves the fake works.
"""

import json
import math

import pytest
from fastapi.testclient import TestClient

from app import convert
from app.main import app, _cache

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


# --- the NaN problem, which is the whole reason convert.py exists -------------------------

@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), None, "abc"])
def test_values_that_cannot_be_json_become_null(value):
    """json.dumps emits a bare NaN token for NaN, which is not valid JSON and fails in the
    browser rather than here. Every one of these has to come back as None."""
    assert convert.clean(value) is None


@pytest.mark.parametrize("value,expected", [(1, 1.0), (0, 0.0), (-2.5, -2.5), ("3.5", 3.5)])
def test_real_numbers_survive_unchanged(value, expected):
    assert convert.clean(value) == expected


# --- service behaviour --------------------------------------------------------------------

def test_health_check_does_no_analysis():
    """A liveness probe that fetched filings would cost money every time the platform pinged
    it, and would fail whenever an upstream source was down."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_an_unresolvable_company_is_a_404_not_a_500(monkeypatch):
    import research_platform.model as rpm
    monkeypatch.setattr(rpm, "build", lambda q: (_ for _ in ()).throw(ValueError("No listed company found for 'X'")))
    r = client.get("/api/company/NOTREAL")
    assert r.status_code == 404
    assert "No listed company found" in r.json()["detail"]


def test_an_upstream_failure_does_not_leak_a_traceback(monkeypatch):
    """A stack trace tells an attacker about internals and tells a user nothing useful."""
    import research_platform.model as rpm

    def boom(q):
        raise RuntimeError("yfinance exploded at line 412 in /usr/lib/site-packages/...")

    monkeypatch.setattr(rpm, "build", boom)
    r = client.get("/api/company/AAPL")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "yfinance" not in detail and "site-packages" not in detail
    assert "try again" in detail.lower()


def test_an_analysis_is_only_run_once_per_company(monkeypatch):
    """Cold analysis takes roughly 24 seconds. Without this the API is unusable."""
    import research_platform.model as rpm
    calls = {"n": 0}

    def counting_build(q):
        calls["n"] += 1
        raise ValueError("stop here, the call count is what matters")

    monkeypatch.setattr(rpm, "build", counting_build)
    client.get("/api/company/AAPL")
    assert calls["n"] == 1


def test_a_failure_is_not_cached(monkeypatch):
    """Caching an error would turn a momentary upstream blip into fifteen minutes of outage."""
    import research_platform.model as rpm
    calls = {"n": 0}

    def flaky(q):
        calls["n"] += 1
        raise ValueError("transient")

    monkeypatch.setattr(rpm, "build", flaky)
    client.get("/api/company/AAPL")
    client.get("/api/company/AAPL")
    assert calls["n"] == 2


def test_ticker_case_and_whitespace_share_one_cache_entry(monkeypatch):
    """Exercised through get_model rather than HTTP: the cache only fills on a SUCCESSFUL
    build, so a fake that raises (as an earlier version of this test used) can never populate
    it and would always look like a cache miss."""
    from app.main import get_model
    import research_platform.model as rpm
    calls = {"n": 0}

    def counting(q):
        calls["n"] += 1
        return object()

    monkeypatch.setattr(rpm, "build", counting)
    for spelling in ("AAPL", "aapl", "  AAPL  "):
        get_model(spelling)
    assert calls["n"] == 1


# --- input validation ----------------------------------------------------------------------

def test_an_unsupported_price_period_is_rejected():
    assert client.get("/api/company/AAPL/prices?period=99y").status_code == 422


def test_absurd_override_values_are_rejected_before_reaching_the_engine():
    """A terminal growth above the discount rate has no finite perpetuity. Catching it in the
    schema gives a clear message instead of an engine exception."""
    assert client.post("/api/company/AAPL/revalue", json={"terminal_growth": 0.5}).status_code == 422
    assert client.post("/api/company/AAPL/revalue", json={"wacc": 1.5}).status_code == 422
    assert client.post("/api/company/AAPL/revalue",
                       json={"revenue_growth_delta": 5.0}).status_code == 422


# --- the real thing --------------------------------------------------------------------------

def _assert_json_is_clean(payload):
    """No NaN or Infinity anywhere, at any depth. This is the invariant a browser depends on."""
    text = json.dumps(payload, allow_nan=False)   # raises if any NaN slipped through
    assert "NaN" not in text and "Infinity" not in text


@pytest.mark.integration
def test_a_real_company_returns_a_complete_clean_summary():
    r = client.get("/api/company/AAPL")
    assert r.status_code == 200
    d = r.json()
    _assert_json_is_clean(d)

    assert d["identity"]["ticker"] == "AAPL"
    assert d["identity"]["years_of_history"] >= 5
    assert 0 <= d["health"]["overall"] <= 100
    assert d["health"]["rating"] in {"Strong", "Healthy", "Adequate", "Weak", "Distressed"}
    assert len(d["health"]["pillars"]) == 5
    assert abs(sum(p["weight"] for p in d["health"]["pillars"]) - 1.0) < 1e-9
    assert d["market"]["share_price"] > 0


@pytest.mark.integration
def test_every_endpoint_returns_json_a_browser_can_parse():
    for path in ["/api/company/AAPL", "/api/company/AAPL/valuation",
                 "/api/company/AAPL/risk", "/api/company/AAPL/ratios",
                 "/api/company/AAPL/prices?period=1y"]:
        r = client.get(path)
        assert r.status_code == 200, path
        _assert_json_is_clean(r.json())


@pytest.mark.integration
def test_ratio_history_is_one_row_per_year_in_order():
    rows = client.get("/api/company/AAPL/ratios").json()
    years = [r["fiscal_year"] for r in rows]
    assert years == sorted(years)
    assert len(set(years)) == len(years)


@pytest.mark.integration
def test_a_lower_discount_rate_raises_the_valuation():
    """The direction is the point. A cheaper cost of capital discounts the same cash flows
    less heavily, so the implied price must rise."""
    base = client.get("/api/company/AAPL/valuation").json()
    assert base["solved"]
    r = client.post("/api/company/AAPL/revalue", json={"wacc": base["wacc"] - 0.02})
    assert r.status_code == 200
    d = r.json()
    assert d["revalued"]["dcf_implied_price"] > d["base"]["dcf_implied_price"]
    assert d["price_change"] > 0


@pytest.mark.integration
def test_the_dcf_never_travels_without_its_calibration_warning():
    """The engine's own rule: this DCF reads below market across its tested universe, so it
    must never be presented alone. An API consumer is the most likely party to quote one
    number in isolation."""
    v = client.get("/api/company/AAPL/valuation").json()
    assert v["warning"] and "on its own" in v["warning"]
