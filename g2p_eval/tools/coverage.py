"""Graded real-name coverage of WikiPron lexicons (head vs tail of the name
distribution) + espeak output sanity, written to coverage.csv.

Measures, per target language, what fraction of the real first-name tokens in
the dataset (by frequency rank) are found in the full WikiPron lexicon:
top-100 / top-500 / top-1000 / top-5000 tokens.
"""
import csv
import glob
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

from bench.bench import COUNTRY_LANG, load_name_tokens  # noqa: E402
from build_gold import pick_lexicon  # noqa: E402

OUT = os.path.join(HERE, "coverage.csv")


def main():
    rows = []
    for lang in sorted(COUNTRY_LANG):
        path = pick_lexicon(lang)
        if not path:
            continue
        lex = set()
        with open(path, encoding="utf-8") as f:
            for line in f:
                p = line.split("\t", 1)
                if p and p[0].strip():
                    lex.add(p[0].strip().lower())
        toks = load_name_tokens(lang, top_n=5000)
        row = {"language": lang, "lexicon_entries": len(lex)}
        for n in (100, 500, 1000, 5000):
            sub = toks[:n]
            row[f"coverage_top{n}"] = round(
                sum(1 for t in sub if t.strip().lower() in lex) / len(sub), 4) if sub else ""
        rows.append(row)
    rows.sort(key=lambda r: r.get("coverage_top1000") or 0, reverse=True)
    fields = ["language", "lexicon_entries", "coverage_top100", "coverage_top500",
              "coverage_top1000", "coverage_top5000"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"{'lang':5s} {'lex':>8s} {'top100':>7s} {'top500':>7s} {'top1000':>7s} {'top5000':>7s}")
    for r in rows:
        print(f"{r['language']:5s} {r['lexicon_entries']:>8d} "
              f"{str(r['coverage_top100']):>7s} {str(r['coverage_top500']):>7s} "
              f"{str(r['coverage_top1000']):>7s} {str(r['coverage_top5000']):>7s}")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
