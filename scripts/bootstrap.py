# -*- coding: utf-8 -*-
"""bootstrap.py -- fetch everything this project needs but does not commit.

Nothing large or licence-encumbered lives in git.  This script fetches it:

    external/name_dataset.zip             3.3 GB raw dataset   (Google Drive, upstream author)
    work/extract/name_dataset/data/*.csv  unzipped dataset     (derived)
    g2p_eval/lexicons/*.tsv               WikiPron dictionaries (CC BY-SA)
    g2p_eval/espeak/                      portable espeak-ng 1.52.0 (GPL-3.0)

Quick start on a fresh clone::

    python scripts/bootstrap.py --status      # what is missing?
    python scripts/bootstrap.py               # dataset + lexicons
    python scripts/bootstrap.py --what all    # + try to fetch espeak-ng

The pipeline also calls this logic automatically: if the dataset is missing,
`python src/pipeline.py extract` will download it unless you set
NAME_COLLECTION_AUTO_DOWNLOAD=0.

Offline / behind a proxy?  Point the loader at your own copy:
    NAME_DATASET_ZIP=D:\\mirrors\\name_dataset.zip
    NAME_DATASET_ZIP_URL=https://my.mirror/name_dataset.zip
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

LEXICON_DIR = os.path.join(ROOT, "g2p_eval", "lexicons")
ESPEAK_DIR = os.path.join(ROOT, "g2p_eval", "espeak")
ESPEAK_MSI_URL = ("https://github.com/espeak-ng/espeak-ng/releases/download/"
                  "1.52.0/espeak-ng-1.52.0.msi")
WIKIPRON_BASE = ("https://raw.githubusercontent.com/CUNY-CL/wikipron/"
                 "master/data/scrape/tsv/")

# Upstream WikiPron files used by this project (iso639-3 + script + variant).
LEXICONS = [
    "amh_ethi_broad.tsv", "ara_arab_broad.tsv", "aze_latn_broad.tsv",
    "bod_tibt_broad.tsv", "cat_latn_broad.tsv", "ces_latn_narrow.tsv",
    "cmn_hani_standard_broad.tsv", "dan_latn_broad.tsv", "deu_latn_broad.tsv",
    "div_thaa_broad.tsv", "ell_grek_broad.tsv", "eng_latn_us_broad.tsv",
    "fas_arab_broad.tsv", "fin_latn_broad.tsv", "fra_latn_broad.tsv",
    "guj_gujr_broad.tsv", "hau_latn_broad.tsv", "heb_hebr_broad.tsv",
    "hin_deva_broad.tsv", "hun_latn_narrow.tsv", "ind_latn_broad.tsv",
    "isl_latn_broad.tsv", "ita_latn_broad.tsv", "jpn_hira_narrow.tsv",
    "kan_knda_broad.tsv", "kaz_cyrl_broad.tsv", "khm_khmr_broad.tsv",
    "kor_hang_narrow.tsv", "lit_latn_broad.tsv", "mal_mlym_broad.tsv",
    "mlt_latn_broad.tsv", "msa_latn_broad.tsv", "mya_mymr_broad.tsv",
    "nld_latn_broad.tsv", "nor_latn_broad.tsv", "pol_latn_broad.tsv",
    "por_latn_po_broad.tsv", "ron_latn_broad.tsv", "rus_cyrl_narrow.tsv",
    "slk_latn_broad.tsv", "slv_latn_broad.tsv", "spa_latn_la_broad.tsv",
    "sqi_latn_broad.tsv", "swa_latn_broad.tsv", "swe_latn_broad.tsv",
    "tam_taml_broad.tsv", "tel_telu_broad.tsv", "tgl_latn_broad.tsv",
    "tha_thai_broad.tsv", "tur_latn_broad.tsv", "ukr_cyrl_narrow.tsv",
    "urd_arab_broad.tsv", "vie_latn_hanoi_narrow.tsv", "yor_latn_broad.tsv",
    "zul_latn_broad.tsv",
]


def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or u == "GB":
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}GB"


def fetch(url: str, dest: str, min_bytes: int = 1000) -> bool:
    """Download url -> dest unless it already exists and looks complete."""
    if os.path.exists(dest) and os.path.getsize(dest) >= min_bytes:
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "name-collection/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                if total > 1 << 20:
                    log(f"  {os.path.basename(dest)} {100*got/total:5.1f}%")
    except Exception as e:  # noqa: BLE001
        if os.path.exists(tmp):
            os.remove(tmp)
        log(f"  FAIL {os.path.basename(dest)}: {type(e).__name__}: {e}")
        return False
    os.replace(tmp, dest)
    return True


# ---------------------------------------------------------------------------
def do_dataset(force: bool = False) -> bool:
    import ensure_data
    st = ensure_data.dataset_status()
    if st["ready"] and not force:
        log(f"dataset: ready ({st['extracted_countries']} country CSVs)")
        return True
    ensure_data.ensure_dataset(auto_download=True)
    return ensure_data.have_dataset()


def do_lexicons(force: bool = False) -> bool:
    os.makedirs(LEXICON_DIR, exist_ok=True)
    have = {f for f in os.listdir(LEXICON_DIR) if f.endswith(".tsv")}
    missing = [f for f in LEXICONS if force or f not in have]
    if not missing:
        log(f"lexicons: ready ({len(have)} files)")
        return True
    log(f"lexicons: fetching {len(missing)} WikiPron files (CC BY-SA) ...")
    ok = 0
    for fn in missing:
        if fetch(WIKIPRON_BASE + fn, os.path.join(LEXICON_DIR, fn)):
            ok += 1
    log(f"lexicons: {ok}/{len(missing)} fetched, {len(have)+ok} total")
    return ok == len(missing)


def do_espeak(force: bool = False) -> bool:
    exe = os.path.join(ESPEAK_DIR, "installed", "SourceDir", "eSpeak NG", "espeak-ng.exe")
    if os.path.exists(exe) and not force:
        log(f"espeak-ng: ready ({os.path.dirname(exe)})")
        return True
    log("espeak-ng: not found; trying the portable MSI route")
    if os.name != "nt":
        log("  non-Windows: install with your package manager "
            "(apt/dnf/brew install espeak-ng) and set G2P_ESPEAK_DIR")
        return False
    msi = os.path.join(ESPEAK_DIR, "espeak-ng-1.52.0.msi")
    if not fetch(ESPEAK_MSI_URL, msi, min_bytes=1_000_000):
        log("  could not download the MSI; install espeak-ng manually and set G2P_ESPEAK_DIR")
        return False
    tool = os.path.join(ROOT, "g2p_eval", "tools", "extract_msi2.py")
    if not os.path.exists(tool):
        log(f"  missing {tool}; extract the MSI manually and set G2P_ESPEAK_DIR")
        return False
    log("  extracting MSI without msiexec (g2p_eval/tools/extract_msi2.py) ...")
    r = subprocess.run([sys.executable, tool, msi, ESPEAK_DIR],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"  extract failed: {(r.stderr or '')[-500:]}")
        return False
    ok = os.path.exists(exe)
    log(f"espeak-ng: {'ready at ' + os.path.dirname(exe) if ok else 'extraction produced no exe'}")
    return ok


def do_status() -> int:
    import ensure_data
    st = ensure_data.dataset_status()
    all_tsv = ([f for f in os.listdir(LEXICON_DIR) if f.endswith(".tsv")]
               if os.path.isdir(LEXICON_DIR) else [])
    present = [f for f in LEXICONS if f in set(all_tsv)]
    derived = len(all_tsv) - len(present)
    exe = os.path.join(ESPEAK_DIR, "installed", "SourceDir", "eSpeak NG",
                       "espeak-ng.exe")
    lines = {
        "dataset (external/name_dataset.zip)": "ready" if st["ready"] else "MISSING",
        "  zip": f"{human(st['zip_bytes'])} at {st['zip_path']}" if st["zip_exists"] else "absent",
        "  extracted CSVs": f"{st['extracted_countries']}/{st['expected_countries']}",
        "WikiPron lexicons":
            f"{len(present)}/{len(LEXICONS)} upstream"
            + (f" (+{derived} derived)" if derived > 0 else ""),
        "espeak-ng portable": "ready" if os.path.exists(exe) else "MISSING",
        "python deps (duckdb/pyarrow)": _deps_status(),
    }
    for k, v in lines.items():
        print(f"  {k:38} {v}")
    return 0 if st["ready"] else 1


def _deps_status() -> str:
    try:
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            sys.path.insert(0, os.path.join(ROOT, "work", "pylibs"))
            import duckdb  # noqa: F401
            import pyarrow  # noqa: F401
        return "ok"
    except Exception:  # noqa: BLE001
        return "MISSING (pip install duckdb pyarrow)"


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetch the external resources this repo does not commit.")
    ap.add_argument("--what", default="dataset,lexicons",
                    help="comma list of: dataset,lexicons,espeak,all "
                         "(default: dataset,lexicons)")
    ap.add_argument("--status", action="store_true", help="report what is present")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    args = ap.parse_args(argv)

    if args.status:
        return do_status()

    what = {w.strip().lower() for w in args.what.split(",") if w.strip()}
    if "all" in what:
        what = {"dataset", "lexicons", "espeak"}
    unknown = what - {"dataset", "lexicons", "espeak"}
    if unknown:
        ap.error(f"unknown --what value(s): {sorted(unknown)}")

    ok = True
    if "dataset" in what:
        try:
            ok &= do_dataset(args.force)
        except Exception as e:  # noqa: BLE001
            log(f"dataset FAILED: {e}")
            ok = False
    if "lexicons" in what:
        ok &= do_lexicons(args.force)
    if "espeak" in what:
        ok &= do_espeak(args.force)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
