# -*- coding: utf-8 -*-
"""common.py -- shared paths / config / helpers for the name->IPA pipeline.

Layout (all paths are under the project root ``name_collection/``)::

    work/extract/name_dataset/data/<CC>.csv   raw dataset (READ-ONLY, 105 files, 491.6M rows)
    docs/country_languages.csv                curated country -> espeak voice table
    docs/METHOD.md                            method document (this code implements it)
    profile/normalize.py                      frozen token normalizer (imported read-only)
    g2p_eval/engines.py                       G2P engine wrapper (imported read-only)
    work/lead_scan/global_freq.parquet        global token frequency table (67.7M rows)
    work/pipeline_tmp/                        scratch produced by this pipeline
    output/                                   deliverables
    demo/                                     demo page + audio
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(ROOT, "work", "extract", "name_dataset", "data")
DOCS_DIR = os.path.join(ROOT, "docs")
PROFILE_DIR = os.path.join(ROOT, "profile")
G2P_EVAL_DIR = os.path.join(ROOT, "g2p_eval")
LEAD_SCAN_DIR = os.path.join(ROOT, "work", "lead_scan")
PYLIBS_DIR = os.path.join(ROOT, "work", "pylibs")

WORK_DIR = os.path.join(ROOT, "work", "pipeline_tmp")
EXTRACT_DIR = os.path.join(WORK_DIR, "extract")
TMP_DUCKDB = os.path.join(WORK_DIR, "duckdb_tmp")

OUTPUT_DIR = os.path.join(ROOT, "output")
DATASET_IPA_DIR = os.path.join(OUTPUT_DIR, "dataset_with_ipa")
LEXICON_PATH = os.path.join(OUTPUT_DIR, "name_ipa_lexicon.parquet")

DEMO_DIR = os.path.join(ROOT, "demo")
AUDIO_DIR = os.path.join(DEMO_DIR, "audio")

# reproducibility
SEED = 20261119

# stage defaults (documented in output/QUALITY_REPORT.md)
DEFAULT_TOP_LIMIT = 40000        # max tokens kept per country by the extractor
DEFAULT_COVERAGE_TARGET = 0.85   # keep the smallest token set covering this share
DEFAULT_NAME_SAMPLE_PCT = 5      # duckdb system sample for top-pair / random-row sampling
DEFAULT_JOIN_COUNTRIES = ["FJ", "IS", "EE"]


def import_duckdb():
    """Import the workspace-vendored duckdb (work/pylibs); no pip required."""
    if PYLIBS_DIR not in sys.path:
        sys.path.insert(0, PYLIBS_DIR)
    import duckdb  # noqa: PLC0415
    return duckdb


def package_paths() -> None:
    """Put the three read-only dependency dirs on sys.path (idempotent)."""
    for p in (PYLIBS_DIR, PROFILE_DIR, G2P_EVAL_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def ensure_dirs() -> None:
    for d in (WORK_DIR, EXTRACT_DIR, TMP_DUCKDB, OUTPUT_DIR, DATASET_IPA_DIR,
              DEMO_DIR, AUDIO_DIR):
        os.makedirs(d, exist_ok=True)


def country_codes() -> list[str]:
    """All 105 country codes, from the raw dataset filenames."""
    return sorted(f[:-4] for f in os.listdir(DATA_DIR) if f.endswith(".csv"))


def csv_path(cc: str) -> str:
    return os.path.join(DATA_DIR, f"{cc}.csv")


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_int(n) -> str:
    return f"{n:,}"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
