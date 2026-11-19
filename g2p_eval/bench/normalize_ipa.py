"""IPA normalisation + comparison metrics used by the G2P benchmark.

Two comparisons are reported, because the end use is TTS:

* ``per_bare``   – phoneme error rate after stripping stress marks. Measures
  segmental quality (were the consonants/vowels right).
* ``per_stress`` – phoneme error rate where the stress mark is attached to the
  following phone (``ˈa`` is one token). This is the metric that punishes wrong
  or missing stress, the dominant audible defect in name TTS.
* ``stress_acc`` – accuracy of the stress *pattern* (which syllable of the word
  carries primary/secondary stress). Counts only gold items that carry stress.
* ``exact_*``    – full-string exact match after the same normalisation.

IPA is segmented into "phones" with a small unicode-aware tokeniser:
a phone = optional stress mark + one base letter + attached combining marks /
modifier letters (length marks, aspiration, palatalisation, nasalisation...).
"""
from __future__ import annotations

import re
import unicodedata

STRESS = "ˈˌ"
PRIMARY = "ˈ"
SECONDARY = "ˌ"
# characters that attach to the *previous* base symbol in IPA
_ATTACH_LEAD = set("ʰʷʲˠˤˀ˞ʱⁿˡ")  # superscript/modifier letters and such
# unify equivalent IPA spellings so engines that use U+0261/U+02A7 etc compare equal
_SYMBOL_MAP = {
    "ɡ": "g",        # latin small letter script g -> g
    "ʧ": "tʃ",       # affricates written as single symbols -> digraphs
    "ʤ": "dʒ",
    "ʨ": "tɕ",
    "ʥ": "dʑ",
    "ɜ": "ɜ",
    "͡": "",          # tie bar
    "͜": "",
    "ˑ": "",          # half-length, not comparable across engines
    "ʴ": "ɹ",
    "ɫ": "l",
}


def normalize_symbols(ipa: str) -> str:
    s = nfc(ipa)
    for a, b in _SYMBOL_MAP.items():
        s = s.replace(a, b)
    return s


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").strip()


def strip_stress(s: str) -> str:
    return nfc(s).translate({ord("ˈ"): None, ord("ˌ"): None})


def phone_tokens(ipa: str, keep_stress: bool = True):
    """Segment an IPA string into phone tokens.

    >>> phone_tokens("lˈɔʁ")
    ['l', 'ˈɔ', 'ʁ']
    >>> phone_tokens("lˈɔʁ", keep_stress=False)
    ['l', 'ɔ', 'ʁ']
    """
    s = normalize_symbols(ipa)
    toks = []
    pending_stress = ""
    base = ""
    for ch in s:
        if ch in STRESS:
            if base:  # flush the phone that precedes the stress mark
                toks.append(pending_stress + base)
                base = ""
            if keep_stress:
                pending_stress = ch
            continue
        if ch.isspace() or ch in ".,;:|/()[]{}":
            if base:
                toks.append(pending_stress + base)
                base = ""
            pending_stress = ""
            continue
        cat = unicodedata.category(ch)
        if base and (cat in ("Mn", "Mc", "Me") or ch in _ATTACH_LEAD or cat == "Lm"):
            base += ch
            continue
        if base:
            toks.append(pending_stress + base)
        base = pending_stress + ch
        pending_stress = ""
    if base:
        toks.append(pending_stress + base)
    # merge stop+fricative pairs that form affricates (d+ʒ -> dʒ) only if the
    # original string wrote them as two symbols; gold/hyp both normalised above.
    merged = []
    for t in toks:
        if merged and merged[-1].lstrip(STRESS) in ("d", "t", "ɖ", "c", "ɟ") \
                and t.lstrip(STRESS)[:1] in ("ʒ", "ʃ", "ɕ", "ʑ"):
            stress = merged[-1][0] if merged[-1][0] in STRESS else ""
            merged[-1] = stress + merged[-1].lstrip(STRESS) + t
        else:
            merged.append(t)
    toks = merged
    return [t for t in toks if t.strip(STRESS)]


def split_stress(toks):
    """['l','ˈɔ','ʁ'] -> (['l','ɔ','ʁ'], [(1,'ˈ')])"""
    bare, marks = [], []
    for i, t in enumerate(toks):
        if t and t[0] in STRESS:
            marks.append((len(bare), t[0]))
            bare.append(t[1:])
        else:
            bare.append(t)
    return bare, marks


def lev(a, b) -> int:
    """Levenshtein distance on two sequences."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def per(gold: str, hyp: str, keep_stress: bool = True) -> tuple[float, bool]:
    """Phoneme error rate + exact match for one gold/hyp pair.

    Returns (per, exact). ``per`` is capped at 1.0+ (insertions can exceed 1).
    """
    g = phone_tokens(gold, keep_stress=keep_stress)
    h = phone_tokens(hyp, keep_stress=keep_stress)
    if not g:
        return (0.0 if not h else 1.0), (not h)
    d = lev(g, h)
    return d / len(g), (d == 0)


def stress_pattern(ipa: str):
    """Position (index among phone tokens) of each stress mark.

    >>> stress_pattern("lˈɔʁ")
    [(1, 'ˈ')]
    """
    toks = phone_tokens(ipa, keep_stress=True)
    _, marks = split_stress(toks)
    return tuple(marks)


def stress_correct(gold: str, hyp: str) -> bool | None:
    """True/False if gold carries stress, None if gold has no stress marks.

    A word matches if its stress *pattern* is identical: same number of marks,
    same phones stressed, same primary/secondary distinction. Extra stress in
    the hypothesis on an unstressed gold word counts as a miss (return False).
    """
    gp = stress_pattern(gold)
    hp = stress_pattern(hyp)
    if not gp:
        return None
    return gp == hp


def zero_width_clean(s: str) -> str:
    return re.sub("[\u200b-\u200f\u202a-\u202e\ufeff]", "", s or "")


def normalize_for_lexicon(word: str) -> str:
    """Key normalisation for WikiPron lexicon lookup."""
    w = unicodedata.normalize("NFC", zero_width_clean(word)).strip().lower()
    w = re.sub(r"\s+", " ", w)
    return w


def summarize(pairs):
    """pairs: list of (gold_ipa, hyp_ipa) -> metric dict (v2).

    Adds:
    * ``per_feat``   – feature-weighted PER (panphon): substitutions cost the
      normalised panphon feature distance of the two phones, not 1.0. This is
      the fair cross-inventory metric (espeak ɭ vs gold lʲ costs ~0.3, not 1.0).
    * ``stress_emit``– fraction of non-empty hypotheses that carry stress marks.
    * ``tone_acc``   – tone-sequence accuracy on items where gold has tones.
    """
    n = len(pairs)
    if n == 0:
        return dict(n=0, per_bare=0.0, per_stress=0.0, per_feat=0.0, exact_bare=0.0,
                    exact_stress=0.0, stress_acc=None, stress_n=0,
                    stress_emit=0.0, tone_acc=None, tone_n=0)
    d_bare = d_stress = d_feat = ex_bare = ex_stress = 0.0
    ok = tot = 0
    emit = emit_n = 0
    tone_ok = tone_n = 0
    for g, h in pairs:
        p1, e1 = per(g, h, keep_stress=False)
        p2, e2 = per(g, h, keep_stress=True)
        pf, ef = per_feat(g, h)
        d_bare += p1
        d_stress += p2
        d_feat += pf
        ex_bare += 1.0 if e1 else 0.0
        ex_stress += 1.0 if e2 else 0.0
        sc = stress_correct(g, h)
        if sc is not None:
            tot += 1
            ok += 1 if sc else 0
        if h:
            emit_n += 1
            emit += 1 if any(c in h for c in STRESS) else 0
        tg, th = tone_sequence(g), tone_sequence(h)
        if tg:
            tone_n += 1
            tone_ok += 1 if tg == th else 0
    return dict(
        n=n,
        per_bare=d_bare / n,
        per_stress=d_stress / n,
        per_feat=d_feat / n,
        exact_bare=ex_bare / n,
        exact_stress=ex_stress / n,
        stress_acc=(ok / tot) if tot else None,
        stress_n=tot,
        stress_emit=(emit / emit_n) if emit_n else 0.0,
        tone_acc=(tone_ok / tone_n) if tone_n else None,
        tone_n=tone_n,
    )


# ---------------------------------------------------------------------------
# feature-weighted comparison (panphon)
# ---------------------------------------------------------------------------
_FT = None
_DIST = None
_FEAT_CACHE = {}
_TONE_RE = re.compile("[\u00b9\u00b2\u00b3\u2070\u2074-\u2079\u02e5-\u02e9]")


def _panphon():
    global _FT, _DIST
    if _FT is None:
        try:
            from panphon.distance import Distance
            from panphon.featuretable import FeatureTable
            _FT = FeatureTable()
            _DIST = Distance()
        except Exception:
            _FT = False
    return _FT


def feature_cost(a: str, b: str) -> float:
    """Substitution cost in [0,1] from panphon feature distance (memoised)."""
    if a == b:
        return 0.0
    key = (a, b)
    if key in _FEAT_CACHE:
        return _FEAT_CACHE[key]
    if not _panphon():
        _FEAT_CACHE[key] = 1.0
        return 1.0
    try:
        d = _DIST.weighted_feature_edit_distance(a, b)
    except Exception:
        d = 2.0
    c = max(0.0, min(1.0, d / 2.0))
    _FEAT_CACHE[key] = c
    return c


def per_feat(gold: str, hyp: str) -> tuple[float, bool]:
    """Feature-weighted phoneme error rate + exact match."""
    g = [t.lstrip(STRESS) for t in phone_tokens(gold, keep_stress=False)]
    h = [t.lstrip(STRESS) for t in phone_tokens(hyp, keep_stress=False)]
    if not g:
        return (0.0 if not h else 1.0), (not h)
    # strip tone symbols from the phone lists (scored separately)
    g = [_TONE_RE.sub("", t) for t in g]
    h = [_TONE_RE.sub("", t) for t in h]
    g = [t for t in g if t]
    h = [t for t in h if t]
    if not g:
        return (0.0 if not h else 1.0), (not h)
    # Levenshtein with feature substitution costs
    prev = [float(j) for j in range(len(h) + 1)]
    for i, ca in enumerate(g, 1):
        cur = [float(i)] + [0.0] * len(h)
        for j, cb in enumerate(h, 1):
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0,
                         prev[j - 1] + feature_cost(ca, cb))
        prev = cur
    return prev[-1] / len(g), (prev[-1] < 1e-9)


def tone_sequence(ipa: str):
    """Extract a tone pattern from IPA/superscript tone marks or inline digits.

    WikiPron cmn/tha/vie use superscript tone numerals/letters; espeak pinyin
    voices emit inline digits (a5, a1). Both are normalised to a string.
    """
    s = nfc(ipa)
    tones = []
    for ch in s:
        if _TONE_RE.match(ch):
            tones.append(_TONE_DIGITS.get(ch, ch))
        elif ch.isdigit():
            tones.append(ch)
    return tuple(tones)


_TONE_DIGITS = {"\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2070": "0",
                "\u2074": "4", "\u2075": "5", "\u2076": "6", "\u2077": "7",
                "\u2078": "8", "\u2079": "9"}


def pairs_diagnostics(pairs, path, limit=20):
    """Write side-by-side gold/hyp pairs so PER numbers are auditable."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{'WORD':<20}\t{'GOLD':<28}\t{'HYP':<28}\tPER_bare\tPER_feat\n")
        for w, g, h in pairs[:limit]:
            p1, _ = per(g, h, keep_stress=False)
            p2, _ = per_feat(g, h)
            f.write(f"{w:<20}\t{g:<28}\t{h:<28}\t{p1:.2f}\t{p2:.2f}\n")


if __name__ == "__main__":
    assert phone_tokens("lˈɔʁ") == ["l", "ˈɔ", "ʁ"], phone_tokens("lˈɔʁ")
    assert phone_tokens("ˈɛm tˈeːr") == ["ˈɛ", "m", "t", "ˈeː", "r"], phone_tokens("ˈɛm tˈeːr")
    assert phone_tokens("dʒˈiːn") == ["dʒ", "ˈiː", "n"], phone_tokens("dʒˈiːn")
    assert strip_stress("dʒˈiːn") == "dʒiːn"
    assert stress_pattern("tˈeːr") == ((1, "ˈ"),)
    assert stress_correct("dʒˈiːn", "dʒiːn") is False
    assert stress_correct("dʒˈiːn", "dʒˈiːn") is True
    assert stress_correct("dʒiːn", "dʒˈiːn") is None
    print("normalize_ipa self-test OK")
    print(per("lˈɔʁ", "lˈɔʁ"), per("lˈɔʁ", "lɔʁ", keep_stress=True))
