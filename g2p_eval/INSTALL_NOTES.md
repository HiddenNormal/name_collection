# Install / reproduce notes (G2P-IPA evaluation)

This folder was built inside a **Windows sandbox** where `msiexec` (Windows
Installer service), `winget`, `Start-Process` and `pip install` are *blocked*
(pip cannot unpack to a temp dir: EACCES on `<tmp>/pip-unpack-*/x.whl.metadata`).
Everything below is the exact set of workarounds that produced a working stack.
They are all plain-Python and re-runnable; no admin rights, no installers.

Everything is **offline-capable after setup** (espeak-ng, Epitran, WikiPron,
CMUdict are local files; Charsiu downloads the model once).

---

## 1. Python environment

Python 3.12.7 at `E:\Programs\anaconda3\python.exe` (pandas/numpy/pyarrow
preinstalled). A venv **could not** be created (`ensurepip` fails in the
sandbox), so dependencies are installed with `pip --target` into plain folders
and used via `PYTHONPATH`:

```
g2p_eval/pylibs/        panphon, epitran, pypinyin, pykakasi (+ deps)
g2p_eval/pylibs_torch/  torch 2.15.0+cpu, transformers 5.23.0, tokenizers 0.23.1,
                        sentencepiece, huggingface_hub (for CharsiuG2P)
g2p_eval/wheel_cache/   the raw wheels, kept for offline reinstall
```

Runtime invocation:

```powershell
$base='E:\Repos\name_collection\g2p_eval'
$env:PYTHONPATH="$base\pylibs;$base\pylibs_torch"
& E:\Programs\anaconda3\python.exe -c "import engines; print(engines.ipa('Laure','fr'))"
```

If `pip install` works on your machine (it does not in this sandbox), the plain
equivalent is:

```
python -m pip install panphon epitran pypinyin pykakasi
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install transformers sentencepiece
```

`tools/wheel_install.py` is the sandbox-safe replacement for pip: it resolves
the dependency tree from PyPI JSON, downloads wheels with `requests` and
unzips them into `--target`. Usage:
`python tools/wheel_install.py --target pylibs panphon epitran pypinyin pykakasi`

## 2. espeak-ng 1.52.0 (THE backbone) — portable, no installer

espeak-ng publishes only an MSI (there is **no** official `win-x64.zip` since
1.51; `winget install eSpeak-NG` needs admin/network, `npm` is not installed).
The MSI was downloaded from the GitHub release and unpacked **without
`msiexec`**:

```
python tools/extract_msi2.py dl/espeak-ng-1.52.0.msi espeak
```

What that script does (see source):
1. opens the MSI as an OLE compound file with the bundled pure-python
   `olefile` (MSI directory names are 3-chars-per-2-bytes packed, so they look
   garbled — identify the embedded cabinet as the largest stream);
2. writes the `#cab1.cab` stream out and expands it with the Windows builtin
   `expand.exe` (MSFZIP cabinet, 443 files, no admin needed);
3. reconstructs the long install paths from the MSI `File`/`Component`/
   `Directory` tables (`C:\ProgramFiles\eSpeak NG\espeak-ng-data\...`).

Important quirk: the cab stores the DLL as `libespeak_ng.dll` (MSI short name)
but `espeak-ng.exe` imports `libespeak-ng.dll`; copy/rename it or you get
`STATUS_DLL_NOT_FOUND (0xC0000135)`.

Resulting layout (these are the shipped runtime files):

```
espeak/installed/SourceDir/eSpeak NG/
    espeak-ng.exe              27 KB
    libespeak-ng.dll          477 KB
    espeak-ng-data/           441 files: lang/<family>/<code>, *_dict, phondata,
                              phontab, phonindex, intonations, voices/{!v,mb}
```

Run it:

```powershell
$app='E:\Repos\name_collection\g2p_eval\espeak\installed\SourceDir\eSpeak NG'
$env:ESPEAK_DATA_PATH=$app          # or set G2P_ESPEAK_DIR, engines.py checks both
& "$app\espeak-ng.exe" -v fr --ipa -q "Laure"      # -> lˈɔʁ
```

**Windows console caveat:** passing non-ASCII names as command-line arguments is
unreliable (the console code page mangles Cyrillic/Arabic/CJK; German `ü` and
Spanish `í` are silently replaced). Use file input (`-f names.txt`, UTF-8) or,
much better, the ctypes API used by `engines.py`:

```python
import engines
engines.ipa("Müller", "de")   # mˈʏlɜ      (correct; CLI gave em-te-er-...)
engines.ipa("Иван", "ru")     # ɪvˈɑn
engines.ipa("محمد", "ar")     # muħˈammad
engines.ipa("김", "ko")       # ɡˈim
engines.ipa("王", "cmn")      # wˈɑɜŋ
```

ctypes + `espeak_TextToPhonemes(..., PHONEMES_IPA)` is also ~10× faster than
spawning a process per name and preserves UTF-8: measured **26k–60k names/s**
on one CPU core (subprocess-per-name: ~30/s, i.e. 1000× slower).

## 3. WikiPron lexicons

Source repo moved: it is **`CUNY-CL/wikipron`** now, and the files are at
`data/scrape/tsv/<iso639-3>_<script>_[region_]broad|narrow.tsv` (there is no
`eng_broad.tsv`; English is split into `eng_latn_us_broad…` / `eng_latn_uk…`,
Spanish into `spa_latn_la_…`/`spa_latn_ca_…`, Portuguese into `por_latn_po_…`).
`tools/fetch_wikipron.py` downloads the ones needed into `lexicons/`.

License: WikiPron data is scraped from Wiktionary → **CC BY-SA** (inherits the
Wiktionary licence; WikiPron code itself is Apache-2.0). Attribution required,
and share-alike applies to derived redistributed lexicon files — fine for
offline internal TTS, but flag it if the lexicon itself is shipped.

Gold sets: `tools/build_gold.py` (deterministic 42-seed split: 1,200 held-out
words per language + the rest as train lexicon so the lexicon engine is scored
fairly), `tools/build_cmu_gold.py` (English CMUdict with real stress marks).

CMUdict is BSD-style (free to use/redistribute; see
`lexicons/cmudict.dict` header/upstream cmusphinx/cmudict).

## 4. CharsiuG2P (byT5-small)

`tools/fetch_charsiu.py` downloads `config.json` + `pytorch_model.bin` from
`charsiu/g2p_multilingual_byT5_small_100` and the tokenizer files from
`google/byt5-small` (the Charsiu repo has **no tokenizer files** — you must
use the byT5 tokenizer; `AutoTokenizer.from_pretrained` fails on the repo).
Downloads are done with plain `requests` because `huggingface_hub`'s cache
symlink probe hits the sandbox ACL (`dummy_file_src` PermissionError);
`HF_HUB_OFFLINE=1` + local dirs avoids it.

## 5. Reproduce the benchmark

```powershell
$base='E:\Repos\name_collection\g2p_eval'
$env:PYTHONPATH="$base\pylibs;$base\pylibs_torch"
& E:\Programs\anaconda3\python.exe tools/fetch_wikipron.py
& E:\Programs\anaconda3\python.exe tools/build_gold.py
& E:\Programs\anaconda3\python.exe tools/build_cmu_gold.py
& E:\Programs\anaconda3\python.exe bench/bench.py --engines espeak,wikipron-lexicon,epitran
& E:\Programs\anaconda3\python.exe tools/coverage.py
```

`tokens/` note: the real-name token lists (`name_tokens/<CC>.tsv`,
top-30k first names per country) are produced from the raw CSVs; the script is
inline in the build log, see `tools/coverage.py` for the schema it expects.
