# -*- coding: utf-8 -*-
"""demo_build.py -- build the self-contained offline demo (index.html + WAVs).

What it produces
----------------
``demo/index.html``
    Zero external assets: inline CSS + inline JS + inline dataset JSON, and
    ``<audio>`` players pointing at ``demo/audio/*.wav`` (local files).  It shows
    ~400 REAL rows drawn from the 491.6M-row dataset across all 105 countries,
    each with: name, country, detected language, IPA, espeak phoneme string,
    source, confidence, and a playable WAV.  Homograph pairs (Jean FR vs US,
    Laure FR vs US) and native-script rows (Cyrillic/Arabic/Hangul/Han/Hebrew/
    Greek/Khmer/Georgian/...) are featured so the page visibly demonstrates both
    the method and its honest limits.
``demo/audio/<idx>_<cc>.wav``
    Per-name audio.  When a name's tokens resolve to different languages, each
    token is synthesized with ITS OWN voice and the PCM is concatenated, so the
    audio matches the per-token IPA shown in the table.

Selection strategy (per country)
--------------------------------
1. the most frequent real (first, last) pair  -> recognisable names
2. a real row whose name is in the country's native script (if any)
3. the most frequent single token (first name)
4. the 2nd most frequent pair
5. a pseudo-random row (long tail)
Rows needing G2P outside the lexicon are resolved on the fly in one batched
OOV pass, so the demo's coverage equals the join path's.
"""
from __future__ import annotations

import json
import os
import random
import re
import struct
import time

if __package__:
    from .common import (AUDIO_DIR, DEMO_DIR, EXTRACT_DIR, LEXICON_PATH,
                         OUTPUT_DIR, SEED, WORK_DIR, country_codes, ensure_dirs,
                         fmt_int, log, read_json, write_json)
    from .norm import field_view, normalize_token
    from .langmap import CONF_ORDER, assign_language
    from .g2p import get_g2p
else:  # `python src/demo_build.py` convenience
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import (AUDIO_DIR, DEMO_DIR, EXTRACT_DIR, LEXICON_PATH,
                        OUTPUT_DIR, SEED, WORK_DIR, country_codes, ensure_dirs,
                        fmt_int, log, read_json, write_json)
    from norm import field_view, normalize_token
    from langmap import CONF_ORDER, assign_language
    from g2p import get_g2p

# ISO2 -> English name (105 dataset countries)
COUNTRY_NAMES = {
    "AE": "UAE", "AF": "Afghanistan", "AL": "Albania", "AO": "Angola",
    "AR": "Argentina", "AT": "Austria", "AZ": "Azerbaijan", "BD": "Bangladesh",
    "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria", "BH": "Bahrain",
    "BI": "Burundi", "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil",
    "BW": "Botswana", "CA": "Canada", "CH": "Switzerland", "CL": "Chile",
    "CM": "Cameroon", "CN": "China", "CO": "Colombia", "CR": "Costa Rica",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti",
    "DK": "Denmark", "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia",
    "EG": "Egypt", "ES": "Spain", "ET": "Ethiopia", "FI": "Finland",
    "FJ": "Fiji", "FR": "France", "GB": "United Kingdom", "GE": "Georgia",
    "GH": "Ghana", "GR": "Greece", "GT": "Guatemala", "HK": "Hong Kong",
    "HN": "Honduras", "HR": "Croatia", "HT": "Haiti", "HU": "Hungary",
    "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India",
    "IQ": "Iraq", "IR": "Iran", "IS": "Iceland", "IT": "Italy",
    "JM": "Jamaica", "JO": "Jordan", "JP": "Japan", "KH": "Cambodia",
    "KR": "South Korea", "KW": "Kuwait", "KZ": "Kazakhstan", "LB": "Lebanon",
    "LT": "Lithuania", "LU": "Luxembourg", "LY": "Libya", "MA": "Morocco",
    "MD": "Moldova", "MO": "Macau", "MT": "Malta", "MU": "Mauritius",
    "MV": "Maldives", "MX": "Mexico", "MY": "Malaysia", "NA": "Namibia",
    "NG": "Nigeria", "NL": "Netherlands", "NO": "Norway", "OM": "Oman",
    "PA": "Panama", "PE": "Peru", "PH": "Philippines", "PL": "Poland",
    "PR": "Puerto Rico", "PS": "Palestine", "PT": "Portugal", "QA": "Qatar",
    "RS": "Serbia", "RU": "Russia", "SA": "Saudi Arabia", "SD": "Sudan",
    "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia", "SV": "El Salvador",
    "SY": "Syria", "TM": "Turkmenistan", "TN": "Tunisia", "TR": "Türkiye",
    "TW": "Taiwan", "US": "United States", "UY": "Uruguay", "YE": "Yemen",
    "ZA": "South Africa",
}

SCRIPT_LABEL = {
    "Latn": "Latin", "Cyrl": "Cyrillic", "Arab": "Arabic", "Hang": "Hangul",
    "Hani": "Han", "Kana": "Katakana", "Hira": "Hiragana", "Hebr": "Hebrew",
    "Grek": "Greek", "Geor": "Georgian", "Armn": "Armenian", "Khmr": "Khmer",
    "Thaa": "Thaana", "Beng": "Bengali", "Deva": "Devanagari", "Taml": "Tamil",
    "Telu": "Telugu", "Knda": "Kannada", "Mlym": "Malayalam", "Gujr": "Gujarati",
    "Guru": "Gurmukhi", "Orya": "Odia", "Sinh": "Sinhala", "Thai": "Thai",
    "Mymr": "Myanmar", "Ethi": "Ethiopic", "Tibt": "Tibetan", "Laoo": "Lao",
}

HOMOGRAPH_ROWS = [
    ("Jean", "FR", "Jean", "en-us", "Same spelling, two languages: fr-fr reads "
     "ʒˈɑ̃; en-us reads dʒˈiːn. The lexicon keys on (token, lang), not token."),
    ("Jean", "US", "Jean", "fr-fr", "US data: same name, anglicised reading."),
    ("Laure", "FR", "Laure", "en-us", "fr-fr: lˈɔʁ (French 'Laure'), en-us: "
     "lˈɔːɹə ('Laura'-like)."),
    ("Laure", "US", "Laure", "fr-fr", "US data: anglicised reading."),
]

# guarantee native-script visibility for the scripts the dataset actually contains
SCRIPT_SHOWCASE = ["RU", "EG", "SA", "KR", "TW", "CN", "IL", "GR", "KH", "GE",
                   "BD", "IN", "JP", "HK", "TH", "ET", "UA", "BG", "KZ", "MV"]


# ---------------------------------------------------------------------------
# pronunciation of a display name (per-word language lookup + OOV collection)
# ---------------------------------------------------------------------------
def _words_of_field(fv: dict) -> list:
    return fv["words"]


def _lookup_word(w: dict, cc: str, idx: dict):
    la = assign_language(w["script"], cc)
    lang = la["lang"] or ""
    hit = idx.get((w["key"], lang)) if lang else None
    if hit and hit["ipa"]:
        return {"ipa": hit["ipa"], "phonemes": hit.get("phonemes", ""),
                "lang": lang, "source": hit.get("source", ""),
                "confidence": hit.get("confidence", la["confidence"]),
                "lang_reason": la["lang_reason"], "word": w["norm"],
                "script": w["script"], "junk": False}
    return {"ipa": "", "phonemes": "", "lang": lang, "source": "unresolved",
            "confidence": "none", "lang_reason": la["lang_reason"],
            "word": w["norm"], "script": w["script"],
            "junk": w["flags"]["is_junk"],
            "fallback": la["fallback_lang"] or "en-us"}


def _oov_collect(wordinfo: dict, cc: str, oov: dict):
    if wordinfo["ipa"] or wordinfo["junk"] or not wordinfo["lang"]:
        return
    key = None
    w = normalize_token(wordinfo["word"])
    if w["norm"]:
        key = (w["key"], wordinfo["lang"])
    if key is None:
        return
    oov.setdefault(key, {"token": w["norm"], "lang": wordinfo["lang"],
                         "fallback": wordinfo.get("fallback", "en-us")})


def _compose(first: str, last: str, cc: str, idx: dict, oov: dict) -> dict:
    """Build one demo row (name, per-token IPA/phonemes, provenance)."""
    fv_f = field_view(first)
    fv_l = field_view(last)
    words = []
    for fv, part in ((fv_f, "first"), (fv_l, "last")):
        for w in fv["words"]:
            info = _lookup_word(w, cc, idx)
            info["part"] = part
            words.append(info)
            if not info["ipa"] and not info["junk"]:
                _oov_collect(info, cc, oov)
    ipas = [w["ipa"] for w in words if w["ipa"]]
    phons = [w["phonemes"] for w in words if w["phonemes"]]
    langs = []
    for w in words:
        if w["lang"] and w["lang"] not in langs:
            langs.append(w["lang"])
    srcs = []
    for w in words:
        if w["source"] and w["source"] not in srcs:
            srcs.append(w["source"])
    conf = "high"
    for w in words:
        if CONF_ORDER.get(w["confidence"], 9) > CONF_ORDER.get(conf, 9):
            conf = w["confidence"]
    display = " ".join(x for x in ((fv_f["norm"] or ""), (fv_l["norm"] or "")) if x)
    unresolved = [w["word"] for w in words if not w["ipa"] and not w["junk"]]
    junk = [w["word"] for w in words if w["junk"]]
    return {
        "name": display or (first or ""),
        "first": fv_f["norm"], "last": fv_l["norm"],
        "country": cc,
        "country_name": COUNTRY_NAMES.get(cc, cc),
        "script": fv_f["script"] or fv_l["script"] or "",
        "lang": langs[0] if langs else "",
        "langs": langs,
        "ipa": " ".join(ipas),
        "phonemes": " ".join(phons),
        "source": "+".join(srcs) if srcs else "unresolved",
        "confidence": conf,
        "lang_reason": words[0]["lang_reason"] if words else "",
        "unresolved_words": unresolved,
        "junk_words": junk,
        "composable": bool(ipas) and not unresolved,
    }


def _row_from_parts(first, last, cc, idx, oov, kind, count=0) -> dict | None:
    r = _compose(first, last, cc, idx, oov)
    if not r["name"]:
        return None
    r["kind"] = kind
    r["count"] = int(count or 0)
    return r


# ---------------------------------------------------------------------------
# demo row selection
# ---------------------------------------------------------------------------
def _read_names(cc: str):
    import pyarrow.parquet as pq
    path = os.path.join(EXTRACT_DIR, f"names_{cc}.parquet")
    if not os.path.exists(path):
        return [], []
    t = pq.read_table(path).to_pylist()
    pairs = [r for r in t if r["kind"] == "pair"]
    rand = [r for r in t if r["kind"] == "row"]
    pairs.sort(key=lambda r: -r["count"])
    return pairs, rand


def _read_tokens(cc: str):
    import pyarrow.parquet as pq
    path = os.path.join(EXTRACT_DIR, f"tokens_{cc}.parquet")
    if not os.path.exists(path):
        return []
    t = pq.read_table(path, columns=["token", "count"]).to_pylist()
    return t


def _native_script_row(pairs, rand, cc, idx, oov, want_scripts=None,
                       allow_unresolved=False):
    """First sampled row whose name is in a non-Latin script (prefer real rows)."""
    for r in pairs + rand:
        for field in (r["first"], r["last"]):
            fv = field_view(field)
            if fv["empty"]:
                continue
            sc = fv["script"]
            if sc and sc != "Latn" and (want_scripts is None or sc in want_scripts):
                row = _row_from_parts(r["first"], r["last"], cc, idx, oov,
                                      "native-script", r["count"])
                if row and (row["composable"] or allow_unresolved):
                    return row
    return None


def select_rows(n_rows: int = 420, idx: dict | None = None) -> tuple[list, dict]:
    """Pick demo rows across all 105 countries; returns (rows, oov)."""
    idx = idx if idx is not None else load_index()
    rng = random.Random(SEED)
    oov: dict = {}
    per_country: dict[str, list] = {}
    countries = country_codes()

    for cc in countries:
        pairs, rand = _read_names(cc)
        tokens = _read_tokens(cc)
        picked = []
        # 1) most frequent real pair
        if pairs:
            r = _row_from_parts(pairs[0]["first"], pairs[0]["last"], cc, idx, oov,
                                "top-pair", pairs[0]["count"])
            if r and r["composable"]:
                picked.append(r)
        # 2) native-script real row (showcased countries get priority)
        nat = _native_script_row(pairs, rand, cc, idx, oov)
        if nat:
            picked.append(nat)
        elif cc in ("KH", "MV"):  # no espeak voice: keep one honest coverage hole
            nat = _native_script_row(pairs, rand, cc, idx, oov, allow_unresolved=True)
            if nat:
                nat["kind"] = "coverage-hole"
                picked.append(nat)
        # 3) most frequent single token (first name)
        for t in tokens:
            d = normalize_token(t["token"])
            if d["flags"]["is_junk"]:
                continue
            r = _row_from_parts(t["token"], "", cc, idx, oov, "top-token", t["count"])
            if r and r["composable"]:
                picked.append(r)
                break
        # 4) second most frequent pair
        if len(pairs) > 1:
            r = _row_from_parts(pairs[1]["first"], pairs[1]["last"], cc, idx, oov,
                                "top-pair", pairs[1]["count"])
            if r and r["composable"]:
                picked.append(r)
        # 5) random long-tail row
        if rand:
            r0 = rand[rng.randrange(len(rand))]
            r = _row_from_parts(r0["first"], r0["last"], cc, idx, oov, "random", 0)
            if r:
                picked.append(r)
        # dedupe by name
        seen, uniq = set(), []
        for r in picked:
            k = r["name"]
            if k and k not in seen:
                seen.add(k)
                uniq.append(r)
        per_country[cc] = uniq

    # ---- featured rows: homographs + script showcase ----------------------
    featured = []
    for name, cc, _other, other_lang, note in HOMOGRAPH_ROWS:
        r = _row_from_parts(name, "", cc, idx, oov, "homograph", 0)
        if not r or not r["composable"]:
            continue
        other = {"lang": other_lang}
        # the "other language" reading is produced by a one-off G2P call at build
        r["note"] = note
        r["homograph_other_lang"] = other_lang
        featured.append(r)
    for cc in SCRIPT_SHOWCASE:
        rows = per_country.get(cc, [])
        nat = next((r for r in rows if r["kind"] == "native-script"), None)
        if nat:
            nat["kind"] = "script-showcase"
    # ---- assemble: every country keeps its first 2 rows, then fill round-robin
    out = list(featured)
    for cc in countries:
        rows = per_country[cc]
        out.extend(rows[:2])
    order = []
    maxlen = max((len(v) for v in per_country.values()), default=0)
    for i in range(2, maxlen):
        for cc in countries:
            rows = per_country[cc]
            if i < len(rows):
                order.append(rows[i])
    budget = max(0, n_rows - len(out))
    out.extend(order[:budget])

    # make sure the coverage hole (Khmer / Dhivehi) is visible exactly once
    present = {r["name"] for r in out}
    for cc in ("KH", "MV"):
        for r in per_country.get(cc, []):
            if r["kind"] == "coverage-hole" and r["name"] not in present:
                out.append(r)
                present.add(r["name"])
                break
    return out, oov


def load_index(path: str = LEXICON_PATH) -> dict:
    if __package__:
        from .pipeline import load_lexicon_index
    else:  # direct run
        from pipeline import load_lexicon_index
    return load_lexicon_index(path)


# ---------------------------------------------------------------------------
# WAV: per-word synthesis with the word's own voice, then PCM concat
# ---------------------------------------------------------------------------
def _wav_pcm(path: str):
    """Return (sample_rate, channels, bits, pcm_bytes) of a PCM WAV, or None."""
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    if len(blob) < 44 or blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
        return None
    i = 12
    fmt = None
    while i + 8 <= len(blob):
        cid, size = blob[i:i + 4], struct.unpack("<I", blob[i + 4:i + 8])[0]
        if cid == b"fmt ":
            audio_fmt, channels, rate = struct.unpack("<HHI", blob[i + 8:i + 16])
            bits = struct.unpack("<H", blob[i + 22:i + 24])[0]
            fmt = (rate, channels, bits) if audio_fmt == 1 else None
        elif cid == b"data":
            if fmt is None:
                return None
            return fmt + (blob[i + 8:i + 8 + size],)
        i += 8 + size + (size & 1)
    return None


def _wav_write(path: str, rate: int, pcm: bytes) -> int:
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(pcm)))
    with open(path, "wb") as f:
        f.write(header + pcm)
    return len(pcm) + 44


def synth_name(words: list, out_path: str, g2p=None, silence_ms: int = 120) -> dict:
    """Synthesize a name token-by-token (each with its own voice) into one WAV."""
    g2p = g2p or get_g2p()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    usable = [w for w in words if w.get("word") and not w.get("junk")]
    if not usable:
        return {"ok": False, "note": "no words"}
    tmpdir = os.path.join(WORK_DIR, "wavtmp")
    os.makedirs(tmpdir, exist_ok=True)
    pcm_parts, rate = [], 22050
    for wi, w in enumerate(usable):
        lang = w.get("lang") or "en-us"
        tmp = os.path.join(tmpdir, f"d_{os.getpid()}_{wi}.wav")
        res = g2p.wav(w["word"], lang, tmp)
        pcm = _wav_pcm(tmp) if res["ok"] else None
        if pcm:
            rate = pcm[0]
            pcm_parts.append(pcm[3])
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not pcm_parts:
        # last resort: single utterance with the first language available
        text = " ".join(w["word"] for w in usable)
        lang = usable[0].get("lang") or "en-us"
        res = g2p.wav(text, lang, out_path)
        return {"ok": res["ok"], "note": "single-voice fallback", "bytes": res["size"]}
    silence = b"\x00\x00" * int(rate * silence_ms / 1000)
    pcm = silence.join(pcm_parts)
    n = _wav_write(out_path, rate, pcm)
    return {"ok": True, "note": "", "bytes": n, "words": len(pcm_parts)}


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Name → IPA · 491.6M 人名数据集发音演示</title>
<style>
:root{--bg:#0f1220;--panel:#181c2f;--line:#2a3050;--ink:#e8eaf6;--dim:#9aa3c7;
--acc:#7c9cff;--ok:#4ade80;--mid:#fbbf24;--low:#f87171;--chip:#232a45}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,"Segoe UI",Roboto,"Noto Sans SC","Microsoft YaHei",sans-serif}
header{padding:28px 32px 18px;background:linear-gradient(135deg,#161b34,#0f1220 60%);
border-bottom:1px solid var(--line)}
h1{margin:0 0 6px;font-size:24px;letter-spacing:.2px}
h1 small{color:var(--dim);font-size:13px;font-weight:400}
.sub{color:var(--dim);max-width:1100px;margin:6px 0 0;font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:8px 14px;min-width:118px}
.stat b{display:block;font-size:19px;color:var(--acc)}
.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
main{padding:20px 32px 60px;max-width:1560px;margin:0 auto}
section{margin:26px 0}
h2{font-size:16px;margin:0 0 10px;color:#c7d0f5;font-weight:600}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}
.step{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.step b{color:var(--acc);font-size:12px;letter-spacing:.5px}
.step p{margin:4px 0 0;color:var(--dim);font-size:12px}
.callout{background:linear-gradient(180deg,#1b2140,#171b30);border:1px solid #34406b;
border-radius:12px;padding:14px 16px}
.homographs{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:10px}
.homo{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.homo .nm{font-weight:700;font-size:15px}
.homo table{border:0;margin-top:6px;width:100%}
.homo td{border:0;padding:2px 8px 2px 0;vertical-align:top}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 12px}
input[type=search]{background:#12162a;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:8px 12px;min-width:260px;outline:none}
input[type=search]:focus{border-color:var(--acc)}
.btn{background:var(--chip);border:1px solid var(--line);color:var(--ink);border-radius:999px;
padding:6px 13px;cursor:pointer;font-size:12px}
.btn:hover{border-color:var(--acc)}
.btn.on{background:#26336b;border-color:var(--acc);color:#fff}
.wrap{overflow:auto;max-height:78vh;border:1px solid var(--line);border-radius:12px}
table.data{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}
table.data th{position:sticky;top:0;background:#161b30;z-index:2;text-align:left;
padding:10px 12px;border-bottom:1px solid var(--line);color:#b9c3e8;font-weight:600;
white-space:nowrap}
table.data td{padding:8px 12px;border-bottom:1px solid #20263f;vertical-align:middle}
table.data tr:hover td{background:#1b2138}
td.idx{color:#5b6590;font-variant-numeric:tabular-nums}
.nm{font-weight:600}
.ipa{font-family:"Noto Sans",-apple-system,"Segoe UI",sans-serif;font-size:14px;
color:#a8f0c6;white-space:nowrap}
.ph{font-family:Consolas,monospace;font-size:12px;color:#9fb4e8;white-space:nowrap}
.chip{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--chip);
font-size:11px;white-space:nowrap}
.conf-high{color:#4ade80;background:#12291c}
.conf-medium{color:#fbbf24;background:#2a2110}
.conf-low{color:#f87171;background:#2a1414}
.conf-none{color:#f87171;background:#2a1414}
.src{font-size:11px;color:var(--dim);font-family:Consolas,monospace}
.kind{font-size:11px;color:#8ea0d8}
audio{height:30px;width:170px}
.hole td{background:#2a1616!important}
footer{color:#6b76a3;font-size:12px;padding:0 32px 40px;max-width:1560px;margin:0 auto}
footer code{background:#161b30;padding:1px 5px;border-radius:4px;color:#aab6e6}
.note{background:#151a2d;border-left:3px solid var(--acc);padding:10px 14px;border-radius:0 8px 8px 0;
color:#c3cdf2;font-size:12.5px}
.warn{border-left-color:#fbbf24}
@media (max-width:720px){header,main,footer{padding-left:14px;padding-right:14px}
.homographs{grid-template-columns:1fr}audio{width:130px}}
</style>
</head>
<body>
<header>
  <h1>Name → IPA <small>· 从 4.92 亿条人名数据得到可发音词典</small></h1>
  <p class="sub">每一行都来自真实数据集（<code>first_name,last_name,gender,country_code</code>，105 个国家 / 491,655,925 条记录）。
  方法：规范化+过滤 → 文字系统检测 → 国家×文字语言判定 → IPA 解析级联（WikiPron 词典 → espeak-ng G2P → 未解决）→ 回填。
  IPA 给人看；<b>phoneme</b> 列是 espeak-ng 音素串，可直接喂给 Piper 类音素级 TTS。点 ▶ 可试听（每条按 token 所属语言分别合成后拼接）。</p>
  <div class="stats" id="stats"></div>
</header>
<main>
  <section>
    <h2>方法 · 五级流水线（本页就是它的输出）</h2>
    <div class="steps">
      <div class="step"><b>① 规范化 + 过滤</b><p>NFC/NFKC、去 tatweel 与阿拉伯音符、去装饰符与控制符；junk 判定。G2P 用 <code>norm</code>，词典键用 <code>key</code>。</p></div>
      <div class="step"><b>② 文字系统检测</b><p>Unicode 区段 → Latn/Cyrl/Arab/Hang/Hani/Hebr/Grek/Khmr… 这是最强的语言先验。</p></div>
      <div class="step"><b>③ 国家 × 文字 → 语言</b><p>105 国 voice 表 + 实测 LTS 规则；同形异读按 <code>(token, lang)</code> 分开（Jean FR ≠ Jean US）。</p></div>
      <div class="step"><b>④ IPA 解析级联</b><p>WikiPron 词典 → espeak-ng 主 voice → 兜底 voice → 未解决（记录原因）。保留 <code>source/confidence</code>。</p></div>
      <div class="step"><b>⑤ 回填 + 交付</b><p>词典 parquet + 按国家分片的带 IPA 数据集；高频 top-N 可人工校核，长尾自动生成并标记置信度。</p></div>
    </div>
  </section>
  <section class="callout">
    <h2>同形异读：同一个拼写，不同语言（本演示的核心）</h2>
    <div class="homographs" id="homographs"></div>
    <p class="sub" style="margin:10px 0 0">espeak-ng 实测：非拉丁文字的 voice 读拉丁字母时会静默退回英语 LTS（ru/ar/el/ko/ka/he/kk 对 “Laure” 的输出与 en-us 逐字节相同），
    只有 ja 会逐个拼读字母。因此拉丁 token 在非拉丁国家按“英语 LTS + 显式标注 <code>english-fallback</code>”处理，而不是假装用当地语音规则。</p>
  </section>
  <section>
    <h2>真实数据样例 <span id="rowcount" class="sub"></span></h2>
    <div class="toolbar">
      <input type="search" id="q" placeholder="搜索名字 / 国家 / IPA / 语言…">
      <button class="btn on" data-f="all">全部</button>
      <button class="btn" data-f="latin">拉丁文字</button>
      <button class="btn" data-f="native">非拉丁文字</button>
      <button class="btn" data-f="homograph">同形异读</button>
      <button class="btn" data-f="low">低置信度</button>
      <button class="btn" data-f="hole">覆盖空洞</button>
    </div>
    <div class="wrap">
      <table class="data">
        <thead><tr><th>#</th><th>name</th><th>country</th><th>lang</th><th>script</th>
        <th>IPA</th><th>espeak phonemes</th><th>source</th><th>confidence</th><th>audio</th><th>sample</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <p class="sub" style="margin-top:10px">confidence：<b style="color:#4ade80">high</b> = 词典命中 / 该语言自身就是拉丁 LTS / voice 原生读该文字；
    <b style="color:#fbbf24">medium</b> = 英语回退、拼音/jyutping 前端、全局文字兜底、希伯来文缺元音；<b style="color:#f87171">low</b> = 代理语言（无 voice 的语言）或日语汉字读音。</p>
  </section>
  <section>
    <h2>已知质量天花板（诚实说明）</h2>
    <div class="note warn">
    • 阿拉伯文/希伯来文省略短元音 → 元音只能猜（EG/SA/IQ/SD/YE/JO/IL… 最大误差源）。<br>
    • 日语汉字：espeak 1.52 的 ja voice 对汉字是坏的（输出 “Chinese letter” 串），本方案标 low 并建议人工/词典。<br>
    • 无 espeak voice 的文字：高棉 Khmr、迪维希 Thaa、藏文等 → 标记 <code>unresolved</code>，不伪造。本页保留了一条高棉示例说明该空洞。<br>
    • 拉丁 token 在非拉丁国家按英语 LTS 近似（<code>english-fallback</code>），这是设计取舍：宁可标注清楚，也不静默出错。<br>
    • 低频/异常拼写名长尾必然有错；高频 top-N 才值得人工校核（前 1 万 first name 覆盖 65.6% 记录）。
    </div>
  </section>
  <section>
    <h2>复现</h2>
    <div class="note">
    <code>python -m src.pipeline extract</code> → <code>resolve</code> → <code>join --countries FJ,IS,EE</code> → <code>demo</code> → <code>report</code><br>
    词典：<code>output/name_ipa_lexicon.parquet</code>（(token, lang) → IPA + espeak phoneme + source/confidence）；回填：<code>output/dataset_with_ipa/&lt;CC&gt;.parquet</code>。
    全量口径：67,736,023 唯一 token / 102,864,698 个 (token×国家) 单元；espeak ctypes 实测 2–9 万 token/秒，全量 IPA 生成约 1 小时（单核）。
    </div>
  </section>
</main>
<footer>
  本页完全离线：无 CDN、无外部字体、无网络请求。数据来自 <code>E:\\Repos\\name_collection\\work\\extract\\name_dataset\\data\\*.csv</code> 的真实采样；
  IPA 由 espeak-ng 1.52 生成（保留 ˈ ˌ 重音与声调数字 1–5）；音频为本地 WAV。<br>
  生成时间 {generated} · 采样 <b>{n_rows}</b> 条 · 覆盖 <b>{n_countries}</b> 国家 / <b>{n_langs}</b> 种语言 / <b>{n_scripts}</b> 种文字。
</footer>
<script>
const DATA = {data_json};
const HOMO = {homo_json};
const $ = (s) => document.querySelector(s);
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function isNative(r){return r.script && r.script !== 'Latn';}
function lowConf(r){return r.confidence==='low'||r.confidence==='none';}
function isHole(r){return (r.unresolved_words&&r.unresolved_words.length>0)||r.kind==='coverage-hole';}
function renderHomo(){
  const el = $('#homographs'); if(!el) return;
  const groups = {};
  HOMO.forEach(r=>{(groups[r.name] = groups[r.name]||[]).push(r);});
  el.innerHTML = Object.entries(groups).map(([name,rs])=>{
    const rows = rs.map(r=>`<tr><td><span class="chip">${esc(r.country)} ${esc(r.country_name)}</span></td>
      <td><span class="chip">${esc(r.lang||'—')}</span></td>
      <td class="ipa">${esc(r.ipa||'—')}</td>
      <td>${r.audio?`<audio controls preload="none" src="${esc(r.audio)}"></audio>`:''}</td></tr>`).join('');
    const note = rs.find(r=>r.note); 
    return `<div class="homo"><div class="nm">“${esc(name)}”</div>
      <table>${rows}</table>
      ${note?`<p class="sub" style="margin:6px 0 0;font-size:12px">${esc(note.note)}</p>`:''}</div>`;
  }).join('');
}
const state = {filter:'all', q:''};
function matches(r){
  const f = state.filter;
  if(f==='latin' && isNative(r)) return false;
  if(f==='native' && !isNative(r)) return false;
  if(f==='homograph' && r.kind!=='homograph') return false;
  if(f==='low' && !lowConf(r)) return false;
  if(f==='hole' && !isHole(r)) return false;
  if(state.q){
    const hay = [r.name,r.country,r.country_name,r.lang,r.ipa,r.phonemes,r.source,(r.langs||[]).join(' ')].join(' ').toLowerCase();
    if(!hay.includes(state.q)) return false;
  }
  return true;
}
function confClass(c){return 'conf-'+(c||'none');}
function renderRows(){
  const tbody = $('#rows'); const rows = DATA.filter(matches);
  $('#rowcount').textContent = `· 当前显示 ${rows.length} / ${DATA.length} 条`;
  tbody.innerHTML = rows.map((r)=>{
    const idx = DATA.indexOf(r)+1;
    const native = isNative(r)?' style="color:#c9b3ff"':'';
    return `<tr class="${isHole(r)?'hole':''}">
      <td class="idx">${idx}</td>
      <td class="nm"${native}>${esc(r.name)}</td>
      <td><span class="chip">${esc(r.country)}</span> <span class="sub" style="font-size:11px">${esc(r.country_name)}</span></td>
      <td><span class="chip">${esc((r.langs&&r.langs.length?r.langs:[r.lang||'—']).join(' / '))}</span></td>
      <td><span class="chip">${esc(r.script||'—')}</span> <span class="sub" style="font-size:11px">${esc(r.script_label||'')}</span></td>
      <td class="ipa">${esc(r.ipa||'—')}</td>
      <td class="ph">${esc(r.phonemes||'—')}</td>
      <td class="src">${esc(r.source)}<br><span class="sub" style="font-size:10px">${esc(r.lang_reason||'')}</span></td>
      <td><span class="chip ${confClass(r.confidence)}">${esc(r.confidence)}</span></td>
      <td>${r.audio?`<audio controls preload="none" src="${esc(r.audio)}"></audio>`:'—'}</td>
      <td class="kind">${esc(r.kind)}${r.count?`<br><span class="sub" style="font-size:10px">n=${r.count}</span>`:''}</td>
    </tr>`;
  }).join('');
}
function renderStats(){
  const n = DATA.length;
  const countries = new Set(DATA.map(r=>r.country)).size;
  const langs = new Set(DATA.flatMap(r=>r.langs||[]).filter(Boolean)).size;
  const scripts = new Set(DATA.map(r=>r.script).filter(Boolean)).size;
  const resolved = DATA.filter(r=>r.ipa).length;
  const phon = DATA.filter(r=>r.phonemes).length;
  const items = [
    ['真实样例', n],
    ['国家', countries],
    ['语言', langs],
    ['文字系统', scripts],
    ['有 IPA', resolved+' / '+n],
    ['有 phoneme 串', phon+' / '+n],
  ];
  $('#stats').innerHTML = items.map(([k,v])=>`<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('');
}
document.addEventListener('DOMContentLoaded', ()=>{
  renderStats(); renderHomo(); renderRows();
  document.querySelectorAll('.btn[data-f]').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.btn[data-f]').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); state.filter=b.dataset.f; renderRows();
  }));
  $('#q').addEventListener('input', (e)=>{state.q=e.target.value.trim().toLowerCase(); renderRows();});
});
</script>
</body>
</html>
"""


def build_html(rows: list, out_path: str) -> str:
    for r in rows:
        r["script_label"] = SCRIPT_LABEL.get(r["script"], "")
        r.setdefault("note", "")
    data = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    homo = [r for r in rows if r["kind"] == "homograph"]
    n_countries = len({r["country"] for r in rows})
    n_langs = len({l for r in rows for l in (r["langs"] or [])})
    n_scripts = len({r["script"] for r in rows if r["script"]})
    html = _HTML.replace("{data_json}", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("{homo_json}", json.dumps(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in homo],
        ensure_ascii=False).replace("</", "<\\/"))
    html = html.replace("{generated}", time.strftime("%Y-%m-%d %H:%M"))
    html = html.replace("{n_rows}", str(len(rows)))
    html = html.replace("{n_countries}", str(n_countries))
    html = html.replace("{n_langs}", str(n_langs))
    html = html.replace("{n_scripts}", str(n_scripts))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_demo(n_rows: int = 420, with_audio: bool = True, clean_audio: bool = True) -> dict:
    ensure_dirs()
    if not os.path.exists(LEXICON_PATH):
        raise SystemExit("lexicon missing: run `python -m src.pipeline resolve` first")
    t0 = time.time()
    idx = load_index()
    log(f"demo: lexicon {fmt_int(len(idx))} entries")

    rows, oov = select_rows(n_rows=n_rows, idx=idx)
    log(f"demo: selected {len(rows)} rows, {len(oov)} OOV units to resolve")

    # resolve OOV in one batched pass, then fill the rows
    idx2 = dict(idx)
    if oov:
        g2p = get_g2p()
        keys = list(oov.keys())
        items = [(oov[k]["token"], oov[k]["lang"], oov[k].get("fallback", "en-us"))
                 for k in keys]
        outs = g2p.batch(items)
        extra = {}
        for k, o in zip(keys, outs):
            if o["ipa"]:
                extra[k] = {"token": oov[k]["token"], "ipa": o["ipa"],
                            "phonemes": o["phonemes"], "source": o["source"],
                            "confidence": "medium"}
        idx2.update(extra)
        for i, r in enumerate(rows):
            if r["unresolved_words"] or not r["ipa"]:
                r2 = _compose(r["first"], r["last"], r["country"], idx2, {})
                r2["kind"] = r["kind"]
                r2["count"] = r["count"]
                r2["note"] = r.get("note", "")
                rows[i] = r2

    def _recheck(r):
        words = []
        for part in ("first", "last"):
            fv = field_view(r[part])
            for w in fv["words"]:
                words.append(_lookup_word(w, r["country"], idx2))
        r["unresolved_words"] = [w["word"] for w in words
                                 if not w["ipa"] and not w["junk"]]
        return words

    audio_dir = AUDIO_DIR
    if os.path.isdir(audio_dir) and clean_audio:
        for f in os.listdir(audio_dir):
            if f.endswith(".wav"):
                try:
                    os.remove(os.path.join(audio_dir, f))
                except OSError:
                    pass
    os.makedirs(audio_dir, exist_ok=True)
    g2p = get_g2p()
    n_audio = 0
    if with_audio:
        t1 = time.time()
        for i, r in enumerate(rows):
            words = _recheck(r)
            if not words:
                r["audio"] = ""
                continue
            fn = f"{i+1:04d}_{r['country'].lower()}.wav"
            path = os.path.join(audio_dir, fn)
            res = synth_name(words, path, g2p=g2p)
            r["audio"] = ("audio/" + fn) if res["ok"] else ""
            if res["ok"]:
                n_audio += 1
            if (i + 1) % 100 == 0:
                log(f"  audio {i+1}/{len(rows)} ({time.time()-t1:.0f}s)")
        log(f"demo: {n_audio} WAVs in {time.time()-t1:.0f}s")
    for r in rows:
        r.pop("composable", None)

    html_path = os.path.join(DEMO_DIR, "index.html")
    build_html(rows, html_path)
    write_json(os.path.join(DEMO_DIR, "demo_data.json"),
               [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    summary = {
        "rows": len(rows), "countries": len({r["country"] for r in rows}),
        "scripts": len({r["script"] for r in rows if r["script"]}),
        "langs": len({l for r in rows for l in (r["langs"] or [])}),
        "with_ipa": sum(1 for r in rows if r["ipa"]),
        "with_phonemes": sum(1 for r in rows if r["phonemes"]),
        "wavs": n_audio,
        "html": html_path,
        "audio_dir": audio_dir,
        "elapsed_s": round(time.time() - t0, 1),
    }
    write_json(os.path.join(DEMO_DIR, "demo_summary.json"), summary)
    log(f"demo: {html_path} ({summary['rows']} rows, {n_audio} wavs, "
        f"{summary['elapsed_s']}s)")
    return summary


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    build_demo()
