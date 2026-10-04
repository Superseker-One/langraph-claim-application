"""'Workflow graph' tab: draws the REAL compiled graph, so the picture can never drift from the code."""
from __future__ import annotations

import streamlit as st

from securecare.graph import build_claim_graph
from securecare.graph.visualize import graph_to_dot
from securecare.services.policy_store import list_demo_policies


@st.cache_data(show_spinner=False)
def _workflow_dot() -> str:
    """Safe to cache: built with llm=None, so it contains no secrets and no per-user data."""
    return graph_to_dot(build_claim_graph(None))


def render_workflow_tab() -> None:
    st.subheader("The LangGraph workflow behind the form")
    st.graphviz_chart(_workflow_dot(), width="stretch")
    st.markdown(
        """
| Where | What lives there |
|---|---|
| `securecare/graph/state.py` | The business schema as `ClaimState` (static, dynamic, conditional, derived, extensible fields) |
| `securecare/graph/nodes.py` | One function per business step (deterministic rules + the one LLM node) |
| `securecare/graph/edges.py` | Routing functions for the conditional edges (business rules) |
| `securecare/graph/builder.py` | Wires nodes and edges into the compiled graph |
| `securecare/agents/` | LLM agents: `extractor` (email → fields) and `communicator` (letters) |
"""
    )


def render_about_tab() -> None:
    st.subheader("Demo policies")
    st.caption("A stand-in for the insurer's policy system. Dates are relative to today.")
    st.dataframe(
        [{"policy": p["policy_number"], "type": p["policy_type"], "sum insured": p["sum_insured"],
          "deductible": p["deductible"], "room limit/day": p["room_rent_limit_per_day"],
          "co-pay %": p["copay_percent"], "status": p["status"], "what it demonstrates": p["note"]}
         for p in list_demo_policies()],
        hide_index=True, width="stretch",
    )
    st.subheader("About this beta")
    st.markdown(
        """
- **Deterministic first:** validation, eligibility and money calculations are plain Python inside graph nodes.
- **AI only for language:** the model extracts details from your email and words the letters. AI text is automatically checked (wording, amounts, links) and falls back to a template, but an officer still reviews every claim.
- **No key, no problem:** without an API key the whole workflow runs and uses template letters.
- **Demo data only:** do not enter real personal or medical information. Nothing is stored by this app. With an API key, the name, diagnosis and pasted text go to the AI provider.
"""
    )
