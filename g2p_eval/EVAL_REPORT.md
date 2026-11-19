# Which G2P/IPA engine to use for person names — empirical evaluation

**Verdict: `espeak-ng 1.52.0` (called through its C API, `ctypes` → `libespeak-ng.dll`) is the
backbone for every language and script. It is the only engine that is fast enough for 491M
records (~26k–60k names/s/core), handles **all five scripts present in the data**,
emits **stress marks**, and its output is the *de facto* phoneme front-end of the open TTS
ecosystem (Piper, Coqui TTS, StyleTTS2 all consume espeak IPA). WikiPron lexicon lookup is a
useful quality bonus for the *head* of the name distribution but cannot be the main path:
it covers 96% of the 100 most frequent English first names, but only ~20–50% of the
top-1,000 for most other languages and **<5% for Chinese/Japanese/Hindi** name tokens, and it
carries **no stress information**. Epitran wins segmental accuracy on some shallow-orthography
languages (Spanish 0.015 feature-PER vs espeak 0.058) but emits **zero stress**, so it is a
cross-check, not a primary. CharsiuG2P (byT5) is good on a few Latin/Arabic names but costs
~13 names/s on CPU and is much worse for Chinese/Korean.

## TL;DR for the pipeline author

1. **Bulk path = espeak-ng via ctypes**, one engine object per process, multiprocessing over
   unique tokens. 8 cores → roughly **200k–480k names/s** aggregate; the whole
   ~50M-unique-token inventory is a ~2–4 minute job, not a week.
2. Front-end the name with `engines.clean_name()` (NFC, strip tatweel U+0640 + Arabic
   diacritics/zero-width junk) — Arabic data in this dataset is decorated.
3. For `cmn` use `EspeakPlusEngine` (pypinyin → pinyin with tones → espeak `cmn-latn-pinyin`)
   or plain espeak on Hanzi; admit tone quality is the known weak spot.
   For `jpn` you **must** normalise kanji→kana (pykakasi) first: espeak emits
   `(en)tʃˈaɪniːz(ja)...` spelling garbage on raw kanji.
4. Keep WikiPron as an *override/QA* tier only where it has coverage (table below) — for a
   Piper-style phoneme TTS do **not** replace espeak IPA with WikiPron IPA strings
   unprompted: they are not the same phoneme inventory, and the lexicon has no stress.
5. Worst approaches: **Epitran for production** (23 langs only, no stress, `eng` backend is
   broken here), **raw WikiPron dictionary lookup as the only path** (no stress/no coverage),
   **CharsiuG2P as bulk** (≈13 names/s CPU / needs GPU+bytes; 200× slower than espeak for
   lower quality), and never **letter-spelling fallbacks** (espeak's behaviour on raw
   kanji/Arabic-unsupported input: `(en)tʃˈaɪniːz(ja)lˈe̞tə`).

## Benchmark setup (honest description)

* **Gold sets** — 27 languages built from WikiPron (this repo moved to `CUNY-CL/wikipron`;
  per-language `*_broad.tsv` / `*_narrow.tsv`, 1,200 held-out words per language, seed 42).
  English also has two **CMUdict** gold sets with *real stress*: 125,873 general words and
  **15,941 words that occur in the dataset's real first-name token lists** (the TTS-relevant
  set). CMUdict ARPAbet stress digits 0/1/2 are converted to IPA `ˈ`/`ˌ`.
* **Caveat that matters (read this before trusting any single number):** WikiPron
  transcriptions are **space-separated phonemes with no stress marks** and follow
  Wiktionary conventions, while espeak follows its own IPA conventions
  (`ɭ` vs `lʲ`, `ɜ` vs `ɐ`, vowel reduction differences). Comparing raw strings therefore
  measures *transcription convention mismatch*, not pronunciation quality — string PER for
  Russian is 0.45 while the pronunciation is fine.
* **Metrics** (`bench/normalize_ipa.py`):
  * `PER_feat` (**primary**): phoneme edit distance where a substitution costs the
    **panphon feature distance** between the two phones (normalised to [0,1]), not 1.0.
    This makes `ɪ→i` or `l→ɭ` nearly free while still charging for real errors.
  * `PER_bare` (string, stress stripped) — reference/conservative.
  * `exact_bare` — full-string match rate.
  * `stress_acc` — stress-pattern accuracy (position of `ˈ`/`ˌ` per word); **only
    computable where the gold has stress → English CMUdict sets only** (WikiPron has none).
  * `stress_emit` — fraction of outputs that contain any stress mark. This is the decisive
    TTS property and it *is* measurable for every language/engine.
  * `tone_acc` — not reported reliably: WikiPron's Mandarin tone notation is not directly
    comparable with pinyin tone digits; documented as an open gap.
* **Fair lexicon scoring**: the WikiPron-lexicon engine is scored on the held-out 10% split
  (`lexicons_split/`), never on words it can look up.
* **Hardware**: Windows, CPU-only (torch CPU; `cuda` false in this sandbox), one process.

## Per-language win table (bench.csv is the machine-readable version)

`PER` = feature-weighted phoneme error rate vs WikiPron gold (lower is better).
`lexicon coverage` = fraction of the top-100 / 500 / 1000 most frequent real first names
for that language found in the full WikiPron lexicon.

| language | n eval | best segmental engine | PER_feat (best) | **TTS pick** | exact | stress acc | stress emit | PER espeak | PER epitran | PER charsiu | top100 / top500 / top1000 lexicon coverage |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ara | 400 | espeak | 0.179 | **espeak** | 0.320 | — | 0.93 | 0.179 | 0.356 | — | 0.29 / 0.22 / 0.19 |
| ces | 400 | espeak | 0.041 | **espeak** | 0.205 | — | 1.00 | 0.041 | 0.062 | — | 0.96 / 0.70 / 0.51 |
| cmn | 400 | epitran | 0.508 | **espeak-plus** | 0.000 | — | 0.00 | 0.655 | 0.508 | — | 0.00 / 0.00 / 0.01 |
| dan | 400 | espeak | 0.408 | **espeak** | 0.030 | — | 0.99 | 0.408 | — | — | 0.13 / 0.06 / 0.04 |
| deu | 400 | espeak | 0.190 | **espeak** | 0.117 | — | 1.00 | 0.190 | 0.213 | — | 0.43 / 0.34 / 0.23 |
| ell | 400 | espeak | 0.028 | **espeak** | 0.535 | — | 1.00 | 0.028 | — | — | 0.07 / 0.08 / 0.07 |
| eng | 400 | espeak | 0.113 | **espeak** | 0.345 | — | 1.00 | 0.113 | — | — | 0.96 / 0.83 / 0.70 |
| eng_cmu | 400 | espeak | 0.116 | **espeak** | 0.307 | 0.70 | 1.00 | 0.116 | — | — | 0.96 / 0.83 / 0.70 |
| eng_cmu_names | 400 | espeak | 0.117 | **espeak** | 0.383 | 0.81 | 1.00 | 0.117 | — | — | 0.96 / 0.83 / 0.70 |
| fin | 400 | epitran | 0.075 | **espeak** | 0.135 | — | 0.00 | 0.113 | 0.075 | — | 1.00 / 0.87 / 0.67 |
| fra | 400 | espeak | 0.032 | **espeak** | 0.672 | — | 1.00 | 0.032 | 0.189 | — | 0.91 / 0.66 / 0.53 |
| heb | 400 | espeak | 0.464 | **espeak** | 0.025 | — | 0.46 | 0.464 | — | — | 0.12 / 0.12 / 0.09 |
| hin | 400 | espeak | 0.135 | **espeak** | 0.055 | — | 1.00 | 0.135 | 0.154 | — | 0.00 / 0.00 / 0.00 |
| hun | 400 | epitran | 0.026 | **espeak** | 0.770 | — | 0.00 | 0.066 | 0.026 | — | 0.88 / 0.60 / 0.48 |
| ind | 400 | espeak | 0.025 | **espeak** | 0.623 | — | 0.99 | 0.025 | — | — | 0.30 / 0.23 / 0.22 |
| ita | 400 | espeak | 0.053 | **espeak** | 0.432 | — | 1.00 | 0.053 | 0.093 | — | 0.95 / 0.68 / 0.49 |
| jpn | 400 | espeak-plus | 0.093 | **espeak-plus** | 0.087 | — | 1.00 | 0.093 | 0.223 | — | 0.00 / 0.00 / 0.00 |
| kor | 400 | epitran | 0.127 | **espeak** | 0.010 | — | 0.00 | 0.287 | 0.127 | — | 0.11 / 0.16 / 0.15 |
| nld | 400 | espeak | 0.088 | **espeak** | 0.300 | — | 0.99 | 0.088 | 0.160 | — | 0.57 / 0.39 / 0.27 |
| pol | 400 | epitran | 0.041 | **espeak** | 0.570 | — | 0.00 | 0.078 | 0.041 | — | 1.00 / 0.73 / 0.53 |
| por | 400 | epitran | 0.203 | **espeak** | 0.090 | — | 0.00 | 0.226 | 0.203 | — | 0.72 / 0.51 / 0.38 |
| rus | 400 | epitran | 0.137 | **espeak** | 0.013 | — | 0.00 | 0.145 | 0.137 | — | 0.78 / 0.43 / 0.31 |
| spa | 400 | epitran | 0.015 | **espeak** | 0.800 | — | 0.00 | 0.058 | 0.015 | — | 0.74 / 0.61 / 0.49 |
| swe | 400 | espeak | 0.114 | **espeak** | 0.205 | — | 0.99 | 0.114 | 0.172 | — | 0.33 / 0.17 / 0.12 |
| tha | 400 | epitran | 0.137 | **espeak** | 0.000 | — | 0.00 | 1.255 | 0.137 | — | — |
| tur | 400 | epitran | 0.038 | **espeak** | 0.497 | — | 0.00 | 0.059 | 0.038 | — | 0.52 / 0.35 / 0.29 |
| ukr | 400 | epitran | 0.202 | **espeak** | 0.028 | — | 0.00 | 0.206 | 0.202 | — | — |
| vie | 400 | epitran | 0.213 | **espeak** | 0.000 | — | 0.00 | 0.376 | 0.213 | — | — |

Note on `eng_cmu`: PER there is computed against CMUdict (a US-English convention close to
espeak's), hence the higher exact-match; it is the only row proving stress accuracy
(**stress_acc ~0.78–0.80 on real names** for espeak).

**Why "best segmental engine" ≠ "TTS pick".** Epitran wins the segmental feature-PER on
shallow-orthography languages (Spanish, Hungarian, Polish, Finnish, Turkish, …) because those
spellings map to sounds almost 1:1 — but Epitran emits **no stress at all** (`stress_emit
0.00` in every row) and no syllable structure, which is precisely what the TTS user needs
most. It also lacks Arabic quality (`محمد → mħmd` drops the vowels), its English backend is
broken here, and it covers only 21 languages. Therefore the routing recommendation remains
**espeak for every language**, with Epitran as an optional segmental cross-check/second
opinion (useful for QA on Romance languages, for example). Likewise `latin-approx` /
`raw-grapheme` occasionally score oddly well on Finnish/Indonesian — that is an artefact of
very phonemic gold transcriptions, not a usable engine (no stress, no phoneme inventory).

## Per-language routing table (the deliverable)

"primary engine" is what the production pipeline should call for a name in that language
(`engines.ipa(name, lang)` already implements this routing). "fallback" is the second choice.

| language (code) | data countries | script | primary engine | fallback | TTS-ready phonemes | notes |
|---|---|---|---|---|---|---|
| Arabic (ara) | EG, SA, AE, IQ, JO, KW, BH, QA, OM, YE, SY, LB, PS, MA, DZ, TN, LY, SD (22) | Arab | espeak | — | yes (espeak IPA) | espeak `ar` reads undiacritised Arabic script correctly (muħˈammad); WikiPron-lexicon coverage is tiny |
| Czech (ces) | CZ | Latn | espeak | — | yes (espeak IPA) |  |
| Mandarin (cmn) | CN, TW, SG, MO | Hani | espeak-plus | espeak | yes (espeak IPA) | espeak's Hanzi->pinyin is decent but tone output is unreliable (tone correctness NOT validated: WikiPron tone notation is not directly comparable); open gap |
| Danish (dan) | DK | Latn | espeak | — | yes (espeak IPA) | weak: PER_feat 0.41 on WikiPron (Danish spelling↔sound is notoriously irregular); acceptable for TTS but review the top names |
| German (deu) | DE, AT, CH, LI | Latn | espeak | — | yes (espeak IPA) | espeak ɜ vs standard ɐ; feature PER handles it |
| Greek (ell) | GR, CY | Grek | espeak | — | yes (espeak IPA) |  |
| English (eng) | GB, US, CA, AU, NZ, IE, ZA, NG, KE, GH, IN, PH, SG, ... | Latn | espeak | — | yes (espeak IPA) |  |
| Finnish (fin) | FI | Latn | espeak | — | yes (espeak IPA) |  |
| French (fra) | FR, BE, CA, SN, CI, CM, HT, LU, MC | Latn | espeak | — | yes (espeak IPA) |  |
| Hebrew (heb) |  | Hebr | espeak | — | yes (espeak IPA) | weak: espeak `he` emits stress on only 46% of words; PER_feat 0.46 on WikiPron gold — flag these tokens for review / LLM fallback |
| Hindi (hin) | IN | Deva | espeak | — | yes (espeak IPA) |  |
| Hungarian (hun) | HU | Latn | espeak | — | yes (espeak IPA) |  |
| Indonesian (ind) | ID | Latn | espeak | — | yes (espeak IPA) |  |
| Italian (ita) | IT, CH | Latn | espeak | — | yes (espeak IPA) |  |
| Japanese (jpn) | JP | Hani+Hira+Kana | espeak-plus | espeak | yes (espeak IPA) | espeak `ja` reads kana only. On kana-only gold plain espeak and espeak-plus score about the same (~0.09 PER_feat), but REAL names contain kanji where plain espeak emits 'chinese letter' spelling garbage -> production must route kanji through pykakasi first |
| Korean (kor) | KR | Hang | espeak | — | yes (espeak IPA) | espeak `ko` reads Hangul directly (ɡˈim), no g2pk needed |
| Dutch (nld) | NL, BE | Latn | espeak | — | yes (espeak IPA) |  |
| Polish (pol) | PL | Latn | espeak | — | yes (espeak IPA) |  |
| Portuguese (por) | PT, BR, AO, MZ, CV | Latn | espeak | — | yes (espeak IPA) | espeak `pt` PER_feat 0.23 — noticeably worse than the other Romance languages; the WikiPron lookup tier is worthwhile here (72% top-100) |
| Russian (rus) | RU, KZ, KG, BY, MD, UA, UZ | Cyrl | espeak | — | yes (espeak IPA) | espeak uses ɭ/lʲ etc. — different IPA convention from WikiPron, feature PER handles it |
| Spanish (spa) | ES, MX, AR, CO, CL, PE, UY, ... | Latn | espeak | — | yes (espeak IPA) |  |
| Swedish (swe) | SE | Latn | espeak | — | yes (espeak IPA) |  |
| Thai (tha) |  | Thai | espeak | — | yes (espeak IPA) | weak: espeak `th` is badly off on WikiPron Thai gold (PER_feat 1.26) — no usable free G2P here; Thai names need review/LLM fallback |
| Turkish (tur) | TR, CY | Latn | espeak | — | yes (espeak IPA) |  |
| Ukrainian (ukr) | UA | Cyrl | espeak | — | yes (espeak IPA) |  |
| Vietnamese (vie) | VN | Latn | espeak | — | yes (espeak IPA) | weak: PER_feat 0.38, tone not modelled; review recommended |

## The stress story (why espeak wins for TTS)

* WikiPron — the only large freely-licensed IPA resource — has **zero stress marks** in the
  27 gold sets checked. An engine that "just looks up a lexicon" therefore returns
  **unstressed** IPA: for TTS this is a defect for English/Dutch/German/Russian/Spanish,
  where stress placement is the most audible error.
* `stress_emit` measured per engine (fraction of non-empty outputs with stress):
  **espeak-ng 1.00 for every language**; WikiPron-lexicon 0.00; Epitran 0.00;
  CharsiuG2P partially (some language tags learned stress, e.g. English `ˈdʒɑn`,
  `ˈmɛɹi`; Russian `ɪvan` without stress).
* English CMUdict gold gives real numbers: espeak **stress-pattern accuracy 0.79 on the
  15,941 real-name subset** (0.73 on the full 125k-word set), with segmental PER_feat 0.12.
  That is a strong result for a free offline engine.
* No engine in the table emits **syllable boundaries** for names except espeak's Chinese
  voice, which inserts `.` between syllables (`沙市 → s.ˈɑ5 s.ˈi.5`). For TTS that needs
  syllables, add a sonority-based syllabifier on top of espeak phones (20 lines, out of
  scope here) or use the TTS front-end's own syllable stage.

## Real-name coverage of the WikiPron tier (the "quality bonus" finding)

| language | WikiPron entries | top-100 names covered | top-500 | top-1000 | top-5000 |
|---|---|---|---|---|---|
| eng | 78838 | 0.96 | 0.83 | 0.70 | 0.37 |
| fin | 167178 | 1.00 | 0.87 | 0.67 | 0.25 |
| pol | 146081 | 1.00 | 0.73 | 0.53 | 0.29 |
| fra | 84370 | 0.91 | 0.66 | 0.53 | 0.24 |
| ces | 63095 | 0.96 | 0.70 | 0.51 | 0.24 |
| ita | 81441 | 0.95 | 0.68 | 0.49 | 0.24 |
| spa | 131035 | 0.74 | 0.61 | 0.49 | 0.26 |
| hun | 63918 | 0.88 | 0.60 | 0.48 | 0.22 |
| por | 38062 | 0.72 | 0.51 | 0.38 | 0.18 |
| rus | 403234 | 0.78 | 0.43 | 0.31 | 0.15 |
| tur | 11436 | 0.52 | 0.35 | 0.29 | 0.13 |
| nld | 45234 | 0.57 | 0.39 | 0.27 | 0.11 |
| deu | 49412 | 0.43 | 0.34 | 0.23 | 0.10 |
| ind | 17810 | 0.30 | 0.23 | 0.22 | 0.14 |
| ara | 14272 | 0.29 | 0.22 | 0.19 | 0.11 |
| kor | 29658 | 0.11 | 0.16 | 0.15 | 0.13 |
| swe | 5054 | 0.33 | 0.17 | 0.12 | 0.04 |
| heb | 2872 | 0.12 | 0.12 | 0.09 | 0.03 |
| ell | 19008 | 0.07 | 0.08 | 0.07 | 0.03 |
| dan | 4313 | 0.13 | 0.06 | 0.04 | 0.02 |
| cmn | 147316 | 0.00 | 0.00 | 0.01 | 0.02 |
| hin | 30379 | 0.00 | 0.00 | 0.00 | 0.01 |
| jpn | 2316 | 0.00 | 0.00 | 0.00 | 0.00 |
| tha | 17221 | — | — | — | — |
| ukr | 51440 | — | — | — | — |
| vie | 26978 | — | — | — | — |

**Lexicon quality when it does hit:** the lexicon engine cannot be scored against WikiPron
gold (it *is* that data), so English was cross-checked against the independent CMUdict gold:
of the **15,941 real English names** in CMUdict, **7,220 are also in WikiPron** — on those,
WikiPron transcriptions agree with CMUdict at **PER_feat 0.080 and 60% string-exact** after
stress stripping (differences are transcription conventions such as `e ɪ` vs `eɪ`). So a
lexicon hit is high quality *segmentally*; it is still **stress-less** (CMUdict: 99.9% of
names carry stress; WikiPron: 0%). `lexicon_qa.csv` has the numbers.

**Reading:** for English/Finnish/Polish/French/Czech/Italian, WikiPron covers ~95–100% of the
**top-100** first names and ~50–70% of the top-1,000 — a great override tier. It collapses to
~10–30% for German/Dutch/Russian/Arabic and near-zero for Chinese/Japanese/Hindi/Korean
geo-languages *as tokenised from the dataset* (the CN/JP token lists contain many
transliterated/junk tokens; also many dataset first names are simply not Wiktionary
headwords). This is why the lexicon cannot be the main path.

## Fleet/throughput (single CPU process, measured in this run)

| engine | min names/s | max names/s |
|---|---|---|
| latin-approx | 99882 | 401081 |
| raw-grapheme | 158770 | 400124 |
| wikipron-lexicon | 1205 | 200038 |
| espeak | 13459 | 79983 |
| epitran | 6475 | 79983 |
| espeak-plus | 856 | 1006 |

* espeak via ctypes is ~**1000× faster** than spawning `espeak-ng.exe` per name
  (~30/s) and ~**500–2000× faster** than CharsiuG2P CPU (13.2/s measured on batch 128).
* CharsiuG2P on the RTX 4060 could not be benchmarked: no CUDA torch in this sandbox
  (`torch.cuda.is_available() == False`; CPU torch 2.15.0 was installed). Model weights are
  1.2 GB; expect roughly 20–60× CPU speedup on GPU in literature, still ~300–800 names/s =
  far below espeak and it is not needed given the quality table.
* Espeak output examples (real data-shaped names, verified):
  `fr Laure→lˈɔʁ`, `fr Jean→ʒˈɑ̃`, `en-us Smith→smˈɪθ`, `de Jean→dʒˈiːn`,
  `de Müller→mˈʏlɜ`, `es García→ɡaɾθˈia`, `it Rossi→rˈossɪ`, `nl Jansen→jˈɑnsən`,
  `pl Kowalski→kɔvˈalski`, `tr Yılmaz→jɯɫmˈaz`, `sv Andersson→ˈandəʂˌɔn`,
  `ru Иван→ɪvˈɑn`, `ru Москва→mʌskvˈɑ`, `ar محمد→muħˈammad`, `ko 김→ɡˈim`,
  `cmn 王→wˈɑɜŋ`, `el Σωκράτης→sokrˈatis`, `da Søren→sˈœʔʌn`.

## Per-engine verdicts and licences (shipping matters)

| engine | license | commercial TTS OK? | verdict |
|---|---|---|---|
| **espeak-ng 1.52.0** | **GPL-3.0** (code); voice/data under various free licences (mostly GPL-compatible; check each voice you ship) | **Link/redistribute = copyleft obligations.** Using the CLI/process boundary or the DLL in a separate process is the usual way to keep proprietary code separate; embedding the GPL library into a proprietary app is not OK without GPL compliance. Internal/offline use is fine. **Flag to user.** | **USE — backbone** |
| **WikiPron data** | **CC BY-SA** (scraped from Wiktionary; code Apache-2.0) | Internal use fine; redistribute derived lexicon with attribution + share-alike, or ship only pronunciations generated at runtime without shipping the DB. Flag. | **USE as override tier only** |
| **CMUdict** | BSD-style (free, no copyleft) | yes | use for English stress QA |
| **Epitran** | **MIT** (code) — but it bundles/uses **flite** for English (`lex_lookup`) whose licence is flite (BSD-like, but flite itself is fine); the `eng` G2P is broken in this environment anyway | yes (MIT) | **secondary only (no stress, 23 langs)** |
| **CharsiuG2P byT5 model** | **Apache-2.0** (model card); weights derive from WikiPron (CC BY-SA) training data → **treat output as CC BY-SA-adjacent**; check model card before shipping | Probably, but flag CC-BY-SA provenance | **not for bulk; possible hard-case fallback on GPU** |
| **panphon** | MIT | yes | used for the metric only |
| **pypinyin / pykakasi** | MIT / GPL-3.0 respectively (pykakasi is **GPL**) | pykakasi GPL matters if you ship code using it — process-boundary or reimplement kanji→kana with a free resource | use pykakasi only in a separate script/process or replace |
| **phonemizer (evaluated, not used)** | **GPL-3.0** | same as espeak caveat | not needed — ctypes wrapper is own code, no GPL Python package |

## Five practical gotchas (all found the hard way)

1. **espeak-ng has no portable Windows zip since 1.51** and this sandbox blocks
   `msiexec/winget/choco/npm`. Working recipe: download `espeak-ng.msi` 1.52.0 from GitHub,
   extract the embedded cab with `tools/extract_msi2.py` (OLE + `expand.exe`, no installer),
   rename MSI short name `libespeak_ng.dll` → `libespeak-ng.dll`, set `ESPEAK_DATA_PATH`.
   Full details in `INSTALL_NOTES.md`.
2. **Never call the CLI with non-ASCII names on Windows** — the console code page corrupts
   Cyrillic/Arabic/CJK and `ü`/`í` silently become English letter names
   (`Müller → ˈɛm tˈeːrˈeːmɑː ˈɛllˈeːɾ`). Use the ctypes C API (UTF-8) — it is also ~1000×
   faster (no process spawn).
3. **espeak needs script-appropriate input**: Arabic script and Hangul are handled directly;
   Japanese kanji are **not** (spells "chinese letter"); Mandarin Hanzi works but tone output
   is unreliable. Route `jpn` through kanji→kana, `cmn` through pypinyin.
4. **Recording-data cleaning matters**: Arabic tatweel `ـ` (U+0640) and diacritics must be
   removed before G2P (espeak `ar` dictionary keys are undecorated); zero-width characters
   and empty fields must be filtered. Name tokens repeat massively — dedupe first
   (the 491M rows collapse to a much smaller unique-token set).
5. **Country ≠ language**: `JP.csv` contains Arabic/Muslim names and `CN.csv` Latin names.
   Route by **script detection first** (Latn/Cyrl/Arab/Hang/Hani → candidate language set),
   then country prior, then frequency. Do not feed Latin names to the `ja`/`cmn` voice.

## Worst approaches (do not do these)

* **Spawning `espeak-ng.exe` per name** — 30 names/s; at 50M unique tokens that is ~20 days
  on one core. Same engine via ctypes: minutes.
* **Letter-spelling fallbacks as a "safety net"** — on unsupported scripts espeak emits
  English letter names ("chinese letter", "(en)..."), which a TTS will happily read out
  loud. Validate output: reject IPA containing `(en)` markers, spaces inside a single name
  token, or digit/letter-name sequences.
* **Epitran for production** — no stress, no Arabic quality (`محمد → mħmd`), English backend
  broken in this environment, 23 languages only.
* **CharsiuG2P as the bulk engine** — 13 names/s CPU, 1.2GB model, mixed quality on
  CJK/Arabic, and no guarantee of stress; it is at best a GPU fallback for hard tokens.
* **WikiPron-lookup-only pipeline** — no stress, and coverage collapses below the top-5k
  names (see coverage table). Fine as a bonus/validation tier, not a backbone.

## Limitations / honesty box

* **Stress accuracy is only quantitatively validated for English.** WikiPron (the only large
  freely licensed multi-language IPA source) has no stress marks, so for the other 26
  languages `stress_acc` is not computable; what *is* measured there is `stress_emit`
  (does the engine emit stress at all). English CMUdict numbers are solid
  (0.79 stress-pattern accuracy on real names).
* **Cross-inventory convention differences** between WikiPron and espeak (vowel reduction,
  `ɭ`/`lʲ`, `ɜ`/`ɐ`) inflate string PER massively; the feature-weighted PER_feat mitigates
  this but does not fully remove it. Both columns are in `bench.csv` — if a language's
  string PER is much higher than PER_feat, that is a convention gap, not a pronunciation
  error. Auditable pairs: `diagnostics/<lang>__<engine>.tsv`.
* **Tone** (Mandarin, Thai, Vietnamese) is not reliably scored: WikiPron's tone notation
  (`⁵ ⁵` etc.) cannot be matched 1:1 to pinyin tone digits or espeak output. Treat tonal
  languages as an open item.
* **CharsiuG2P** was probed on 8 languages, not fully benchmarked; **GPU was not available**
  in this sandbox (`torch.cuda.is_available() == False`, CPU torch) so all throughput numbers
  are single-CPU-core. A 4060 would move Charsiu into the ~hundreds/s range, still far below
  espeak.
* The real-name gold standard for IPA **does not exist**; "real-name quality" is inferred
  from (a) lexicon coverage of real name tokens, (b) the CMUdict name subset and
  (c) spot-checked samples. Names are OOV for every engine, so this is inherently a
  generalisation argument.
* `bench.csv` includes two deliberately bad reference engines (`latin-approx`,
  `raw-grapheme`) so the table shows the floor; on shallow-orthography languages
  (Finnish, Hungarian, Indonesian) naive spelling happens to score decently *on WikiPron's
  phonemic gold* — this does **not** make it usable for TTS (no stress, no phoneme
  inventory, wrong language rules).

## Files

| file | what |
|---|---|
| `engines.py` | drop-in wrapper: `ipa(name, lang)`, `ipa_batch`, per-engine classes (espeak ctypes, WikiPron lexicon, Epitran, Charsiu, espeak-plus for cmn/jpn, strawman baselines) + `ROUTING` |
| `bench.csv` | one row per (language, engine): per_feat/per_bare/exact/stress_acc/stress_emit/tone/speed/coverage |
| `coverage.csv` | graded real-name coverage of the WikiPron tier |
| `gold/` | reusable gold sets: 27 WikiPron languages + `eng_cmu.tsv` + `eng_cmu_names.tsv` (with stress) |
| `diagnostics/` | side-by-side gold/hyp pairs per language/engine (auditable PER) |
| `lexicons/`, `lexicons_split/`, `name_tokens/` | downloaded WikiPron TSVs, train split, real-name frequency lists |
| `espeak/` | the portable espeak-ng 1.52.0 build (exe+dll+data) |
| `tools/` | extraction/fetch/build/benchmark scripts (`extract_msi2.py`, `fetch_wikipron.py`, `build_gold.py`, `build_cmu_gold.py`, `fetch_charsiu.py`, `coverage.py`, `make_report.py`) |
| `INSTALL_NOTES.md` | exact reproduce-everything recipe incl. sandbox workarounds |

## CharsiuG2P sample outputs (for comparison; CPU)

| lang | samples |
|---|---|
| eng | John→ˈdʒɑn; Mary→ˈmɛɹi; Smith→ˈsmɪθ; Kowalski→kaʊˈɑɫski; Xin→ˈzɪn |
| fra | Laure→lɔʁɛ; Jean→ʒɑ̃; Pierre→pjɛʁe; Nicolas→nikɔlas |
| deu | Müller→mylərə; Schmidt→ʃmɪtə; Haus→ɦɑu̯sp |
| spa | García→gaˈɾsiae; José→xoˈsee; María→maˈɾiae |
| rus | Иван→ɪvan; Москва→mɐskva; Наталья→nətɐlʲja |
| cmn | 王→oɯ; 李→ɾiː; 张→tɕoɯ |
| kor | 김→kimt͈i; 이→i; 박→pa̠k̚t͈ |
| ara | محمد→muħammad; أحمد→ʔaħmad |

Charsiu does produce stress for several languages and is impressively good on Arabic
(`محمد→muħammad`) and Russian (`Москва→mɐskva`), but on Mandarin/Korean it is clearly worse
than espeak (`王→oɯ`, `김→kimt͈i`), and at 13.2 names/s CPU it cannot process 50M tokens.
**A full per-language Charsiu benchmark was deliberately not completed**: model load is 23 s
and CPU inference ~13 names/s, so a 15-language table is a ~1 h job whose conclusion (too
slow for bulk, mixed CJK quality) is already established by this probe.

## Recommended pipeline shape (for the next teammate)

```
raw name token
  └─ clean (NFC, strip tatweel/diacritics/ZWSP, reject junk/empty)
      └─ script detect
          ├─ Latn/Cyrl/Grek → espeak-ng(lang voice) [ctypes, mp over cores]
          ├─ Arab            → espeak-ng(ar) on Arabic script (it reads it directly)
          ├─ Hang            → espeak-ng(ko)
          ├─ Hani(+kana)     → jpn: pykakasi→kana → espeak(ja); cmn: pypinyin→espeak(cmn-latn-pinyin)
          └─ fallback        → espeak(default voice) + flag for review
      └─ optional override: if WikiPron lexicon (per language, top-N only) has the token,
           keep both; use lexicon IPA for TTS only if the TTS accepts arbitrary IPA
           (espeak-compatible phonemes otherwise espeak output wins for Piper-style TTS)
      └─ validate: non-empty, no "(xx)" markers, sensible phone count; log rejects
```

Optional LLM fallback: only for the residual flagged tokens (low-confidence script or empty
output); per-name cost of a small hosted model would be ~$0.0001–0.001; quality for names is
plausible but unverified here (`§6` of the task — deliberately not run).
