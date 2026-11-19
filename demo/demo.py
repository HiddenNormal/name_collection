# -*- coding: utf-8 -*-
"""demo.py -- command-line demo: turn a name into IPA (and optionally audio).

Examples (run from ``E:\\Repos\\name_collection``)::

    python demo/demo.py                          # scripted demo: names x countries
    python demo/demo.py "Laure" --country FR
    python demo/demo.py "Jean" --country FR --country US     # homograph: two rows
    python demo/demo.py "Nguyễn" --lang vi                   # force a language
    python demo/demo.py --file names.txt --audio            # batch, write WAVs
    python demo/demo.py --file names.txt --json > out.json

Output columns:  name | country | lang | IPA | espeak phonemes | source | confidence

Notes
-----
* IPA is the human-readable deliverable; the espeak phoneme string is the
  representation a Piper-style phoneme TTS consumes directly.
* Tone digits (Vietnamese/Thai/Cantonese) and stress marks (ˈ ˌ) are kept as-is.
* If a name is not in the lexicon it is G2P'd live with the same language rules the
  pipeline uses, so this CLI works the same way the batch pipeline does.
* Windows console note: the default code page (GBK on this machine) cannot print
  IPA.  Run ``chcp 65001`` first, or use ``--json``/redirect to a file.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.common import AUDIO_DIR, DEFAULT_JOIN_COUNTRIES, LEXICON_PATH, read_json  # noqa: E402
from src.norm import field_view  # noqa: E402
from src.langmap import assign_language  # noqa: E402
from src.g2p import get_g2p  # noqa: E402
from src.pipeline import load_lexicon_index  # noqa: E402
from src.demo_build import synth_name  # noqa: E402

# script -> default country when the user does not pass --country (dataset prior)
DEFAULT_COUNTRY = {
    "Latn": "US", "Cyrl": "RU", "Arab": "EG", "Hebr": "IL", "Grek": "GR",
    "Hang": "KR", "Hani": "CN", "Kana": "JP", "Hira": "JP", "Deva": "IN",
    "Beng": "BD", "Taml": "IN", "Telu": "IN", "Knda": "IN", "Mlym": "IN",
    "Gujr": "IN", "Guru": "IN", "Orya": "IN", "Sinh": "IN", "Thai": "TH",
    "Ethi": "ET", "Khmr": "KH", "Thaa": "MV", "Geor": "GE", "Armn": "AM",
    "Mymr": "MM", "Tibt": "CN", "Laoo": "LA",
}

EXAMPLES = [
    ("Laure", "FR"), ("Laure", "US"),        # homograph pair
    ("Jean", "FR"), ("Jean", "US"),          # homograph pair
    ("Smith", "US"), ("Müller", "DE"), ("García", "ES"), ("Rossi", "IT"),
    ("Kowalski", "PL"), ("Јован", "RS"), ("Иван", "RU"), ("Иван", "KZ"),
    ("محمد", "EG"), ("محمد", "SA"), ("김철수", "KR"), ("王小明", "CN"),
    ("陳大文", "TW"), ("Wong", "HK"), ("Yosef", "IL"), ("Σωκράτης", "GR"),
    ("გიორგი", "GE"), ("田中", "JP"), ("สมชาย", "TH"), ("Nguyễn", "US"),
]

TRUNC = "…"


def _w(s: str) -> int:
    """Display width (CJK wide chars count 2, combining marks 0)."""
    n = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _pad(s: str, width: int) -> str:
    s = s if s is not None else ""
    w = _w(s)
    if w > width:
        while _w(s + TRUNC) > width and s:
            s = s[:-1]
        s = s + TRUNC
        w = _w(s)
    return s + " " * max(0, width - w)


class Demo:
    def __init__(self, lexicon_path: str = LEXICON_PATH):
        self.g2p = get_g2p()
        self.lex = {}
        if os.path.exists(lexicon_path):
            self.lex = load_lexicon_index(lexicon_path)
        self._oov = {}

    # link one word dict -> pronunciation record
    def resolve_word(self, w: dict, cc: str, force_lang: str | None = None) -> dict:
        la = assign_language(w["script"], cc)
        lang = force_lang or la["lang"] or ""
        hit = self.lex.get((w["key"], lang)) if lang else None
        if hit and hit["ipa"]:
            return {"word": w["norm"], "lang": lang, "ipa": hit["ipa"],
                    "phonemes": hit["phonemes"], "source": hit["source"],
                    "confidence": hit["confidence"],
                    "lang_reason": la["lang_reason"],
                    "fallback": la["fallback_lang"] or "en-us",
                    "junk": w["flags"]["is_junk"]}
        self._oov.setdefault((w["key"], lang), {
            "token": w["norm"], "lang": lang,
            "fallback": la["fallback_lang"] or "en-us",
            "confidence": la["confidence"]})
        return {"word": w["norm"], "lang": lang, "ipa": "", "phonemes": "",
                "source": "unresolved", "confidence": "none",
                "lang_reason": la["lang_reason"],
                "fallback": la["fallback_lang"] or "en-us",
                "junk": w["flags"]["is_junk"]}

    def resolve_name(self, name: str, cc: str | None = None,
                     force_lang: str | None = None) -> dict:
        fv = field_view(name)
        if fv["empty"]:
            return {"name": name, "country": cc or "", "words": [], "ipa": "",
                    "phonemes": "", "langs": [], "sources": [], "confidence": "none"}
        if not cc:
            cc = DEFAULT_COUNTRY.get(fv["script"], "US")
        words = [self.resolve_word(w, cc, force_lang) for w in fv["words"]]
        langs, sources = [], []
        for w in words:
            if w["lang"] and w["lang"] not in langs:
                langs.append(w["lang"])
            if w["source"] not in sources:
                sources.append(w["source"])
        conf = "high"
        order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        for w in words:
            if order.get(w["confidence"], 9) > order.get(conf, 9):
                conf = w["confidence"]
        return {"name": fv["norm"], "country": cc, "words": words,
                "ipa": " ".join(w["ipa"] for w in words if w["ipa"]),
                "phonemes": " ".join(w["phonemes"] for w in words if w["phonemes"]),
                "langs": langs, "sources": sources or ["unresolved"],
                "confidence": conf, "script": fv["script"]}

    def fill_oov(self):
        if not self._oov:
            return 0
        keys = list(self._oov.keys())
        items = [(self._oov[k]["token"], self._oov[k]["lang"],
                  self._oov[k].get("fallback", "en-us")) for k in keys]
        outs = self.g2p.batch(items)
        n = 0
        for k, o in zip(keys, outs):
            if o["ipa"]:
                self.lex[k] = {"token": self._oov[k]["token"], "ipa": o["ipa"],
                               "phonemes": o["phonemes"], "source": o["source"],
                               "confidence": self._oov[k].get("confidence", "medium")}
                n += 1
        self._oov.clear()
        return n


COLUMNS = [("name", 22), ("country", 7), ("lang", 13), ("IPA", 26),
           ("phonemes", 24), ("source", 19), ("conf", 6)]


def print_table(results: list[dict], show_phonemes: bool = True) -> None:
    if not results:
        return
    for r in results:
        if not r.get("ipa") and r.get("words"):
            still = [w for w in r["words"] if not w["ipa"]]
            if still:
                pass
    cols = COLUMNS if show_phonemes else [c for c in COLUMNS if c[0] != "phonemes"]
    head = " | ".join(_pad(name, w) for name, w in cols)
    rule = "-+-".join("-" * w for _, w in cols)
    print(head)
    print(rule)
    for r in results:
        langs = " / ".join(r.get("langs") or [r.get("lang", "")]) or "-"
        src = ",".join(r.get("sources") or []) or "-"
        vals = {
            "name": r.get("name", ""),
            "country": r.get("country", ""),
            "lang": langs,
            "IPA": r.get("ipa", "") or "(unresolved)",
            "phonemes": r.get("phonemes", "") or "-",
            "source": src,
            "conf": r.get("confidence", ""),
        }
        print(" | ".join(_pad(vals[name], w) for name, w in cols))
    print(rule)
    print(f"{len(results)} name(s). IPA = deliverable; phonemes = espeak-ng string for a "
          f"phoneme-level TTS. Stress ˈ ˌ and tone digits 1-5 are preserved.")
    if any(not r.get("ipa") for r in results):
        print("NOTE: '(unresolved)' means no espeak voice exists for that script "
              "(documented hole, e.g. Khmer/Dhivehi) or the token was junk.")


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="name -> IPA demo (uses output/name_ipa_lexicon.parquet + live espeak G2P)")
    ap.add_argument("names", nargs="*", help="name(s) to pronounce")
    ap.add_argument("--country", action="append", default=None,
                    help="ISO2 country code; repeatable to compare (e.g. FR + US)")
    ap.add_argument("--lang", default=None, help="force a language, e.g. vi / ru / en-us")
    ap.add_argument("--file", help="text file with one name per line ('Name' or 'Name,CC')")
    ap.add_argument("--audio", action="store_true", help="also synthesize a WAV per name")
    ap.add_argument("--audio-dir", default=AUDIO_DIR)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--no-phonemes", action="store_true")
    ap.add_argument("--examples", action="store_true",
                    help="run the scripted examples (default when no name/file given)")
    args = ap.parse_args(argv)

    demo = Demo()
    queries: list[tuple[str, str | None, str | None]] = []  # (name, country, lang)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "," in line:
                    nm, cc = line.rsplit(",", 1)
                    queries.append((nm.strip(), cc.strip().upper(), args.lang))
                else:
                    queries.append((line, None, args.lang))
    for nm in args.names:
        countries = args.country or [None]
        for cc in countries:
            queries.append((nm, cc.upper() if cc else None, args.lang))
    if not queries:
        queries = [(nm, cc, None) for nm, cc in EXAMPLES]

    # first pass: resolve against the lexicon, collect OOV, single batched G2P
    results = [demo.resolve_name(nm, cc, lang) for nm, cc, lang in queries]
    n_fill = demo.fill_oov()
    if n_fill:
        results = [demo.resolve_name(nm, cc, lang) for nm, cc, lang in queries]
    if n_fill:
        print(f"[demo] {n_fill} OOV token(s) resolved live via espeak G2P", file=sys.stderr)

    # audio
    if args.audio:
        os.makedirs(args.audio_dir, exist_ok=True)
        made = 0
        for i, r in enumerate(results, 1):
            words = [{"word": w["word"], "lang": w["lang"],
                      "junk": w["junk"]} for w in r.get("words", [])]
            if not words:
                continue
            base = "".join(ch for ch in r["name"] if ch.isalnum())[:24] or "name"
            path = os.path.join(args.audio_dir, f"cli_{i:03d}_{base}.wav")
            res = synth_name(words, path, g2p=demo.g2p)
            r["audio"] = path if res["ok"] else ""
            if res["ok"]:
                made += 1
                print(f"[demo] audio {made}: {path}", file=sys.stderr)
        if not made:
            print("[demo] audio synthesis failed", file=sys.stderr)

    if args.json:
        out = [{k: v for k, v in r.items() if k != "words"} |
               {"words": [{kk: vv for kk, vv in w.items() if kk != "junk"}
                          for w in r.get("words", [])]} for r in results]
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print_table(results, show_phonemes=not args.no_phonemes)
        if args.audio:
            print(f"\nWAV files written under: {args.audio_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
