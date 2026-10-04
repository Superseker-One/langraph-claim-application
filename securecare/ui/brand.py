"""Supersek brand layer: logo, typography and the few styles Streamlit's theme config cannot express.

The colours, fonts and radius live in .streamlit/config.toml ([theme]). This module adds what a theme
cannot: the SUPERSEK logo, uppercase tracked headings with the signature gradient, the dot-grid
background, tab/button/alert polish and a footer.

SECURITY: everything rendered here is a module-level CONSTANT. Never interpolate user, LLM or exception
text into these strings: this is the only place in the app that emits raw HTML/CSS.
"""
from __future__ import annotations

import streamlit as st

# ---- palette (supersek-brand-template.md)
PRIMARY_PURPLE = "#221CD2"
MID_PURPLE = "#7874EA"
DARK_BG = "#1F2223"
DARK_SURFACE = "#393E40"
LIGHT_TEXT = "#DCDAD7"
MUTED_TEXT = "#A7A29A"
SUBTLE_TEXT = "#8A8379"
WARM_ACCENT = "#E2DCC5"

BRAND_DOMAIN = "supersek.com"
BRAND_TAGLINE = "Deep-Dive Security Consulting"

# Subtle dot-grid texture for the dark background (an SVG pattern, so no CSS gradient is involved).
_DOT_GRID = (
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='28'%3E"
    "%3Ccircle cx='2' cy='2' r='1' fill='%23DCDAD7' fill-opacity='0.10'/%3E%3C/svg%3E\")"
)

_CSS = f"""
<style>
:root {{
  --ss-primary: {PRIMARY_PURPLE};
  --ss-mid: {MID_PURPLE};
  --ss-bg: {DARK_BG};
  --ss-surface: {DARK_SURFACE};
  --ss-light: {LIGHT_TEXT};
  --ss-muted: {MUTED_TEXT};
  --ss-subtle: {SUBTLE_TEXT};
  --ss-warm: {WARM_ACCENT};
}}

/* ---- canvas: dark background with the dot grid */
.stApp, [data-testid="stAppViewContainer"] {{
  background-color: var(--ss-bg);
  background-image: {_DOT_GRID};
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{ border-right: 1px solid var(--ss-surface); background-image: none; }}
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1200px; }}

/* ---- logo bar */
.ss-brandbar {{ display: flex; align-items: center; gap: 14px; margin: 0 0 1.4rem 0; flex-wrap: wrap; }}
.ss-logo {{
  font-weight: 800; font-size: 22px; text-transform: uppercase; letter-spacing: -0.01em;
  color: var(--ss-light); line-height: 1;
}}
.ss-dot {{
  display: inline-block; width: 7px; height: 7px; margin-left: 3px; border-radius: 50%;
  background: var(--ss-primary); box-shadow: 0 0 0 1px var(--ss-mid);
}}
.ss-tagline {{
  font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--ss-subtle);
  padding-left: 14px; border-left: 1px solid var(--ss-surface);
}}
@media (max-width: 640px) {{ .ss-tagline {{ border-left: none; padding-left: 0; }} }}
.ss-logo-sm {{ font-weight: 800; text-transform: uppercase; letter-spacing: -0.01em; color: var(--ss-light); }}

/* ---- typography: uppercase, wide tracking, signature gradient on the big headings */
h1, h2 {{ text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700; }}
h3 {{ letter-spacing: 0.02em; font-weight: 600; color: var(--ss-light); }}
h1 {{ font-size: clamp(1.7rem, 3.4vw, 2.7rem) !important; line-height: 1.15; }}
h1, h2 {{
  display: inline-block; max-width: 100%;
  background: linear-gradient(90deg, {LIGHT_TEXT}, {MID_PURPLE}, {PRIMARY_PURPLE}, {MID_PURPLE}, {LIGHT_TEXT});
  -webkit-background-clip: text; background-clip: text;
  -webkit-text-fill-color: transparent; color: transparent;
}}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {{
  color: var(--ss-muted); line-height: 1.6;
}}
[data-testid="stMarkdownContainer"] strong {{ color: var(--ss-light); }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
  color: var(--ss-subtle); letter-spacing: 0.01em;
}}

/* ---- tabs (Streamlit renders each as div[data-testid="stTab"] with a <p> label inside) */
[data-testid="stTab"] p {{
  text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; font-size: 0.82rem; color: var(--ss-muted);
}}
[data-testid="stTab"][aria-selected="true"] p {{ color: var(--ss-mid); }}
[data-testid="stTab"]:hover p {{ color: var(--ss-light); }}

/* ---- read-only text (letters, summaries): keep them clearly legible */
textarea:disabled {{
  color: var(--ss-light) !important; -webkit-text-fill-color: var(--ss-light) !important; opacity: 1 !important;
}}

/* ---- buttons: solid brand purple for the main action, quiet surface buttons otherwise */
[data-testid="stBaseButton-primary"] {{
  background-color: var(--ss-primary); border: 1px solid var(--ss-primary); color: var(--ss-light);
  text-transform: uppercase; letter-spacing: 0.06em; font-weight: 700;
}}
[data-testid="stBaseButton-primary"]:hover {{ background-color: var(--ss-mid); border-color: var(--ss-mid); color: var(--ss-bg); }}
[data-testid="stBaseButton-secondary"], [data-testid="stDownloadButton"] button {{
  background-color: var(--ss-surface); border: 1px solid var(--ss-surface); color: var(--ss-light); font-weight: 500;
}}
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stDownloadButton"] button:hover {{
  border-color: var(--ss-mid); color: var(--ss-mid);
}}

/* ---- panels, metrics, callouts */
[data-testid="stExpander"] details {{ border: 1px solid var(--ss-surface); background: transparent; }}
[data-testid="stExpander"] summary {{ color: var(--ss-light); font-weight: 500; }}
[data-testid="stMetricLabel"] {{ color: var(--ss-muted); text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.75rem; }}
[data-testid="stMetricValue"] {{ color: var(--ss-light); font-weight: 700; }}
[data-testid="stAlert"] {{ border-left: 3px solid var(--ss-mid); border-radius: 2px; }}
hr {{ border-color: var(--ss-surface) !important; }}

/* ---- footer */
.ss-footer {{
  margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--ss-surface);
  font-size: 12px; color: var(--ss-subtle); letter-spacing: 0.04em;
}}
.ss-footer a {{ color: var(--ss-mid); text-decoration: none; }}
</style>
"""

_HEADER = (
    '<div class="ss-brandbar">'
    '<span class="ss-logo">SUPERSEK<span class="ss-dot"></span></span>'
    f'<span class="ss-tagline">{BRAND_TAGLINE}</span>'
    "</div>"
)

_FOOTER = (
    '<div class="ss-footer">'
    '<span class="ss-logo-sm">SUPERSEK</span><span class="ss-dot"></span> &nbsp;·&nbsp; '
    f'<a href="https://{BRAND_DOMAIN}" target="_blank" rel="noopener noreferrer">{BRAND_DOMAIN}</a>'
    f" &nbsp;·&nbsp; {BRAND_TAGLINE}"
    "</div>"
)


def apply_brand() -> None:
    """Inject the brand stylesheet. Call once per run, right after st.set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_brand_header() -> None:
    st.markdown(_HEADER, unsafe_allow_html=True)


def render_brand_footer() -> None:
    st.markdown(_FOOTER, unsafe_allow_html=True)
