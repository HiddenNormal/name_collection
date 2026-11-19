"""Build reusable gold evaluation sets from WikiPron broad/narrow TSVs.

For every language with a lexicon we produce:

* ``gold/<lang>.tsv``            word<TAB>ipa  (~N_GOLD held-out pronunciations)
* ``lexicons_split/<lang>_train.tsv``  the remaining lexicon entries, so that the
  WikiPron-lexicon engine can be scored *without* trivially seeing the gold
  answers (production can of course use the full lexicon; report both numbers).
* ``gold/meta.json``             counts + filtering rules.

Splitting is deterministic (seed 42).  Word filtering keeps single-token words
in the language's script that look like names/lexicon entries, and drops
WikiPron entries whose pronunciation is obviously broken (empty, contains
placeholders, parentheses, slashes).
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEX_DIR = os.path.join(HERE, "lexicons")
GOLD_DIR = os.path.join(HERE, "gold")
SPLIT_DIR = os.path.join(HERE, "lexicons_split")

N_GOLD = 1200
N_TRAIN_MAX = 250_000
SEED = 42

# language -> (regex for acceptable words, human note)
WORD_RE = {
    "eng": re.compile(r"^[a-z][a-z'\-]{1,19}$"),
    "fra": re.compile(r"^[a-zàâæçéèêëîïôœùûüÿ][a-zàâæçéèêëîïôœùûüÿ'\-]{1,19}$"),
    "spa": re.compile(r"^[a-záéíóúüñ][a-záéíóúüñ'\-]{1,19}$"),
    "deu": re.compile(r"^[a-zäöüß][a-zäöüß'\-]{1,19}$"),
    "ita": re.compile(r"^[a-zàèéìòù][a-zàèéìòù'\-]{1,19}$"),
    "por": re.compile(r"^[a-záâãàçéêíóôõúü][a-záâãàçéêíóôõúü'\-]{1,19}$"),
    "nld": re.compile(r"^[a-zàáâäèéêëìíîïòóôöùúûü][a-zàáâäèéêëìíîïòóôöùúûü'\-]{1,19}$"),
    "rus": re.compile(r"^[а-яё][а-яё'\-]{1,19}$"),
    "pol": re.compile(r"^[a-ząćęłńóśźż][a-ząćęłńóśźż'\-]{1,19}$"),
    "tur": re.compile(r"^[a-zçğıöşü][a-zçğıöşü'\-]{1,19}$"),
    "swe": re.compile(r"^[a-zåäöé][a-zåäöé'\-]{1,19}$"),
    "ara": re.compile(r"^[\u0620-\u064a][\u0620-\u064a\u0640'\-]{1,19}$"),
    "cmn": re.compile(r"^[\u4e00-\u9fff]{1,4}$"),
    "kor": re.compile(r"^[\uac00-\ud7af]{1,6}$"),
    "jpn": re.compile(r"^[\u3040-\u30ffー]{1,8}$"),
    "hin": re.compile(r"^[\u0900-\u097f]{1,12}$"),
    "ces": re.compile(r"^[a-záčďéěíňóřšťúůýž][a-záčďéěíňóřšťúůýž'\-]{1,19}$"),
    "hun": re.compile(r"^[a-záéíóöőúüű][a-záéíóöőúű'\-]{1,19}$"),
    "ukr": re.compile(r"^[а-яіїєґ][а-яіїєґ'\-]{1,19}$"),
    "vie": re.compile(r"^[a-zàáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ][a-zàáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ'\-]{1,19}$"),
    "ell": re.compile(r"^[α-ωάέήίόύώ][α-ωάέήίόύώ'\-]{1,19}$"),
    "dan": re.compile(r"^[a-zæøåé][a-zæøåé'\-]{1,19}$"),
    "fin": re.compile(r"^[a-zäöå][a-zäöå'\-]{1,19}$"),
    "ind": re.compile(r"^[a-z][a-z'\-]{1,19}$"),
    "tha": re.compile(r"^[\u0e00-\u0e7f]{1,12}$"),
    "heb": re.compile(r"^[\u05d0-\u05ea]{1,12}$"),
    "fas": re.compile(r"^[\u0600-\u06ff]{1,15}$"),
    "urd": re.compile(r"^[\u0600-\u06ff]{1,15}$"),
}

BAD_PRON = re.compile(r"[\[\]{}()/\\|=<>*#@!~_^]|\.\.\.|\?\?|http")
IPA_CHARS = set("abcdefghijklmnopqrstuvwxyzɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɞɟɡɢɣɤɥɦɨɪʝɯɰŋɲɳɴøɵɸθœɶʘɹɺɻɽɾʀʁʂʃʈʧʉʊʋʌʍχʏʐʑʒʔʕʡʢʜʢˀˈˌːˑ̥̤̰̩̯̪̺̻̼̻̃̊ʻʼʷʲˠˤθðʃʒŋɲɳɴʎʟɫɬɮɹɾɻʀʁʂʈʧʤʦʣq")


def load_tsv(path):
    entries = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            word, pron = parts[0].strip(), parts[1].strip()
            if not word or not pron:
                continue
            entries.append((word, pron))
    return entries


def good_pron(pron):
    if not pron or BAD_PRON.search(pron):
        return False
    letters = sum(1 for ch in pron if ch.isalpha())
    if letters == 0 or len(pron) > 80:
        return False
    # require at least one symbol outside plain ASCII (IPA or latin w/ diacritics)
    return any(ord(ch) > 127 or ch in IPA_CHARS for ch in pron) or len(pron) <= 12


def pick_lexicon(lang):
    """Pick the best available tsv for a language (broad > narrow)."""
    if not os.path.isdir(LEX_DIR):
        return None
    files = [f for f in os.listdir(LEX_DIR) if f.endswith(".tsv")]
    exact = {
        "eng": ["eng_latn_us_broad_filtered.tsv", "eng_latn_us_broad.tsv"],
        "spa": ["spa_latn_la_broad_filtered.tsv", "spa_latn_la_broad.tsv", "spa_latn_ca_broad_filtered.tsv"],
        "fra": ["fra_latn_broad_filtered.tsv", "fra_latn_broad.tsv"],
        "deu": ["deu_latn_broad_filtered.tsv", "deu_latn_broad.tsv"],
        "por": ["por_latn_po_broad_filtered.tsv", "por_latn_po_broad.tsv"],
        "ita": ["ita_latn_broad.tsv"],
        "nld": ["nld_latn_broad.tsv"],
        "rus": ["rus_cyrl_narrow.tsv"],
        "pol": ["pol_latn_broad.tsv"],
        "tur": ["tur_latn_broad.tsv"],
        "swe": ["swe_latn_broad.tsv"],
        "ara": ["ara_arab_broad.tsv"],
        "cmn": ["cmn_hani_standard_broad.tsv", "cmn_latn_standard_broad.tsv"],
        "kor": ["kor_hang_narrow_filtered.tsv", "kor_hang_narrow.tsv"],
        "jpn": ["jpn_hira_narrow_filtered.tsv", "jpn_hira_narrow.tsv", "jpn_kana_narrow_filtered.tsv"],
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
    }
    for name in exact.get(lang, []):
        if name in files:
            return os.path.join(LEX_DIR, name)
    return None


def main():
    os.makedirs(GOLD_DIR, exist_ok=True)
    os.makedirs(SPLIT_DIR, exist_ok=True)
    rng = random.Random(SEED)
    meta = {"seed": SEED, "n_gold": N_GOLD, "languages": {}}
    langs = sys.argv[1:] or sorted(set(
        f.split("_")[0] for f in os.listdir(LEX_DIR) if f.endswith(".tsv")))
    for lang in langs:
        path = pick_lexicon(lang)
        if not path:
            print(f"{lang}: no lexicon, skipped")
            continue
        wr = WORD_RE.get(lang, re.compile(r"^\S{2,20}$"))
        entries = load_tsv(path)
        by_word = {}
        for w, p in entries:
            w2 = unicodedata.normalize("NFC", w).strip()
            if not wr.match(w2):
                continue
            if not good_pron(p):
                continue
            if w2 not in by_word:
                by_word[w2] = p
        words = sorted(by_word)
        rng.shuffle(words)
        gold_words = words[:N_GOLD]
        train_words = words[N_GOLD:N_GOLD + N_TRAIN_MAX]
        with open(os.path.join(GOLD_DIR, f"{lang}.tsv"), "w", encoding="utf-8") as f:
            for w in gold_words:
                f.write(f"{w}\t{by_word[w]}\n")
        with open(os.path.join(SPLIT_DIR, f"{lang}_train.tsv"), "w", encoding="utf-8") as f:
            for w in train_words:
                f.write(f"{w}\t{by_word[w]}\n")
        meta["languages"][lang] = dict(
            source=os.path.basename(path), n_raw=len(entries),
            n_kept=len(by_word), n_gold=len(gold_words), n_train=len(train_words))
        print(f"{lang}: raw={len(entries)} kept={len(by_word)} gold={len(gold_words)} train={len(train_words)} ({os.path.basename(path)})")
    json.dump(meta, open(os.path.join(GOLD_DIR, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("wrote", os.path.join(GOLD_DIR, "meta.json"))


if __name__ == "__main__":
    main()
