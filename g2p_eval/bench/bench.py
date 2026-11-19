"""Run the G2P/IPA engine benchmark against the gold sets.

Outputs ``bench.csv`` (one row per language x engine), per-engine diagnostic
pair files under ``diagnostics/``, plus a compact printed table.

Metrics are TTS-oriented (see bench/normalize_ipa.py):
  per_feat   feature-weighted PER via panphon (PRIMARY cross-engine metric)
  per_bare   string-exact PER after stress removal (reference)
  exact_bare full-string match rate
  stress_acc primary/secondary stress-pattern accuracy (only where gold has
             stress: English CMUdict subset; None elsewhere)
  stress_emit fraction of outputs carrying any stress mark (TTS requirement)
  tone_acc   tone-sequence accuracy where gold is tonal (cmn/tha/vie)
  tokens_per_sec  single-process CPU throughput
  full_lexicon_name_coverage  fraction of top real first names found in the
             full WikiPron lexicon (measures the lexicon tier's reach)

Usage:
    PYTHONPATH=pylibs python bench/bench.py [--langs ...] [--engines ...]
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import sys
import time

logging.disable(logging.WARNING)  # epitran/flite are extremely chatty

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from normalize_ipa import pairs_diagnostics, summarize  # noqa: E402

GOLD_DIR = os.path.join(ROOT, "gold")
TOKENS_DIR = os.path.join(ROOT, "name_tokens")
SPLIT_DIR = os.path.join(ROOT, "lexicons_split")
DIAG_DIR = os.path.join(ROOT, "diagnostics")
LEX_DIR = os.path.join(ROOT, "lexicons")

# country -> language prior for the "real name coverage" column
COUNTRY_LANG = {
    "fra": ["FR", "BE", "CA", "SN", "CI", "CM", "HT", "LU", "MC", "BF", "BJ", "TG", "ML"],
    "deu": ["DE", "AT", "CH", "LI"],
    "spa": ["ES", "MX", "AR", "CO", "CL", "PE", "UY", "CR", "GT", "PA", "DO", "EC", "BO", "HN", "PY", "SV", "NI", "PR", "CU", "VE"],
    "ita": ["IT", "CH", "SM"],
    "por": ["PT", "BR", "AO", "MZ", "CV", "GW", "ST", "TL"],
    "nld": ["NL", "BE"],
    "rus": ["RU", "KZ", "KG", "BY", "MD", "UA", "UZ", "TJ", "TM", "GE", "AM", "AZ"],
    "pol": ["PL"],
    "tur": ["TR", "CY"],
    "swe": ["SE"],
    "ara": ["EG", "SA", "AE", "IQ", "JO", "KW", "BH", "QA", "OM", "YE", "SY", "LB", "PS", "DZ", "MA", "TN", "LY", "SD", "MR", "DJ", "SO", "KM"],
    "cmn": ["CN", "TW", "SG", "MO"],
    "kor": ["KR", "KP"],
    "jpn": ["JP"],
    "eng": ["GB", "US", "CA", "AU", "NZ", "IE", "ZA", "NG", "GH", "KE", "IN", "PH", "SG", "MY", "TZ", "UG", "ZW", "JM", "TT", "FJ", "MT", "CY"],
    "ukr": ["UA"],
    "ces": ["CZ"],
    "hun": ["HU"],
    "vie": ["VN"],
    "hin": ["IN"],
    "fas": ["IR", "AF", "TJ"],
    "ell": ["GR", "CY"],
    "heb": ["IL"],
    "tha": ["TH"],
    "dan": ["DK"],
    "fin": ["FI"],
    "ind": ["ID"],
    "ron": ["RO", "MD"],
    "urd": ["PK"],
}

ENGINE_NAMES = ["espeak", "espeak-plus", "wikipron-lexicon", "epitran",
                "charsiu", "latin-approx", "raw-grapheme"]
LANG_ONLY = {"espeak-plus": {"cmn", "jpn", "yue"}}
# engines that are too slow or broken for some languages
ENGINE_SKIP = {
    # epitran's English G2P uses flite's lex_lookup which is not installed here;
    # it raises KeyError per word -> would just measure a broken backend
    "epitran": {"eng", "heb", "ell", "dan", "ron", "fas", "urd"},
}
# cap the gold sample for slow engines (per language)
ENGINE_MAX_GOLD = {"epitran": 400, "charsiu": 250}
ESPEAK_VOICE = {
    "eng": "en-us", "fra": "fr", "spa": "es", "deu": "de", "ita": "it",
    "por": "pt", "nld": "nl", "rus": "ru", "pol": "pl", "tur": "tr",
    "swe": "sv", "ara": "ar", "cmn": "cmn", "kor": "ko", "jpn": "ja",
    "hin": "hi", "ell": "el", "dan": "da", "fin": "fi", "ind": "id",
    "vie": "vi", "heb": "he", "tha": "th", "ces": "cs", "hun": "hu",
    "ukr": "uk", "fas": "fa", "urd": "ur", "ron": "ro",
}


def base_lang(lang):
    """eng_cmu / eng_cmu_names are English gold variants."""
    if lang.startswith("eng_cmu"):
        return "eng"
    return lang


def load_gold(lang):
    path = os.path.join(GOLD_DIR, f"{lang}.tsv")
    if not os.path.exists(path):
        return [], []
    words, prons = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0] and p[1]:
                words.append(p[0])
                prons.append(p[1])
    return words, prons


def load_name_tokens(lang, top_n=5000):
    """Most frequent real first-name tokens from the mapped countries."""
    seen = {}
    rank = 0
    for cc in COUNTRY_LANG.get(lang, []):
        path = os.path.join(TOKENS_DIR, f"{cc}.tsv")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= top_n:
                    break
                p = line.rstrip("\n").split("\t")
                if len(p) < 2:
                    continue
                tok = p[0].strip()
                if not tok or len(tok) > 30:
                    continue
                if tok not in seen:
                    rank += 1
                    seen[tok] = rank
    return [t for t, _ in sorted(seen.items(), key=lambda kv: kv[1])][:top_n]


class LexTrainEngine:
    """WikiPron lexicon restricted to the *train* split (fair held-out eval)."""
    name = "wikipron-lexicon"

    def __init__(self):
        self._tables = {}

    def table(self, lang):
        if lang in self._tables:
            return self._tables[lang]
        path = os.path.join(SPLIT_DIR, f"{lang}_train.tsv")
        t = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    if len(p) >= 2:
                        t[p[0].strip().lower()] = p[1].strip()
        self._tables[lang] = t
        return t

    def ipa(self, text, lang):
        return self.table(lang).get(text.strip().lower(), "")


def full_lexicon_coverage(lang, tokens):
    from build_gold import pick_lexicon
    path = pick_lexicon(lang)
    if not path or not tokens:
        return None
    table = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split("\t", 1)
            if p and p[0]:
                table.add(p[0].strip().lower())
    hits = sum(1 for t in tokens if t.strip().lower() in table)
    return hits / len(tokens)


def make_engine(name, lang):
    if name in LANG_ONLY and lang not in LANG_ONLY[name]:
        return None
    import engines as E
    try:
        if name == "espeak":
            e = E.EspeakEngine()
            voice = ESPEAK_VOICE.get(lang, lang)
            if not e.has_voice(voice):
                return None
            e._forced_voice = voice
            return e
        if name == "espeak-plus":
            base = E.EspeakEngine()
            voice = ESPEAK_VOICE.get(lang, lang)
            if not base.has_voice(voice):
                return None
            e = E.EspeakPlusEngine(base)
            e._forced_voice = voice
            return e
        if name == "wikipron-lexicon":
            return LexTrainEngine()
        if name == "epitran":
            e = E.EpitranEngine()
            e._get(lang)
            return e
        if name == "charsiu":
            return E.CharsiuEngine()
        if name == "latin-approx":
            return E.LatinApproxEngine()
        if name == "raw-grapheme":
            return E.RawGraphemeEngine()
    except Exception as exc:
        print(f"  [{name}/{lang}] unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return None


def run_engine(engine, name, words, lang):
    voice = getattr(engine, "_forced_voice", lang)
    out = []
    t0 = time.time()
    if name in ("charsiu",):
        out = engine.ipa_batch(words, voice)
    elif name == "espeak":
        out = engine.ipa_batch(words, voice)
    else:
        for w in words:
            try:
                out.append(engine.ipa(w, voice))
            except Exception as e:
                print(f"  [{name}/{lang}] error on {w!r}: {e}", file=sys.stderr)
                out.append("")
    return out, time.time() - t0


FIELDS = ["language", "base_language", "engine", "n_gold", "n_eval", "covered",
          "full_lexicon_name_coverage", "per_feat", "per_bare", "exact_bare",
          "stress_acc", "stress_n", "stress_emit", "tone_acc", "tone_n",
          "tokens_per_sec", "nonempty_rate", "notes"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="")
    ap.add_argument("--engines", default="")
    ap.add_argument("--out", default=os.path.join(ROOT, "bench.csv"))
    ap.add_argument("--max-gold", type=int, default=4000)
    ap.add_argument("--append", action="store_true")
    args = ap.parse_args()

    import engines  # noqa: F401

    langs = [x.strip() for x in args.langs.split(",") if x.strip()] or \
        sorted(f[:-4] for f in os.listdir(GOLD_DIR) if f.endswith(".tsv"))
    engine_names = [x.strip() for x in args.engines.split(",") if x.strip()] or ENGINE_NAMES
    os.makedirs(DIAG_DIR, exist_ok=True)

    rows = []
    if args.append and os.path.exists(args.out):
        with open(args.out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        done = {(r["language"], r["engine"]) for r in rows}
    else:
        done = set()

    for lang in langs:
        base = base_lang(lang)
        words, golds = load_gold(lang)
        if not words:
            print(f"!! no gold for {lang}")
            continue
        name_tokens = load_name_tokens(base)
        lex_cov = full_lexicon_coverage(base, name_tokens) if name_tokens else None
        print(f"\n== {lang}: gold={len(words)} real_name_tokens={len(name_tokens)} "
              f"full_lexicon_name_coverage={lex_cov if lex_cov is None else round(lex_cov, 3)}")
        # deterministic subsample for very large gold sets (eng_cmu: 125k)
        if len(words) > args.max_gold:
            rng = random.Random(42)
            idx = sorted(rng.sample(range(len(words)), args.max_gold))
            words = [words[i] for i in idx]
            golds = [golds[i] for i in idx]
        for ename in engine_names:
            if (lang, ename) in done:
                continue
            eng = make_engine(ename, base)
            if eng is None:
                continue
            if ename in ENGINE_SKIP and base in ENGINE_SKIP[ename]:
                print(f"   {ename:18s} SKIPPED (backend broken for {base})")
                continue
            ew, eg = words, golds
            cap = ENGINE_MAX_GOLD.get(ename)
            if cap and len(ew) > cap:
                ew, eg = ew[:cap], eg[:cap]
            hyps, dt = run_engine(eng, ename, ew, base)
            n_eval = len(ew)
            covered = sum(1 for h in hyps if h)
            pairs = [(w, g, h) for w, g, h in zip(ew, eg, hyps) if h]
            m = summarize([(g, h) for _, g, h in pairs])
            if ename == "wikipron-lexicon" and covered < n_eval:
                miss = (n_eval - covered) / n_eval
                m["per_feat"] = m["per_feat"] * covered / n_eval + miss
                m["per_bare"] = m["per_bare"] * covered / n_eval + miss
                m["exact_bare"] = m["exact_bare"] * covered / n_eval
                m["stress_emit"] = m["stress_emit"] * covered / n_eval
            speed = n_eval / dt if dt > 0 else 0.0
            note = ""
            if m["stress_n"] == 0:
                note = "gold has no stress marks (WikiPron); stress_acc n/a"
            if ename == "wikipron-lexicon":
                note = ("PER n/a by protocol (gold words are held out of the train "
                        "lexicon; scoring the dictionary against itself is meaningless) "
                        "-> use coverage.csv / lexicon_qa.csv")
            if lang in ("cmn", "tha", "vie") and ename in ("espeak", "espeak-plus"):
                note += "; tone digits compared separately"
            row = dict(
                language=lang, base_language=base, engine=ename,
                n_gold=len(words) if not args.append else len(words), n_eval=n_eval,
                covered=round(covered / n_eval, 4) if n_eval else 0,
                full_lexicon_name_coverage=None if lex_cov is None else round(lex_cov, 4),
                per_feat=round(m["per_feat"], 4),
                per_bare=round(m["per_bare"], 4),
                exact_bare=round(m["exact_bare"], 4),
                stress_acc=None if m["stress_acc"] is None else round(m["stress_acc"], 4),
                stress_n=m["stress_n"],
                stress_emit=round(m["stress_emit"], 4),
                tone_acc=None if m["tone_acc"] is None else round(m["tone_acc"], 4),
                tone_n=m["tone_n"],
                tokens_per_sec=round(speed, 1),
                nonempty_rate=round(covered / n_eval, 4) if n_eval else 0,
                notes=note,
            )
            rows.append(row)
            pairs_diagnostics(pairs, os.path.join(DIAG_DIR, f"{lang}__{ename}.tsv"), limit=25)
            print(f"   {ename:18s} PERfeat={row['per_feat']:.3f} PERstr={row['per_bare']:.3f} "
                  f"exact={row['exact_bare']:.3f} stress_acc={row['stress_acc']} "
                  f"stress_emit={row['stress_emit']:.2f} tone_acc={row['tone_acc']} "
                  f"speed={row['tokens_per_sec']:.0f}/s nonempty={row['nonempty_rate']:.2f}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        wtr = csv.DictWriter(f, fieldnames=FIELDS)
        wtr.writeheader()
        for r in rows:
            wtr.writerow({k: r.get(k, "") for k in FIELDS})
    print(f"\nwrote {args.out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
