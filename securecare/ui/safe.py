"""Safe display of UNTRUSTED text (LLM output, provider/exception messages, pasted data) in Streamlit.

st.success/error/warning/info/markdown/caption all render MARKDOWN, so a string such as
'[Verify your claim](http://x) ![px](http://y/p.png) :red[**URGENT**]' becomes a live link, an image
beacon and coloured text inside a trusted-looking banner. Run every such string through safe_text()
first. Pass RAW (unescaped) text: this module escapes it once.
"""
from __future__ import annotations

import re
from typing import Optional

import streamlit as st

from securecare.security import describe_exception, redact_secrets
from securecare.textsafe import clean_line

FLASH_KINDS = ("success", "info", "warning", "error")
_ZWSP = "​"
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~$&{}])")


def safe_text(value: object, max_len: int = 250, secret: Optional[str] = None) -> str:
    """One line of plain text that Streamlit's markdown renderer shows literally.

    Order matters: clean (control/zero-width/bidi/surrogates removed, one line) -> redact keys (while the
    token is still contiguous) -> cap the length -> escape. Escaping covers markdown/HTML punctuation,
    LaTeX ($) and the three auto-link / shortcode openers (':' before a name or '[' or '//', '@', 'www.'),
    which are defused with an invisible zero-width space because backslashes do not stop emoji shortcodes
    (':smile:') or auto-links.
    """
    text = clean_line(redact_secrets("" if value is None else str(value), secret))
    text = redact_secrets(text, secret)
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    text = _MD_SPECIAL.sub(r"\\\1", text)
    text = re.sub(r"^(\d+)([.)])", r"\1\\\2", text)           # '1. ' would start an ordered list
    text = re.sub(r"^([-+])", r"\\\1", text)                    # '- ' would start a bullet list
    text = re.sub(r":(?=[A-Za-z0-9_+\-\[/\\])", ":" + _ZWSP, text)   # :emoji: / :red[...] / http://
    text = text.replace("@", "@" + _ZWSP)                       # email auto-links
    return re.sub(r"(?i)\bwww\.", lambda m: m.group(0) + _ZWSP, text)


def safe_exception(exc: BaseException, secret: Optional[str] = None, max_len: int = 250) -> str:
    """'ErrorType: message' for display: key-redacted (exact value + key-shaped), capped, escaped."""
    return safe_text(describe_exception(exc, secret, max_len=max_len), max_len, secret)


# ------------------------------------------------------------------ flash message across st.rerun()
def set_flash(kind: str, text: object, secret: Optional[str] = None) -> None:
    """Queue a banner for the next run. The text is escaped once, here."""
    st.session_state["flash"] = {"kind": kind if kind in FLASH_KINDS else "info",
                                 "text": safe_text(text, 600, secret)}


def show_flash() -> None:
    """Show (and clear) the queued banner. Only whitelisted kinds are dispatched to st.*."""
    flash = st.session_state.pop("flash", None)
    if isinstance(flash, dict) and flash.get("kind") in FLASH_KINDS and isinstance(flash.get("text"), str):
        getattr(st, flash["kind"])(flash["text"])
