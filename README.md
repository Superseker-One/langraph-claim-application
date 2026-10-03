# SekureCare Claims Assistant (beta)

A Streamlit app for **health-insurance reimbursement claim intake**, powered by a **LangGraph** workflow.
Built as the class project for the *FDE LangGraph* sessions: collect → validate → run the graph → deploy.

* Validated form (inline alerts on every field, conditional sections, repeating bill rows)
* LangGraph workflow: eligibility → calculation → document check → status → AI-drafted letters → verification
* Optional AI (via [OpenRouter](https://openrouter.ai)): email autofill + letters. **Works without a key** (template letters)
* Bring-your-own-key, **never shared between visitors** (see [Key safety](#key-safety))

## Project structure

```
securecare-claims/
├── app.py                       # Streamlit entry point (UI shell only)
├── requirements.txt
├── pytest.ini
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example     # the app needs NO secrets
├── securecare/
│   ├── config.py                # constants, business-rule thresholds, EXTENSIBLE FIELD REGISTRY
│   ├── schemas.py               # Pydantic models + field validators (the FORM schema)
│   ├── validation.py            # form -> {widget_key: error}; business rules vs policy store
│   ├── security.py              # key-shape check, secret redaction
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
│   │   ├── llm.py               #   the ONLY place the LLM client is created (OpenRouter)
│   │   ├── extractor.py         #   email text -> structured fields
│   │   └── communicator.py      #   officer summary + claimant letter (+ verification, fallback)
│   └── ui/                      # Streamlit widgets
│       ├── sidebar.py           #   API-key box (the key-safety logic lives here)
│       ├── form.py              #   claim form bound to session_state
│       ├── autofill.py          #   "paste your email" feature
│       ├── results.py           #   result screen
│       └── workflow_tab.py      #   live drawing of the compiled graph + About tab
└── tests/                       # 38 tests: validation, graph, security, end-to-end UI
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
| Evals / "deployable is not deployed" | `verify_communications`, `final_checks` |

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
4. **Advanced settings:** choose Python **3.11 or 3.12**. No secrets are required.
5. **Deploy.** The first build installs `requirements.txt`; later pushes to `main` redeploy automatically.

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

**Flush after use:** after every AI action the key box is rotated to a brand-new widget (a `key_nonce`),
so Streamlit discards the old widget and the secret in it. The visitor can opt in to *Keep key for this
browser session*, and a *Clear key now* button always works.

These behaviours are covered by tests in `tests/test_app_smoke.py` (two simulated visitors on one app,
key absent from the second visitor's session and from `os.environ`, key erased after submit).

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

## Beta limitations

Demo data only (do not enter real personal or medical information). The policy store is a mock. Nothing is persisted.
