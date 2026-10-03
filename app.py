"""SecureCare Claims Assistant: Streamlit entry point.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud -> main file path: app.py

This file is only the UI shell. The product logic lives in securecare/:
    securecare/graph/    <- the LangGraph workflow (the core unit)
    securecare/agents/   <- LLM agents used by the graph and the form
    securecare/ui/       <- Streamlit widgets
"""
import streamlit as st

from securecare.agents.llm import build_llm
from securecare.config import APP_TAGLINE, APP_TITLE
from securecare.graph import build_claim_graph, build_initial_state, run_claim
from securecare.security import looks_like_openrouter_key, redact_secrets
from securecare.ui.autofill import render_autofill
from securecare.ui.form import load_sample, render_claim_form, reset_form
from securecare.ui.results import render_result
from securecare.ui.sidebar import flush_key_after_use, get_api_key, render_sidebar
from securecare.ui.workflow_tab import render_about_tab, render_workflow_tab

st.set_page_config(page_title=APP_TITLE, page_icon="🏥", layout="wide")

settings = render_sidebar()

st.title(f"🏥 {APP_TITLE}")
st.caption(APP_TAGLINE)

flash = st.session_state.pop("flash", None)
if flash:
    getattr(st, flash["kind"])(flash["text"])

tab_claim, tab_graph, tab_about = st.tabs(["📝 New Claim", "🧭 Workflow graph", "ℹ️ About & demo data"])

with tab_claim:
    t1, t2, t3, _ = st.columns([1.1, 1.3, 1, 3])
    t1.button("Load sample", on_click=load_sample, args=("simple",), help="Illness claim, all details filled in")
    t2.button("Load accident sample", on_click=load_sample, args=("accident",), help="Accident claim, FIR copy missing")
    t3.button("New claim", on_click=reset_form)

    render_autofill(settings)
    result = render_claim_form()

    st.divider()
    if st.button("Submit claim", type="primary"):
        st.session_state["show_all_errors"] = True
        if not result.ok:
            st.session_state["flash"] = {"kind": "error", "text":
                                         f"Please fix {len(result.errors)} highlighted field(s) before submitting."}
            st.rerun()
        else:
            llm, notice = None, None
            api_key = get_api_key()
            if api_key and looks_like_openrouter_key(api_key):
                llm = build_llm(api_key, settings.model)        # fresh client, never cached
            elif api_key:
                notice = "The key you entered does not look like an OpenRouter key, so AI drafting was skipped."
            api_key = ""                                        # drop our reference to the secret

            try:
                graph = build_claim_graph(llm)
                with st.spinner("Running the claim workflow…"):
                    run = run_claim(graph, build_initial_state(result.submission, use_llm=llm is not None))
                st.session_state["last_run"] = run
            except Exception as exc:  # noqa: BLE001
                st.error(f"Workflow error: {redact_secrets(f'{type(exc).__name__}: {exc}')[:250]}")
                run = None
            finally:
                llm = graph = None
                flush_key_after_use(settings)                   # wipe the key widget after use

            if run is not None:
                st.session_state["flash"] = {"kind": "warning" if notice else "success",
                                             "text": notice or "Claim processed. Scroll down for the result."}
                st.rerun()

    if "last_run" in st.session_state:
        render_result(st.session_state["last_run"])

with tab_graph:
    render_workflow_tab()

with tab_about:
    render_about_tab()
