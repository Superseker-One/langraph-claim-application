"""Run a compiled graph and capture a node-by-node trace (for the UI) in a single pass."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from securecare.graph.state import ClaimState


@dataclass
class RunResult:
    state: Dict[str, Any]
    trace: List[Dict[str, Any]] = field(default_factory=list)


def run_claim(graph, initial_state: ClaimState) -> RunResult:
    final_state: Dict[str, Any] = {}
    trace: List[Dict[str, Any]] = []
    for mode, chunk in graph.stream(initial_state, stream_mode=["updates", "values"]):
        if mode == "updates":
            for node_name, update in chunk.items():
                trace.append({"step": len(trace) + 1, "node": node_name,
                              "updated": sorted((update or {}).keys())})
        else:  # "values": full state after each step; the last one is the final state
            final_state = chunk
    return RunResult(state=final_state, trace=trace)
