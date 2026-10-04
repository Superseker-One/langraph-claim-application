"""Render the compiled graph as Graphviz DOT, so Streamlit can draw it with st.graphviz_chart
(no internet / mermaid.ink needed). Colours follow the Supersek palette; every string here is a constant
or a node name from the compiled graph, never user input."""
from __future__ import annotations

# Supersek palette (see securecare/ui/brand.py)
_SURFACE, _LIGHT, _MUTED = "#393E40", "#DCDAD7", "#A7A29A"
_PRIMARY, _MID = "#221CD2", "#7874EA"


def graph_to_dot(compiled_graph) -> str:
    g = compiled_graph.get_graph()
    lines = [
        "digraph G {", "rankdir=TB;", 'bgcolor="transparent";',
        f'node [shape=box, style="rounded,filled", fillcolor="{_SURFACE}", color="{_MID}", fontcolor="{_LIGHT}", '
        'fontname="Inter, Helvetica, Arial", fontsize=12];',
        f'edge [color="{_MUTED}", fontcolor="{_MUTED}", fontname="Inter, Helvetica, Arial", fontsize=10];',
    ]
    for node_id in g.nodes:
        if node_id in ("__start__", "__end__"):
            label = "START" if node_id == "__start__" else "END"
            lines.append(f'"{node_id}" [label="{label}", shape=oval, fillcolor="{_PRIMARY}", color="{_MID}", '
                         f'fontcolor="{_LIGHT}"];')
        else:
            lines.append(f'"{node_id}" [label="{node_id}"];')
    for edge in g.edges:
        style = ' [style=dashed, label="%s"]' % edge.data if edge.conditional and edge.data else (
            " [style=dashed]" if edge.conditional else "")
        lines.append(f'"{edge.source}" -> "{edge.target}"{style};')
    lines.append("}")
    return "\n".join(lines)
