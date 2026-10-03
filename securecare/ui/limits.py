"""Per-session throttle for AI actions (autofill click, AI-drafted submission).

Counters live in st.session_state under keys that "New claim" does not touch, so a visitor cannot reset
the budget by resetting the form. This limits accidental or abusive provider calls from one session; it
is NOT authentication or a global rate limit (a new browser session starts a new budget).
"""
from __future__ import annotations

import math
import time
from typing import MutableMapping, Optional

import streamlit as st

from securecare.config import MAX_AI_CALLS_PER_SESSION, MIN_SECONDS_BETWEEN_AI_CALLS

AI_CALLS_KEY = "ai_calls_used"
AI_LAST_CALL_KEY = "ai_last_call_at"


def ai_blocked_message(state: Optional[MutableMapping] = None, now: Optional[float] = None) -> Optional[str]:
    """None if an AI action is allowed now, otherwise a friendly reason."""
    state = st.session_state if state is None else state
    if state.get(AI_CALLS_KEY, 0) >= MAX_AI_CALLS_PER_SESSION:
        return (f"You have used the {MAX_AI_CALLS_PER_SESSION} AI actions allowed in this session. "
                "The claim workflow still works and uses template letters.")
    now = time.monotonic() if now is None else now
    last = state.get(AI_LAST_CALL_KEY)
    if last is not None and 0 <= now - last < MIN_SECONDS_BETWEEN_AI_CALLS:
        wait = math.ceil(MIN_SECONDS_BETWEEN_AI_CALLS - (now - last))
        return f"Please wait {wait} second(s) before the next AI action."
    return None


def record_ai_call(state: Optional[MutableMapping] = None, now: Optional[float] = None) -> None:
    """Count one AI action (call this right before the provider is contacted)."""
    state = st.session_state if state is None else state
    state[AI_CALLS_KEY] = state.get(AI_CALLS_KEY, 0) + 1
    state[AI_LAST_CALL_KEY] = time.monotonic() if now is None else now
