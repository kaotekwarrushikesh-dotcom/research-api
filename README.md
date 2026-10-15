# Institutional Research API

HTTP access to the financial statement, valuation and risk engines. Built so a React frontend
(Cloudflare Pages, Lovable, anything) has something real to call.

**This service contains no financial logic.** Every number is computed by Module 1 (filings,
ratios, health score), Module 2 (DCF, WACC) or Module 3 (market risk), which are installed as
dependencies and left untouched. If a figure looks wrong, the fix belongs in the engine that
produced it, where its tests already live.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | Liveness. Does no analysis, so a probe never costs a fetch |
| GET | `/api/company/{query}` | Everything for an overview: identity, market, fundamentals, health, valuation, risk |
| GET | `/api/company/{query}/valuation` | DCF, WACC, upside, multiples |
| GET | `/api/company/{query}/risk` | Volatility, beta, drawdown, VaR, expected shortfall, composite |
| GET | `/api/company/{query}/ratios` | Full history, one row per fiscal year |
| GET | `/api/company/{query}/prices?period=1y\|3y\|5y\|10y` | Daily closes, minor units already converted |
| POST | `/api/company/{query}/revalue` | What-if: override growth, margin, WACC, terminal growth, tax |

`query` accepts a ticker or a company name: `AAPL` and `Apple` both work.

Interactive docs at `/docs`, OpenAPI schema at `/openapi.json`. Point a frontend generator at
that schema rather than hand-writing types.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload
```

```bash
.venv/bin/python -m pytest tests/ -q -m "not integration"   # offline
.venv/bin/python -m pytest tests/ -q                        # includes live calls
```

## Two decisions worth knowing

**Caching is not optional.** A cold analysis makes five network round trips and takes roughly
17 to 24 seconds. Results are cached in process for 900 seconds, matching the TTL the
Streamlit apps already use so a figure does not have one staleness there and another here.
Failures are never cached: holding a transient network error for fifteen minutes would turn a
blip into an outage.

**Every float passes through `convert.clean()`.** Pandas produces NaN constantly for ratios
with a missing denominator, and `json.dumps` emits a bare `NaN` token that is not valid JSON.
It fails in the browser, far from the cause. NaN and infinity both become `null`, which a
frontend can branch on. A test asserts with `allow_nan=False` that no endpoint can regress on
this.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `ALLOWED_ORIGINS` | localhost dev ports | Comma-separated origins allowed by CORS. Set to the Cloudflare Pages domain in production |
| `CACHE_TTL_S` | 900 | Seconds to hold an analysis |
| `SEC_USER_AGENT` | none | Required by the SEC on every EDGAR request. Must be a real contact or requests get throttled |
| `PORT` | 8080 | Injected by Cloud Run |

## Deploying

Cloud Run, because the engines need scipy, statsmodels, arch, numpy and pandas. Those are
compiled C and Fortran and will not run on Cloudflare Workers or Supabase Edge Functions,
which execute JavaScript.

```bash
gcloud run deploy research-api \
  --source . --region europe-west1 --allow-unauthenticated \
  --memory 2Gi --cpu 1 --timeout 120 --min-instances 1 \
  --set-env-vars "SEC_USER_AGENT=Your Name you@domain.com,ALLOWED_ORIGINS=https://yourapp.pages.dev"
```

`--min-instances 1` keeps one instance warm. With the engines' dependency weight a cold start
is 10 to 30 seconds, which is a poor first impression for a frontend.

## Architecture

```
React (Cloudflare Pages)  ->  this API (Cloud Run)  ->  fsi | valuation_engine | risk_engine
                                                              |
                                                   SEC EDGAR · FRED · Yahoo Finance
```
