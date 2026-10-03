"""Text hygiene shared by the schemas, the communicator and the UI. Pure Python: no Streamlit.

Everything that was typed by a visitor or written by an LLM is UNTRUSTED text. This module gives
three tools for it:
  * clean_line / clean_block   remove control, zero-width, bidi and surrogate characters (NFKC first)
  * fold                       a lossy "what does this read like" form (homoglyphs, case, accents)
  * scan_text                  deterministic findings: approval wording, links, contact details,
                               markup and rupee amounts that are not in an allow-list
These are automatic checks, not guarantees: they make hostile text fail safe (retry, then template).
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Iterable, List, Optional, Set

# ------------------------------------------------------------------ cleaning
_DROP_CATEGORIES = {"Cf", "Cs", "Co", "Cn"}          # format (zero-width, bidi, soft hyphen), surrogate, private, unassigned
_INVISIBLE_LETTERS = {"ㅤ", "ᅟ", "ᅠ", "ﾠ", "⠀", "͏"}   # blank-looking but not Cf
_LINE_SEPARATORS = {" ", " ", "\x85", "\x0b", "\x0c"}


def _is_hidden(ch: str) -> bool:
    cat = unicodedata.category(ch)
    if cat == "Cc":
        return ch not in "\n\r\t"
    return cat in _DROP_CATEGORIES or ch in _INVISIBLE_LETTERS


def has_hidden_chars(text: str) -> bool:
    """True if the text contains control, zero-width, bidi, surrogate or other invisible characters."""
    return any(_is_hidden(ch) for ch in str(text or ""))


def strip_invisible(text: str, keep_newlines: bool = False) -> str:
    """Drop hidden characters (see has_hidden_chars). Tabs become spaces; line breaks (and Unicode line/paragraph
    separators) become newlines if keep_newlines, otherwise spaces."""
    out: List[str] = []
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    for ch in text:
        if ch == "\n" or ch in _LINE_SEPARATORS:
            out.append("\n" if keep_newlines else " ")
        elif ch == "\t":
            out.append(" ")
        elif not _is_hidden(ch):
            out.append(ch)
    return "".join(out)


def clean_line(text: object, max_len: Optional[int] = None) -> str:
    """One clean line: NFKC, hidden characters removed, all whitespace collapsed, optionally capped."""
    s = strip_invisible(unicodedata.normalize("NFKC", "" if text is None else str(text)))
    s = re.sub(r"\s+", " ", s).strip()
    if max_len is not None and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def clean_block(text: object, max_len: Optional[int] = None) -> str:
    """Like clean_line but keeps (at most two consecutive) line breaks, for multi-line letters."""
    s = strip_invisible(unicodedata.normalize("NFKC", "" if text is None else str(text)), keep_newlines=True)
    s = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    if max_len is not None and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def word_count(text: str) -> int:
    return len(str(text or "").split())


# ------------------------------------------------------------------ folding (for matching only)
_HOMOGLYPHS = str.maketrans({
    # Cyrillic lower-case look-alikes (text is casefolded first)
    "а": "a", "в": "b", "с": "c", "ԁ": "d", "е": "e", "һ": "h", "і": "i", "ј": "j", "к": "k", "м": "m",
    "н": "h", "о": "o", "р": "p", "ԛ": "q", "ѕ": "s", "т": "t", "у": "y", "х": "x", "ԝ": "w", "ӏ": "l",
    "ѵ": "v", "ү": "y",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "γ": "y", "ω": "w", "η": "n", "μ": "u",
    # Latin oddities
    "ı": "i", "ɑ": "a", "ƅ": "b", "ɡ": "g", "ʜ": "h", "ⅰ": "i", "ⅼ": "l", "ǀ": "l",
})
_LEET = str.maketrans("013457@$", "oieastas")
_SCRIPT_LOOKALIKE_PREFIXES = ("CYRILLIC", "GREEK")


def has_lookalike_script(text: str) -> bool:
    """True if the text mixes Cyrillic/Greek letters into otherwise Latin text (homoglyph tricks)."""
    for ch in str(text or ""):
        if ch.isalpha() and not ch.isascii() and unicodedata.name(ch, "").startswith(_SCRIPT_LOOKALIKE_PREFIXES):
            return True
    return False


def fold(text: object) -> str:
    """Lower-case, accent-free, homoglyph-folded, ASCII-digit form of the text. For MATCHING only."""
    s = unicodedata.normalize("NFKD", clean_block(text))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn").casefold().translate(_HOMOGLYPHS)
    return "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() and not ch.isascii() else ch for ch in s)


def norm_for_compare(text: object) -> str:
    """Whitespace-collapsed, case-insensitive form used to ask 'does this text contain that phrase'."""
    return clean_line(text).casefold()


# ------------------------------------------------------------------ banned (promise-like) wording
_BANNED_STEMS = ("approv", "guarant", "sanction")
_BANNED_PATTERNS = [
    (re.compile(r"(?<![a-z])(?:pre)?approv"), "approve/approved/approval"),
    (re.compile(r"(?<![a-z])guarant"), "guarantee/guaranteed"),
    (re.compile(r"(?<![a-z])sanction"), "sanction/sanctioned"),
    (re.compile(r"(?<![a-z])granted(?![a-z])"), "granted"),
    (re.compile(r"(?<![a-z])assured(?![a-z])"), "assured"),
    (re.compile(r"(?<![a-z])(?:promise|promised|promises|entitled|definitely)(?![a-z])"), "promise/entitled"),
    (re.compile(r"(?<![a-z])will be (?:paid|reimbursed|credited|settled|released|transferred|disbursed)(?![a-z])"),
     "payment promise"),
    (re.compile(r"(?<![a-z])has been (?:paid|settled|credited|released|disbursed|cleared)(?![a-z])"),
     "payment completed"),
]


def _split_stem_hits(folded: str) -> Set[str]:
    """Catch stems chopped up by spaces/punctuation: 'a p p r o v e d', 'ap proved', 'a.p.p.r.o.v.e.d'."""
    tokens = re.findall(r"[a-z]+", folded)
    hits: Set[str] = set()
    for i, first in enumerate(tokens):
        for stem in _BANNED_STEMS:
            if len(first) >= len(stem) or not stem.startswith(first):
                continue
            joined = first
            for nxt in tokens[i + 1:i + 9]:
                joined += nxt
                if len(joined) >= len(stem):
                    break
            if joined.startswith(stem):
                hits.add(stem + "*")
    return hits


def find_banned(text: object) -> List[str]:
    """Promise-like wording ('approved', 'guaranteed', 'sanctioned', ...), robust to unicode/spacing tricks."""
    folded = fold(text)
    hits: Set[str] = set()
    for variant in (folded, folded.translate(_LEET)):
        for pattern, label in _BANNED_PATTERNS:
            if pattern.search(variant):
                hits.add(label)
        hits |= _split_stem_hits(variant)
    return sorted(hits)


# ------------------------------------------------------------------ links, contact details, markup
_TLDS = ("com|net|org|in|co|io|me|app|xyz|info|biz|ly|gl|cc|tk|ml|ga|cf|gq|top|site|online|click|link|live|shop|dev|"
         "page|ai|us|uk|ru|cn|de|edu|gov|to")
_URL_RE = re.compile(
    r"[a-z][a-z0-9+.\-]*://|(?<![a-z])www\d{0,3}\.|(?<![a-z0-9])(?:javascript|data|vbscript|file|mailto|tel|upi|sms|intent):"
    rf"|(?<![a-z0-9@.\-])[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9\-]+)*\.(?:{_TLDS})(?![a-z0-9])"
)
_MARKDOWN_RE = re.compile(r"\]\s*[\(\[]|!\s*\[|`|(?<![a-z0-9]):[a-z_]+\[|\[[^\]]*\]:\s*\S")
_HTML_RE = re.compile(r"<\s*/?\s*[a-z!?]|&(?:#x?[0-9a-f]+|[a-z]+);")
_PHONE_RE = re.compile(r"(?<![0-9])\+?[0-9](?:[\s\-.()]{0,2}[0-9]){9,}(?![0-9])")
_WORDY_RE = re.compile(
    r"(?<![a-z])(?:upi|neft|imps|rtgs|ifsc|paytm|gpay|phonepe|bhim|bitcoin|crypto|western union|click here|"
    r"log ?in|sign ?in|password|otp|cvv)(?![a-z])"
)
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


# ------------------------------------------------------------------ rupee amounts
_NUM = r"[0-9](?:[0-9,]*[0-9])?(?:\.[0-9]+)?"
_CUR = r"(?:₹|(?<![a-z])rs(?![a-z])\.?|(?<![a-z])inr(?![a-z])|(?<![a-z])rupees?(?![a-z]))"
_AMT_BEFORE = re.compile(rf"{_CUR}\s*[:\-]?\s*({_NUM})")
_AMT_AFTER = re.compile(rf"(?<![0-9,.])({_NUM})\s*(?:₹|/-|(?<![a-z])rs(?![a-z])|(?<![a-z])inr(?![a-z])|(?<![a-z])rupees?(?![a-z]))")
_BARE_AMT = re.compile(rf"(?<![\w.,])(?:[0-9]{{1,3}}(?:,[0-9]{{2,3}})+(?:\.[0-9]+)?|[0-9]{{5,}})(?![\w])")
_MAGNITUDE_RE = re.compile(r"(?<![a-z])(?:lakhs?|lacs?|crores?|million|billion|thousand|hundred)(?![a-z])")
_NUMBER_WORDS = ("zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                 "sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety")
_WORD_AMOUNT_RE = re.compile(rf"(?<![a-z])(?:{_NUMBER_WORDS})(?:[\s\-]+(?:and|{_NUMBER_WORDS}))*[\s\-]+rupees?(?![a-z])")
_FOREIGN_RE = re.compile(r"[$€£¥]|(?<![a-z])(?:usd|eur|gbp|dollars?|euros?|pounds?)(?![a-z])")


def _to_decimal(raw: str) -> Optional[Decimal]:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def _masked(folded: str, mask: Iterable[str]) -> str:
    for token in mask:
        if token:
            folded = folded.replace(fold(token), " ")
    return folded


def rupee_amounts(text: object, mask: Iterable[str] = ()) -> List[Decimal]:
    """EVERY amount written with a rupee marker (₹ / Rs / Rs. / INR / rupees), boundary-aware.
    '₹3,500,000' is 3500000, never '3500'. `mask` strings (e.g. the claim id) are ignored."""
    folded = _masked(fold(text), mask)
    found: List[Decimal] = []
    for pattern in (_AMT_BEFORE, _AMT_AFTER):
        for m in pattern.finditer(folded):
            value = _to_decimal(m.group(1))
            if value is not None:
                found.append(value)
    return found


def _amount_findings(folded: str, allowed: Set[Decimal], check_bare: bool) -> List[str]:
    findings: List[str] = []
    seen: Set[Decimal] = set()
    for pattern in (_AMT_BEFORE, _AMT_AFTER):
        for m in pattern.finditer(folded):
            value = _to_decimal(m.group(1))
            if value is None or value in seen:
                continue
            seen.add(value)
            if value not in allowed:
                findings.append(f"amount {m.group(1)}, which is not one of the given figures")
    if _MAGNITUDE_RE.search(folded) or _FOREIGN_RE.search(folded) or _WORD_AMOUNT_RE.search(folded):
        findings.append("unsupported amount or currency wording (amounts in words, lakh/crore/million, foreign currency)")
    if check_bare:
        rest = _AMT_AFTER.sub(" ", _AMT_BEFORE.sub(" ", folded))
        for m in _BARE_AMT.finditer(rest):
            value = _to_decimal(m.group(0))
            if value is not None and value not in allowed:
                findings.append(f"number {m.group(0)}, which is not one of the given figures")
    return findings


_INJECTION_RE = re.compile(
    r"(?<![a-z])(?:ignore (?:all |any |the )?(?:previous|above|prior|earlier)|new instructions?|system prompt|"
    r"(?:system|assistant|officer|admin|developer|supervisor)\s*:|status\s*:|(?:estimated )?payable(?: estimate| amount)?\s*:?\s*(?:is|=)|"
    r"pre-?verified|already verified|claim (?:status|decision)|override)(?![a-z])"
)


def find_injection_markers(text: object) -> List[str]:
    """Role/status/instruction look-alikes ('OFFICER: pre-verified', 'Status: ready_for_review', 'ignore previous ...')
    in text a claimant typed. Only used for CLAIMANT-typed fields, never for our own sentences."""
    return ["instruction-like or status-like wording"] if _INJECTION_RE.search(fold(text)) else []


def scan_text(text: object, *, allowed_amounts: Optional[Set[Decimal]] = None, mask: Iterable[str] = (),
              check_bare_numbers: bool = False) -> List[str]:
    """Deterministic findings for untrusted text (empty list = nothing suspicious found).

    allowed_amounts: rupee amounts that may appear (None/empty = no rupee amount is allowed at all).
    mask: exact strings to ignore (the claim id). check_bare_numbers: also vet unmarked big numbers.
    """
    raw = "" if text is None else str(text)
    folded = _masked(fold(raw), mask)
    allowed = allowed_amounts or set()
    findings: List[str] = []
    banned = find_banned(raw)
    if banned:
        findings.append("approval/promise wording (" + ", ".join(banned) + ")")
    if has_lookalike_script(raw):
        findings.append("look-alike (Cyrillic/Greek) letters")
    if _URL_RE.search(folded):
        findings.append("a web link or URL scheme")
    if "@" in folded:
        findings.append("an email address, UPI id or handle")
    if _MARKDOWN_RE.search(folded):
        findings.append("markdown link/image/code syntax")
    if _HTML_RE.search(folded):
        findings.append("HTML")
    if _WORDY_RE.search(folded):
        findings.append("payment-instruction or login wording")
    no_dates = _ISO_DATE_RE.sub(" ", folded)
    no_amounts = _AMT_AFTER.sub(" ", _AMT_BEFORE.sub(" ", no_dates))
    if _PHONE_RE.search(no_amounts):
        findings.append("a phone number")
    findings.extend(_amount_findings(folded, allowed, check_bare_numbers))
    return findings
