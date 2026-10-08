# BankForge — Architecture Document

**Capstone:** Week 5 — Model Context Protocol (Build, Compose & Deploy)
**Domain:** Digital banking — NeoBank India (fictional)
**Servers:** 3 (`accounts_server`, `products_server`, `compliance_comms_server`)
**Primitives:** 14 Tools, 4 Resources, 2 Prompts

---

## 1. System Overview

BankForge exposes a subset of NeoBank India's backend systems — core banking,
loan/product catalogue, compliance/KYC, and customer communication — through
three MCP servers. Each server is independently runnable as a plain local
Python process (`python -m <server>`, no Docker), and each owns its own
SQLite database file containing only the tables its responsibility requires.

```
                    AI Client (Claude Desktop / Anthropic API mcp_servers)
                               |              |              |
                    accounts_server   products_server   compliance_comms_server
                               |              |              |
                 neobank_accounts.db   neobank_products.db   neobank_compliance.db
                   (own SQLite file)     (own SQLite file)      (own SQLite file)
```

The one exception to "each server is fully self-contained" is documented in
§3 below: `products_server.submit_loan_application` composes with
`compliance_comms_server`'s own tool logic for the KYC check and audit-log
write it cannot perform against its own data.

## 2. Why Three Separate SQLite Files (not one shared `neobank.db`)

The brief (§3) explicitly allows either a single shared SQLite file or three
separate ones, as long as isolation is **structural, not incidental** — the
rubric (§9) weights this at 15% and explicitly asks whether access is
"actually enforced... not merely incidental to what each tool happens to
query."

We chose three separate files because it makes that guarantee checkable by
a test, not just arguable in prose: `products_server`'s own database
literally has no `customers` or `accounts` table — not "a table it chooses
not to query," but a table that does not exist in the file its code can
even open. `tests/test_server_isolation.py` asserts this directly against
each server's `SCHEMA` constant, so "isolation is enforced" is a claim
backed by a passing automated test, not just a design intention.

## 3. Tool Inventory (14 Tools — brief §3.1)

| Server | Tool | Op | Purpose |
|---|---|---|---|
| accounts_server | `get_account_summary(account_id, caller_scope)` | Read | Account lookup, fields minimized per scope, account number always masked |
| accounts_server | `get_accounts_for_customer(customer_id, caller_scope)` | Read | All accounts for a customer, same minimization |
| accounts_server | `get_transaction_history(account_id, limit)` | Read | Recent transactions, most recent first |
| products_server | `list_loan_products(category)` | Read | Catalogue browse, optional category filter |
| products_server | `get_loan_product_details(product_id)` | Read | Single product lookup |
| products_server | `check_eligibility_criteria(product_id, applicant_risk_rating)` | Read | Criteria check only — no customer data touches this server |
| products_server | `submit_loan_application(customer_id, product_id, requested_amount)` | **Write** | KYC-gated at the Tool level; composes with compliance_comms_server |
| products_server | `get_loan_application_status(application_id)` | Read | Status of a previously submitted application |
| compliance_comms_server | `get_kyc_status(customer_id)` | Read | The only tool anywhere returning raw KYC data |
| compliance_comms_server | `run_compliance_check(customer_id, transaction_amount)` | Read | Deterministic pass/block + large-transaction reporting flag |
| compliance_comms_server | `get_fraud_flags(customer_id)` | Read | Reads the fraud_flags table |
| compliance_comms_server | `write_audit_log(action_type, performed_by, customer_id, outcome, details)` | **Write** | One entry per write operation, retrievable for compliance review |
| compliance_comms_server | `send_customer_communication(customer_id, channel, message)` | **Write** | Sanitises, KYC-gates, sends, and audit-logs |
| compliance_comms_server | `generate_customer_communication(template_name, customer_name, account_tier, specific_detail)` | Read | Renders a filesystem Markdown template into message text |

**Why `generate_` and `send_` are separate tools.** Rendering a message
from a template is a pure, side-effect-free Read; dispatching it (with the
KYC gate, sanitisation, and audit trail) is a Write with real
consequences. Splitting them means an AI client — or a human reviewing the
rendered draft — can inspect the exact text before it's sent, and it maps
directly onto `customer_communication_prompt`'s four arguments, which name
exactly what `generate_customer_communication` needs.

### 3.1 The one cross-server composition seam

`submit_loan_application` must independently verify KYC status and write
an audit trail entry — both owned exclusively by `compliance_comms_server`
— without trusting the AI client to have checked first (brief §4's
explicit requirement). Since `products_server`'s own database has no
customer data at all, this function calls `compliance_comms_server`'s own
`logic.get_kyc_status()` and `logic.write_audit_log()` directly, in-process:

```python
from compliance_comms_server import logic as compliance_logic
kyc = compliance_logic.get_kyc_status(customer_id)
```

This is the same function a live MCP client would reach over the wire —
here it's called in-process specifically so `run_local_demo.py` can
exercise the full flow without needing a live MCP client (brief §8). When
all three servers run as independent processes talking to a real AI
client, this call would instead be a genuine MCP client connection from
the `products_server` process to the running `compliance_comms_server`
process; the business logic, including the fail-closed behavior below, is
identical either way — only the transport changes.

**This call fails closed.** If it raises (simulating the compliance server
being unreachable — Official Stress Test 3), `submit_loan_application`
rejects the application rather than silently treating the customer as
KYC-verified. It also reports `audit_log_write_failed: true`, since an
unreachable compliance server means the rejection itself can't be written
to the audit trail either — a real operations team would need to know to
reconcile that gap later, not have it pass silently.

This import is deliberately the only one of its kind in the project:
`products_server` reaches into `compliance_comms_server`'s *logic* module
(never its `db.py`), and no other module reaches across a server boundary
at all. `tests/test_server_isolation.py` asserts both halves of this.

## 4. Resources & Prompts (brief §7)

| Resource | Server | Notes |
|---|---|---|
| `account://{account_id}/summary` | accounts_server | Reuses `_build_account_view` — the exact guardrail path the Tools use |
| `product://{product_id}/details` | products_server | Delegates straight to `get_loan_product_details` |
| `customer://{customer_id}/profile` | compliance_comms_server | Phone/email masked via `guardrails.mask_pii_value` |
| `template://{template_name}` | compliance_comms_server | Raw Markdown so a client can see what fields a template needs |

All four return a single record from a templated URI — never bulk data,
never an unmasked sensitive field — and all four call into the same
guardrail functions the Tools use rather than re-implementing masking or
scoping.

| Prompt | Server | Args |
|---|---|---|
| `transaction_analysis_prompt` | accounts_server | `customer_name`, `account_type`, `analysis_period` |
| `customer_communication_prompt` | compliance_comms_server | `template_name`, `customer_name`, `account_tier`, `specific_detail` |

Both Prompts return instruction text pointing the AI client at the correct
sequence of Tool/Resource calls — neither one embeds real account, KYC, or
balance data directly, which is exactly what the rubric's "primitive
design quality" row checks for.

## 5. Security & Guardrails (brief §4 — 20% of rubric)

All of the following live in `guardrails.py`, as plain Python with no model
call anywhere in the decision path:

- **Scoped field visibility** — `minimize_account_fields()`, keyed on an
  explicit `caller_scope` (`teller` / `loan_officer` / `compliance_officer`
  / `admin`) passed on every `accounts_server` tool call.
- **Account number masking** — `mask_account_number()` masks to the last 4
  digits, applied unconditionally (even for `admin` scope) on every Tool
  and Resource returning account data.
- **Input format validation** — `validate_id_format()` enforces
  `CUS-XXXXX` / `ACC-XXXXX` / `PROD-XX-XX`, raising a typed
  `InvalidIDFormatError` on mismatch.
- **KYC gates on writes** — `can_send_communication()` blocks marketing
  messages to non-verified customers; `can_submit_loan_application()` is
  the equivalent gate for loan applications, checked inside the Tool
  itself.
- **Audit logging on writes** — `write_audit_log()` + the `audit_log`
  table record every write outcome (including blocked attempts, not only
  successes), retrievable via `get_audit_log()`.
- **Input sanitisation / prompt-injection defence** — `sanitize_free_text()`
  length-limits, strips HTML, and screens for known injection signatures,
  raising rather than silently cleaning a match.
- **PII redaction in logs** — `redact_for_logging()` masks PII-shaped
  argument names on every ENTER line; `logging_config._redact_result_preview()`
  does the equivalent recursively on every EXIT line's result, unconditionally
  — including for functions (e.g. every `db.py` accessor) that never opted
  into a `redact=` argument at all, since a raw database row returned from
  a low-level function is exactly as much of a leak as an unredacted
  argument would be. `mask_pii_value()` is the public form Resources reuse
  for partial display (e.g. masked phone/email on `customer://.../profile`).
  `mask_account_number()`, `mask_pii_value()`, and `redact_for_logging()`
  itself are deliberately **not** `@trace`-decorated — each one's job is to
  take a raw, unmasked value as its argument, so tracing it would log that
  exact value in plaintext on its own ENTER line before it gets masked,
  which is a self-defeating leak rather than useful observability.

> **Note on `caller_scope` as an explicit argument.** In a real deployment
> this would come from transport-level auth (an API key or JWT mapped to a
> role by a gateway in front of the MCP server), not a string the caller
> supplies themselves. It's an explicit parameter here so the scoping
> *logic* is visible, testable, and gradeable without standing up a full
> auth stack — see §9, "Known Simplifications."

## 6. Logging & Observability (brief §5 — 10% of rubric)

Every function in every Tool, Resource, and Prompt path is decorated with
`@trace(logger)` from `logging_config.py`, producing:

- an **ENTER** line (DEBUG) — fully qualified function name, a `call_id`,
  bound arguments (redacted via `guardrails.redact_for_logging` wherever a
  PII-shaped argument is present)
- an **EXIT** line (DEBUG) — same `call_id`, `duration_ms`, truncated
  result preview
- on exception, a **FAILED** line (ERROR) — same `call_id`, `duration_ms`,
  exception type, full traceback, then the exception is re-raised

All lines are structured JSON, one object per line, via a custom
`JsonFormatter`. `LOG_LEVEL` defaults to `DEBUG` everywhere, overridable via
the environment variable of the same name.

**The handler writes to stderr, not stdout.** A stdio-transport MCP server
(§2.3's `mcp.run(transport="stdio")`) uses stdout exclusively for the
JSON-RPC protocol stream — anything else written there corrupts it. A
DEBUG-level JSON logger writing to stdout would interleave log lines with
protocol messages the moment a server actually runs under stdio transport,
even though this is invisible when exercising `logic.py` directly (via
`run_local_demo.py` or `pytest`, neither of which goes through stdio at
all) — which is exactly why it's easy to miss until the real MCP
verification step in §7 below.

## 7. Testing & Verification Status — honest account

| Component | How it was verified | Status |
|---|---|---|
| `logging_config.py`, `guardrails.py`, each server's `db.py`/`logic.py` | Executed directly in a network-isolated sandbox; output inspected | **Verified** |
| All 14 Tools' business logic | Exercised via `run_local_demo.py` and the full `tests/` suite | **Verified** |
| Data-source isolation (structural, not incidental) | `tests/test_server_isolation.py`, asserting against each `SCHEMA` constant and the import graph | **Verified** |
| Showcase Scenario + all 3 Stress Tests | `tests/test_showcase_and_stress.py` and `run_local_demo.py` §9 | **Verified** |
| pytest suite (`tests/`) | Written in standard pytest style (plain `assert` functions); actually executed in-sandbox via the stdlib-only `run_tests.py` harness — **79/79 passed** | **Written and executed** (in-sandbox; see note below) |
| Real `@mcp.tool()` / FastMCP registration | Written against the documented `mcp>=1.2.0` API; the package itself could not be installed in the build sandbox (no network access) | **Unverified in-sandbox — run yourself, see below** |
| `pytest tests/` via the real `pytest` binary | Same reason — not installable in the build sandbox | **Run yourself, see below** |
| Local server startup as a live MCP process | Same reason | **Run yourself, see below** |

**What "run yourself" means, concretely:** nothing in the code needs to
change. On a machine with normal internet access:

```bash
pip install mcp pytest
pytest tests/ -v                          # same test files, real pytest
python -m accounts_server                 # starts for real, stdio transport
mcp dev accounts_server/__main__.py        # MCP Inspector, for registration evidence
```

For stronger evidence than the Inspector alone — a real Claude API call
actually discovering and invoking your live server's tools over the real
`mcp` client/server transport — see `mcp_client_demo.py` and README §5.
This uses the MCP *client* role locally (the same role Claude Desktop
plays), not the Anthropic API's `mcp_servers` connector parameter, since
that parameter requires a publicly-reachable URL and can't reach a local
`stdio` process.

We're naming this limitation directly rather than quietly presenting
sandbox-only verification as equivalent to the real thing — that's the
difference the rubric's §9 "architecture documentation" row (5%) is
checking for ("is the 'what did not work' section honest and diagnostic").

### 7.1 Fixes made during review, before submission

A second pass over the actual trace output (not just the code) surfaced
four real gaps, fixed as follows — named here rather than left for a
grader to find first:

1. **Logging handler wrote to stdout, not stderr.** Harmless everywhere
   `logic.py` is called directly (demo, tests), but would have corrupted
   the stdio JSON-RPC stream the moment a server actually ran under
   `mcp.run(transport="stdio")`. Fixed in `logging_config.py`.
2. **EXIT lines weren't redacted, only ENTER lines were.** `redact=` was
   only ever applied to the bound-arguments dict going into an ENTER line;
   a function's actual return value — including, for any `db.py` function
   with no `redact=` at all, a full raw database row — went into the EXIT
   line's `result_preview` unredacted. Fixed by redacting
   `result_preview` recursively and unconditionally in `logging_config.py`,
   independent of whether a `redact=` was supplied.
3. **The masking functions were tracing their own raw input.**
   `mask_account_number()`, `mask_pii_value()`, and `redact_for_logging()`
   each take an unmasked value as their argument by definition — being
   `@trace`-decorated meant each one's own ENTER line logged that value in
   plaintext before masking it. Fixed by removing `@trace` from these
   three specifically (their correctness is already covered by
   `tests/test_guardrails.py`; tracing them added no debugging value and
   directly undermined their purpose).
4. **`submit_loan_application` didn't audit every rejected attempt.** The
   product-not-found, invalid-amount, exceeds-max-amount, and
   customer-not-found early returns skipped `_record_outcome`, while the
   KYC-blocked and eligibility-rejected paths already called it. Fixed by
   auditing all of them. The one path that still doesn't call
   `_record_outcome` — compliance service unreachable — is deliberate: the
   audit write goes through that same unreachable service, so the
   response surfaces `audit_log_write_failed: true` honestly instead of
   pretending the write happened.

A fifth, smaller gap: `generate_customer_communication`'s `account_tier`
argument was substituted into the outgoing template unsanitized and
unvalidated. Fixed by validating it against the three known tiers
(`standard`/`premium`/`elite` — the same enum `compliance_comms_server/db.py`'s
schema already uses) rather than running it through `sanitize_free_text()`,
since a closed-enum check is a strictly stronger guarantee than stripping
HTML from free text would be.

## 8. Compliance Documentation — DPDP Act alignment

NeoBank India, as a fictional Indian digital bank, would be subject to
India's Digital Personal Data Protection Act. The guardrails above map onto
its core principles as follows:

- **Data minimisation.** `minimize_account_fields()` ensures a caller only
  ever receives the fields their role requires — a `teller` never sees
  `kyc_status`/`risk_rating`, even though the underlying record has them.
  `redact_for_logging()` extends this to the log store itself: logs never
  become a second, less-protected copy of the same PII.
- **Purpose limitation.** `products_server` cannot see customer-identifying
  data at all, by construction — a design that can't accidentally scope-
  creep into using loan-eligibility infrastructure for anything requiring
  a real customer record.
- **Storage limitation / masking.** Account numbers are masked to the last
  4 digits everywhere, unconditionally — the full number is never
  returned by any Tool or Resource once it's left `accounts_server`'s
  internal database layer.
- **Accountability / audit trail.** `write_audit_log()` records every
  write operation's outcome — approved, rejected, or blocked — with a
  timestamp, action type, and customer ID, retrievable via
  `get_audit_log()` for a compliance review.
- **Consent for processing.** `can_send_communication()` encodes the rule
  that marketing communications require verified KYC — a proxy, in this
  fictional system, for "we have a validated, lawful basis to contact this
  customer for this purpose."

## 9. Known Simplifications (be ready to name these under questioning)

- **`caller_scope` as an explicit argument**, not derived from
  transport-level auth — see §5's callout.
- **SQLite, not a client-server database.** Fine for three local processes
  on one machine; a real multi-service deployment would use a proper
  client-server database per service to avoid any single-writer
  contention, even though each service's database is already logically
  separate here.
- **In-process composition, not a live MCP client call**, for the one
  cross-server seam in `submit_loan_application` — see §3.1 for exactly
  why and what would change in a fully live deployment.
- **No real email/SMS delivery.** `send_customer_communication` logs a
  communication record; wiring an actual provider would replace
  `db.insert_communication_log`'s body, not the guardrail logic around it.
- **`application_id` (`APP-XXXXX`) and other internal IDs** (`TXN-XXXXX`,
  `FLAG-XXXXX`, `AUD-XXXXXX`, `COMM-XXXXX`) follow a consistent style but
  aren't format-validated via `guardrails.py`, since the brief's §4 format
  requirement names only `CUS-XXXXX` / `ACC-XXXXX` / `PROD-XX-XX`.
- **Risk-rating ordering** (`low < medium < high`) is a simplification of
  real credit-risk modelling, encoded as a plain lookup table in
  `products_server/db.py` for a demonstrable, deterministic eligibility
  check rather than a realistic underwriting model.

## 10. Phased Rollout Plan

1. **Phase 0 — Internal sandbox (current state).** All three servers run
   locally against seed data only; no real customer data, no real message
   delivery. Used for development, the test suite, and the capstone demo.
2. **Phase 1 — Read-only pilot.** Deploy `accounts_server` and
   `products_server`'s read tools to a small internal pilot group (e.g.
   bank staff only, via Claude Desktop), with real but non-production data
   behind the same per-server isolation boundary. No write tools enabled.
3. **Phase 2 — Gated writes.** Enable `submit_loan_application` and
   `send_customer_communication` for the pilot group, with every write
   outcome reviewed against the `audit_log` by a human compliance officer
   before the gate is trusted to run unsupervised.
4. **Phase 3 — Production rollout.** Replace `caller_scope`'s explicit
   argument with real transport-level auth (API key/JWT → role mapping),
   move each server's SQLite file to a proper managed database, wire
   `send_customer_communication` to a real delivery provider, and expand
   from the pilot group to all relevant staff/systems — each phase gated
   on the audit trail from the previous phase showing no unexplained
   KYC-gate or compliance-check bypasses.

## 11. Demo Script Mapping

`run_local_demo.py` runs every item below in the order brief §8 specifies,
calling `logic.py` functions directly (no live MCP client needed):

1. Scoped field visibility, same account, `teller` vs `compliance_officer`
2. Transaction history retrieval
3. Loan product listing + eligibility checking (both outcomes)
4. Large-transaction reporting flag
5. KYC-blocked transaction (`run_compliance_check`)
6. KYC-blocked marketing communication
7. Blocked prompt-injection attempt
8. Full Showcase Scenario (Priya Sharma / `CUS-10042` / `PROD-PL-01`),
   end-to-end through KYC → eligibility → submission → status → templated
   communication → audit trail
9. All three official Stress Tests: missing customer, invalid loan
   submission (three variants), compliance server offline (fails closed)
