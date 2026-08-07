# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`vault-mock` is a local FastAPI simulator of Thought Machine's **Vault Core** banking platform. It exists so a Spring Boot orchestrator service (external to this repo) can be developed against a faithful replica of Vault's HTTP contract (routes, headers, payload shapes, error format) before VPN/production access to the real `core-api.tm.blx-demo.com` is available. The intended migration path is a pure environment-variable swap (`APP_VAULT_BASE_URL`, `APP_VAULT_AUTH_TOKEN`) — no orchestrator code changes.

Port **9000**, Python 3.12, three real dependencies: FastAPI, Uvicorn, Pydantic (plus OpenTelemetry for tracing — see Telemetry below).

Full narrative documentation (in Spanish) lives in `README-vault-mock.md`. It is a good conceptual read but has drifted from the current code in a few places (e.g. it shows an older `pre_posting_code(vault, postings)` signature and a `vault.get_parameter(...)` call that no longer exist) — when in doubt, trust the source under `app/` and `contracts/` over the README's inline code samples.

## Commands

```bash
# Setup (Windows)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run (hot reload)
uvicorn app.main:app --reload --port 9000

# Health check
curl http://localhost:9000/health

# Swagger UI
# http://localhost:9000/docs
```

There is no test suite, linter, or build step configured in this repo — verification is done by hitting the running endpoints (see `README-vault-mock.md`'s "Testing" section for example `curl` calls against `/v1/smart-contracts/validate`).

**Known gap**: `requirements.txt` does not list the `opentelemetry-*` packages that `app/core/telemetry.py` imports, even though they're present in `venv`. If setting up a fresh environment, install them manually or expect `ImportError` on startup.

## Architecture

### Request flow

Two routers, both gated by the `X-Auth-Token` header (not `Authorization: Bearer` — this mismatch with typical REST conventions is deliberate, it mirrors real Vault):

- `app/api/contracts.py` → `POST /v1/smart-contracts/validate` — loads a smart contract, runs its `pre_posting_code` hook against proposed parameters, returns accept/reject. **A rejection is a `200 OK` with `accepted: false`**, not an HTTP error — the contract executed successfully, it just declined the request.
- `app/api/accounts.py` → `POST/GET /v1/accounts`, `GET /v1/accounts/{id}` — CRUD against the in-memory store. Account creation is idempotent on `request_id`: replaying the same `request_id` returns the existing account instead of erroring or duplicating.

Auth: `app/auth.py` defines `require_auth_token`, a FastAPI dependency checking `X-Auth-Token` against a hardcoded dev token set (`mock-dev-token`, `mock-test-token`). Applied per-route via `Depends(...)`, not as global middleware.

### The Smart Contract engine (the distinctive part)

Vault's defining feature is that product rules are **executable Python files**, not config rows. This repo reimplements a minimal version of that:

- `contracts/*.py` — one file per product (currently only `personal_loan_ai.py`). Each defines module-level `parameters` (a list of dicts with `name`/`level`/`shape`/`default_value`/etc.), and hook functions like `pre_posting_code(postings, effective_date)` and optionally `derived_parameters(effective_date)`.
- Inside a contract, `vault` and `Rejected` are **not imported** — they're injected as globals into the module's namespace at load time, exactly mimicking how the real Vault Contract Execution Engine works. A hook accesses the current request's data via `vault.get_parameter_timeseries(name="...").latest()` and signals a policy rejection by `raise Rejected(message, reason_code="...")`.
- `app/engine/contract_loader.py` (`load_contract(product_id, vault_obj)`) does the injection: it uses `importlib.util.spec_from_file_location` to load `contracts/{product_id}.py` as an isolated module, sets `module.vault`, `module.Rejected`, plus stub shapes/decorators (`NumberShape`, `StringShape`, `Level`, `Tside`, `requires(...)`, `Parameter = lambda **kw: kw`) so the contract file evaluates without needing the real Thought Machine SDK, then `exec_module`s it.
- `app/engine/vault_sdk.py` defines `MockVault` (the simulated `vault` object — `get_parameter_timeseries`, `get_balance_timeseries`, `instruct_posting_batch`, `make_internal_transfer_instructions`), `Rejected` (the rejection exception carrying `message` + `reason_code`), `ParamTimeseries`/`BalanceTimeseries`/`BalanceEntry` (timeseries wrappers — `BalanceTimeseries` is a stub, always effectively empty/zero).
- `app/api/contracts.py` orchestrates the above per-request: coerces incoming `instance_param_vals` (all strings, per Vault convention) to `int`/`Decimal`/`str` via `_coerce_params`, merges in the contract's `TEMPLATE`-level defaults via `_merge_with_defaults`, builds a fresh `MockVault`, loads the contract, and invokes `pre_posting_code`, catching `Rejected` to build the response.

**Adding a new product** means adding a new `contracts/<product_id>.py` file with `parameters` + `pre_posting_code` — no changes needed anywhere in `app/`.

### Store

`app/store.py` holds a single in-memory `STORE` dict (`accounts`, `postings`, `customers`, `decisions`) — no database. It's wiped on every server restart; `reset_store()` exists for tests. This is intentional: the real Vault Core owns persistence, so there's no plan to add a DB here before migrating off the mock.

### Telemetry

`app/core/telemetry.py`'s `setup_telemetry(app, service_name)` wires OpenTelemetry tracing (OTLP HTTP exporter, default endpoint `http://localhost:4318/v1/traces`, overridable via `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`) and instruments the FastAPI app so it picks up incoming W3C `traceparent` headers from the Spring Boot orchestrator, chaining spans into the existing distributed trace. This module is written to be shared verbatim with a sibling service (`credit-ai-service`) — keep it generic if editing.

### CORS

`app/main.py` allows `localhost:4200`/`localhost:3000` (Angular/React dev servers) so a browser dashboard can call this mock directly. This is explicitly a dev-only convenience — in production the browser would never talk to Vault directly, only through the orchestrator.
