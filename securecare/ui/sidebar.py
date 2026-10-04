"""Sidebar: OpenRouter key entry + model choice.

HOW THE KEY IS KEPT PRIVATE (the leak you may have seen in other Streamlit apps comes from
doing one of the things in the 'never' list):

  NEVER  os.environ["OPENROUTER_API_KEY"] = key -> process-wide, every visitor shares it
  NEVER  @st.cache_resource / @st.cache_data      -> cached objects are shared by all sessions
  NEVER  a module-level global or a file          -> same process, same value for everyone
  NEVER  st.secrets for a visitor's own key       -> secrets are the app OWNER's, shared by all

  DO     keep the key in a widget bound to st.session_state (one dict per browser session)
  DO     pass it explicitly to the client (api_key=...) for ONE run, then drop the reference
  DO     flush it afterwards: we rotate the widget's key (nonce) AND delete the old value from
         session_state, in a `finally`, on every path (success, validation failure, exception)
"""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from securecare.config import DEFAULT_MODEL, MODEL_OPTIONS

KEY_PREFIX = "openrouter_key_"


@dataclass
class Settings:
    model: str
    keep_key: bool


def _widget_name() -> str:
    return f"{KEY_PREFIX}{st.session_state.get('key_nonce', 0)}"


def get_api_key() -> str:
    """The key typed by THIS session's visitor ('' if none)."""
    return (st.session_state.get(_widget_name()) or "").strip()


def flush_api_key() -> None:
    """Rotate the widget key (the browser discards the old widget) AND delete the old widget's value from
    session_state right now, so the secret is gone from the server-side session even on error paths where
    no rerun follows."""
    old = _widget_name()
    st.session_state["key_nonce"] = st.session_state.get("key_nonce", 0) + 1
    st.session_state.pop(old, None)


def flush_key_after_use(settings: Settings) -> None:
    """Wipe the key unless the visitor opted to keep it. Call it from a `finally`. Callers that flush a
    typed key follow with st.rerun() so the browser also drops the old (still filled) widget."""
    if not settings.keep_key and get_api_key():
        flush_api_key()


def render_sidebar() -> Settings:
    current = _widget_name()
    for stale in [k for k in st.session_state.keys() if k.startswith(KEY_PREFIX) and k != current]:
        del st.session_state[stale]            # make sure no old key survives in this session

    with st.sidebar:
        st.header("OpenRouter access")
        st.text_input(
            "OpenRouter API key", type="password", key=current, placeholder="sk-or-v1-...",
            help="Optional. Needed only for AI features: email autofill and AI-drafted letters.",
        )
        keep = st.checkbox(
            "Keep key for this browser session", value=False, key="keep_key",
            help="Off (recommended): the key is erased right after each AI action.",
        )
        st.button("Clear key now", on_click=flush_api_key, width="stretch")
        model = st.selectbox("Model", MODEL_OPTIONS, index=MODEL_OPTIONS.index(DEFAULT_MODEL), key="model")
        st.caption(
            "Your key exists only in **your** browser session. It is never saved, logged, cached "
            "or shared with other visitors, and it is erased after each use unless you tick *Keep*."
        )
        if not get_api_key():
            st.info("No key? The claim workflow still runs. Letters use templates instead of AI.")
    return Settings(model=model, keep_key=keep)
