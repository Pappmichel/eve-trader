# EVE Trader — C-J Import Trading & Production

Two tools for EVE Online, sharing one backend and one SQLite store:

- **Trading** — a market-arbitrage toolkit that buys goods in Jita (The
  Forge) and sells them in a private player structure ("C-J"): candidate
  discovery, historical backtesting, shortlist margin tracking, own-order
  tracking, and realized-trade reconciliation between two characters.
- **Production** — Tech I/II/Reaction manufacturing planning for the same
  home structure: buy-vs-build decisions, stock targets, buy/build lists,
  invention cost/probability, and logistics status, all driven by a local
  Fuzzwork SDE cache plus live ESI/Goonmetrics prices.

Highlights:
- A SQLite store (`data/eve_trader.db`) as the durable source of truth for
  both tools.
- A full OAuth2 (Authorization Code + PKCE) login flow against EVE SSO, with
  automatic token refresh and per-character token storage.
- A React + FastAPI web app (charts, sortable/virtualized tables, live
  metrics) - see "Running the app" below.
- Retry/backoff and ESI error-limit (HTTP 420) handling.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -e .                                       # installs the `eve-trader` command

cp .env.example .env          # fill in EVE_SSO_CLIENT_ID (register at developers.eveonline.com)
cp config.example.yaml config.yaml   # adjust structure_id, character names, thresholds
```

### Registering an EVE SSO application

1. Go to https://developers.eveonline.com/applications and create a new application.
2. Connection type: **Authorization Code** (PKCE-capable / "public" client — no secret required).
3. Callback URL: `http://localhost:8765/callback` (must match `.env` exactly).
4. Scopes: exactly
   `esi-markets.read_character_orders.v1`,
   `esi-markets.structure_markets.v1`,
   `esi-wallet.read_character_wallet.v1`,
   plus (for Production) `esi-assets.read_assets.v1`, `esi-industry.read_character_jobs.v1`,
   `esi-characters.read_blueprints.v1`.
   (Only request scopes you've enabled for the app *and* that the code
   actually uses — extra/misspelled scopes make EVE SSO reject the whole
   login with `invalid_scope`.)
5. Copy the client ID into `.env`.

## Daily workflow

Trading:

```bash
# One-time per character
eve-trader auth --role buyer     # the character that buys in Jita
eve-trader auth --role seller    # the character that sells in the structure

# Daily
eve-trader refresh-shortlist     # recompute landed cost / margin / decision
eve-trader build-universe        # (occasionally) rebuild the full ESI candidate universe
eve-trader build-focused         # filter down to sensible import candidates
eve-trader find-new-candidates --safe   # backtest new candidates vs. shortlist
eve-trader add-to-shortlist      # promote Add=True candidates onto the shortlist
eve-trader reconcile-trades      # match realized buy/sell wallet transactions

# Or all at once:
eve-trader pipeline --safe
```

For day-to-day use, the web app (below) covers every one of these steps with
buttons instead of CLI commands - the CLI is mainly useful for scripting/
automation. Production has no CLI equivalent yet; use the web app.

## Configuration

All trading and production parameters (regions, structure ID, import
cost/m³, haircut, margin thresholds, character names, ME/TE structure/rig
setup) live in `config.yaml` — copy `config.example.yaml` and adjust.
Anything you don't override keeps the built-in default.

Secrets (SSO client id) live in `.env` — copy `.env.example`.

## Running the app

**Development** (two processes, hot-reload on both sides):

```bash
uvicorn eve_trader.api.main:app --reload      # backend on :8000
cd frontend && npm install && npm run dev     # frontend on :5173, proxies /api to :8000
```

Open http://localhost:5173.

**Single-process "real run"** (build the frontend once, FastAPI serves both
the API and the built static files on one port):

```bash
cd frontend && npm run build                  # writes frontend/dist/
cd .. && uvicorn eve_trader.api.main:app       # serves API + UI on :8000
```

Open http://localhost:8000.

The web app covers every workflow step with buttons instead of CLI commands -
login (buyer/seller for Trading, one or more producer characters for
Production, all via EVE SSO), the rare setup steps (Load Market Groups →
Filter Candidates, Refresh SDE), the daily "⚡ Search + Add + Clean Up" button
(finds new candidates, adds the recommended ones, and prunes stale/over-cap
items in one go), Refresh Shortlist, Reconcile Trades, Compute Buy/Build
List, and "Run Complete Pipeline" for all of Trading at once. Both the web
app and the CLI (`eve-trader ...`) call the exact same logic in
`eve_trader/actions.py` / `eve_trader/production/actions.py`, so they never
drift apart.

## Tests

```bash
pytest
```

Tests use mocked HTTP responses (no network / EVE SSO access required) and
cover the scoring/decision/pricing logic for both tools.

## Deploying somewhere reachable beyond localhost

See [`deploy/README.md`](deploy/README.md) - a single-user deployment (e.g.
a free-tier VPS) behind the access gate (`eve_trader/access_gate.py`), which
requires an EVE SSO login matching an allowlisted character/corp/alliance
before any part of the app is reachable. Off by default; local dev is
unaffected either way.

## Interface

The web app has a dark "trading terminal" look (inspired by the EVE HUD):
color-coded status columns (Import = cyan, Already ordered = blue, Skip =
amber, Inactive = red) and a KPI bar up top with the most important numbers
at a glance.

## Notes / limitations

- `region_order_stats` / `structure_order_stats` price off a robust
  "percentile" price (ignoring extreme troll orders): the 5th-percentile
  sell price / 5th-percentile-from-top buy price. Adjust `_percentile` in
  `esi_client.py` if you want a different definition.

## License

[MIT](LICENSE). EVE Trader is an unofficial third-party tool, not affiliated
with or endorsed by CCP hf. EVE, EVE Online, CCP, and all related logos and
trademarks are the property of CCP hf.
