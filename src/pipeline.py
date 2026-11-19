# -*- coding: utf-8 -*-
"""pipeline.py -- name -> IPA for the 491.6M-row name dataset. Library + CLI.

Implements the five-stage method documented in ``docs/METHOD.md``::

    extract  (src/extract.py)  raw CSVs -> per-country unique tokens + name samples
    resolve  (this file)       token -> script -> language -> (lexicon | G2P) -> lexicon parquet
    join     (this file)       stream a country CSV, attach IPA per row -> parquet shards
    demo     (src/demo_build.py)  self-contained HTML + WAVs
    report   (src/report.py)   output/QUALITY_REPORT.md

Key artifacts
-------------
``output/name_ipa_lexicon.parquet``
    The reusable dictionary.  Keyed by **(token_key, lang)** -- NOT token alone --
    so homographs get one pronunciation per language (Jean/fr-fr vs Jean/en-us).
    Columns: token, key, lang, ipa, phonemes, script, source, confidence,
    lang_reason, confidence_reason, n_records, n_units, n_countries, top_country.
``output/dataset_with_ipa/<CC>.parquet``
    The original rows + ``first_ipa`` / ``last_ipa`` / ``ipa_full`` + provenance.
``output/coverage_by_country.csv``, ``output/coverage_by_language.csv``
    Measured coverage and provenance audit for the quality report.

CLI examples (run from the project root, i.e. ``E:\\Repos\\name_collection``)::

    python -m src.pipeline extract                 # stage 1 (105 CSVs, ~10 min)
    python -m src.pipeline resolve                 # stage 2+4 -> lexicon (~5 min)
    python -m src.pipeline join --countries FJ,IS,EE
    python -m src.pipeline join --countries FJ --limit 5000
    python -m src.pipeline demo                    # index.html + wavs
    python -m src.pipeline report
    python -m src.pipeline all --countries FJ,IS,EE
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time

if __package__:
    from .common import (DATASET_IPA_DIR, DEFAULT_COVERAGE_TARGET,
                         DEFAULT_JOIN_COUNTRIES, DEFAULT_TOP_LIMIT, DEMO_DIR,
                         EXTRACT_DIR, LEAD_SCAN_DIR, LEXICON_PATH, OUTPUT_DIR,
                         WORK_DIR, DATA_DIR, country_codes, csv_path,
                         ensure_dirs, fmt_int, log, read_json, write_json)
    from .norm import field_view, normalize_token
    from .langmap import CONF_ORDER, assign_language, validate_table
    from .g2p import get_g2p
else:  # `python src/pipeline.py` convenience run
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import (DATASET_IPA_DIR, DEFAULT_COVERAGE_TARGET,
                        DEFAULT_JOIN_COUNTRIES, DEFAULT_TOP_LIMIT, DEMO_DIR,
                        EXTRACT_DIR, LEAD_SCAN_DIR, LEXICON_PATH, OUTPUT_DIR,
                        WORK_DIR, DATA_DIR, country_codes, csv_path,
                        ensure_dirs, fmt_int, log, read_json, write_json)
    from norm import field_view, normalize_token
    from langmap import CONF_ORDER, assign_language, validate_table
    from g2p import get_g2p

# ---------------------------------------------------------------------------
# WikiPron lexicon layer (cascade layer 1): language -> ISO 639-3 used by the
# g2p_eval/lexicons/<iso3>_<script>_broad.tsv files downloaded by the evaluator.
# Only languages that actually have a lexicon file are listed; others skip layer 1.
# ---------------------------------------------------------------------------
WIKIPRON_LANG = {
    "en": "eng", "en-us": "eng", "en-gb": "eng", "fr-fr": "fra", "fr-ca": "fra",
    "fr-ch": "fra", "de": "deu", "it": "ita", "es-es": "spa", "es-419": "spa",
    "es-mx": "spa", "nl": "nld", "sv": "swe", "da": "dan", "nb": "nor",
    "fi": "fin", "pl": "pol", "ro": "ron", "tr": "tur", "ca": "cat",
    "id": "ind", "ar": "ara", "fa": "fas", "he": "heb", "el": "ell",
    "hi": "hin", "ta": "tam", "te": "tel", "kn": "kan", "ml": "mal",
    "th": "tha", "ur": "urd", "zu": "zul", "ja": "jpn", "ko": "kor",
    "cmn": "cmn", "yue": "yue", "vi": "vie",
}


# ---------------------------------------------------------------------------
# stage 2+4: resolve tokens -> (token, lang) -> IPA   [build_lexicon]
# ---------------------------------------------------------------------------
def _read_tokens(cc: str):
    """Read one country's token shard -> list[(token,count,cum,total)]."""
    import pyarrow.parquet as pq
    path = os.path.join(EXTRACT_DIR, f"tokens_{cc}.parquet")
    if not os.path.exists(path):
        return []
    t = pq.read_table(path, columns=["token", "count", "cum_count", "total_tokens"])
    return list(zip(t["token"].to_pylist(), t["count"].to_pylist(),
                    t["cum_count"].to_pylist(), t["total_tokens"].to_pylist()))


def cut_index(rows, coverage_target: float, cap: int) -> int:
    """Smallest token prefix whose cumulative count reaches ``coverage_target``."""
    if not rows:
        return 0
    total = rows[0][3] or 0
    n = min(len(rows), cap)
    if total <= 0:
        return n
    need = coverage_target * total
    lo, hi = 0, n - 1
    while lo < hi:  # rows are sorted by descending count -> cum is monotone
        mid = (lo + hi) // 2
        if rows[mid][2] >= need:
            hi = mid
        else:
            lo = mid + 1
    return lo + 1


def _load_wikipron(lang: str):
    """Return a dict-like WikiPron lexicon for ``lang`` (or None).

    Reads the evaluator's downloaded broad TSVs read-only via
    ``engines.WikiPronLexiconEngine``; returns None when there is no file.
    """
    iso = WIKIPRON_LANG.get(lang)
    if not iso:
        return None
    try:
        eng = get_g2p().engines
        return eng.WikiPronLexiconEngine()._load(iso)  # dict: lowercased word -> IPA
    except Exception:
        return None


def build_lexicon(coverage_target: float = DEFAULT_COVERAGE_TARGET,
                  top_limit: int = DEFAULT_TOP_LIMIT,
                  countries=None, use_lexicon: bool = False,
                  restrict: int = 0) -> dict:
    """Stage 2+4: language assignment + IPA resolution -> name_ipa_lexicon.parquet.

    ``use_lexicon`` defaults to **False**: WikiPron broad transcriptions carry no
    stress marks and use a different phone inventory, so substituting them for
    espeak IPA measurably degrades TTS output (g2p-evaluator + Lead finding; see
    QUALITY_REPORT.md).  Enable with ``--lexicon`` for QA/comparison only.
    """
    ensure_dirs()
    countries = countries or country_codes()
    t0 = time.time()

    # ---- pass A: normalize / filter / assign language / merge (key,lang) -----
    units: dict[tuple, dict] = {}
    country_stats: dict[str, dict] = {}
    junk_tokens = junk_instances = kept_tokens = kept_instances = 0
    missing_shards = []

    for i, cc in enumerate(countries, 1):
        rows = _read_tokens(cc)
        if not rows:
            missing_shards.append(cc)
            continue
        total = rows[0][3] or 0
        n_keep = cut_index(rows, coverage_target, top_limit)
        kept_tokens += n_keep
        kept_instances += rows[n_keep - 1][2] if n_keep else 0
        st = {"country": cc, "token_instances": total, "tokens_kept": n_keep,
              "instances_kept": rows[n_keep - 1][2] if n_keep else 0,
              "instance_coverage": round((rows[n_keep - 1][2] / total), 4) if total else 0,
              "junk_tokens": 0, "junk_instances": 0,
              "unresolved_tokens": 0, "unresolved_instances": 0,
              "langs": {}}
        for raw, cnt, _cum, _tot in rows[:n_keep]:
            d = normalize_token(raw)
            if d["flags"]["is_junk"]:
                st["junk_tokens"] += 1
                st["junk_instances"] += cnt
                junk_tokens += 1
                junk_instances += cnt
                continue
            la = assign_language(d["script"], cc)
            lang = la["lang"]
            if lang is None:
                st["unresolved_tokens"] += 1
                st["unresolved_instances"] += cnt
                lang = ""
            key = d["key"]
            if not key:
                continue
            u = units.get((key, lang))
            if u is None:
                units[(key, lang)] = {
                    "key": key, "token": d["norm"], "lang": lang,
                    "script": d["script"], "lang_reason": la["lang_reason"],
                    "confidence": la["confidence"],
                    "confidence_reason": la["confidence_reason"],
                    "fallback_lang": la["fallback_lang"] or "",
                    "n_records": cnt, "n_units": 1,
                    "countries": {cc}, "top_country": cc,
                    "_top_count": cnt,
                }
            else:
                u["n_records"] += cnt
                u["n_units"] += 1
                u["countries"].add(cc)
                if cnt > u["_top_count"]:
                    u["_top_count"] = cnt
                    u["token"] = d["norm"]
                    u["top_country"] = cc
                if CONF_ORDER.get(la["confidence"], 9) < CONF_ORDER.get(u["confidence"], 9):
                    u["confidence"] = la["confidence"]
                    u["confidence_reason"] = la["confidence_reason"]
            st["langs"][lang or "(unresolved)"] = st["langs"].get(lang or "(unresolved)", 0) + 1
        country_stats[cc] = st
        if i % 20 == 0:
            log(f"  resolve/assign: {i}/{len(countries)} countries, "
                f"{fmt_int(len(units))} (token,lang) units")

    log(f"pass A: {fmt_int(len(units))} (token,lang) units from "
        f"{fmt_int(kept_tokens)} kept tokens ({fmt_int(junk_tokens)} junk) "
        f"in {time.time()-t0:.1f}s")

    # ---- pass B: lexicon layer (WikiPron) -----------------------------------
    lex_ips = {}
    if use_lexicon:
        needed = {u["lang"] for u in units.values() if u["lang"]}
        for lang in needed:
            tbl = _load_wikipron(lang)
            if tbl:
                lex_ips[lang] = tbl
        log(f"pass B: WikiPron lexicons loaded for {sorted(lex_ips)}")

    # ---- pass C: G2P for the rest -------------------------------------------
    t1 = time.time()
    g2p = get_g2p()
    keys = list(units.keys())
    items, todo = [], []
    hits = 0
    for k in keys:
        u = units[k]
        if not u["lang"]:
            continue
        tbl = lex_ips.get(u["lang"])
        if tbl:
            hit = tbl.get(u["token"].lower())
            if hit:
                u["ipa"] = hit
                u["phonemes"] = ""
                u["source"] = "lexicon:wikipron"
                u["confidence"] = "high"
                u["confidence_reason"] = "WikiPron broad transcription (no stress marks)"
                u["_lex_hit"] = True
                hits += 1
                continue
        items.append((u["token"], u["lang"], u["fallback_lang"] or "en-us"))
        todo.append(k)
    log(f"pass C: {fmt_int(hits)} lexicon hits, {fmt_int(len(items))} G2P units")
    if items:
        outs = g2p.batch(items, progress_every=500_000)
        for k, o in zip(todo, outs):
            u = units[k]
            u["ipa"] = o["ipa"]
            u["phonemes"] = o["phonemes"]
            u["source"] = o["source"]
            if not o["ipa"]:
                u["confidence"] = "none"
                u["confidence_reason"] = o["note"]
            elif o["fallback_used"]:
                u["confidence"] = "medium"
                u["confidence_reason"] = o["note"]
                u["lang_reason"] = "g2p-fallback-voice"
    g2p_s = time.time() - t1
    log(f"pass C done: {fmt_int(len(items))} units in {g2p_s:.1f}s "
        f"({fmt_int(len(items)/max(g2p_s, 1e-9))} units/s)")

    # ---- write lexicon ------------------------------------------------------
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows_out = []
    for u in units.values():
        rows_out.append({
            "token": u["token"], "key": u["key"], "lang": u["lang"],
            "script": u["script"],
            "ipa": u.get("ipa", ""), "phonemes": u.get("phonemes", ""),
            "source": u.get("source", "unresolved:no-voice"),
            "confidence": u.get("confidence", "none"),
            "lang_reason": u["lang_reason"],
            "confidence_reason": u.get("confidence_reason", ""),
            "n_records": u["n_records"], "n_units": u["n_units"],
            "n_countries": len(u["countries"]), "top_country": u["top_country"],
        })
    rows_out.sort(key=lambda r: -r["n_records"])
    tbl = pa.Table.from_pylist(rows_out)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pq.write_table(tbl, LEXICON_PATH, compression="zstd")
    lex_mb = os.path.getsize(LEXICON_PATH) / 1e6

    # ---- coverage / provenance tables --------------------------------------
    _write_lexicon_coverages(rows_out, country_stats)
    summary = {
        "stage": "resolve",
        "coverage_target": coverage_target,
        "top_limit": top_limit,
        "countries": len(country_stats),
        "missing_shards": missing_shards,
        "units": len(rows_out),
        "tokens_kept": kept_tokens,
        "instances_kept": kept_instances,
        "junk_tokens": junk_tokens,
        "junk_instances": junk_instances,
        "resolved_units": sum(1 for r in rows_out if r["ipa"]),
        "resolution_rate": round(100.0 * sum(1 for r in rows_out if r["ipa"])
                                 / max(len(rows_out), 1), 2),
        "lexicon_path": LEXICON_PATH,
        "lexicon_mb": round(lex_mb, 1),
        "g2p_seconds": round(g2p_s, 1),
        "g2p_units_per_sec": round(len(items) / max(g2p_s, 1e-9)),
        "by_source": _count(rows_out, "source"),
        "by_confidence": _count(rows_out, "confidence"),
        "by_lang_reason": _count(rows_out, "lang_reason"),
        "lang_reason_by_units": _count(rows_out, "lang_reason"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    write_json(os.path.join(OUTPUT_DIR, "lexicon_summary.json"), summary)
    log(f"lexicon: {fmt_int(len(rows_out))} units -> {LEXICON_PATH} ({lex_mb:.1f} MB)")
    return summary


def _count(rows, field):
    out = {}
    for r in rows:
        out[r[field]] = out.get(r[field], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _write_lexicon_coverages(rows, country_stats):
    """Emit output/coverage_by_country.csv + output/coverage_by_language.csv."""
    # per-language coverage
    by_lang = {}
    for r in rows:
        lang = r["lang"] or "(unresolved)"
        d = by_lang.setdefault(lang, {"units": 0, "records": 0, "resolved": 0,
                                      "high": 0, "medium": 0, "low": 0, "none": 0,
                                      "with_phonemes": 0})
        n = r["n_records"]
        d["units"] += 1
        d["records"] += n
        if r["ipa"]:
            d["resolved"] += 1
        d[r["confidence"]] = d.get(r["confidence"], 0) + 1
        if r["phonemes"]:
            d["with_phonemes"] += 1
    path = os.path.join(OUTPUT_DIR, "coverage_by_language.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lang", "units", "records", "resolved", "resolution_pct",
                    "high", "medium", "low", "none", "with_phonemes"])
        for lang, d in sorted(by_lang.items(), key=lambda kv: -kv[1]["records"]):
            w.writerow([lang, d["units"], d["records"], d["resolved"],
                        round(100.0 * d["resolved"] / max(d["units"], 1), 2),
                        d["high"], d["medium"], d["low"], d["none"], d["with_phonemes"]])

    # per-country coverage (token-instance share; record share estimated with
    # first/last independence from the Lead's exact per-country row counts)
    pc_raw = read_json(os.path.join(LEAD_SCAN_DIR, "per_country_counts.json"), []) or []
    pc = {row["country"]: row for row in pc_raw} if isinstance(pc_raw, list) else pc_raw
    path = os.path.join(OUTPUT_DIR, "coverage_by_country.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["country", "rows", "token_instances", "tokens_kept",
                    "instances_kept", "instance_coverage_pct",
                    "junk_tokens", "junk_instances", "unresolved_pct_of_kept",
                    "estimated_record_coverage_pct", "top_langs"])
        for cc, st in sorted(country_stats.items()):
            meta = pc.get(cc) or {}
            rows_n = meta.get("rows")
            est = ""
            if rows_n and st["instance_coverage"]:
                est = round(100.0 * st["instance_coverage"] ** 2, 1)
            top_langs = sorted(st["langs"].items(), key=lambda kv: -kv[1])[:4]
            w.writerow([
                cc, rows_n or "", st["token_instances"], st["tokens_kept"],
                st["instances_kept"], round(100.0 * st["instance_coverage"], 2),
                st["junk_tokens"], st["junk_instances"],
                round(100.0 * st["unresolved_instances"] / max(st["instances_kept"], 1), 2),
                est, "|".join(f"{k}:{v}" for k, v in top_langs)])
    return path


# ---------------------------------------------------------------------------
# stage 5: join IPA back onto the original rows
# ---------------------------------------------------------------------------
def load_lexicon_index(path: str = LEXICON_PATH) -> dict:
    import pyarrow.parquet as pq
    t = pq.read_table(path, columns=["key", "lang", "token", "ipa", "phonemes",
                                     "source", "confidence"])
    idx = {}
    for key, lang, tok, ipa, phon, src, conf in zip(
            t["key"].to_pylist(), t["lang"].to_pylist(), t["token"].to_pylist(),
            t["ipa"].to_pylist(), t["phonemes"].to_pylist(),
            t["source"].to_pylist(), t["confidence"].to_pylist()):
        idx[(key, lang)] = {"token": tok, "ipa": ipa, "phonemes": phon,
                            "source": src, "confidence": conf}
    return idx


def _resolve_words(words: list, cc: str, idx: dict) -> dict:
    """Resolve a list of normalized word dicts against ``idx`` (key,lang)->pronunciation.

    Missing entries are reported as ``unresolved``; the caller batches those
    through G2P and calls again with the enlarged index.
    """
    out = {"ipa": "", "phonemes": "", "langs": [], "sources": [],
           "resolved_words": 0, "n_words": 0, "unresolved": 0, "junk": 0}
    ipas, phons = [], []
    for w in words:
        if not w["norm"]:
            continue
        out["n_words"] += 1
        if w["flags"]["is_junk"]:
            out["junk"] += 1
            out["unresolved"] += 1
            continue
        la = assign_language(w["script"], cc)
        lang = la["lang"] or ""
        hit = idx.get((w["key"], lang)) if lang else None
        if hit and hit["ipa"]:
            ipas.append(hit["ipa"])
            if hit["phonemes"]:
                phons.append(hit["phonemes"])
            out["langs"].append(lang)
            out["sources"].append(hit["source"])
            out["resolved_words"] += 1
        else:
            out["unresolved"] += 1
    out["ipa"] = " ".join(ipas)
    out["phonemes"] = " ".join(phons)
    out["langs"] = sorted(set(out["langs"]))
    out["sources"] = sorted(set(out["sources"]))
    return out


def _collect_oov(words: list, cc: str, idx: dict, oov: dict) -> None:
    """Record unresolved (key, lang) word units for the batched OOV G2P pass."""
    for w in words:
        if not w["norm"] or w["flags"]["is_junk"]:
            continue
        la = assign_language(w["script"], cc)
        lang = la["lang"] or ""
        if lang and (w["key"], lang) in idx:
            continue
        oov.setdefault((w["key"], lang), {
            "token": w["norm"], "lang": lang,
            "fallback": la["fallback_lang"] or "en-us",
            "reason": la["lang_reason"], "confidence": la["confidence"]})


def _join_one(cc: str, rows: list) -> list:
    """Turn raw (first,last,gender) triples into row dicts carrying word lists."""
    out = []
    for first, last, gender in rows:
        fv_f = field_view(first)
        fv_l = field_view(last)
        out.append({
            "country_code": cc,
            "first_name": first or "", "last_name": last or "",
            "gender": gender or "",
            "first_norm": fv_f["norm"], "last_norm": fv_l["norm"],
            "first_empty": fv_f["empty"], "last_empty": fv_l["empty"],
            "_fw": fv_f["words"], "_lw": fv_l["words"],
        })
    return out


def _finalize_row(r: dict, cc: str, idx: dict) -> dict:
    """Resolve both name fields and return the public row schema."""
    rf = _resolve_words(r["_fw"], cc, idx)
    rl = _resolve_words(r["_lw"], cc, idx)
    ipa_full = (f"{rf['ipa']} {rl['ipa']}".strip() if rf["ipa"] and rl["ipa"]
                else (rf["ipa"] or rl["ipa"]))
    return {
        "country_code": r["country_code"],
        "first_name": r["first_name"], "last_name": r["last_name"],
        "gender": r["gender"],
        "first_norm": r["first_norm"], "last_norm": r["last_norm"],
        "first_ipa": rf["ipa"], "last_ipa": rl["ipa"], "ipa_full": ipa_full,
        "first_phonemes": rf["phonemes"], "last_phonemes": rl["phonemes"],
        "first_lang": ",".join(rf["langs"]), "last_lang": ",".join(rl["langs"]),
        "first_source": ",".join(rf["sources"]), "last_source": ",".join(rl["sources"]),
        "first_resolved": bool(rf["n_words"]) and rf["resolved_words"] == rf["n_words"],
        "last_resolved": bool(rl["n_words"]) and rl["resolved_words"] == rl["n_words"],
        "first_unresolved_words": rf["unresolved"], "last_unresolved_words": rl["unresolved"],
        "first_junk_words": rf["junk"], "last_junk_words": rl["junk"],
        "first_empty": r["first_empty"], "last_empty": r["last_empty"],
    }



def join_dataset(countries=None, limit: int = 0, out_dir: str = DATASET_IPA_DIR,
                 use_oov_g2p: bool = True, threads: int = 8) -> dict:
    """Stage 5: attach IPA to the original rows of ``countries``; writes parquet shards."""
    ensure_dirs()
    if __package__:
        from .common import import_duckdb
    else:
        from common import import_duckdb
    duckdb = import_duckdb()
    countries = countries or DEFAULT_JOIN_COUNTRIES
    lex = load_lexicon_index()
    log(f"join: lexicon index {fmt_int(len(lex))} entries")
    t_all = time.time()
    oov: dict[tuple, dict] = {}
    prepared: dict[str, list] = {}
    read_stats = {}

    con = duckdb.connect()
    con.execute(f"SET threads={threads}")
    cols = ("{'column0':'VARCHAR','column1':'VARCHAR','column2':'VARCHAR',"
            "'column3':'VARCHAR'}")
    for cc in countries:
        path = csv_path(cc).replace("\\", "/")
        lim = f" LIMIT {int(limit)}" if limit else ""
        rows = con.execute(
            f"SELECT column0, column1, column2 FROM read_csv('{path}', header=false, "
            f"quote='\"', escape='\"', columns={cols}, ignore_errors=true){lim}"
        ).fetchall()
        t0 = time.time()
        prepared[cc] = _join_one(cc, rows)
        for r in prepared[cc]:
            _collect_oov(r["_fw"], cc, lex, oov)
            _collect_oov(r["_lw"], cc, lex, oov)
        read_stats[cc] = {"rows": len(rows), "prepare_s": round(time.time() - t0, 2)}
        log(f"  join/prepare {cc}: {fmt_int(len(rows))} rows, "
            f"{fmt_int(len(oov))} OOV (token,lang) so far")

    # batched OOV G2P (long-tail names that were not in the per-country token cut)
    t1 = time.time()
    oov_stats = {"oov_units": len(oov), "resolved": 0, "unresolved": 0,
                 "by_source": {}}
    if use_oov_g2p and oov:
        g2p = get_g2p()
        keys = list(oov.keys())
        items = [(oov[k]["token"], oov[k]["lang"], oov[k]["fallback"]) for k in keys]
        outs = g2p.batch(items, progress_every=100_000)
        for k, o in zip(keys, outs):
            d = oov[k]
            d["ipa"] = o["ipa"]
            d["phonemes"] = o["phonemes"]
            d["source"] = o["source"]
            if o["ipa"]:
                oov_stats["resolved"] += 1
            else:
                oov_stats["unresolved"] += 1
            oov_stats["by_source"][o["source"]] = oov_stats["by_source"].get(o["source"], 0) + 1
    oov_s = time.time() - t1
    log(f"join: OOV G2P {fmt_int(len(oov))} units in {oov_s:.1f}s")

    # extend the in-memory index with the freshly G2P'd OOV units, then emit rows
    idx = dict(lex)
    for k, d in oov.items():
        if d.get("ipa"):
            idx[k] = {"token": d["token"], "ipa": d["ipa"],
                      "phonemes": d.get("phonemes", ""),
                      "source": d.get("source", "g2p:espeak"),
                      "confidence": d.get("confidence", "medium")}

    # write shards
    import pyarrow as pa
    import pyarrow.parquet as pq
    os.makedirs(out_dir, exist_ok=True)
    summary = {"stage": "join", "countries": {}, "oov": oov_stats,
               "oov_seconds": round(oov_s, 1), "lexicon_entries": len(lex)}
    for cc in countries:
        rows = [_finalize_row(r, cc, idx) for r in prepared[cc]]
        table = pa.Table.from_pylist(rows)
        out_path = os.path.join(out_dir, f"{cc}.parquet")
        pq.write_table(table, out_path, compression="zstd")
        n = len(rows)
        first_ok = sum(1 for r in rows if r["first_ipa"] or r["first_empty"])
        last_ok = sum(1 for r in rows if r["last_ipa"] or r["last_empty"])
        full_ok = sum(1 for r in rows
                      if (r["first_ipa"] or r["first_empty"])
                      and (r["last_ipa"] or r["last_empty"]))
        summary["countries"][cc] = {
            **read_stats[cc],
            "rows": n,
            "first_ipa_pct": round(100.0 * first_ok / max(n, 1), 2),
            "last_ipa_pct": round(100.0 * last_ok / max(n, 1), 2),
            "full_row_ipa_pct": round(100.0 * full_ok / max(n, 1), 2),
            "out_path": out_path, "out_mb": round(os.path.getsize(out_path) / 1e6, 2),
        }
        log(f"  wrote {out_path} ({summary['countries'][cc]['out_mb']} MB, "
            f"full-row coverage {summary['countries'][cc]['full_row_ipa_pct']}%)")
    summary["elapsed_s"] = round(time.time() - t_all, 1)
    write_json(os.path.join(OUTPUT_DIR, "join_summary.json"), summary)
    return summary


# ---------------------------------------------------------------------------
# global frequency artifact (top-N tokens by global record count)
# ---------------------------------------------------------------------------
def global_top_tokens(n: int = 2_000_000) -> dict:
    """Emit output/global_top_tokens.parquet (rank, token, n_first, n_last, n_total).

    Uses ``work/lead_scan/global_freq.parquet`` (67.7M rows, exact full-dataset
    counts) as the source; this is the "which tokens deserve manual review" list
    from METHOD.md §1.6.
    """
    if __package__:
        from .common import import_duckdb
    else:
        from common import import_duckdb
    duckdb = import_duckdb()
    import pyarrow as pa
    import pyarrow.parquet as pq
    src = os.path.join(LEAD_SCAN_DIR, "global_freq.parquet").replace("\\", "/")
    if not os.path.exists(src):
        log("global: lead_scan/global_freq.parquet missing, skipped")
        return {}
    con = duckdb.connect()
    con.execute("SET threads=8")
    os.makedirs(WORK_DIR, exist_ok=True)
    con.execute(f"SET temp_directory='{os.path.join(WORK_DIR, 'duckdb_tmp').replace(chr(92), '/')}'")
    t0 = time.time()
    q = f"""
    WITH g AS (
      SELECT tok,
             sum(CASE WHEN field = 'first' THEN n ELSE 0 END) AS n_first,
             sum(CASE WHEN field = 'last'  THEN n ELSE 0 END) AS n_last,
             sum(n) AS n_total
      FROM read_parquet('{src}') GROUP BY tok
    ),
    r AS (SELECT *, row_number() OVER (ORDER BY n_total DESC, tok) AS rank FROM g)
    SELECT rank, tok, n_first, n_last, n_total FROM r WHERE rank <= {int(n)}
    ORDER BY rank
    """
    rows = con.execute(q).fetchall()
    tbl = pa.table({
        "rank": pa.array([r[0] for r in rows], type=pa.int64()),
        "token": pa.array([r[1] for r in rows]),
        "n_first": pa.array([r[2] for r in rows], type=pa.int64()),
        "n_last": pa.array([r[3] for r in rows], type=pa.int64()),
        "n_total": pa.array([r[4] for r in rows], type=pa.int64()),
    })
    out = os.path.join(OUTPUT_DIR, "global_top_tokens.parquet")
    pq.write_table(tbl, out, compression="zstd")
    # coverage curve at the ranks the method doc quotes
    total_first = sum(r[2] for r in rows)
    total_last = sum(r[3] for r in rows)
    all_first = con.execute(
        f"SELECT sum(n) FROM read_parquet('{src}') WHERE field='first'").fetchone()[0]
    all_last = con.execute(
        f"SELECT sum(n) FROM read_parquet('{src}') WHERE field='last'").fetchone()[0]
    curve = {}
    for rank in (1_000, 10_000, 100_000, 500_000, 1_000_000, 1_000_000 * 2):
        if rank > len(rows):
            break
        cf = sum(r[2] for r in rows[:rank]) / max(all_first, 1)
        cl = sum(r[3] for r in rows[:rank]) / max(all_last, 1)
        curve[str(rank)] = {"first_pct": round(100 * cf, 2), "last_pct": round(100 * cl, 2)}
    summary = {"tokens": len(rows), "path": out,
               "mb": round(os.path.getsize(out) / 1e6, 1),
               "total_first_records": all_first, "total_last_records": all_last,
               "coverage_curve": curve, "elapsed_s": round(time.time() - t0, 1)}
    write_json(os.path.join(OUTPUT_DIR, "global_top_tokens_summary.json"), summary)
    log(f"global: {fmt_int(len(rows))} tokens -> {out} in {summary['elapsed_s']}s")
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description="name -> IPA pipeline for the 491.6M-row name dataset")
    sub = ap.add_subparsers(dest="stage", required=True)

    p = sub.add_parser("extract", help="stage 1: token inventory + name samples")
    p.add_argument("--countries")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_LIMIT)

    p = sub.add_parser("resolve", help="stage 2+4: language assignment + IPA -> lexicon")
    p.add_argument("--coverage-target", type=float, default=DEFAULT_COVERAGE_TARGET)
    p.add_argument("--top", type=int, default=DEFAULT_TOP_LIMIT)
    p.add_argument("--countries")
    p.add_argument("--lexicon", action="store_true",
                   help="enable the WikiPron tier (OFF by default: unstressed, "
                        "non-espeak inventory -> degrades TTS)")

    p = sub.add_parser("join", help="stage 5: attach IPA to original rows")
    p.add_argument("--countries", default=",".join(DEFAULT_JOIN_COUNTRIES))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-oov-g2p", action="store_true")

    p = sub.add_parser("demo", help="build demo/index.html + demo/audio/*.wav")
    p.add_argument("--rows", type=int, default=420)
    p.add_argument("--no-audio", action="store_true")

    p = sub.add_parser("report", help="write output/QUALITY_REPORT.md")
    p.add_argument("--join-report", action="store_true",
                   help="also run a small join on FJ/IS/EE for end-to-end numbers")

    p = sub.add_parser("global", help="emit global top-N token artifact")
    p.add_argument("--n", type=int, default=2_000_000)

    p = sub.add_parser("all", help="extract -> resolve -> join -> demo -> report")
    p.add_argument("--countries", default=",".join(DEFAULT_JOIN_COUNTRIES))
    p.add_argument("--rows", type=int, default=420)
    p.add_argument("--no-audio", action="store_true")

    p = sub.add_parser("info", help="show environment / dependency status")
    return ap


def _cc_list(s: str | None):
    return [c.strip().upper() for c in s.split(",") if c.strip()] if s else None


def main(argv=None) -> int:
    if not __package__:
        pass
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_arg_parser().parse_args(argv)
    ensure_dirs()

    # The raw dataset is git-ignored (3.3GB). Fetch it on first use unless the
    # user opted out with NAME_COLLECTION_AUTO_DOWNLOAD=0.
    if args.stage in ("extract", "resolve", "join", "global", "all"):
        try:
            if __package__:
                from . import ensure_data as _ed
            else:
                import ensure_data as _ed
            _ed.ensure_dataset()
        except Exception as _e:  # noqa: BLE001
            log(f"dataset unavailable: {_e}")
            log("hint: python scripts/bootstrap.py --what dataset")
            return 3

    if args.stage == "extract":  # delegate to extract.py
        if __package__:
            from . import extract as ex
        else:
            import extract as ex
        ex.extract_countries(_cc_list(args.countries), max(args.top, 1000))
        return 0

    if args.stage == "resolve":
        build_lexicon(args.coverage_target, args.top, _cc_list(args.countries),
                      use_lexicon=args.lexicon)
        return 0

    if args.stage == "join":
        join_dataset(_cc_list(args.countries), args.limit,
                     use_oov_g2p=not args.no_oov_g2p)
        return 0

    if args.stage == "demo":
        if __package__:
            from . import demo_build
        else:
            import demo_build
        demo_build.build_demo(n_rows=args.rows, with_audio=not args.no_audio)
        return 0

    if args.stage == "report":
        if __package__:
            from . import report as rp
        else:
            import report as rp
        rp.build_report()
        return 0

    if args.stage == "global":
        global_top_tokens(args.n)
        return 0

    if args.stage == "all":
        if __package__:
            from . import extract as ex
            from . import demo_build, report as rp
        else:
            import extract as ex
            import demo_build
            import report as rp
        ex.extract_countries(None, DEFAULT_TOP_LIMIT)  # cached if already run
        global_top_tokens(2_000_000)
        build_lexicon()
        ccs = _cc_list(args.countries)
        join_dataset(ccs)
        demo_build.build_demo(n_rows=args.rows, with_audio=not args.no_audio)
        rp.build_report()
        return 0

    if args.stage == "info":
        log("environment: python " + sys.version.split()[0])
        try:
            g = get_g2p()
            log(f"espeak-ng: {g.data_dir}")
        except Exception as e:
            log(f"espeak-ng: UNAVAILABLE ({e})")
        log(f"country voices: {json.dumps(validate_table())}")
        try:
            n_cc = len(country_codes())
        except OSError:
            n_cc = 0
        if n_cc:
            log(f"dataset: {n_cc} country CSVs in {DATA_DIR}")
        else:
            log("dataset: MISSING (git-ignored) -> run: python scripts/bootstrap.py --what dataset")
        lex = read_json(os.path.join(OUTPUT_DIR, "lexicon_summary.json"))
        if lex:
            log(f"lexicon: {fmt_int(lex.get('units', 0))} units, "
                f"{lex.get('resolution_rate')}% resolved")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
