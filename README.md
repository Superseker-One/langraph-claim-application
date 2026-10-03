# SekureCare Claims Assistant (beta)

A Streamlit app for **health-insurance reimbursement claim intake**, powered by a **LangGraph** workflow.
Built as the class project for the *FDE LangGraph* sessions: collect → validate → run the graph → deploy.

* Validated form (inline alerts on every field, conditional sections, repeating bill rows)
* LangGraph workflow: eligibility → calculation → document check → status → AI-drafted letters → automatic checks
* Optional AI (via [OpenRouter](https://openrouter.ai)): email autofill + letters. **Works without a key** (template letters)
* Bring-your-own-key, **never shared between visitors** (see [Key safety](#key-safety))
* **Demo only.** See [Security hardening](#security-hardening) and [Known limitations](#known-limitations) before reusing any of this

## Project structure

```
securecare-claims/
├── app.py                       # Streamlit entry point (UI shell only)
├── requirements.txt             # version RANGES: what Streamlit Community Cloud installs
├── requirements-lock.txt        # exact versions the tests passed with (runtime packages, no pytest)
├── pytest.ini
├── .devcontainer/
│   └── devcontainer.json        # Codespaces: installs requirements.txt, runs streamlit with its default CORS/XSRF protection
├── .streamlit/
│   ├── config.toml              # XSRF protection on, showErrorDetails = "none"
│   └── secrets.toml.example     # the app needs NO secrets
├── securecare/
│   ├── config.py                # constants, business-rule thresholds, field limits, EXTENSIBLE FIELD REGISTRY
│   ├── schemas.py               # Pydantic models + validators (the FORM schema, conditional sections, IST "today")
│   ├── validation.py            # form -> {widget_key: error}; business rules vs policy store; duplicate bills
│   ├── security.py              # strict key-shape check, secret redaction (pattern + exact value)
│   ├── textsafe.py              # control/zero-width/bidi stripping, homoglyph folding, wording/amount/link scanner
│   ├── formatting.py            # ₹ formatting
│   ├── samples.py               # sample claims (relative dates)
│   ├── services/
│   │   └── policy_store.py      # mock policy admin system (system of record)
│   ├── graph/                   # ★ THE CORE UNIT: the LangGraph workflow
│   │   ├── state.py             #   ClaimState = the business schema (+ reducers)
│   │   ├── nodes.py             #   one function per business step
│   │   ├── edges.py             #   routing functions for conditional edges
│   │   ├── builder.py           #   wires it together, returns the compiled graph
│   │   ├── runner.py            #   runs the graph, captures a node-by-node trace
│   │   └── visualize.py         #   graph -> Graphviz DOT for the UI
│   ├── agents/                  # LLM agents
│   │   ├── llm.py               #   the ONLY place the LLM client is created (OpenRouter, max_tokens capped)
│   │   ├── extractor.py         #   email text -> structured fields
│   │   └── communicator.py      #   officer summary + claimant letter (+ automatic checks, template fallback)
│   └── ui/                      # Streamlit widgets
│       ├── sidebar.py           #   API-key box (the key-safety logic lives here)
│       ├── form.py              #   claim form bound to session_state
│       ├── autofill.py          #   "paste your email" feature (every extracted value is clamped)
│       ├── safe.py              #   escapes untrusted text before it is shown in banners / markdown
│       ├── limits.py            #   per-session throttle for AI actions
│       ├── results.py           #   result screen
│       └── workflow_tab.py      #   live drawing of the compiled graph + About tab
└── tests/                       # 274 tests: validation, schemas, rules, graph, communicator checks, security,
                                 # text scanner, UI helpers, end-to-end UI (AppTest), deployment files
```

**Dependency direction** (nothing points backwards): `ui → graph → agents → config/schemas`.
`graph/` and `agents/` never import Streamlit, so they can be reused in an API, a notebook or a test.

## The workflow (`securecare/graph/builder.py`)

```
START → intake → lookup_policy ─┬─(reject)──→ reject_claim ──────────────────→ END
                                └─(continue)→ compute_estimate → check_documents → decide_status
                                                     ┌──(draft)── draft_communications ⇄ verify_communications ──┐
                                      decide_status ─┤                    (bounded retry loop)                 ├→ final_checks → END
                                                     └──(skip: no key)── write_template_communications ─────────┘
```

| Concept from the notebooks | Where it appears |
|---|---|
| Static fields | `claimant`, `hospitalization`, `policy` in `ClaimState` |
| Dynamic (1..N) + reducer | `bill_items`, `documents`; `review_reasons` / `audit_log` use `operator.add` |
| Conditional field + conditional edge | `patient`, `accident_details`; `route_after_policy`, `route_llm` |
| Derived fields | totals, payable estimate, `missing_documents`, `status` |
| Extensible fields + registry | `EXTRA_FIELD_REGISTRY` in `config.py` (adding a field = one dict, no graph change) |
| Bounded retry loop | `draft_communications ⇄ verify_communications` (max `MAX_LLM_ATTEMPTS`) |
| Evals / "deployable is not deployed" | `verify_communications` (automatic checks on AI text), `final_checks` (money, status, documents and text consistency) |

Demo policies you can type are listed in the app's **About** tab (e.g. `POL-458921` happy path,
`POL-555000` lapsed, `POL-123456` waiting-period).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

AI features (email autofill, AI-drafted letters) use an [OpenRouter](https://openrouter.ai/keys) key.
Paste it into the sidebar box (it looks like `sk-or-v1-...`) and pick a model. Models are OpenRouter slugs
such as `openai/gpt-4o-mini`; to offer others, add them to `MODEL_OPTIONS` in `securecare/config.py`
(choose models that support tool calling). Without a key the app still works and uses template letters.

Run the tests (no network or API key needed):

```bash
pip install pytest
pytest
```

**Python and dependency versions.** The test-suite was run on **Python 3.14.4** (macOS) with the exact
package versions in `requirements-lock.txt`. Other Python versions are not tested here: the code does not
use 3.14-only syntax and the devcontainer image is Python 3.11, but treat those as *expected to work, not
verified*. Streamlit Community Cloud installs `requirements.txt` (version **ranges**), so a fresh deploy may
resolve newer minor versions than the lock file; the lock file records the known-good set. To reproduce the
tested environment: `pip install -r requirements-lock.txt pytest`.

## Deploy on Streamlit Community Cloud

1. **Push to GitHub**
   ```bash
   git init && git add . && git commit -m "SekureCare claims assistant (beta)"
   git branch -M main
   git remote add origin https://github.com/<you>/securecare-claims.git
   git push -u origin main
   ```
   Check that `.streamlit/secrets.toml` and `.env` are **not** in the commit (they are git-ignored).
2. Go to **share.streamlit.io** → **Create app** → pick the repo and branch `main`.
3. **Main file path:** `app.py`
4. **Advanced settings:** pick a Python version (see *Python and dependency versions* above: only 3.14.4 has
   actually been tested). No secrets are required.
5. **Deploy.** The first build installs `requirements.txt` (ranges, not the lock file); later pushes to `main`
   redeploy automatically. `.streamlit/config.toml` keeps XSRF protection on and hides error details from visitors.

Community Cloud notes: free apps go to sleep when idle (cold start on the next visit), the filesystem is
ephemeral (this app stores nothing), and public apps are visible to anyone with the link.

## Key safety

Each visitor types **their own** OpenRouter key in the sidebar. Other Streamlit apps leak keys between users
when they do one of these things, and this project avoids all of them:

| Leaky pattern | Why it leaks | What we do instead |
|---|---|---|
| `os.environ["OPENROUTER_API_KEY"] = key` | environment is **process-wide**, shared by all sessions | pass `api_key=` explicitly to the client |
| `@st.cache_resource` / `@st.cache_data` on anything touching the key or the LLM client | caches are **shared across sessions** | no caching of LLM objects; the graph is rebuilt per submission |
| module-level global / file / `.env` for the visitor's key | one process, one value for everybody | key lives only in the visitor's `st.session_state` |
| `st.secrets` for the visitor's key | secrets belong to the app **owner** and are shared | not used; the app needs no secrets |
| key in graph state, run config or logs | gets returned, displayed, traced or logged | the LLM client is **injected** into the graph at build time; errors pass through `redact_secrets()` |

**Flush after use:** after every AI action the key box is rotated to a brand-new widget (a `key_nonce`) **and**
the old value is deleted from `st.session_state`, inside a `finally`, on every path out of a submit or an autofill
(success, validation failure, exception, malformed key, throttled). A rerun follows so the browser drops the old
widget too. The visitor can opt in to *Keep key for this browser session*, and a *Clear key now* button always works.

**Shape and redaction:** `looks_like_openrouter_key` only accepts `sk-or-` plus 16-200 ASCII letters, digits, `_`
or `-` (no whitespace, control or non-ASCII characters). `redact_secrets` is case-insensitive, needs a non-alphanumeric
character before `sk-` (so *risk-based* is untouched), masks the whole token up to whitespace/quotes/delimiters,
and takes an optional exact secret that is removed in raw, `repr`/JSON and URL-encoded form. It is applied to the
workflow error, the autofill error and the stored AI error, and again inside the UI text escaper.
The LLM client is built inside the `try`, so a failure there is redacted too, and `showErrorDetails = "none"` hides
tracebacks from visitors.

These behaviours are covered by tests in `tests/test_app_smoke.py`, `tests/test_app_security.py` and
`tests/test_security.py` (two simulated visitors on one app, key absent from the second visitor's session and from
`os.environ`, key erased after submit, after a forced workflow exception, after a failed autofill).

> Do **not** put your own OpenRouter key in Streamlit secrets for a public app: every visitor would spend your credits.
> If you need that later, add authentication and a per-user usage cap first.
> Also avoid enabling LangSmith tracing on a public deployment: traces would contain claim details.

## Class build order (2 hours)

| Time | Build | Files |
|---|---|---|
| 0:00 | Frame: schema as Pydantic + `ClaimState` | `schemas.py`, `graph/state.py` |
| 0:20 | Validation with inline alerts | `validation.py`, `ui/form.py` |
| 0:50 | The graph: nodes, edges, builder | `graph/nodes.py`, `edges.py`, `builder.py` |
| 1:20 | AI agents + key handling | `agents/`, `ui/sidebar.py` |
| 1:40 | Push to GitHub, deploy, test live | this README |

## Change requests to practise (Engage phase)

* Add a registry field (e.g. *discharge_type*) in `config.py`, and watch the form and validation pick it up.
* Add a new rule node (e.g. flag claims above 80% of the sum insured) and a conditional edge to it.
* Add a new document rule in `config.py` (`CATEGORY_REQUIRED_DOCS`).

## Security hardening

What the code now checks **automatically**. These are layered checks that fail safe (retry, then a template, then an
officer); they reduce risk, they are **not a guarantee** and not a substitute for review by a claims officer.

* **AI text is checked before it is stored** (`agents/communicator.py::check_communications`, `textsafe.py`): hidden,
  control, zero-width, bidi and surrogate characters are stripped; text is normalised (NFKC, homoglyphs, case,
  letter-spaced words) before looking for approval/promise wording (*approve, guarantee, sanction, granted, will be paid ...*);
  **every** rupee amount must equal one of the given figures (exact match, not substring); links, email/UPI ids, phone
  numbers, markdown links/images and HTML are rejected; both texts need the claim id; the letter needs the estimate and
  every missing document; the officer summary needs every review reason; length is capped. After two failed drafts a
  deterministic template is used.
* **Claimant free text is untrusted**: cleaned to one line, **quoted** in template letters, and withheld (with an
  officer review reason) if it looks like amounts, links, approval wording or instructions. Names must look like names
  (letters, up to 5 words); digits are ASCII-only; zero-width-only values are treated as empty.
* **Business rules**: accident / patient sections are mandatory when their condition holds; patient date of birth and
  MLC/FIR year must be plausible; an accident claim inside the waiting period or with a planned admission is not
  rejected but is routed to an officer with an audit entry; a large share of the claim in the free-form *other*
  category is flagged (room rent is capped, *other* is not); claims at or above the high-value threshold go to an
  officer; co-pay uses integer half-up rounding; duplicate bills are caught across number styles
  (`RM-1001` = `RM1001` = `rm/01001`) and by identical category + date + amount; claim ids use `secrets`.
* **`final_checks`** re-verifies money (non-negative, payable <= claimed and <= sum insured), status vs missing
  documents, and the final texts, and downgrades the claim to officer review if anything disagrees.
* **UI text safety** (`ui/safe.py`): LLM, provider, exception and autofill text is cleaned, key-redacted, capped and
  escaped before it appears in a banner, so pasted Markdown cannot render links, images or colour. Autofill values are
  type-checked and clamped to the form limits, never leave the declaration ticked, and the flash message lists the
  fields that were overwritten. The extractor's "missing information" is mapped to known field labels only.
* **Cost and abuse**: `max_tokens` is capped on the LLM client; each session gets at most 10 AI actions with a 3 second
  gap (`config.py`), tracked in `st.session_state` and **not** reset by *New claim*. This slows accidental or scripted
  use from one session; it is not authentication and does not stop a new browser session.
* **Deployment**: no CORS/XSRF opt-outs in the devcontainer, XSRF protection on, error details hidden, dependencies
  governed by `requirements.txt` (ranges) with the tested versions recorded in `requirements-lock.txt`.

## Known limitations

This is a **mock / demo** application. The following cannot be fixed without real back-end systems and are
deliberately **not** claimed to be solved:

* **No claimant-to-policy binding and no policy-number enumeration protection**: anyone who types a valid demo policy
  number can file against it, and the form reveals whether a number exists. A real system needs authentication and
  rate limiting at the policy-admin boundary.
* **No cross-claim ledger**: cumulative sum insured, claims split into several smaller ones (the high-value check
  only sees one claim), and re-submission of the same bills as a new claim are not detected. Nothing is persisted.
* **Documents are self-declared checkboxes**: nothing is uploaded or verified; an officer must check the real documents.
* **Network-hospital status and TPA reference numbers are not verified** against any registry.
* **MLC / FIR numbers are not verified** (only their format and year are checked), so the accident waiting-period
  exemption still depends on an officer's check.
* **Mock policy store and demo data only**: do not enter real personal or medical information. If you use the AI
  features, the name, diagnosis and any pasted text are sent to the AI provider (OpenRouter) under your own key.
* The per-session AI throttle is per browser session, not per user or per IP.
* Text checks are heuristics: unusual spellings of promise wording that the normaliser does not fold could still pass
  the automatic check, which is why every claim still needs officer review.
