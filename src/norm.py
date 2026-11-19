# -*- coding: utf-8 -*-
"""norm.py -- thin, opinionated wrapper around the frozen ``profile/normalize.py``.

The pipeline never re-implements normalization: it imports the data-profiler's
module (read-only) and adds the two helpers the pipeline needs on top of it:

* ``field_view(raw)``      -- normalize a whole CSV field (may hold several words,
                             e.g. ``"Anne Marie"``) and hand back word-level
                             token dicts ready for language assignment / G2P;
* ``token_view(raw)``      -- normalize a single token (what the extractor stores).

Contract (from ``profile/normalize.py``):
    ``norm``  clean display surface -> feed THIS to G2P
    ``key``   casefolded + Arabic-alef-unified join key -> JOIN the lexicon on THIS
    ``flags['is_junk']`` -> exclude from IPA synthesis
"""
from __future__ import annotations

import os
import sys

if __package__:
    from .common import PROFILE_DIR
else:  # `python src/norm.py` convenience
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import PROFILE_DIR

if PROFILE_DIR not in sys.path:
    sys.path.insert(0, PROFILE_DIR)

import normalize as _nz  # noqa: E402  (frozen read-only dependency)

normalize_token = _nz.normalize_token
normalize_key = _nz.normalize_key
is_junk = _nz.is_junk
classify_script = _nz.classify_script

__all__ = ["normalize_token", "normalize_key", "is_junk", "classify_script",
           "token_view", "field_view", "words_of"]


def token_view(raw: str) -> dict:
    """Normalize one token; returns the normalize_token dict (see module docstring)."""
    return normalize_token(raw)


def words_of(norm: str) -> list[str]:
    """Split an already-normalized field surface into word tokens."""
    return [w for w in norm.split(" ") if w]


def field_view(raw: str) -> dict:
    """Normalize a full CSV field and expose its word tokens.

    Returns::

        {"raw":..., "norm":..., "key":..., "script":..., "flags":...,
         "words":[<normalize_token dict per word>], "empty": bool}

    Multi-word names stay one *key* for display purposes, but G2P/lexicon work is
    per word (METHOD.md §2-① last bullet + §5-4). Junk is decided per word.
    """
    whole = normalize_token(raw)
    if not whole["norm"]:
        return {"raw": raw or "", "norm": "", "key": "", "script": "",
                "flags": whole["flags"], "words": [], "empty": True}
    words = []
    for w in words_of(whole["norm"]):
        d = normalize_token(w)
        if not d["norm"]:
            continue
        words.append(d)
    return {"raw": raw or "", "norm": whole["norm"], "key": whole["key"],
            "script": whole["script"], "flags": whole["flags"],
            "words": words, "empty": False}


if __name__ == "__main__":  # pragma: no cover - tiny smoke test
    for s in ["  O\u2019Brien ", "_Aya", "\u255a\u25ba Rasha", "Anne Marie",
              "\u0645\u0640\u0640\u062d\u0645\u062f", ""]:
        v = field_view(s)
        print(repr(s), "->", repr(v["norm"]), [w["norm"] for w in v["words"]],
              "junk" if v["empty"] or all(w["flags"]["is_junk"] for w in v["words"]) else "ok")
