"""Render the compiled graph as Graphviz DOT, so Streamlit can draw it with st.graphviz_chart
(no internet / mermaid.ink needed)."""
from __future__ import annotations


def graph_to_dot(compiled_graph) -> str:
    g = compiled_graph.get_graph()
    lines = ["digraph G {", "rankdir=TB;", 'node [shape=box, style="rounded,filled", fillcolor="#EEF3FF", fontname="Helvetica"];']
    for node_id in g.nodes:
        if node_id in ("__start__", "__end__"):
            label = "START" if node_id == "__start__" else "END"
            lines.append(f'"{node_id}" [label="{label}", shape=oval, fillcolor="#DDEBDD"];')
        else:
            lines.append(f'"{node_id}" [label="{node_id}"];')
    for edge in g.edges:
        style = ' [style=dashed, label="%s"]' % edge.data if edge.conditional and edge.data else (
            " [style=dashed]" if edge.conditional else "")
        lines.append(f'"{edge.source}" -> "{edge.target}"{style};')
    lines.append("}")
    return "\n".join(lines)
