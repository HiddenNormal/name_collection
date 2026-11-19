# name_dataset profile — normalization, script mix, junk filter, and the unique-token inventory

Full-scan profile of all **105 country CSVs** (`491,655,925` rows, `9.95 GB`) for the
name→IPA/TTS pipeline. Every number below is from an **exact full scan** of every file —
no `head()`, no `limit`, no random-offset sampling in any count (the CSVs are not
homogeneous along their length; see §7).

- Generated: 2026-11-19, by `data-profiler`.
- Normalizer version in this report: **`normalize.py` v1.1** (v1.1 fixes a v1.0 Indic
  trailing-mark bug — see §5.3; the parquet inventory artifacts were built with v1.0,
  see the artifact caveat in §5.3).
- Artifacts: `E:\Repos\name_collection\profile\` (schemas and consumption contract in §1).

---

## 0. TL;DR

- **491,655,925 rows** / 105 countries confirmed exactly (`description.txt` says
  491,655,925). 983,311,850 name-field occurrences (2 per row).
- **Unique-token inventory size**
  - per-country normalized distinct sum: **101,592,045** (first 45,220,974 + last
    56,371,071); raw per-country distinct sum: **102,864,698** (Lead's independent
    trim-only count; our untrimmed count is 102,865,216). → normalization folds
    **1.24%** of per-country tokens (fold 1.0125x): case + NFKC + whitespace/decoration
    collapse. Egyptian-style Arabic decoration accounts for nearly all of it (EG 1.056x,
    SD 1.072x, YE 1.071x, IQ 1.064x; Latin/Cyrillic countries are 1.000–1.005x).
  - **global (deduplicated across countries): 59,791,550 unique normalized tokens** vs
    **67,736,023 raw trim-only tokens** (Lead's independent scan) → normalization removes
    **11.7% of the G2P worklist** globally. This 59.79M is the number of pronunciations a
    fully-deduplicated lexicon would need.
- **Junk is rare.** `is_junk` = **115,926 occurrences = 0.0118%** of the 980,309,426
  non-empty name-field occurrences (69,726 distinct keys). Empty name fields add 0.305%.
  **Recommended filter retention = 99.683%** of field occurrences (99.988% of non-empty
  tokens are usable). Decoration is mostly *strippable*, not fatal: tatweel, Arabic
  diacritics, box-drawing/dingbats, `_`/`@` prefixes, ZWJ etc. all normalize away
  (`╚► Rasha`→`Rasha`, `_Aya`→`Aya`, `محہٰمد`→`محمد`).
- **Script mix — two definitions, both reported** (see §3.1): whole-token-dominant
  occurrence share **Latn 82.55% / Arab 14.58% / Cyrl 2.11% / non-Latin 17.45%**;
  exact character-weighted share **Latn 84.07% / Arab 13.09% / Cyrl 2.31% /
  non-Latin 15.93%**. (Lead's independent random-offset estimate: Latn 82.90 / Arab
  14.38 / Cyrl 2.13 / non-Latin 17.10 — consistent within ~1 pp.)
- **Country ≠ language**: only 6 countries are Arabic-script-dominant (IQ, JO, LY, SD,
  SY, YE); EG is 50.4% Latin-script vs 49.4% Arabic-script; KW and QA are ~100% Latin
  (transliterated); JP is 86.8% Latin-script tokens. Script mix per country is in §3.2.
- **18.859%** of rows have empty gender; 0.199% empty first name, 0.412% empty last name
  (KR has 82.5% empty last names — Korean naming convention — so its *field* retention
  is 58.4% even though its junk rate is ~0).
- Merged/multi-word names: **9.33% of non-empty occurrences**, 39,545,042 distinct keys.
  Not junk: mostly Spanish double surnames and Dutch/German particles
  (`De La Cruz`, `Van Der Merwe`, `Van Den Berg`); highest in PE 34.4%, ES 29.0%.
- **Zipf**: top-1000 normalized keys cover **34.2% of all first-name occurrences**
  (raw-token curve: 36.7%); rank 1 `محمد` = 6,160,965 occurrences (0.63% of everything).
  G2P the unique keys by frequency rank; the long tail is singletons.
- **Known limitations**: Arabic homoglyph letter variants are not folded in v1
  (1.49% of Arabic occurrences; §5.4); no language tags in the data (§3.3);
  v1.0-built parquet norms for ~0.09% of occurrences (Indic trailing marks) are stale
  relative to `normalize.py` v1.1 (§5.3).

---

## 1. Deliverables and how to consume them

| artifact | size | content |
|---|---|---|
| `profile/unique_tokens.parquet` | **101,592,045 rows**, 25 cols, 2.3 GB | one row per **(country, field, normalized key)**; all tokens incl. junk (so filters are auditable) |
| `profile/sampled_tokens.parquet` | **105,000 rows** (top-500 per country+field, non-junk) | convenience/demo subset, **same schema** as `unique_tokens.parquet` |
| `profile/sampled_tokens.META.json` | — | provenance, `top_n_per_country_field=500`, `partial=false` |
| `profile/country_stats.parquet` (+ `country_stats.csv`) | 105 rows × 45 cols | per-country: rows, empties, gender, raw-vs-normalized distinct, script mix, junk shares, retention, top-20 tokens |
| `profile/normalize.py` | — | reusable normalizer + junk filter, stdlib-only, self-tested (`python normalize.py`) |

`unique_tokens.parquet` schema:
`country, field, key, norm, token, script, mixed, count, count_m, count_f, count_u,
n_variants, n_words, is_junk, is_single, is_long, no_letters, has_digit, has_symbol,
has_punct, has_tatweel, has_arab_diac, has_invisible, handle_like, is_decorated_only`

- `count` = exact number of occurrences of `key` in that `country`+`field`, summed over
  its raw surface variants. **This is the correct frequency ranking** (demo top-N must
  sort on it, not on presence).
- `token` = most frequent raw surface form; `norm` = normalized surface; `key` =
  canonical casefolded lookup key. **G2P `norm`; join/dedup on `key`.**
- `count_m/count_f/count_u` = gender split of those occurrences.
- `mixed` = token carries ≥2 scripts; `n_variants` = number of distinct raw forms that
  fold into this key; `n_words` = whitespace-separated word count.
- Per-country inventory files also exist as `work/profile_tmp/countries/<CC>.parquet`
  (same schema, one file per country) if a per-country join is cheaper.

Consumption contract (frozen API, `normalize.py` v1.1):

```python
import sys; sys.path.insert(0, r"E:\Repos\name_collection\profile")
from normalize import normalize_token, is_junk

r = normalize_token(raw_string)
r["norm"]              # clean surface -> feed to G2P/TTS
r["key"]               # canonical casefolded key -> JOIN / dedup on this
r["script"]            # Latn/Cyrl/Arab/Hang/Hani/Hira/Kana/Grek/Hebr/Deva/Beng/...
r["flags"]["is_junk"]  # True -> drop before spending TTS compute
r["key_loose"]         # extra-aggressive fallback key (see §5.5 warning)

# per-row join: df["first_name"].map(normalize_key) -> merge with unique_tokens.parquet
```

Filter recommendation:

```
keep a token iff not flags['is_junk']        # 0.0118% of non-empty occurrences dropped
treat an empty first/last name as "no token", not as junk
# field-level retention (incl. empties) is 99.683% overall; worst country is KR 58.4%
# only because it has 82.5% empty last names — its junk-only retention is 99.999%
```

---

## 2. Scale: rows, fields, gender, distinct tokens

| metric | value |
|---|---|
| rows (records) | **491,655,925** |
| name-field occurrences (first+last, incl. empty) | 983,311,850 |
| empty first-name fields | 977,876 (0.199% of rows) |
| empty last-name fields | 2,024,548 (0.412% of rows) |
| rows with both names empty | **0** |
| empty share of name-field occurrences | **0.305%** |
| gender M / F / unknown(empty) | 242,152,573 / 156,780,484 / 92,722,868 — unknown **18.859%** |
| raw-distinct first names (sum over countries, untrimmed) | 45,906,134 |
| raw-distinct last names (sum over countries, untrimmed) | 56,959,082 |
| **normalized-distinct first names** (sum over countries) | **45,220,974** |
| **normalized-distinct last names** (sum over countries) | **56,371,071** |
| **normalized-distinct total** (per-country sum) | **101,592,045** |
| raw-distinct total (per-country sum, Lead's trim-only) | 102,864,698 |
| **global unique normalized keys** (deduped across countries) | **59,791,550** |
| global unique raw tokens, trim-only (Lead's independent scan) | 67,736,023 |

Cross-check on the raw side: our untrimmed per-country distinct counts are 45,906,134 /
56,959,082 versus Lead's trim-only 45,905,862 / 56,958,836 (+272 / +246 difference from
tokens distinguished only by leading/trailing whitespace — expected). For every one of the
105 countries our raw-distinct count is ≥ Lead's trim-only count (0 violations).

Normalization fold by field: first 45,906,134 → 45,220,974 (1.0151x), last 56,959,082 →
56,371,071 (1.0104x). Per-country fold table is in §5.2; range 1.000–1.072.

Per-country inventory size extremes: largest **EG 5,294,113** keys, US 4,650,616,
SA 5,890,374 (last-name heavy), IT 2,820,476, FR 3,776,975; smallest **KH 3,892**,
SV 3,633, FJ 6,879, BF 5,188 (16 countries have <100k rows). 15 countries have >10M rows;
median country = 1,463,873 rows.

---

## 3. Script composition — and why country ≠ language

### 3.1 Two definitions, two numbers (both exact)

1. **Whole-token dominant script (occurrence-weighted)** — for each name field of each
   row, assign the token its dominant letter script, weight by occurrence count. This is
   the measure implemented in `unique_tokens.parquet.script`; it answers *"if this token
   is G2P'd as one unit, which script's G2P should handle it?"*
2. **Character-weighted script share** — count each character of the normalized surface,
   weighted by occurrence. This is the exact version of Lead's random-offset scan; it
   answers *"what fraction of written characters are in script X?"*

| script | (1) whole-token dominant share | (2) char-weighted share |
|---|---|---|
| Latn | **82.554%** | **84.073%** |
| Arab | **14.576%** | **13.089%** |
| Cyrl | **2.106%** | **2.309%** |
| Hebr | 0.307% | 0.227% |
| Beng | 0.152% | 0.134% |
| Hani | 0.124% | 0.0398% |
| Deva | 0.035% | 0.0317% |
| Grek | 0.024% | 0.0285% |
| Ethi | 0.026% | 0.0172% |
| Hang | 0.018% | 0.0057% |
| other / smaller | 2.4% total | 2.7% total |
| **non-Latin** | **17.446%** | **15.927%** |

Definitions and why they differ: whole-token dominance up-weights scripts whose tokens
are short (one Hangul/Han token = one occurrence regardless of length), character
weighting up-weights long tokens (Latin names are longer in characters). Concrete
examples (per country, char-weighted): KR 50.8% Hangul chars but 72.3% Hangul-dominant
tokens; CN 3.7% Hani chars but 8.9% Hani-dominant tokens; JP 5.6% vs 13.2%. For workload
sizing use (1) (token = unit of G2P); for "how much non-Latin orthography is in the data"
use (2).

Independent cross-check: Lead's random-offset character-level scan, weighted by exact row
counts, gives **Latn 82.90% / Arab 14.38% / Cyrl 2.13% / non-Latin 17.10%**. Our exact
character-weighted measure gives Latn 84.07% / Arab 13.09% / Cyrl 2.31% / non-Latin
15.93%. The ~1 pp gap is sampling/weighting residue in the estimate (random-offset sample
vs full inventory) — no contradiction at this scale.

### 3.2 Per-country composition (29 countries with >10% non-Latin tokens)

`dom` = dominant script share of occurrences; `2nd` = next script; `char-NL` = exact
character-weighted non-Latin share. A country's primary language (coarse ISO mapping) is
shown for context, not inferred from names.

| country | primary language | dom | dom % | 2nd | 2nd % | non-Latin tokens | char-NL |
|---|---|---|---|---|---|---|---|
| YE | Arabic | Arab | 85.6% | Latn | 14.4% | 85.6% | — |
| SD | Arabic | Arab | 77.3% | Latn | 22.6% | 77.4% | — |
| IQ | Arabic | Arab | 75.5% | Latn | 24.4% | 75.6% | 76.2% |
| RU | Russian | Cyrl | 73.9% | Latn | 26.0% | 74.0% | 74.5% |
| KZ | Kazakh/Russian | Cyrl | 73.4% | Latn | 26.6% | 73.4% | 73.9% |
| KR | Korean | Hang | 72.2% | Latn | 27.7% | 72.3% | 50.9% |
| LY | Arabic | Arab | 66.6% | Latn | 33.2% | 66.8% | — |
| SY | Arabic | Arab | 60.9% | Latn | 39.0% | 61.0% | — |
| JO | Arabic | Arab | 51.9% | Latn | 47.8% | 52.2% | 50.3% |
| EG | Arabic | Latn | 50.4% | Arab | 49.4% | 49.6% | 48.9% |
| PS | Arabic | Latn | 53.6% | Arab | 30.8% (Hebr 13.7%) | 46.4% | 43.0% |
| IL | Hebrew/Arabic | Latn | 57.9% | Hebr | 26.3% (Arab 13.6%) | 42.1% | 38.3% |
| AF | Persian/Pashto | Latn | 58.4% | Arab | 41.4% | 41.6% | 39.1% |
| SA | Arabic | Latn | 66.8% | Arab | 32.2% | 33.2% | 32.3% |
| TM | Turkmen | Latn | 69.9% | Cyrl | 30.0% | 30.1% | 29.9% |
| BG | Bulgarian | Latn | 70.5% | Cyrl | 29.4% | 29.5% | 29.8% |
| KH | Khmer | Latn | 70.6% | Khmr | 27.3% | 29.4% | 29.9% |
| MD | Romanian | Latn | 74.4% | Cyrl | 25.5% | 25.6% | 26.1% |
| LB | Arabic | Latn | 75.7% | Arab | 23.8% | 24.3% | — |
| TW | Chinese | Latn | 76.4% | Hani | 23.4% | 23.6% | 11.0% |
| OM | Arabic | Latn | 81.0% | Arab | 16.3% | 19.0% | — |
| GR | Greek | Latn | 81.6% | Grek | 16.1% | 18.4% | 19.4% |
| DZ | Arabic | Latn | 84.5% | Arab | 15.0% | 15.5% | — |
| GE | Georgian | Latn | 85.3% | Geor | 10.0% | 14.7% | — |
| JP | Japanese | Latn | 86.8% | Hani | 10.4% (Kana 1.2%) | 13.2% | 5.6% |
| EE | Estonian | Latn | 89.0% | Cyrl | 10.9% | 11.0% | — |
| BH | Arabic | Latn | 89.1% | Arab | 8.7% | 10.9% | — |
| MO | Chinese/Portuguese | Latn | 89.2% | Hani | 7.0% (Kana/Hang 1.5%) | 10.8% | — |
| BD | Bengali | Latn | 89.9% | Beng | 9.9% | 10.1% | 9.9% |

Of the 105 country files: **96 are Latin-dominant, 6 Arabic-dominant, 2 Cyrillic-dominant,
1 Hangul-dominant**. 68 countries are >95% Latin-script occurrences; 29 are >10% non-Latin.

### 3.3 Latin countries still contain non-Latin tokens — every one of them

The non-Latin tokens with the largest counts inside Latin-dominant countries (they need
language-aware G2P *even though the country is "Latin"*):

| country | token | script | occurrences |
|---|---|---|---|
| TR | `محمد` | Arab | 83,090 |
| DZ | `محمد` | Arab | 68,771 |
| OM | `محمد` | Arab | 47,813 |
| TR | `احمد` | Arab | 51,210 |
| DZ | `ام` | Arab | 50,153 |
| TR | `ابو` | Arab | 44,050 |
| KZ | `александр` | Cyrl | 19,379 |
| IL | `محمد` | Arab | — (IL is 13.6% Arab-script) |

and in total 1,613,504 distinct keys are multi-script (0.64% of occurrences).

**What the data does not contain: language tags.** A country code is the only origin
signal. Two consequences: (a) pick/what-if G2P by *token script* first, then use a country
prior for Latin-script tokens (FR `marie` vs US `marie` vs JP `marie`); (b) exact
per-country *language* mix cannot be derived from this data without a name-origin
classifier — script mix is the strongest available proxy. This is also why KW/QA (primary
language Arabic, but 100% Latin-script transliterated tokens) must not be routed to an
Arabic G2P just because of their country code.

---

## 4. Data quality and the recommended junk filter

All shares below are occurrence-weighted over the **980,309,426 non-empty name-field
occurrences**, exactly (a token with a flag contributes all its occurrences).

| flag | occurrences | share | meaning |
|---|---|---|---|
| `is_junk` | **115,926** | **0.01183%** | **recommended drop flag** (union of below) |
| `no_letters` | 20,289 | 0.00207% | no letter at all after normalization |
| `has_digit` | 15,184 | 0.00155% | contains a digit (years, handles, `John2`) |
| `has_symbol` | 80,847 | 0.00825% | Unicode symbols: box-drawing, dingbats, emoji, arrows |
| `has_punct` | 66,262,993 | 6.759% | punctuation — **legit** (`O'Brien`, `Jean-Luc`), not junk by itself |
| `has_tatweel` | 4,333 | 0.00044% | raw had Arabic tatweel/kashida U+0640 (stripped) |
| `has_arab_diac` | 273,004 | 0.02785% | raw had Arabic tashkeel/Quranic marks (stripped) |
| `has_invisible` | 66 | 0.0000067% | zero-width/bidi/format chars (removed) |
| `handle_like` | 1,729 | 0.000176% | starts `_`/`@`/`#`, contains `@`, URL-ish |
| `is_decorated_only` | 32,374 | 0.00330% | decoration stripped leaving ≤1 letter |
| `is_single` | 87,648 | 0.00894% | single-character token (legit for CJK; audit only) |
| `is_long` | 1,349 | 0.000138% | >60 chars after normalization |
| multi-word (`n_words≥2`, not a junk flag) | 91,484,935 | 9.333% | merged/compound names; keep as one token — highest in PE 34.4%, ES 29.0%; SA 8.52%, EG 9.98% |

**Recommended filter rule** (implemented as `is_junk`):

```
junk = flags['is_junk']
# = no_letters OR has_digit OR has_symbol OR is_long OR handle_like OR is_decorated_only
keep_token  = (not junk)                   # 99.988% of non-empty tokens
keep_field  = (not empty) and keep_token   # 99.683% of all field occurrences
```

Explicitly **not junk**: single-character CJK given names, multi-word names, apostrophes,
hyphens, accented letters, legitimate Arabic names whose decoration was stripped —
stripping decoration *salvages* names, it does not damage them.

**Junk examples** (highest-frequency keys flagged `is_junk`):

| country | raw token | occurrences |
|---|---|---|
| IQ | `ۦ،` (Arabic marks + comma only) | 12,511 |
| IQ | `ٰۗٛۧۙۙﮩٰۗٛۧۙ` (diacritic-only) | 4,287 |
| EG | `ۦ، ۦ،` | 2,076 |
| EG | `ٰۗٛۧۙۙﮩٰۗٛۧۙ` | 1,018 |
| MY | `မိုး` (stale v1.0 flag — see §5.3) | 583 |
| IQ | `ﹺﹺ` | 565 |
| ZA | `M├╝Ller` (mojibake of "Müller" present in source file) | 427 |
| US | `ﹺﹺ` | 360 |
| MA | `` `_ `` | 355 |

Corrupted UTF-8: only **75 distinct keys / 84 occurrences** contain U+FFFD (the CSVs are
not perfectly UTF-8; the source corruption is negligible but nonzero).

### 4.1 Per-country junk rates and retention (exact)

Junk is not uniformly distributed. Highest junk rates (of non-empty occurrences):

| country | rows | junk occurrences | junk share | empty share | field retention |
|---|---|---|---|---|---|
| ZA | 14,323,333 | 81,124 | **0.283%** | 0.005% | 99.675% |
| KH | 2,759 | 4 | 0.074% | 1.52% | 98.405% |
| IQ | 16,820,683 | 17,844 | 0.053% | — | 99.722% |
| SG | 3,035,848 | 1,239 | 0.021% | 1.46% | 98.523% |
| MY | 11,609,132 | 2,584 | 0.011% | 0.88% | 99.110% |
| JO | 3,105,730 | 442 | 0.0071% | 0.08% | 99.917% |
| AE | 6,792,773 | 923 | 0.0068% | 0.41% | 99.595% |
| MA | 17,456,945 | 2,241 | 0.0065% | 1.34% | 98.659% |
| MO | 397,980 | 45 | 0.0062% | 9.30% | 90.695% |
| YE | 4,600,723 | 481 | 0.0052% | 0.11% | 99.895% |

**Retention is limited by empty fields, not junk**, in CJK/Korean files: KR 58.4%, TW
81.2%, MO 90.7%, CN 92.4%, HK 94.8% field retention — but their junk-only retention is
>99.99%. KR's 41.6% empty share is 98,886 empty *last* names out of 119,805 rows (Korean
single-name convention: surname is often absent from `last_name` while `first_name`
carries the full name). Treat empties as "no token" rather than as data loss; the other
field of the row is still usable.

Global max flag concentrations: `has_arab_diac` max IQ 0.40%, `has_symbol` max ZA 0.28%,
`has_tatweel` max IQ 0.004%, `handle_like` max MA 0.004%, `is_single` max KH 3.37%.
Single-character tokens are globally 0.0089% of non-empty occurrences (87,648 occurrences;
3,798 distinct keys); per country they peak at KH 3.37%, BD 0.22%, OM 0.10% — SA is
0.015% (not 1.5%; an earlier partial summary mixed up the percentage formatting).

---

## 5. Normalization specification (`profile/normalize.py`)

### 5.1 Rules

Each raw token maps to `norm` (clean surface to G2P) and `key` (canonical join key):

1. **Unicode NFKC, then NFC.** Folds full-width (`Ａ`→`A`), Arabic presentation forms
   (`ﮩ` U+FBAA → `ه`), ligatures (`ﬁ`→`fi`); NFC recomposes Hangul jamo into syllables.
   NFKC (not just NFC) is required because the dataset contains Arabic presentation-form
   junk that would otherwise stay invisible to G2P.
2. **Remove format/control/private-use characters**: ZWJ/ZWNJ (U+200C/200D), ZWSP, BOM,
   soft hyphen, bidi controls, variation selectors, Hangul fillers. They break string
   equality but are invisible to TTS.
3. **Strip Arabic decoration**: tatweel/kashida `U+0640` and all tashkeel/Quranic marks
   (`U+0610-061A, 064B-065F, 0670, 06D6-06ED, 08D3-08E1, 08E3-08FF`). In this dataset
   they are stylistic, not lexical; keeping them would explode the inventory with
   near-duplicates and confuse G2P.
4. **Keep meaningful accents** in Latin/Cyrillic/Greek (`José` ≠ `Jose` in `key`).
   `key_loose` (NFD, drop marks/punctuation/space) is provided for accent-insensitive
   rollup: `José`→`jose`, `O'Brien`→`obrien`.
5. **Case**: `norm` keeps the original casing for display; `key = casefold(norm)` plus
   ß→ss, so `NILZ`/`Nilz`/`nilz` collapse to one lexicon entry.
6. **Arabic alef unification in `key`**: `أ إ آ ٱ` → `ا` (same pronunciation, decorative
   orthographic variants); `norm` keeps original letters.
7. **Whitespace**: any run (incl. NBSP/ideographic space) → single ASCII space; trimmed.
   Multi-word names stay **one** token (`n_words` records the word count).
8. **Edge trimming**: leading/trailing punctuation, symbols, whitespace and underscores
   removed (`_Aya`→`Aya`, `╚► Rasha`→`Rasha`, `'Ali`→`Ali`); interior punctuation kept
   (`O'Brien`, `Al-Sayed`). Quote/dash variants folded: `’‘`´→`'`, `–—‑−`→`-`.
9. **Trailing combining marks** (v1.1 fix): kept for all scripts except Arabic/Hebrew,
   where they are optional vocalisation. See §5.3.
10. **Repeated punctuation/symbol runs** (3+ identical) collapse to one char
    (`***`→`*`, `═══`→`═`); runs of combining marks are never collapsed.
11. Empty / no-letter tokens return `norm=""` with `is_junk=True`.

`key` is the recommended lexicon key: case-insensitive, whitespace-normalized, Arabic
decoration-free, but orthographically faithful enough to preserve pronunciation
(accents, vowel signs, non-Latin letters). `key_loose` is only a supplementary fallback
(accent-insensitive dedup for Latin/Cyrillic); **do not use `key_loose` for Indic
scripts** — it intentionally removes vowel signs, which are letters there.

### 5.2 Normalization fold per country (raw-distinct → normalized-distinct)

Largest folds:

| country | raw distinct | normalized distinct | fold | reason |
|---|---|---|---|---|
| SD | 3,398,859 | 3,169,623 | 1.0723 | Arabic tashkeel/homoglyph decoration + case |
| YE | 1,434,283 | 1,339,451 | 1.0708 | same |
| IQ | 2,863,093 | 2,689,734 | 1.0645 | same |
| SY | 1,195,590 | 1,130,129 | 1.0579 | same |
| EG | 5,591,135 | 5,294,113 | 1.0561 | same |
| LY | 1,406,374 | 1,332,330 | 1.0556 | same |
| JO | 764,189 | 735,084 | 1.0396 | same |
| PS | 920,090 | 899,556 | 1.0228 | Arabic + Hebrew vocalisation |
| KH | 3,976 | 3,892 | 1.0216 | case/trimming (tiny file) |
| AF | 211,976 | 207,869 | 1.0198 | Arabic decoration |

Latin/Cyrillic countries: 1.000–1.005 (case folding + edge trimming only). Arabic
inventory fold is 2–7%, not 2x: decoration variants are numerous (hundreds of thousands
of distinct decorated keys) but their total occurrence weight is small relative to the
plain spellings.

### 5.3 v1.1 fix — Indic trailing vowel signs (IMPORTANT)

`normalize.py` **v1.0** trimmed trailing combining marks via a generic `\W` edge trim.
That is correct for Arabic/Hebrew vocalisation but **wrong for Indic scripts**, where a
trailing combining mark is a lexical vowel sign:

| raw | v1.0 (buggy) | v1.1 (fixed) |
|---|---|---|
| `আলী` (Bengali, ends U+09C0 Mc) | `আল` ("Ali"→"Al") | `আলী` |
| `দানু` (Bengali, ends U+09C1 Mn) | `দান` | `দানু` |
| `তৌ` (Bengali, ends U+09CC Mc) | `ত` | `তৌ` |
| `हिन्दी` (Devanagari, ends U+0940 Mc) | `হिन्द` | `हिन्दी` |

The v1.1 rule: trailing combining marks are stripped **only when the token's script is
Arabic or Hebrew** (optional vocalisation); all other scripts keep them. Internal marks
were already preserved in both versions. Arabic behaviour is unchanged
(`مُحَمَّد`→`محمد`). Self-test: `python profile/normalize.py` → 29 cases + 10 asserts,
0 failures.

**Affected volume** (measured against the v1.0-built inventory): **315,876 keys /
871,531 occurrences = 0.0886% of all name-field occurrences** have a most-frequent raw
surface ending in a combining mark (and were therefore affected). By script:
Beng 135,018 keys / 529,240 occ; Deva 56,947 / 123,135; Mymr 30,404 / 76,774;
Taml 35,955 / 61,830; Thai 22,032 / 27,842; Mlym 9,242 / 14,903; Telu 6,461 / 9,609;
Gujr 4,027 / 6,964; Sinh 5,125 / 6,830; Guru 3,177 / 4,524; Knda 3,270 / 4,486;
Khmr 2,895 / 3,864; remaining scripts <600 occurrences each. A few dozen of these
(e.g. Myanmar `မိုး` in MY/SG) were also mis-flagged `is_junk` by v1.0.

**Artifact caveat:** `unique_tokens.parquet`, `sampled_tokens.parquet` and
`country_stats.parquet` were built with `normalize.py` **v1.0**, before this fix. Their
`norm`/`key` for those 315,876 Indic keys (0.09% of occurrences) have the final vowel
sign stripped. All other scripts are unaffected. Two options: (a) rebuild artifacts by
re-running `scan_country.py` on all 105 files (~10 min with 8 workers) then
`build_artifacts.py`; (b) at consume time, for tokens whose `script` is in
{Beng, Deva, Taml, Telu, Knda, Mlym, Gujr, Guru, Orya, Sinh, Thai, Khmr, Mymr, Tibt,
Laoo}, recompute `norm`/`key` from the stored raw `token` with `normalize_token()` v1.1
(exact for the most-frequent surface; rare variants may still merge slightly
differently). Recommended for the final pipeline: (a).

### 5.4 Known limitations (v1.1)

- **Arabic homoglyph variants are not folded**: letters used decoratively such as `ہ ڪ
  ڷ ڼ ے ۅ` are still distinct letters, so `محہمد` remains a different key from `محمد`.
  Measured: **786,486 Arabic keys** contain such variant letters — 2,121,789 occurrences
  = **1.49% of Arabic-script occurrences**. A small map (ہ/ھ→ه, ڪ→ك, ے/ې→ي, ڵ→ل, ڼ→ن,
  ...) applied in `key` (v2) would fold these; recommended for a tighter Arabic lexicon
  but not applied yet (API was frozen for downstream consumers).
- **Invalid UTF-8 in source**: 75 keys / 84 occurrences contain U+FFFD; a handful of
  mojibake tokens exist (e.g. ZA `M├╝Ller`). Negligible but present.
- **No language tags** (§3.3). Country is the only origin signal; a country-conditioned
  or script-conditioned G2P + language prior is the best available strategy.
- **Gender missing for 18.86% of rows**. Gender can inform some G2P engines (e.g. Slavic
  surname endings); use `count_m/count_f`, but do not require it.
- **Merged/multi-word names** (9.33% of occurrences) are one token by design. If the
  pipeline wants per-word synthesis, split on space and normalize each part; top
  examples are Spanish double surnames and Dutch/German particles (`De La Cruz`,
  `Van Der Merwe`, `Van Den Berg`, `Maria Del Carmen`).
- Throughput of `normalize_token` on unique tokens: ~60k tokens/s for Indic/other
  non-Latin script samples, ~170k tokens/s for Latin samples (single core, CPython).
  Normalizing the full 101.6M distinct per-country keys is therefore a one-off
  10–30 min job (or minutes spread over 8 processes) — but G2P should use the
  globally-deduped **59.79M**-key list, and for a demo only the top-frequency slice.

---

## 6. Frequency structure / Zipf

### 6.1 Raw-token coverage curve (Lead's independent global scan, trim-only identity)

| top-K raw tokens | first-name coverage | last-name coverage |
|---|---|---|
| 1 | 0.82% | 0.42% |
| 10 | 4.07% | 2.82% |
| 100 | 13.34% | 8.70% |
| 1,000 | **36.70%** | **21.47%** |
| 5,000 | 57.81% | 35.67% |
| 10,000 | 65.58% | 42.58% |
| 100,000 | 82.51% | 65.47% |
| 1,000,000 | 90.64% | 83.72% |

### 6.2 Normalized keys (this inventory)

- top-1000 **normalized** keys cover **34.23%** of all first-name occurrences
  (measured exactly: 490,678,049 first-name occurrences, ~168.0M of them in top-1000 keys).
  Lower than the raw 36.70% only because normalization merges raw variants *within* the
  ranking, so top-1000 covers a slightly different (cleaner) set — the curve shape is the
  same.
- Global top tokens (sum over countries): `محمد` 6,160,965; `ahmed` 4,267,817;
  `mohamed` 4,199,349; `احمد` 3,745,281; `ali` 3,566,074; `khan` 2,154,500;
  `علي` 1,964,130; `maria` 1,811,508; `ابو` 1,721,537; `md` 1,590,730.
- Normalized global keys are **59,791,550** vs 67,736,023 raw — **11.7% fewer G2P calls**
  for full coverage if the pipeline dedupes globally.

### 6.3 Per-country uniqueness (why demo top-N works)

Uniqueness ratio varies enormously with file size: small files are near-unique samples
(KH 3,892 keys / 2,759 rows; FJ 6,879 / 5,362; SV 3,633 / 4,479), while large countries
are dense (US 4,650,616 keys / 32,308,973 rows = 144 keys per 1k rows; IT 79 per 1k;
EG 117 per 1k). The long tail is almost all singleton names: 16 countries have <100k rows
and 45 countries have <1M rows. For the demo, sort by `count` and take top-N per country
from `unique_tokens.parquet` / `sampled_tokens.parquet` (top-500 per country+field ≈
105k rows total); the top-1000 global keys already cover a third of all first names.

---

## 7. Method, exactness, and cross-checks

- **Full scans only.** Every CSV read end-to-end with duckdb
  (`read_csv(header=false, quote='"', escape='"', ignore_errors=true)`): one pass for
  row/gender/empty stats + two passes for the exact `(raw_token, field, gender)`
  frequency aggregate. No `head()`, no `limit`, no random-offset estimation in any
  reported number.
- **Exact counts.** `unique_tokens` counts are exact occurrence counts, not estimates.
  Deduplication is at (normalized key, country, field) with the most-frequent raw surface
  kept as `token` (ties broken lexicographically) and `n_variants` recording the folding.
- **Why head-of-file sampling would be wrong**: EG's first thousands of lines are
  ~100% Latin-script while the full file is **50.4% Latin / 49.4% Arabic-script tokens**
  (text-book example of length-non-homogeneous files); SA's head is 100% Latin while the
  file is 66.8%/32.2%; JO's head is ~59/41 while the file is 51.9/47.8; KW and QA are
  ~100% Latin everywhere (so "Gulf + Arabic" assumptions fail too).
- **Cross-check vs Lead's independent random-offset scan** (`work/lead_scan/`):
  - rows: our sum **491,655,925** = Lead's total exactly; **0 mismatches** across all 105
    countries; empty first/last/gender counts also match.
  - raw distinct tokens: ours (untrimmed) ≥ Lead's (trim-only) for all 105 countries,
    0 violations; sums 45,906,134/56,959,082 vs 45,905,862/56,958,836.
  - script mix: both measures agree with the independent scan within measurement-method
    differences (§3.1); no country deviates by more than the token-vs-character
    weighting effect (largest for CJK: CN 8.9% vs 4.3%, JP 13.2% vs 5.6%, KR 72.3% vs
    50.9% whole-token vs char-level).
- **Timings** (this machine, 8 parallel worker processes, 105 files): whole dataset scan
  ≈ 14 min wall (EG 287 s, IT 108 s, US 155 s, SA 227 s under contention; KH 0.1 s).
  Artifact build ≈ 4 min (`unique_tokens.parquet` 101.6M rows / 2.3 GB, 105k-row
  sampled table, 105-row country stats). Global merge over 101.6M rows ≈ 48 s.

### Reproduction

```powershell
# 1. per-country full scans (any subset of ISO codes; outputs work/profile_tmp/)
python work/profile_tmp/scan_country.py <CC1> <CC2> ...
# 2. combine into deliverables (full 105-country artifacts + globals)
python work/profile_tmp/build_artifacts.py --topn 500
python work/profile_tmp/lean_globals.py          # global unique keys + Zipf
# 3. report + exact char-weighted script shares
python work/profile_tmp/char_weighted_scripts.py
python work/profile_tmp/gen_report.py
```

All intermediate per-country parquets (`work/profile_tmp/countries/<CC>.parquet`), stats
JSONs (`work/profile_tmp/stats/<CC>.json`), script/quality evidence
(`report_extra.json`, `char_scripts.json`, `affected_marks.json`, `crosscheck.json`) and
scan logs (`work/profile_tmp/logs/scan.log`) are kept for auditability.

---

## 8. Recommendations for the pipeline

1. **Filter before synthesis**: drop `is_junk`; treat empty name fields as "no token".
   Retention 99.683% of field occurrences (99.988% of non-empty tokens).
2. **G2P `norm`, join on `key`.** Use the per-country rows for country-conditioned G2P;
   dedupe globally on `key` for the lexicon/worklist (**59,791,550 keys**).
3. **Route by script, prior by country.** Script mix per country is in
   `country_stats.parquet` (`script_row_share_json`, `top_script`, `nonlatin_row_share`);
   every Latin country still has non-Latin tokens (TR `محمد` 83k occurrences).
4. **Prioritize by frequency.** Top-1000 normalized keys = 34% of first-name
   occurrences; the demo can use `sampled_tokens.parquet` (top-500/country+field) and
   cover the majority of real traffic with ~100k G2P outputs.
5. **Arabic v2 (optional)**: fold homoglyph letter variants (1.49% of Arabic occurrences)
   into `key` if Arabic lexicon size matters; keep v1 otherwise.
6. **Rebuild artifacts after `normalize.py` v1.1** for the Indic trailing-mark fix
   (0.09% of occurrences affected), or recompute Indic `norm`/`key` from stored `token`
   at consume time.
7. **Decide multi-word policy** (9.3% of occurrences, PE 34% / ES 29%): synthesize as one
   phrase (`norm` is already whitespace-normalized) or split on space; both work with
   `normalize_token()` per word.
