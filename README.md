# BankForge — MCP Server Ecosystem for Digital Banking

Week 5 Capstone — Model Context Protocol: Build, Compose & Deploy
Domain: Digital banking — NeoBank India (fictional)

Three independently deployable MCP servers (`accounts_server`, `products_server`,
`compliance_comms_server`), each backed by its own local SQLite database, exposing
14 Tools, 4 Resources, and 2 Prompts over the Model Context Protocol. See
`ARCHITECTURE.md` for the full design writeup.

## 1. Prerequisites

- Python 3.11+
- pip, with network access (to install `mcp` and `pytest` — see note below)

> **Note on this build's test/verification status.** This project was built
> and its business logic fully tested in a network-isolated sandbox, so
> `pytest` and the real `mcp` SDK could not be installed there. Every test
> in `tests/` was actually executed in that environment using the stdlib
> runner `run_tests.py` (79/79 passing) — see "What was verified vs. what
> needs your machine" in `ARCHITECTURE.md`. Run the commands below once on
> your own machine (with network access) to get the official `pytest`/MCP
> Inspector evidence the rubric asks for; nothing needs to change in the
> code to do so.

## 2. Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt  # pinned to mcp<2.0.0 — the servers use the 1.x FastMCP API
pip install pytest               # needed to run the suite in tests/

# 3. Seed all three (separate) local databases
python3 -m accounts_server.db
python3 -m products_server.db
python3 -m compliance_comms_server.db
```

This creates three independent SQLite files under `data/`:
`neobank_accounts.db`, `neobank_products.db`, `neobank_compliance.db`.
Each server's own `__main__.py` also re-seeds (idempotently, `force=False`)
on startup, so this step is a convenience, not a strict requirement.

## 3. Running the servers locally

Each server is a standalone MCP server, run as its own process:

```bash
python3 -m accounts_server
python3 -m products_server
python3 -m compliance_comms_server
```

No Docker or other container runtime is used or required — everything runs
as a plain local Python process. Point your MCP client (a Claude Desktop
config entry — see `claude_desktop_config.example.json` — or the Anthropic
API's `mcp_servers` parameter) at each running process.

To inspect a server with the MCP Inspector:

```bash
mcp dev accounts_server/__main__.py
mcp dev products_server/__main__.py
mcp dev compliance_comms_server/__main__.py
```

## 4. Running the demo and the test suite

```bash
# Exercises every §8 demo step, incl. the Showcase Scenario and all 3
# Stress Tests — no live MCP client needed, calls logic.py functions directly.
python3 run_local_demo.py

# Quieter output (hides DEBUG trace lines, keeps the demo narrative):
LOG_LEVEL=WARNING python3 run_local_demo.py

# Official test suite — must be run, not just present:
pytest tests/ -v

# Fallback, stdlib-only runner (used during development where pytest
# wasn't installable) — same test files, no pytest required:
python3 run_tests.py
```

## 5. Live verification with the real Claude API (optional, strongest evidence)

If you have an Anthropic API key, `mcp_client_demo.py` gives you stronger
proof than the MCP Inspector alone: it spawns a server as a real `mcp`
stdio subprocess, asks it for its real registered tool list, hands that
to Claude over the Messages API, and lets Claude actually call tools
against the live server.

```bash
pip install -r requirements-verify.txt   # just `anthropic` — mcp is already required
export ANTHROPIC_API_KEY=sk-ant-...

python3 mcp_client_demo.py accounts_server \
    "What's the account summary for ACC-10042, viewed as a teller?"

python3 mcp_client_demo.py products_server \
    "Check whether CUS-10042 is eligible for PROD-PL-01, then submit a loan application for 2,50,000 if so."
```

**Why not the Anthropic API's `mcp_servers` parameter instead?** That
feature (the MCP *connector*) is for servers reachable at a public URL —
Anthropic's own infrastructure calls out to it directly, so it can't reach
a `stdio` process on your laptop. `mcp_client_demo.py` runs the MCP
*client* side locally instead (same role Claude Desktop plays), which is
the right tool for verifying a local server with a real model in the loop.
If you later deploy a server with `streamable-http` transport behind a
public URL, the `mcp_servers` parameter would then apply directly.

## 6. Project layout

```
bankforge/
├── logging_config.py              # @trace — ENTER/EXIT/FAILED, structured JSON (§5)
├── guardrails.py                  # ID validation, masking, scoping, KYC gates,
│                                  #   sanitisation, PII redaction, compliance rules (§4)
├── accounts_server/
│   ├── db.py                      # owns neobank_accounts.db (accounts, customers*)
│   ├── logic.py                   # 3 tools + 1 Resource's business logic
│   └── __main__.py                # FastMCP registration — `python -m accounts_server`
├── products_server/
│   ├── db.py                      # owns neobank_products.db (loan_products, loan_applications)
│   ├── logic.py                   # 5 tools + 1 Resource; composes with compliance_comms_server
│   └── __main__.py
├── compliance_comms_server/
│   ├── db.py                      # owns neobank_compliance.db (customers, fraud_flags,
│   │                              #   audit_log, communications_log)
│   ├── logic.py                   # 6 tools + 2 Resources + 1 Prompt's business logic
│   ├── communication_templates/   # loan_approval.md, kyc_reminder.md, marketing_offer.md
│   └── __main__.py
├── tests/                         # pytest-style; also runnable via run_tests.py
│   ├── test_logging_config.py
│   ├── test_guardrails.py
│   ├── test_accounts_logic.py
│   ├── test_products_logic.py
│   ├── test_compliance_logic.py
│   ├── test_server_isolation.py   # proves data-source isolation is structural
│   └── test_showcase_and_stress.py
├── run_local_demo.py              # brief §8, in order, no live MCP client needed
├── run_tests.py                   # stdlib-only test runner (dev-time fallback)
├── ARCHITECTURE.md                # full design writeup + compliance + rollout plan
├── requirements.txt
├── .env.example
└── claude_desktop_config.example.json
```

\* `accounts_server` keeps a minimal customers copy (name/kyc_status/risk_rating)
for its own field-visibility scoping — the authoritative, full customer
record lives only in `compliance_comms_server`. See `ARCHITECTURE.md`.

## 7. Official Showcase Scenario & Stress Tests

Seeded and exercised by both `run_local_demo.py` and `tests/test_showcase_and_stress.py`:

- **Showcase Scenario:** Priya Sharma / `CUS-10042` / `PROD-PL-01` — full
  KYC → eligibility → loan submission → status check → templated
  communication → audit trail flow.
- **Stress Test 1 — missing customer:** `CUS-99999` (well-formed, doesn't exist).
- **Stress Test 2 — invalid loan submission:** non-verified-KYC applicant,
  ineligible risk rating, and amount exceeding the product's max.
- **Stress Test 3 — compliance server offline:** simulated outage of the
  `compliance_comms_server` composition call inside `submit_loan_application` —
  the application is rejected (fails closed), never silently approved.
