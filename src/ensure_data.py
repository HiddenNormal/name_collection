# -*- coding: utf-8 -*-
"""ensure_data.py -- make sure the external resources this project needs exist.

The raw dataset is **not** committed to git (3.3 GB, and it derives from the
Facebook 533M leak -- see README "Data source & privacy").  This module checks
for it and downloads it on demand.

Layout it manages::

    external/name_dataset.zip                          <-- downloaded (ignored by git)
    work/extract/name_dataset/data/<CC>.csv             <-- unzipped (ignored by git)

Usage from code::

    from ensure_data import ensure_dataset
    ensure_dataset()          # download if missing, then unzip if missing

Usage from the CLI::

    python scripts/bootstrap.py                 # dataset + lexicons + espeak check
    python scripts/bootstrap.py --what dataset
    python scripts/bootstrap.py --status
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXTERNAL_DIR = os.path.join(ROOT, "external")
ZIP_PATH = os.path.join(EXTERNAL_DIR, "name_dataset.zip")
EXTRACT_ROOT = os.path.join(ROOT, "work", "extract")
DATA_DIR = os.path.join(EXTRACT_ROOT, "name_dataset", "data")

# --- canonical source -------------------------------------------------------
# philipperemy/name-dataset  (PyPI `names-dataset`, MIT code)
# raw CSV bundle published on Google Drive by the upstream author.
DRIVE_FILE_ID = "1QDbtPWGQypYxiS4pC_hHBBtbRHk9gEtr"
EXPECTED_BYTES = 3_499_114_012          # size of the published name_dataset.zip
EXPECTED_ENTRIES = 108                  # 105 country CSVs + data/ + root + description.txt
EXPECTED_COUNTRIES = 105

# Overrides (checked in this order):
#   NAME_DATASET_ZIP       path to a local zip  (copy instead of download)
#   NAME_DATASET_ZIP_URL   direct URL           (any host, e.g. your own mirror)
ENV_LOCAL = "NAME_DATASET_ZIP"
ENV_URL = "NAME_DATASET_ZIP_URL"

CHUNK = 1 << 20


class DataUnavailable(RuntimeError):
    """Raised when a required external resource is missing and cannot be fetched."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[bootstrap] {msg}", flush=True)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def _sha256(path: str, limit: int | None = None) -> str:
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            read += len(b)
            if limit and read >= limit:
                break
    return h.hexdigest()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def extracted_countries() -> list[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(DATA_DIR) if f.endswith(".csv"))


def have_dataset() -> bool:
    return len(extracted_countries()) == EXPECTED_COUNTRIES


def dataset_status() -> dict:
    cc = extracted_countries()
    return {
        "zip_path": ZIP_PATH,
        "zip_exists": os.path.exists(ZIP_PATH),
        "zip_bytes": os.path.getsize(ZIP_PATH) if os.path.exists(ZIP_PATH) else 0,
        "extracted_dir": DATA_DIR,
        "extracted_countries": len(cc),
        "expected_countries": EXPECTED_COUNTRIES,
        "ready": len(cc) == EXPECTED_COUNTRIES,
        "env_local": os.environ.get(ENV_LOCAL),
        "env_url": os.environ.get(ENV_URL),
        "drive_file_id": DRIVE_FILE_ID,
        "expected_bytes": EXPECTED_BYTES,
    }


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------
def _drive_urls(file_id: str) -> list[str]:
    return [
        # preferred: the "usercontent" endpoint honours confirm=t for big files
        f"https://drive.usercontent.google.com/download?id={file_id}"
        f"&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
    ]


def _looks_like_html(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(512).lstrip().lower()
        return head.startswith(b"<!doctype") or head.startswith(b"<html")
    except OSError:
        return False


def _existing_bytes(path: str) -> int:
    if not os.path.exists(path):
        return 0
    if _looks_like_html(path):
        # a previous attempt saved the Google Drive confirmation page
        os.remove(path)
        return 0
    return os.path.getsize(path)


def _download_to(url: str, dest: str, resume_from: int = 0) -> int:
    """Stream ``url`` into ``dest``, resuming at ``resume_from`` if possible."""
    headers = {"User-Agent": "name-collection-bootstrap/1.0"}
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        status = getattr(r, "status", 200)
        ctype = (r.headers.get("Content-Type") or "").lower()
        total = int(r.headers.get("Content-Length") or 0)
        if resume_from and status != 206 and resume_from:
            # server ignored the Range header -> start over
            log("server does not support resume; restarting download")
            resume_from = 0
        mode = "ab" if resume_from else "wb"
        if "text/html" in ctype:
            raise DataUnavailable(
                "server returned an HTML page instead of the zip "
                "(Google Drive quota / confirmation page). "
                f"Download it manually and set {ENV_LOCAL}=<path>, see below.")
        started = time.time()
        with open(dest, mode) as f:
            done = resume_from
            while True:
                b = r.read(CHUNK)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if total:
                    el = max(time.time() - started, 1e-6)
                    pct = 100.0 * done / (resume_from + total)
                    log(f"  {_human(done)} / {_human(resume_from + total)} "
                        f"({pct:5.1f}%)  {_human(done/el)}/s")
        return done


def download_zip(force: bool = False) -> str:
    """Fetch name_dataset.zip into external/. Returns the local path."""
    os.makedirs(EXTERNAL_DIR, exist_ok=True)

    local = os.environ.get(ENV_LOCAL)
    if local:
        if not os.path.exists(local):
            raise DataUnavailable(f"{ENV_LOCAL} points at a missing file: {local}")
        if os.path.abspath(local) != os.path.abspath(ZIP_PATH):
            log(f"copying local zip from {ENV_LOCAL}: {local}")
            shutil.copyfile(local, ZIP_PATH)

    if not os.path.exists(ZIP_PATH) or force:
        urls = []
        if os.environ.get(ENV_URL):
            urls.append(os.environ[ENV_URL])
        urls += _drive_urls(DRIVE_FILE_ID)

        last_err = None
        for url in urls:
            host = re.sub(r"^https?://([^/]+).*$", r"\1", url)
            have = _existing_bytes(ZIP_PATH)
            if have == EXPECTED_BYTES and not force:
                break
            log(f"downloading from {host} (resume at {have} bytes)...")
            try:
                _download_to(url, ZIP_PATH, resume_from=have)
            except DataUnavailable:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                log(f"  failed: {type(e).__name__}: {e}")
                continue
            size = os.path.getsize(ZIP_PATH)
            if size == EXPECTED_BYTES:
                break
            log(f"  size mismatch after download: got {size} expected {EXPECTED_BYTES}")
            last_err = DataUnavailable("size mismatch")
        else:
            raise DataUnavailable(
                "could not download name_dataset.zip automatically.\n"
                f"  last error: {last_err}\n"
                "Manually download the 3.3GB file from:\n"
                f"    https://drive.google.com/file/d/{DRIVE_FILE_ID}/view\n"
                f"  then either place it at {ZIP_PATH}\n"
                f"  or run:  {ENV_LOCAL}=<path> python scripts/bootstrap.py --what dataset")

    size = os.path.getsize(ZIP_PATH)
    if size != EXPECTED_BYTES:
        raise DataUnavailable(
            f"{ZIP_PATH} is {size} bytes but {EXPECTED_BYTES} were expected. "
            "Delete it and re-run to download again.")
    log(f"zip ok: {_human(size)} sha256={_sha256(ZIP_PATH)}")
    return ZIP_PATH


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
def extract_zip(force: bool = False) -> str:
    """Unzip external/name_dataset.zip into work/extract/. Idempotent."""
    if have_dataset() and not force:
        log(f"dataset already extracted: {len(extracted_countries())} country CSVs")
        return DATA_DIR
    if not os.path.exists(ZIP_PATH):
        raise DataUnavailable(f"missing {ZIP_PATH}; run with --what dataset first")

    os.makedirs(EXTRACT_ROOT, exist_ok=True)
    log(f"extracting -> {EXTRACT_ROOT}")
    t0 = time.time()
    with zipfile.ZipFile(ZIP_PATH) as z:
        names = z.namelist()
        if len(names) < EXPECTED_ENTRIES - 5:
            raise DataUnavailable(
                f"zip looks wrong: {len(names)} entries, expected ~{EXPECTED_ENTRIES}")
        total = sum(i.file_size for i in z.infolist())
        done = 0
        for info in z.infolist():
            z.extract(info, EXTRACT_ROOT)
            done += info.file_size
            if info.filename.endswith(".csv"):
                log(f"  {_human(done)} / {_human(total)}  {info.filename.split('/')[-1]}")
    n = len(extracted_countries())
    if n != EXPECTED_COUNTRIES:
        raise DataUnavailable(f"extracted {n} CSVs, expected {EXPECTED_COUNTRIES}")
    log(f"extracted {n} country CSVs in {time.time()-t0:.0f}s -> {DATA_DIR}")
    return DATA_DIR


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------
def ensure_dataset(auto_download: bool | None = None) -> str:
    """Make sure the dataset is extracted and usable.

    ``auto_download``: None -> do what the env says (default: download),
    True -> always download when missing, False -> raise instead.
    """
    if have_dataset():
        return DATA_DIR
    if auto_download is None:
        auto_download = os.environ.get("NAME_COLLECTION_AUTO_DOWNLOAD", "1") != "0"
    if not auto_download:
        raise DataUnavailable(
            "dataset missing and auto-download disabled.\n"
            "Run: python scripts/bootstrap.py --what dataset")
    if not os.path.exists(ZIP_PATH) or os.path.getsize(ZIP_PATH) != EXPECTED_BYTES:
        download_zip()
    return extract_zip()


def main(argv=None) -> int:
    import json
    argv = sys.argv[1:] if argv is None else argv
    if "--status" in argv or "-s" in argv:
        st = dataset_status()
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0 if st["ready"] else 1
    ensure_dataset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
