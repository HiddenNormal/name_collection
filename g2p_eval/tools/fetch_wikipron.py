"""Download WikiPron broad TSVs for target languages.

Repo: CUNY-CL/wikipron, files at data/scrape/tsv/<iso639-3>_<script>_broad.tsv
(e.g. eng_latn_broad.tsv, fra_latn_broad.tsv, ara_arab_broad.tsv,
 cmn_hani_broad.tsv, kor_hang_broad.tsv, rus_cyrl_broad.tsv).
Writes g2p_eval/lexicons/<lang>_<script>_broad.tsv and prints sizes.
"""
import json
import os
import re
import sys
import time

import requests

OUT = r"E:\Repos\name_collection\g2p_eval\lexicons"
os.makedirs(OUT, exist_ok=True)
H = {"User-Agent": "dsh"}
LANGS = ["eng", "fra", "spa", "deu", "ita", "por", "nld", "rus", "pol", "tur",
         "swe", "ara", "cmn", "kor", "jpn", "hin", "ces", "hun", "ron", "ukr",
         "vie", "tha", "heb", "ell", "dan", "fin", "nor", "cat", "ind", "zul",
         "fas", "urd", "ben", "tam", "tel", "mal", "kan", "zho", "yue", "deu"]

raw = "https://raw.githubusercontent.com/CUNY-CL/wikipron/master/data/scrape/tsv/{name}"

# list actual files once
r = requests.get("https://api.github.com/repos/CUNY-CL/wikipron/contents/data/scrape/tsv",
                 headers=H, timeout=60)
files = [x["name"] for x in r.json()]
print("remote tsv files:", len(files))
by_lang = {}
for f in files:
    m = re.match(r"^([a-z]{3})_([a-z]{4})_broad\.tsv$", f)
    if m:
        by_lang.setdefault(m.group(1), []).append(f)
print("langs with broad tsv:", len(by_lang))

WANT = sys.argv[1:] or LANGS
# exact filenames (WikiPron has per-region/script variants for big languages)
EXACT = {
    "eng": ["eng_latn_us_broad_filtered.tsv", "eng_latn_us_broad.tsv"],
    "fra": ["fra_latn_broad_filtered.tsv", "fra_latn_broad.tsv"],
    "spa": ["spa_latn_la_broad_filtered.tsv", "spa_latn_la_broad.tsv", "spa_latn_ca_broad_filtered.tsv"],
    "deu": ["deu_latn_broad_filtered.tsv", "deu_latn_broad.tsv"],
    "ita": ["ita_latn_broad.tsv"],
    "por": ["por_latn_po_broad_filtered.tsv", "por_latn_po_broad.tsv"],
    "nld": ["nld_latn_broad.tsv"],
    "rus": ["rus_cyrl_narrow.tsv"],
    "pol": ["pol_latn_broad.tsv"],
    "tur": ["tur_latn_broad.tsv"],
    "swe": ["swe_latn_broad.tsv"],
    "ara": ["ara_arab_broad.tsv"],
    "cmn": ["cmn_hani_standard_broad.tsv", "cmn_latn_standard_broad.tsv"],
    "kor": ["kor_hang_narrow_filtered.tsv", "kor_hang_narrow.tsv"],
    "jpn": ["jpn_hira_narrow_filtered.tsv", "jpn_hira_narrow.tsv"],
    "hin": ["hin_deva_broad.tsv"],
    "ces": ["ces_latn_narrow.tsv"],
    "hun": ["hun_latn_narrow_filtered.tsv"],
    "ukr": ["ukr_cyrl_narrow.tsv"],
    "vie": ["vie_latn_hanoi_narrow_filtered.tsv"],
    "ell": ["ell_grek_broad.tsv"],
    "dan": ["dan_latn_broad.tsv"],
    "fin": ["fin_latn_broad.tsv"],
    "ind": ["ind_latn_broad.tsv"],
    "tha": ["tha_thai_broad.tsv"],
    "heb": ["heb_hebr_broad.tsv"],
    "fas": ["fas_arab_broad.tsv"],
    "urd": ["urd_arab_broad.tsv"],
}

for lang in WANT:
    found = None
    for name in EXACT.get(lang, []):
        if name in files:
            found = name
            break
    if found is None:
        print(f"{lang}: NO suitable tsv")
        continue
    dest = os.path.join(OUT, found)
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        print(f"{lang}: cached {os.path.getsize(dest)/1e6:.2f}MB ({found})")
        continue
    t = time.time()
    rr = requests.get(raw.format(name=found), timeout=600, headers=H)
    if rr.status_code != 200:
        print(f"{lang}: GET {rr.status_code}")
        continue
    open(dest, "wb").write(rr.content)
    print(f"{lang}: saved {len(rr.content)/1e6:.2f}MB in {time.time()-t:.0f}s ({found})")
print("DONE")
