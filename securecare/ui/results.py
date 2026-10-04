"""Render the outcome of a graph run."""
from __future__ import annotations

import json

import streamlit as st

from securecare.config import DOCUMENT_LABELS
from securecare.formatting import format_inr
from securecare.graph.runner import RunResult
from securecare.textsafe import clean_block
from securecare.ui.safe import safe_text

STATUS_STYLE = {
    "ready_for_review": (st.success, "Ready for officer review"),
    "officer_review": (st.info, "Needs officer attention"),
    "pending_documents": (st.warning, "Pending: documents missing"),
    "rejected": (st.error, "Cannot be processed"),
}


def render_result(run: RunResult) -> None:
    s = run.state
    st.divider()
    st.header(f"Claim {s['claim_id']}")
    banner, label = STATUS_STYLE.get(s["status"], (st.info, safe_text(s["status"], 40)))
    banner(f"**{label}**")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total claimed", format_inr(s["total_claimed"]))
    c2.metric("Admissible", format_inr(s.get("admissible_amount", 0)))
    c3.metric("Estimated payable", format_inr(s["payable_estimate"]),
              help="An estimate only. The final amount is decided by a claims officer.")

    if "policy" in s and s["status"] != "rejected":
        with st.expander("How the estimate was calculated"):
            p = s["policy"]
            st.markdown(
                f"- Total bills: **{format_inr(s['total_claimed'])}**\n"
                f"- Room rent above the limit of {format_inr(p['room_rent_limit_per_day'])}/day "
                f"× {s['stay_days']} day(s): **−{format_inr(s['room_rent_excess'])}**\n"
                f"- Deductible: **−{format_inr(s['deductible_applied'])}**\n"
                f"- Co-pay ({p['copay_percent']}%): **−{format_inr(s['copay_amount'])}**\n"
                f"- Capped at sum insured {format_inr(p['sum_insured'])}\n"
                f"- **Estimated payable: {format_inr(s['payable_estimate'])}**"
            )

    if s.get("missing_documents"):
        st.warning("**Documents still needed:** "
                   + ", ".join(DOCUMENT_LABELS.get(d) or safe_text(d, 60) for d in s["missing_documents"]))
    if s.get("review_reasons"):
        st.markdown("**Why an officer will look at this:**\n"
                    + "\n".join(f"- {safe_text(r, 300)}" for r in s["review_reasons"]))

    st.subheader("Letter to claimant")
    st.text_area("claimant_letter", clean_block(s["claimant_letter"]), height=190, label_visibility="collapsed",
                 disabled=True)
    st.subheader("Summary for the claims officer")
    st.text_area("officer_summary", clean_block(s["officer_summary"]), height=130, label_visibility="collapsed",
                 disabled=True)

    source = ("🤖 AI-drafted and automatically checked. It still needs officer review before anything is sent."
              if s.get("comms_source") == "llm"
              else "📄 template text (no AI used). An officer still reviews the claim.")
    st.caption(source)
    if s.get("comms_error"):
        st.warning("The AI step failed, so template text was used. (" + safe_text(s["comms_error"], 300) + ")")

    with st.expander("🧭 Workflow trace: which nodes ran"):
        st.dataframe(
            [{"step": t["step"], "node": t["node"], "state keys updated": ", ".join(t["updated"])} for t in run.trace],
            hide_index=True, width="stretch",
        )
        st.code("\n".join(s.get("audit_log", [])), language="text")
    with st.expander("🔎 Final graph state (JSON)"):
        st.json(s)
    st.download_button("Download claim record (JSON)", json.dumps(s, indent=2, default=str),
                       file_name=f"{s['claim_id']}.json", mime="application/json")
