"""SekureCare Claims Assistant: Streamlit entry point.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud -> main file path: app.py

This file is only the UI shell. The product logic lives in securecare/:
    securecare/graph/    <- the LangGraph workflow (the core unit)
    securecare/agents/   <- LLM agents used by the graph and the form
    securecare/ui/       <- Streamlit widgets
"""
from pathlib import Path

import streamlit as st

from securecare.agents.llm import build_llm
from securecare.config import APP_TAGLINE, APP_TITLE
from securecare.graph import build_claim_graph, build_initial_state, run_claim
from securecare.security import describe_exception, looks_like_openrouter_key
from securecare.ui.brand import apply_brand, render_brand_footer, render_brand_header
from securecare.ui.autofill import render_autofill
from securecare.ui.form import load_sample, render_claim_form, reset_form
from securecare.ui.limits import ai_blocked_message, record_ai_call
from securecare.ui.results import render_result
from securecare.ui.safe import set_flash, show_flash
from securecare.ui.sidebar import flush_key_after_use, get_api_key, render_sidebar
from securecare.ui.workflow_tab import render_about_tab, render_workflow_tab

st.set_page_config(page_title=APP_TITLE, page_icon=str(Path(__file__).parent / "assets" / "favicon.png"),
                   layout="wide")
apply_brand()                                   # Supersek theme: CSS constants only, never user text


def submit_claim(result, settings) -> None:
    """Run the workflow for a validated form and ALWAYS end with a rerun, so the key widget that the caller
    wipes in its `finally` is also dropped by the browser. Outcomes (success, notice, error) travel as a
    flash banner whose text is escaped by ui/safe.py."""
    if not result.ok:
        set_flash("error", f"Please fix {len(result.errors)} highlighted field(s) before submitting.")
        st.rerun()

    llm = graph = notice = None
    api_key = ""
    outcome = ("success", "Claim processed. Scroll down for the result.")
    try:
        api_key = get_api_key()
        if api_key and not looks_like_openrouter_key(api_key):
            notice = "The key you entered does not look like an OpenRouter key, so AI drafting was skipped."
        elif api_key and (blocked := ai_blocked_message()):
            notice = f"{blocked} AI drafting was skipped."
        elif api_key:
            record_ai_call()
            llm = build_llm(api_key, settings.model)            # fresh client, never cached; built INSIDE the try
        graph = build_claim_graph(llm)
        with st.spinner("Running the claim workflow…"):
            run = run_claim(graph, build_initial_state(result.submission, use_llm=llm is not None))
        st.session_state["last_run"] = run
        if notice:
            outcome = ("warning", notice)
    except Exception as exc:  # noqa: BLE001
        outcome = ("error", "Workflow error: " + describe_exception(exc, api_key))
    finally:
        llm = graph = None
        api_key = ""                                            # drop our reference to the secret
    set_flash(*outcome)
    st.rerun()


settings = render_sidebar()

render_brand_header()
st.title(APP_TITLE)
st.caption(APP_TAGLINE)

show_flash()

tab_claim, tab_graph, tab_about = st.tabs(["New claim", "Workflow graph", "About & demo data"])

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
        try:
            submit_claim(result, settings)
        finally:
            flush_key_after_use(settings)                       # wipe the key widget on EVERY path

    if "last_run" in st.session_state:
        render_result(st.session_state["last_run"])

with tab_graph:
    render_workflow_tab()

with tab_about:
    render_about_tab()

render_brand_footer()
