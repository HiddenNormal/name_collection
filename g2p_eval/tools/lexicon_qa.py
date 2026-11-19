"""Cross-source QA of the WikiPron lexicon against CMUdict for English names.

The WikiPron-lexicon engine cannot be scored against WikiPron gold (by
construction it would be scoring itself). This script instead answers the
practical question: when the lexicon DOES contain a name, is its transcription
close to an independent gold standard? For English we have one: CMUdict.

Outputs lexicon_qa.csv + prints the numbers for the report.
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "bench"))
sys.path.insert(0, os.path.join(HERE, "tools"))

from normalize_ipa import summarize  # noqa: E402
from build_gold import load_tsv, pick_lexicon  # noqa: E402

OUT = os.path.join(HERE, "lexicon_qa.csv")


def main():
    wp = pick_lexicon("eng")
    print("wikipron eng:", wp)
    wp_tab = {}
    for w, p in load_tsv(wp):
        wp_tab.setdefault(w.strip().lower(), p)
    cmu = {}
    with open(os.path.join(HERE, "gold", "eng_cmu_names.tsv"), encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 2:
                cmu[p[0].strip().lower()] = p[1]
    inter = [(w, wp_tab[w], cmu[w]) for w in cmu if w in wp_tab]
    print(f"CMUdict name set={len(cmu)}  wikiPron eng={len(wp_tab)}  overlap={len(inter)}")
    m = summarize([(g, h) for _, g, h in inter])
    print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if v is not None})
    for w, g, h in inter[:10]:
        print(f"  {w:15s} wikiPron={g:22s} cmudict={h}")
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["source_a", "source_b", "n_overlap", "per_feat", "per_bare",
                     "exact_bare", "stress_emit_a", "stress_emit_b"])
        wr.writerow(["wikipron-eng", "cmudict-names", len(inter), round(m["per_feat"], 4),
                     round(m["per_bare"], 4), round(m["exact_bare"], 4),
                     round(m["stress_emit"], 4), 1.0])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
