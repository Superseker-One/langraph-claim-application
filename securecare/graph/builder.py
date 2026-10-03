"""Assemble and compile the claim workflow. THIS is the file to read first.

                START
                  |
               intake
                  |
            lookup_policy
              /       \\
        (reject)     (continue)
           |             |
     reject_claim   compute_estimate
           |             |
          END      check_documents
                         |
                   decide_status
                     /        \\
                (draft)      (skip: no API key)
                   |             |
        draft_communications   write_template_communications
              |      ^           |
        verify_communications    |
          |        |(retry)      |
          |--------'             |
          v                      |
       final_checks <------------'
            |
           END
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from securecare.graph import edges, nodes
from securecare.graph.state import ClaimState


def build_claim_graph(llm: Optional[Any] = None):
    """Build and compile the graph.

    `llm` is injected here (dependency injection). Pass None to run without any AI:
    the deterministic workflow still works and the letters come from templates.
    Build a NEW graph per submission: it is cheap, and it means the LLM client (which holds
    the visitor's API key) is never shared between sessions.
    """
    builder = StateGraph(ClaimState)

    # ---- nodes
    builder.add_node("intake", nodes.intake)
    builder.add_node("lookup_policy", nodes.lookup_policy)
    builder.add_node("reject_claim", nodes.reject_claim)
    builder.add_node("compute_estimate", nodes.compute_estimate)
    builder.add_node("check_documents", nodes.check_documents)
    builder.add_node("decide_status", nodes.decide_status)
    builder.add_node("draft_communications", nodes.make_draft_communications(llm))
    builder.add_node("write_template_communications", nodes.write_template_communications)
    builder.add_node("verify_communications", nodes.verify_communications)
    builder.add_node("final_checks", nodes.final_checks)

    # ---- edges
    builder.add_edge(START, "intake")
    builder.add_edge("intake", "lookup_policy")
    builder.add_conditional_edges(
        "lookup_policy", edges.route_after_policy,
        {"reject": "reject_claim", "continue": "compute_estimate"},
    )
    builder.add_edge("reject_claim", END)
    builder.add_edge("compute_estimate", "check_documents")
    builder.add_edge("check_documents", "decide_status")
    builder.add_conditional_edges(
        "decide_status", edges.route_llm,
        {"draft": "draft_communications", "skip": "write_template_communications"},
    )
    builder.add_edge("write_template_communications", "final_checks")
    builder.add_edge("draft_communications", "verify_communications")
    builder.add_conditional_edges(
        "verify_communications", edges.route_after_verify,
        {"done": "final_checks", "retry": "draft_communications"},
    )
    builder.add_edge("final_checks", END)

    return builder.compile()
