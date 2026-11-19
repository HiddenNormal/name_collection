"""Build an English gold set with *real stress marks* from CMUdict.

WikiPron broad/narrow data has no stress marks at all, so English is the only
language where we can quantitatively score stress placement against an
independent gold standard: CMUdict encodes stress as ARPAbet digits (1=primary,
2=secondary, 0=none), which we convert to IPA with ˈ / ˌ.

Outputs:
  gold/eng_cmu.tsv        all well-formed CMUdict entries  word<TAB>ipa
  gold/eng_cmu_names.tsv  the subset whose word occurs in the real English
                          first-name token lists (the TTS-relevant set)
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CMU = os.path.join(HERE, "lexicons", "cmudict.dict")
GOLD = os.path.join(HERE, "gold")
TOKENS = os.path.join(HERE, "name_tokens")

# ARPAbet -> IPA (stress handled separately)
ARP = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ",
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "EH": "ɛ", "ER": "ɝ",
    "EY": "eɪ", "F": "f", "G": "g", "HH": "h", "IH": "ɪ", "IY": "i",
    "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ",
    "OW": "oʊ", "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ",
    "T": "t", "TH": "θ", "UH": "ʊ", "UW": "u", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "ʒ",
}
VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY",
          "OW", "OY", "UH", "UW"}
NAME_RE = re.compile(r"^[a-z][a-z'\-]{1,18}$")


def arpa_to_ipa(phones):
    out = []
    for ph in phones:
        stress = ""
        base = ph
        if ph and ph[-1].isdigit():
            d = ph[-1]
            base = ph[:-1]
            if d == "1":
                stress = "ˈ"
            elif d == "2":
                stress = "ˌ"
        ipa = ARP.get(base)
        if ipa is None:
            return None
        if base in VOWELS:
            if base == "AH" and stress == "":
                ipa = "ə"
            elif base == "ER" and stress == "":
                ipa = "ɚ"
        out.append(stress + ipa)
    return " ".join(out)


def load_english_name_tokens():
    names = set()
    for cc in ["GB", "US", "CA", "AU", "NZ", "IE", "ZA", "NG", "GH", "KE",
               "IN", "PH", "SG", "MY", "JM", "TT", "FJ", "MT"]:
        p = os.path.join(TOKENS, f"{cc}.tsv")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 20000:
                    break
                tok = line.split("\t")[0].strip()
                if tok:
                    names.add(tok.lower())
    return names


def main():
    os.makedirs(GOLD, exist_ok=True)
    entries = {}
    with open(CMU, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith(";;;"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            word = parts[0]
            word = re.sub(r"\(\d+\)$", "", word)  # alternate pronunciation
            if not NAME_RE.match(word):
                continue
            ipa = arpa_to_ipa(parts[1:])
            if not ipa:
                continue
            if word not in entries:  # keep first (canonical) pronunciation
                entries[word] = ipa
    names = load_english_name_tokens()
    name_subset = {w: p for w, p in entries.items() if w in names}
    with open(os.path.join(GOLD, "eng_cmu.tsv"), "w", encoding="utf-8") as f:
        for w in sorted(entries):
            f.write(f"{w}\t{entries[w]}\n")
    with open(os.path.join(GOLD, "eng_cmu_names.tsv"), "w", encoding="utf-8") as f:
        for w in sorted(name_subset):
            f.write(f"{w}\t{name_subset[w]}\n")
    print(f"eng_cmu: {len(entries)} entries; eng_cmu_names: {len(name_subset)} "
          f"(from {len(names)} real name tokens)")
    for w in list(sorted(name_subset))[:10]:
        print("  ", w, "->", name_subset[w])


if __name__ == "__main__":
    main()
