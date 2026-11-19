# -*- coding: utf-8 -*-
"""langmap.py -- stage 2/3: script detection + country x script -> espeak voice.

This is the decision layer that makes the whole pipeline right or wrong; the
end-to-end method is documented in ``docs/METHOD.md`` §2-②/③ and §3.1.

The one non-obvious, measured fact (independently confirmed by the Lead):

    espeak-ng voices whose language uses a NON-Latin script silently fall back to
    ENGLISH letter-to-sound for Latin input.  ``ru/ar/el/ko/ka/he/kk/bg/cmn`` all
    return a byte-identical ``en-us`` reading for ``Laure``/``Smith``; only ``ja``
    is different - it SPELLS the letters ("L A U R E").
    Voices of Latin-script languages (fr-fr/de/tr/pl/sr/az/tk/vi/...) do their own
    LTS and produce genuinely different, correct-by-language readings.

Consequences implemented here:
  * Latin token + country whose primary voice does Latin LTS -> that voice (high).
  * Latin token + non-Latin-primary country -> ``en-us`` explicitly (medium), so
    the anglicisation is *visible in the provenance* (``english-fallback``) rather
    than accidental.  Exceptions: CN/TW -> ``cmn-latn-pinyin``; HK/MO ->
    ``yue-latn-jyutping`` (both really do interpret romanised input); JP ->
    ``en-us`` because ``ja`` spells Latin letters.
  * Non-Latin token -> country-conditioned script voice (KZ Cyrl->kk, BG Cyrl->bg,
    RU Cyrl->ru, TM Cyrl->tk, RS Cyrl->sr, IL Hebr->he, GR Grek->el, ...), else a
    global script default, else ``unresolved`` (Khmer/Thaana/Tibetan have no voice).

Every assignment carries four provenance fields used across the pipeline:
``lang``, ``lang_reason``, ``confidence``, ``confidence_reason`` (+ ``fallback_lang``).
"""
from __future__ import annotations

import csv
import os

if __package__:
    from .common import DOCS_DIR
else:  # `python src/langmap.py` convenience
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import DOCS_DIR

COUNTRY_TABLE_PATH = os.path.join(DOCS_DIR, "country_languages.csv")

# ---------------------------------------------------------------------------
# Voices whose *language* is written in the Latin script -> real Latin LTS.
# Measured against en-us on "Laure"/"Smith"/"Natalya": these differ from en-us.
# (Lead's independent verification agrees.)
# ---------------------------------------------------------------------------
LATIN_LTS_VOICES = {
    "en", "en-us", "en-gb", "fr-fr", "fr-ca", "fr-ch", "de", "it", "es-es",
    "es-419", "es-mx", "pt-pt", "pt-br", "nl", "sv", "da", "nb", "fi", "pl",
    "cs", "sk", "hu", "ro", "hr", "sl", "sq", "et", "lt", "lb", "tr", "az",
    "tk", "sr", "vi", "ms", "id", "sw", "af", "ca", "cy", "ga", "is", "eu",
    "mt", "ht", "qu", "quc", "pap", "lfn", "ia", "eo", "tl", "nn",
}

# ---------------------------------------------------------------------------
# Global script -> default espeak voice.  Used only when the country's own voice
# does not natively read that script.  None = espeak-ng has no voice (documented
# coverage hole; do NOT silently substitute English, which would spell the text).
# ---------------------------------------------------------------------------
SCRIPT_VOICE = {
    "Cyrl": "ru", "Arab": "ar", "Hebr": "he", "Grek": "el", "Geor": "ka",
    "Armn": "hy", "Hang": "ko", "Hani": "cmn", "Kana": "ja", "Hira": "ja",
    "Ethi": "am", "Beng": "bn", "Deva": "hi", "Taml": "ta", "Telu": "te",
    "Knda": "kn", "Mlym": "ml", "Gujr": "gu", "Guru": "pa", "Orya": "or",
    "Sinh": "si", "Thai": "th", "Mymr": "my", "Laoo": None, "Tibt": None,
    "Khmr": None, "Thaa": None, "Syrc": None, "Adlm": None, "Olck": None,
}

# scripts each voice can natively read (letters of that script, not English LTS)
VOICE_NATIVE_SCRIPT = {
    "ar": {"Arab"}, "fa": {"Arab"}, "ur": {"Arab"}, "ps": {"Arab"},
    "he": {"Hebr"}, "ru": {"Cyrl"}, "uk": {"Cyrl"}, "bg": {"Cyrl"},
    "sr": {"Cyrl", "Latn"}, "mk": {"Cyrl"}, "kk": {"Cyrl"}, "ky": {"Cyrl"},
    "tt": {"Cyrl"}, "be": {"Cyrl"}, "ba": {"Cyrl"}, "cv": {"Cyrl"},
    "el": {"Grek"}, "ka": {"Geor"}, "hy": {"Armn"}, "am": {"Ethi"},
    "hi": {"Deva"}, "mr": {"Deva"}, "ne": {"Deva"}, "bn": {"Beng"},
    "as": {"Beng"}, "pa": {"Guru"}, "gu": {"Gujr"}, "or": {"Orya"},
    "ta": {"Taml"}, "te": {"Telu"}, "kn": {"Knda"}, "ml": {"Mlym"},
    "si": {"Sinh"}, "th": {"Thai"}, "my": {"Mymr"}, "ko": {"Hang"},
    "ja": {"Hani", "Hira", "Kana"}, "cmn": {"Hani"}, "yue": {"Hani"},
    "cmn-latn-pinyin": {"Latn"}, "yue-latn-jyutping": {"Latn"},
    "vi": {"Latn"},
}

# country-specific voice for a script the country's own primary voice cannot read
# (or reads with the wrong language); every value was measured, see METHOD.md.
COUNTRY_SCRIPT_VOICE = {
    ("KZ", "Cyrl"): "kk", ("RU", "Cyrl"): "ru", ("BG", "Cyrl"): "bg",
    ("TM", "Cyrl"): "tk", ("RS", "Cyrl"): "sr", ("MD", "Cyrl"): "ro",
    ("AZ", "Cyrl"): "ru", ("GE", "Cyrl"): "ru", ("IL", "Cyrl"): "ru",
    ("EE", "Cyrl"): "ru", ("LT", "Cyrl"): "ru", ("PS", "Cyrl"): "ru",
    ("IR", "Arab"): "fa", ("AF", "Arab"): "fa",
    ("CN", "Hani"): "cmn", ("TW", "Hani"): "cmn", ("HK", "Hani"): "yue",
    ("MO", "Hani"): "yue", ("JP", "Hani"): "ja", ("KR", "Hani"): "cmn",
    ("JP", "Kana"): "ja", ("JP", "Hira"): "ja", ("MO", "Kana"): "ja",
}

# Latin input in these countries is romanised Chinese/Cantonese -> dedicated voice
CMA_LATN = {"CN": "cmn-latn-pinyin", "TW": "cmn-latn-pinyin",
            "HK": "yue-latn-jyutping", "MO": "yue-latn-jyutping"}

# Countries whose primary voice is a *proxy* for an unsupported language
# (docs/country_languages.csv "notes" column) -> confidence capped at low for
# tokens that rely on the primary voice.
PROXY_COUNTRIES = {
    "AF": "Pashto unsupported; Dari (fa) used as proxy",
    "BI": "Kirundi unsupported; French fallback is shaky",
    "BW": "Tswana unsupported",
    "FJ": "Fijian unsupported",
    "GH": "Twi/Ewe unsupported",
    "KH": "Khmer unsupported",
    "MU": "Mauritian Creole unsupported",
    "MV": "Dhivehi unsupported",
    "NG": "Yoruba/Igbo/Hausa unsupported",
    "PH": "Tagalog unsupported; Indonesian proxy",
    "ZA": "Zulu/Xhosa unsupported",
    "IS": "Icelandic voice weak on names",
}

# sources of linguistic prior, for the report's "why this language" table
COUNTRY_TABLE_SOURCE = ("docs/country_languages.csv (105 rows; every primary/secondary "
                        "voice validated against the installed espeak-ng voice list)")

_VOICE_SCRIPT_CACHE: dict[str, tuple] = {}

CONF_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}


def load_country_table() -> dict[str, dict]:
    table = {}
    with open(COUNTRY_TABLE_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row = {k: (v or "") for k, v in row.items()}
            table[row["country"]] = row
    return table


COUNTRY_TABLE = load_country_table()


def _result(lang, reason, confidence, confidence_reason, fallback=None):
    return {"lang": lang, "fallback_lang": fallback, "lang_reason": reason,
            "confidence": confidence, "confidence_reason": confidence_reason}


def _has_voice(lang: str) -> bool:
    """True if the espeak-ng install has this voice (cached, lazy engine import)."""
    if lang in _VOICE_SCRIPT_CACHE:
        return _VOICE_SCRIPT_CACHE[lang][0]
    ok = True
    try:
        if __package__:
            from .g2p import get_g2p
        else:
            from g2p import get_g2p
        ok = get_g2p().has_voice(lang)
    except Exception:  # pragma: no cover - engine check is best-effort
        ok = True
    _VOICE_SCRIPT_CACHE[lang] = (ok,)
    return ok


def assign_language(script: str, country: str) -> dict:
    """Country x script -> {lang, fallback_lang, lang_reason, confidence, ...}.

    ``script`` is the dominant letter script from ``normalize.py`` ('' when the
    token has no letters -> unresolved before this is even called).
    """
    row = COUNTRY_TABLE.get(country)
    if row is None:  # unknown country code: fall back to English / script default
        if script in (None, "", "Latn"):
            return _result("en-us", "english-fallback", "low", "unknown-country")
        v = SCRIPT_VOICE.get(script)
        if not v:
            return _result(None, "unsupported-script", "none", f"no voice for {script}")
        return _result(v, "script:default", "low", "unknown-country")

    primary = row["primary_voice"]
    proxy = PROXY_COUNTRIES.get(country)

    # ---------------- non-Latin script -------------------------------------
    if script and script != "Latn":
        # 1) explicit country-conditioned choice (measured to matter: KZ/BG/RU/TM)
        v = COUNTRY_SCRIPT_VOICE.get((country, script))
        if v and _has_voice(v):
            if script in ("Hani", "Hira"):
                conf, why = "low", "CJK reading requires a pronunciation dictionary (espeak limitation)"
            else:
                conf, why = "high", "country-conditioned script voice"
            return _result(v, "script:country", conf, why)

        # 2) the country's own primary voice natively reads this script
        if script in VOICE_NATIVE_SCRIPT.get(primary, set()) and _has_voice(primary):
            if script in ("Hani", "Hira"):
                conf, why = "low", "CJK reading requires a pronunciation dictionary (espeak limitation)"
            elif script == "Hebr":
                conf, why = "medium", "Hebrew script omits short vowels"
            else:
                conf, why = "high", "primary voice reads this script natively"
            return _result(primary, "script:country", conf, why)

        # 3) global script default
        v = SCRIPT_VOICE.get(script)
        if v and _has_voice(v):
            if script in ("Hani", "Hira"):
                conf, why = "low", "CJK reading requires a pronunciation dictionary (espeak limitation)"
            elif script == "Hebr":
                conf, why = "medium", "Hebrew script omits short vowels"
            elif script == "Kana":
                conf, why = "medium", "kana reading is reliable, kanji is not"
            else:
                conf, why = "medium", "global script default (country voice does not cover it)"
            return _result(v, "script:default", conf, why)

        return _result(None, "unsupported-script", "none",
                       f"espeak-ng has no voice for {script} (documented coverage hole)")

    # ---------------- Latin script -----------------------------------------
    if primary in LATIN_LTS_VOICES and _has_voice(primary):
        if proxy:
            return _result(primary, "country-latin-lts", "low", f"proxy voice: {proxy}")
        return _result(primary, "country-latin-lts", "high", "primary voice does Latin LTS")

    if country in CMA_LATN and _has_voice(CMA_LATN[country]):
        return _result(CMA_LATN[country], "pinyin-latn", "medium",
                       "romanised Chinese/Cantonese; espeak pinyin/jyutping front-end is approximate")

    if proxy:
        return _result("en-us", "english-fallback", "low",
                       f"proxy country + English LTS: {proxy}")
    # ja is the only voice that spells Latin letters instead of reading them, so
    # romanised Japanese goes to English LTS (documented deviation from METHOD §2-③).
    why = ("ja voice spells Latin letter names; using English LTS"
           if primary == "ja" else
           "non-Latin primary voice silently applies English LTS to Latin input")
    return _result("en-us", "english-fallback", "medium", why)


def confidence_of(result: dict) -> str:
    return result.get("confidence", "none")


def validate_table(engine_has_voice=None) -> dict:
    """Check every voice referenced by the country table exists; returns a report."""
    voices = set()
    for row in COUNTRY_TABLE.values():
        voices.add(row["primary_voice"])
        for v in row["secondary_voices"].split(","):
            v = v.strip()
            if v:
                voices.add(v)
    if engine_has_voice is None:
        if __package__:
            from .g2p import get_g2p
        else:
            from g2p import get_g2p
        g = get_g2p()
        engine_has_voice = g.has_voice
    missing = sorted(v for v in voices if not engine_has_voice(v))
    return {"voices": len(voices), "missing": missing}


if __name__ == "__main__":  # pragma: no cover - quick self-check
    import json
    print(json.dumps(validate_table(), indent=1))
    demo = [("RU", "Cyrl"), ("KZ", "Cyrl"), ("BG", "Cyrl"), ("EG", "Arab"),
            ("IL", "Hebr"), ("GR", "Grek"), ("KR", "Hang"), ("CN", "Hani"),
            ("TW", "Latn"), ("HK", "Latn"), ("JP", "Latn"), ("KH", "Khmr"),
            ("MV", "Thaa"), ("US", "Latn"), ("FR", "Latn"), ("GE", "Geor")]
    for cc, sc in demo:
        print(cc, sc, "->", assign_language(sc, cc))
