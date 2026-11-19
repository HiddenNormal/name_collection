# -*- coding: utf-8 -*-
r"""normalize.py -- canonical name-token normalizer for the name_dataset TTS pipeline.

Self-contained (Python stdlib only; no pandas/polars required). Import:

    import sys; sys.path.insert(0, r"E:\\Repos\\name_collection\\profile")
    from normalize import normalize_token, normalize_many, normalize_key, is_junk

    r = normalize_token("  O\u2019Brien ")   # dict
    r["norm"]   # -> "O'Brien"        <- display / feed to G2P
    r["key"]    # -> "o'brien"        <- canonical lexicon lookup key (JOIN ON THIS)
    r["script"] # -> "Latn"
    r["flags"]["is_junk"]  # -> False

Bulk use: apply to the UNIQUE raw tokens of the dataset (not all 491.6M rows),
then join rows back through the key.  Throughput ~ 300-500k tokens/s on one core
after the unique-token agg has been computed by duckdb/polars.

WHAT THIS DOES
--------------
The raw dataset has 491.6M rows ``first_name,last_name,gender,country_code``
(no header).  Tokens are dirty: case inconsistency, Unicode compatibility forms,
zero-width joiners, Arabic tatweel/kashida decoration (U+0640), Arabic tashkeel
(diacritics), box-drawing / dingbat decoration, leading underscores, curly
quotes, full-width letters, ragged whitespace, Hangul compatibility jamo.

For TTS the pipeline should synthesize ONE pronunciation per *unique* token
surface and join back on the canonical key.  This module produces, for each raw
string:

``norm``       normalized surface (human/G2P-readable).  Case preserved.
               NFKC + NFC, format/control/private chars removed, tatweel +
               Arabic diacritics stripped, whitespace collapsed, edge
               punctuation trimmed, quote/dash variants folded to ASCII,
               Hangul jamo composed, full-width folded.  PRINT THIS / G2P THIS.
``key``        canonical lexicon key = casefold(norm) + Arabic alef variants
               unified (U+0622/0623/0625/0671 -> U+0627).  JOIN ON THIS.
               Case-insensitive, so "NILZ"/"Nilz"/"nilz" collapse.
``key_loose``  extra-aggressive key: key minus all combining marks/punctuation/
               spaces (NFD -> strip non-alnum).  Supplementary dedup key, e.g.
               "O'Brien" -> "obrien", "José" -> "jose" while key keeps "josé".
``script``     dominant Unicode script of the letters: Latn/Cyrl/Arab/Hang/
               Hani/Hira/Kana/Grek/Hebr/Deva/Beng/Geor/Khmr/Thaa/Ethi/Thai/...
               or "other"; "" if the token has no letters.
``mixed``      True if >=2 scripts carry letters (e.g. Latin+Hangul, Latn+Arab).
``scripts``    "Hang+Latn"-style breakdown, dominant first.
``n_chars``, ``n_words``, ``flags`` (quality flags dict, see JUNK below).

NORMALIZATION DECISIONS / RATIONALE
-----------------------------------
1. NFKC (not just NFC): folds full-width chars (Ａ->A), Arabic presentation
   forms (ﮩ U+FBAA -> ه), ligatures (ﬁ->fi), superscripts.  The dataset contains
   Arabic presentation-form junk; NFKC turns it into real letters instead of
   dropping the name.  NFC is applied afterwards so Hangul compatibility jamo
   recompose into syllables.
2. Remove Cf/control/private-use chars: ZWJ/ZWNJ (U+200C/200D), ZWSP, BOM, soft
   hyphen, bidi controls, variation selectors, Hangul fillers.  They are
   invisible to TTS but break string equality / lookups.
3. Arabic: strip tatweel U+0640 and all tashkeel/Quranic marks
   (U+0610-061A, 064B-065F, 0670, 06D6-06ED, 08D3-08E1, 08E3-08FF).  In this
   dataset these are decoration, not lexical content; keeping them would
   explode the inventory with near-duplicates and confuse G2P.
4. Latin/Cyrillic/Greek/...: KEEP combining accents in ``norm``/``key``
   (José != Jose; accents carry pronunciation).  ``key_loose`` is available
   when accent-insensitive rollup is wanted.
5. Case: ``norm`` keeps the original casing for display; ``key`` casefolds so
   "NILZ"/"Nilz"/"nilz" are one lexicon entry.
6. Whitespace: any whitespace run (incl. NBSP, ideographic space) -> single
   ASCII space; trimmed.  Multi-word names stay ONE token (merged full names
   are common); ``n_words``/``has_space`` let callers split later.
7. Edge punctuation/symbols trimmed ("_Aya" -> "Aya", "╚► Rasha" -> "Rasha",
   "'Ali" -> "Ali").  Interior punctuation kept ("O'Brien", "Al-Sayed") --
   pronounceable/ignorable.  Quote/dash variants folded: ’‘`´ -> ', –—‑− -> -.
8. Combining marks are NEVER stripped at the END of a token unless the token is
   Arabic or Hebrew (v1.1 fix, 2026-11-19).  In Indic scripts a trailing
   combining mark is a lexical VOWEL SIGN, not decoration: Bengali "আলী"
   (Ali) ends in U+09C0 Mc, Devanagari "हिन्दी" ends in U+0940 Mc,
   Bengali "দানু" ends in U+09C1 Mn.  v1.0's generic `\W` edge-trim silently
   deleted these ("Ali"->"Al"), which is a correctness bug.  For Arabic
   (tashkeel) and Hebrew (niqqud) trailing marks are optional vocalisation and
   are still trimmed; Arabic internal decoration is removed by rule 3 anyway.
   ``key_loose`` still drops all marks by design (aggressive fallback key).
9. Runs of 3+ identical PUNCTUATION/SYMBOL chars collapse ("***" -> "*",
   "═══" -> "═") -- decoration.  Runs of combining marks are never collapsed.
10. Empty / no-letter tokens come back with norm="" / junk flag set.

JUNK FILTER (recommended; measured rates in report.md)
------------------------------------------------------
Exclude a token from IPA synthesis iff ``flags['is_junk']`` is True:

    is_junk = (is_empty            # nothing left after normalization
               or no_letters       # digits/punct/symbols/marks only
               or has_digit        # handles, years, "John2"
               or has_symbol       # box drawing, dingbats, emoji, arrows...
               or is_long          # len(norm) > 60 -- merged rows/paragraphs
               or handle_like      # raw starts with _/@/#, or URL-ish, '@'
               or is_decorated_only)  # decoration reduced to <=1 letter

Deliberately NOT junk: single characters (legit CJK given names; flagged
``is_single`` only), multi-word names, apostrophes/hyphens, accented letters,
legitimate Arabic names whose decoration was stripped.

VERSION
-------
v1.0 (2026-11-19): initial frozen API (shipped to pipeline-builder).
v1.1 (2026-11-19): bug-fix -- preserve trailing combining marks for all scripts
    except Arabic/Hebrew (Indic vowel signs were being silently stripped:
    Bengali আলী->আল, Devanagari हिन्दी->হিন্দ).  All other behaviour identical.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = [
    "normalize_token", "normalize_many", "classify_script",
    "is_junk", "normalize_key", "TATWEEL",
]

TATWEEL = "\u0640"

# --- character classes ------------------------------------------------------
_MARK_CATS = frozenset({"Mn", "Mc", "Me"})
_PUNCT_CATS = frozenset({"Pc", "Pd", "Pe", "Pf", "Pi", "Po", "Ps"})
_SYMBOL_CATS = frozenset({"Sm", "Sc", "Sk", "So"})
_DIGIT_CATS = frozenset({"Nd", "Nl", "No"})

# --- code-point ranges deleted unconditionally ------------------------------
# Cc (controls), Cf (format/invisible), Co (private use), variation selectors,
# Hangul fillers, Arabic tatweel + tashkeel/Quranic marks, soft hyphen.
_DELETE_RANGES = (
    (0x0000, 0x001F), (0x007F, 0x009F),                      # Cc
    (0x00AD, 0x00AD),                                       # soft hyphen
    (0x0600, 0x0605), (0x061C, 0x061C), (0x06DD, 0x06DD),   # Arabic format
    (0x070F, 0x070F), (0x0890, 0x0891), (0x08E2, 0x08E2),
    (0x180E, 0x180E),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
    (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB),
    (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F),
    (0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD),  # private use
    (0xFE00, 0xFE0F), (0xE0100, 0xE01EF),                   # variation selectors
    (0x115F, 0x1160), (0x3164, 0x3164), (0xFFA0, 0xFFA0),   # Hangul fillers
    (0x0640, 0x0640),                                       # tatweel/kashida
    (0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670),   # Arabic diacritics
    (0x06D6, 0x06ED), (0x08D3, 0x08E1), (0x08E3, 0x08FF),   # Quranic marks etc
)


def _build_delete_table() -> dict[int, None]:
    d: dict[int, None] = {}
    for lo, hi in _DELETE_RANGES:
        for cp in range(lo, hi + 1):
            d[cp] = None
    return d


_DELETE_TABLE = _build_delete_table()

# Arabic diacritics (same ranges, for has_arab_diac flag) as a compiled regex.
_ARAB_DIAC_RE = re.compile(
    "[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u08d3-\u08e1\u08e3-\u08ff]"
)
# invisible/format chars (for has_invisible flag)
_FMT_RE = re.compile(
    "[\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e"
    "\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb"
    "\ufe00-\ufe0f\U000e0100-\U000e01ef]"
)

# typographic variants that are pure presentation for names
_QUOTE_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u02bc": "'", "\u02b9": "'", "\u2032": "'", "\u00b4": "'",
    "\u0060": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\ufe58": "-", "\ufe63": "-",
    "\uff0d": "-", "\uff3f": "_",
})

_WS_RE = re.compile(r"\s+")
# 3+ repeats of the same char (only repeated PUNCTUATION/SYMBOLS are collapsed as
# decoration; repeated combining marks are never touched -- Indic vowel signs etc.)
_RUN_RE = re.compile(r"(.)\1{2,}", re.DOTALL)
# non-word chars (for key_loose: drop marks/punct/symbols/space/underscore)
_NONWORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_URLISH_RE = re.compile(r"(https?://|www\.|\.com|\.net|\.org|@)", re.IGNORECASE)

# scripts whose trailing combining marks are optional VOCALISATION and may be
# trimmed at token end (Arabic tashkeel, Hebrew niqqud).  For every other script a
# trailing combining mark is a LEXICAL letter -- e.g. Indic vowel signs:
# Bengali "আলী" = আ+ল+ী (U+09C0, Mc), Devanagari "हिन्दी" ends in ी (U+0940, Mc).
# Stripping those silently corrupts names ("Ali" -> "Al"), so they are KEPT.
# (v1.1 fix, 2026-11-19.)
_TRAILING_MARK_STRIP_SCRIPTS = frozenset({"Arab", "Hebr"})


def _trim_edges(s: str) -> str:
    """Trim edge punctuation/symbols/whitespace/underscores and orphan marks.

    Trailing combining marks are KEPT unless the token is Arabic/Hebrew (where
    they are optional vocalisation, matching v1.0 behaviour for those scripts).
    """
    n = len(s)
    i = 0
    while i < n:
        ch = s[i]
        cat = unicodedata.category(ch)
        # a leading mark has no base character before it -> orphan, drop it
        if ch.isspace() or cat[0] in "PSZ" or cat in _MARK_CATS:
            i += 1
        else:
            break
    j = n
    while j > i:
        ch = s[j - 1]
        cat = unicodedata.category(ch)
        if ch.isspace() or cat[0] in "PSZ":
            j -= 1
        else:
            break
    s = s[i:j]
    if s and unicodedata.category(s[-1])[0] == "M":
        script = classify_script(s)[0]
        if script in _TRAILING_MARK_STRIP_SCRIPTS:
            k = len(s)
            while k > 0 and unicodedata.category(s[k - 1])[0] == "M":
                k -= 1
            s = s[:k]
    return s


def _collapse_repeats(s: str) -> str:
    """Collapse runs of 3+ identical punctuation/symbol chars to one char.

    Marks are never collapsed (combining marks can be lexical).
    """
    if len(s) < 3 or _RUN_RE.search(s) is None:
        return s
    out = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        j = i + 1
        while j < n and s[j] == ch:
            j += 1
        if j - i >= 3 and unicodedata.category(ch)[0] in "PS":
            out.append(ch)
        else:
            out.append(s[i:j])
        i = j
    return "".join(out)

# very common case: pure ASCII-ish name (fast path)
_ASCII_FAST_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]*$")

# --- Script classification ---------------------------------------------------
# (start, end, script-tag) covering the scripts that actually occur in names.
_SCRIPT_RANGES = (
    (0x0041, 0x005A, "Latn"), (0x0061, 0x007A, "Latn"), (0x00AA, 0x00AA, "Latn"),
    (0x00BA, 0x00BA, "Latn"), (0x00C0, 0x024F, "Latn"), (0x0250, 0x02AF, "Latn"),
    (0x02B0, 0x02B8, "Latn"), (0x02E0, 0x02E4, "Latn"), (0x1D00, 0x1D7F, "Latn"),
    (0x1D80, 0x1DBF, "Latn"), (0x1E00, 0x1EFF, "Latn"), (0x2C60, 0x2C7F, "Latn"),
    (0xA720, 0xA7FF, "Latn"), (0xAB30, 0xAB6F, "Latn"), (0xFB00, 0xFB06, "Latn"),
    (0x0131, 0x0131, "Latn"), (0x0149, 0x0149, "Latn"), (0xFF21, 0xFF3A, "Latn"),
    (0xFF41, 0xFF5A, "Latn"),
    (0x0370, 0x03FF, "Grek"), (0x1F00, 0x1FFF, "Grek"),
    (0x0400, 0x04FF, "Cyrl"), (0x0500, 0x052F, "Cyrl"), (0x2DE0, 0x2DFF, "Cyrl"),
    (0xA640, 0xA69F, "Cyrl"), (0x1C80, 0x1C8F, "Cyrl"),
    (0x0530, 0x058F, "Armn"),
    (0x0590, 0x05FF, "Hebr"), (0xFB1D, 0xFB4F, "Hebr"),
    (0x0600, 0x06FF, "Arab"), (0x0750, 0x077F, "Arab"), (0x08A0, 0x08FF, "Arab"),
    (0xFB50, 0xFDFF, "Arab"), (0xFE70, 0xFEFF, "Arab"), (0x1EE00, 0x1EEFF, "Arab"),
    (0x0700, 0x074F, "Syrc"),
    (0x0780, 0x07BF, "Thaa"),
    (0x0900, 0x097F, "Deva"), (0x0980, 0x09FF, "Beng"), (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"), (0x0B00, 0x0B7F, "Orya"), (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"), (0x0C80, 0x0CFF, "Knda"), (0x0D00, 0x0D7F, "Mlym"),
    (0x0D80, 0x0DFF, "Sinh"), (0x0E00, 0x0E7F, "Thai"), (0x0E80, 0x0EFF, "Laoo"),
    (0x0F00, 0x0FFF, "Tibt"), (0x1000, 0x109F, "Mymr"),
    (0x10A0, 0x10FF, "Geor"), (0x2D00, 0x2D2F, "Geor"),
    (0x1100, 0x11FF, "Hang"), (0x3130, 0x318F, "Hang"), (0xA960, 0xA97F, "Hang"),
    (0xAC00, 0xD7AF, "Hang"), (0xD7B0, 0xD7FF, "Hang"),
    (0x1200, 0x137F, "Ethi"), (0x1380, 0x139F, "Ethi"), (0x2D80, 0x2DDF, "Ethi"),
    (0x1780, 0x17FF, "Khmr"), (0x19E0, 0x19FF, "Khmr"),
    (0x3040, 0x309F, "Hira"), (0x30A0, 0x30FF, "Kana"),
    (0x31F0, 0x31FF, "Kana"), (0xFF66, 0xFF9D, "Kana"),
    (0x3400, 0x4DBF, "Hani"), (0x4E00, 0x9FFF, "Hani"),
    (0xF900, 0xFAFF, "Hani"), (0x2E80, 0x2EFF, "Hani"), (0x2F00, 0x2FDF, "Hani"),
    (0x3005, 0x3007, "Hani"), (0x20000, 0x2FA1F, "Hani"),
    (0x1E900, 0x1E95F, "Adlm"), (0x1C50, 0x1C7F, "Olck"),
)
_SCRIPT_RANGES = tuple(sorted(_SCRIPT_RANGES))


def _char_script(cp: int) -> str:
    """Script tag for a letter code point ('' if unmapped)."""
    lo, hi = 0, len(_SCRIPT_RANGES) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        s, e, tag = _SCRIPT_RANGES[mid]
        if cp < s:
            hi = mid - 1
        elif cp > e:
            lo = mid + 1
        else:
            return tag
    return ""


def classify_script(text: str) -> tuple[str, dict[str, int]]:
    """Dominant letter script + per-script letter counts (marks/digits skipped)."""
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            if 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A:
                counts["Latn"] = counts.get("Latn", 0) + 1
            continue
        if unicodedata.category(ch)[0] != "L":
            continue
        tag = _char_script(cp) or "other"
        counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return "", {}
    return max(counts, key=lambda k: (counts[k], k)), counts


_ALEF_UNIFY = str.maketrans({
    "\u0622": "\u0627", "\u0623": "\u0627", "\u0625": "\u0627", "\u0671": "\u0627",
})


def _norm_surface(raw: str) -> str:
    """Dirty raw string -> clean display/G2P surface (see module docstring)."""
    s = raw
    if not s:
        return ""
    if not s.isascii():
        # NFKC: compatibility folds (fullwidth, presentation forms, ligatures).
        # NFC afterwards: recomposes Hangul jamo / elided accents.
        s = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", s))
        s = s.translate(_DELETE_TABLE)      # invisible + Arabic decoration
        s = s.translate(_QUOTE_FOLD)        # curly quotes / dashes -> ASCII
    # collapse any whitespace run to one ASCII space, trim
    s = _WS_RE.sub(" ", s).strip()
    # trim edge punctuation/symbols/underscores ("_Aya" -> "Aya"); keep trailing
    # combining marks for non-Arabic/Hebrew scripts (they are lexical letters)
    s = _trim_edges(s)
    # collapse decorative runs of 3+ identical punctuation/symbols
    return _collapse_repeats(s)


def _analyze(norm: str) -> tuple[str, dict[str, int], int, bool, bool, bool]:
    """One pass over norm: script counts, letter count, digit/punct/symbol flags."""
    counts: dict[str, int] = {}
    n_letters = 0
    has_digit = has_punct = has_symbol = False
    for ch in norm:
        cp = ord(ch)
        if cp < 0x80:
            if 0x41 <= cp <= 0x5A or 0x61 <= cp <= 0x7A:
                counts["Latn"] = counts.get("Latn", 0) + 1
                n_letters += 1
            elif 0x30 <= cp <= 0x39:
                has_digit = True
            elif 0x2D <= cp <= 0x2F or cp in (0x27, 0x5F, 0x40, 0x23):
                has_punct = True
            continue
        cat = unicodedata.category(ch)
        c0 = cat[0]
        if c0 == "L":
            tag = _char_script(cp) or "other"
            counts[tag] = counts.get(tag, 0) + 1
            n_letters += 1
        elif c0 == "N":
            has_digit = True
        elif c0 == "P":
            has_punct = True
        elif c0 == "S":
            has_symbol = True
    script = max(counts, key=lambda k: (counts[k], k)) if counts else ""
    return script, counts, n_letters, has_digit, has_punct, has_symbol


def _flags(norm: str, raw: str, n_letters: int, has_digit: bool,
           has_punct: bool, has_symbol: bool) -> dict:
    n_chars = len(norm)
    n_words = norm.count(" ") + 1 if norm else 0
    is_single = n_chars == 1
    is_long = n_chars > 60
    handle_like = bool(_URLISH_RE.search(raw)) or raw.lstrip()[:1] in ("_", "@", "#")
    is_decorated_only = n_letters <= 1 and len(raw) >= 4
    no_letters = n_letters == 0
    return {
        "is_empty": n_chars == 0,
        "no_letters": no_letters,
        "has_digit": has_digit,
        "has_punct": has_punct,
        "has_symbol": has_symbol,
        "has_tatweel": TATWEEL in raw,
        "has_arab_diac": _ARAB_DIAC_RE.search(raw) is not None,
        "has_invisible": _FMT_RE.search(raw) is not None,
        "is_single": is_single,
        "is_long": is_long,
        "handle_like": handle_like,
        "is_decorated_only": is_decorated_only,
        "is_junk": (no_letters or has_digit or has_symbol or is_long
                    or handle_like or is_decorated_only),
    }


def normalize_token(raw: str | None) -> dict:
    """Normalize one raw name token; never raises.

    ``raw=None``/``""`` -> empty result with flags['is_junk']=True.
    Returns keys: raw, norm, key, key_loose, script, mixed, scripts,
    n_chars, n_words, flags.
    """
    raw = "" if raw is None else str(raw)

    # fast path: plain ASCII name, starts/ends with a letter, only ' .- inside
    if raw and raw.isascii() and _ASCII_FAST_RE.match(raw):
        norm = _WS_RE.sub(" ", raw).strip() if ("  " in raw or raw[0] == " ") else raw
        key = norm.lower()
        flags = {
            "is_empty": False, "no_letters": False, "has_digit": False,
            "has_punct": ("." in norm or "'" in norm or "-" in norm
                          or " " in norm),
            "has_symbol": False, "has_tatweel": False, "has_arab_diac": False,
            "has_invisible": False, "is_single": len(norm) == 1,
            "is_long": len(norm) > 60, "handle_like": False,
            "is_decorated_only": False,
            "is_junk": len(norm) > 60,
        }
        return {
            "raw": raw, "norm": norm, "key": key,
            "key_loose": _NONWORD_RE.sub("", key),
            "script": "Latn", "mixed": False, "scripts": "Latn",
            "n_chars": len(norm), "n_words": norm.count(" ") + 1,
            "flags": flags,
        }

    norm = _norm_surface(raw)
    if not norm:
        return {
            "raw": raw, "norm": "", "key": "", "key_loose": "",
            "script": "", "mixed": False, "scripts": "",
            "n_chars": 0, "n_words": 0,
            "flags": {
                "is_empty": True, "no_letters": True, "has_digit": False,
                "has_punct": False, "has_symbol": False, "has_tatweel": False,
                "has_arab_diac": False, "has_invisible": False, "is_single": False,
                "is_long": False, "handle_like": False, "is_decorated_only": True,
                "is_junk": True,
            },
        }

    key = norm.casefold().translate(_ALEF_UNIFY)
    # key_loose: NFD then drop every non-alnum char (marks, punct, symbols, space)
    key_loose = unicodedata.normalize(
        "NFC", _NONWORD_RE.sub("", unicodedata.normalize("NFD", key)))

    script, counts, n_letters, has_digit, has_punct, has_symbol = _analyze(norm)
    scripts = "+".join(sorted(counts, key=lambda k: (-counts[k], k))) if counts else ""
    mixed = len(counts) > 1
    return {
        "raw": raw, "norm": norm, "key": key, "key_loose": key_loose,
        "script": script, "mixed": mixed, "scripts": scripts,
        "n_chars": len(norm), "n_words": norm.count(" ") + 1,
        "flags": _flags(norm, raw, n_letters, has_digit, has_punct, has_symbol),
    }


def is_junk(raw: str | None) -> bool:
    """Convenience: True if the token should be excluded before IPA synthesis."""
    return normalize_token(raw)["flags"]["is_junk"]


def normalize_many(raws) -> list[dict]:
    """Normalize an iterable of raw strings -> list of dicts (same order)."""
    return [normalize_token(r) for r in raws]


def normalize_key(raw: str | None) -> str:
    """Convenience: just the canonical lexicon key of a raw token."""
    return normalize_token(raw)["key"]


# ---------------------------------------------------------------------------
# Self-test: python normalize.py
# ---------------------------------------------------------------------------
def _self_test() -> int:  # pragma: no cover
    cases = [
        ("Nilz", "Nilz", "nilz", "Latn"),
        ("NILZ", "NILZ", "nilz", "Latn"),
        ("  John  ", "John", "john", "Latn"),
        ("O\u2019Brien", "O'Brien", "o'brien", "Latn"),
        ("Jos\u00e9", "Jos\u00e9", "jos\u00e9", "Latn"),
        ("Jose\u0301", "Jos\u00e9", "jos\u00e9", "Latn"),
        ("\uff21\uff59\uff41", "Aya", "aya", "Latn"),
        ("_Aya", "Aya", "aya", "Latn"),
        ("\u255a\u25ba Rasha", "Rasha", "rasha", "Latn"),
        ("Anne Marie", "Anne Marie", "anne marie", "Latn"),
        ("\u041d\u0430\u0442\u0430\u043b\u044c\u044f", "\u041d\u0430\u0442\u0430\u043b\u044c\u044f",
         "\u043d\u0430\u0442\u0430\u043b\u044c\u044f", "Cyrl"),
        ("\uc11c\uc815", "\uc11c\uc815", "\uc11c\uc815", "Hang"),
        ("\u5f66\u5a9b", "\u5f66\u5a9b", "\u5f66\u5a9b", "Hani"),
        ("\u0645\u0640\u0640\u062d\u0645\u062f", "\u0645\u062d\u0645\u062f", "\u0645\u062d\u0645\u062f", "Arab"),
        ("\u0623\u062d\u0645\u062f", "\u0623\u062d\u0645\u062f", "\u0627\u062d\u0645\u062f", "Arab"),
        ("", "", "", ""),
        ("12345", "12345", "12345", ""),
        ("John2", "John2", "john2", "Latn"),
        ("AAA###", "AAA", "aaa", "Latn"),
        ("@john_doe", "john_doe", "john_doe", "Latn"),
        ("mary-jane", "mary-jane", "mary-jane", "Latn"),
        ("D\u2019Angelo", "D'Angelo", "d'angelo", "Latn"),
        # --- v1.1: trailing combining marks are lexical in Indic scripts ---
        # Bengali Ali: \u0986 \u09b2 \u09c0  (ends U+09C0 Mc vowel sign II)
        ("\u0986\u09b2\u09c0", "\u0986\u09b2\u09c0", "\u0986\u09b2\u09c0", "Beng"),
        # Bengali Danu: \u09a6 \u09be \u09a8 \u09c1 (ends U+09C1 Mn vowel sign U)
        ("\u09a6\u09be\u09a8\u09c1", "\u09a6\u09be\u09a8\u09c1", "\u09a6\u09be\u09a8\u09c1", "Beng"),
        # Bengali Tau: \u09a4 \u09cc (ends U+09CC Mc vowel sign AU)
        ("\u09a4\u09cc", "\u09a4\u09cc", "\u09a4\u09cc", "Beng"),
        # Devanagari Hindi: ends U+0940 vowel sign II, internal virama U+094D kept
        ("\u0939\u093f\u0928\u094d\u0926\u0940", "\u0939\u093f\u0928\u094d\u0926\u0940",
         "\u0939\u093f\u0928\u094d\u0926\u0940", "Deva"),
        # Arabic/I Hebrew behaviour preserved: internal marks / trailing vocalisation
        ("\u0645\u064f\u062d\u064e\u0645\u064e\u0651\u062f", "\u0645\u062d\u0645\u062f",
         "\u0645\u062d\u0645\u062f", "Arab"),
        ("\u05d0\u05b8", "\u05d0", "\u05d0", "Hebr"),
        # edge decoration still trimmed
        ("!!!Aya***", "Aya", "aya", "Latn"),
    ]
    failures = 0
    for raw, norm, key, script in cases:
        r = normalize_token(raw)
        if (r["norm"], r["key"], r["script"]) != (norm, key, script):
            failures += 1
            print("FAIL", repr(raw), "->", repr(r["norm"]), repr(r["key"]), r["script"],
                  "| want", repr(norm), repr(key), script)
    asserts = [
        (normalize_token("Jos\u00e9")["key_loose"], "jose"),
        (normalize_token("O'Brien")["key_loose"], "obrien"),
        (normalize_token("John2")["flags"]["is_junk"], True),
        (normalize_token("12345")["flags"]["is_junk"], True),
        (normalize_token("\u255a\u25ba Rasha")["flags"]["is_junk"], False),
        (normalize_token("\u041d\u0430\u0442\u0430\u043b\u044c\u044f")["flags"]["is_junk"], False),
        (normalize_token("")["flags"]["is_junk"], True),
        # v1.1 regression guards
        (normalize_token("\u0986\u09b2\u09c0")["n_chars"], 3),
        (normalize_token("\u09a4\u09cc")["n_chars"], 2),
        (normalize_token("\u0939\u093f\u0928\u094d\u0926\u0940")["norm"][-1],
         "\u0940"),
    ]
    for got, want in asserts:
        if got != want:
            failures += 1
            print("ASSERT FAIL", repr(got), "!=", repr(want))
    print(f"self-test: {len(cases)} cases + {len(asserts)} asserts, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_test())
