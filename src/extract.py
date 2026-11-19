# -*- coding: utf-8 -*-
"""extract.py -- stage 1 of the pipeline: token inventory + real-name samples.

Streams all 105 raw CSVs once with duckdb (no full materialisation, no pandas) and
emits *per-country* parquet shards:

``work/pipeline_tmp/extract/tokens_<CC>.parquet``
    token surface (raw, before normalization), exact occurrence count, cumulative
    count (descending), and the country's total token-instance count.  Words are
    split on single spaces; tokens are lower-cased only downstream, by
    ``profile/normalize.py``, so the extractor stays dumb/fast.
    Keeping cumulative counts lets the resolver pick the smallest token set that
    covers a target share of the country's name records (coverage-driven cut).

``work/pipeline_tmp/extract/names_<CC>.parquet``
    real (first, last, gender) rows sampled two ways:
      kind='pair' -- most frequent full-name pairs (from a 5% system sample)
      kind='row'  -- pseudo-random rows (hash order) for the long tail
    These feed the demo table (recognisable names) and are never G2P'd directly --
    a full name is composed from token-level IPA at join time.

Design notes
------------
* Homegrown duckdb SQL is much faster than per-token regex cleaning: US.csv
  (32.3M rows) tokenises in ~8 s with ``string_split`` + one ``\\p{L}`` filter,
  vs ~72 s with unnest+3 regexes per token.
* Small files are read whole (no sampling) so their frequency table is exact.
* Idempotent: existing shards are skipped unless ``--force``.
"""
from __future__ import annotations

import argparse
import os
import time

if __package__:
    from .common import (DEFAULT_NAME_SAMPLE_PCT, DEFAULT_TOP_LIMIT, EXTRACT_DIR,
                         LEAD_SCAN_DIR, TMP_DUCKDB, country_codes, csv_path,
                         ensure_dirs, fmt_int, log, write_json)
else:  # `python src/extract.py` convenience run
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from common import (DEFAULT_NAME_SAMPLE_PCT, DEFAULT_TOP_LIMIT, EXTRACT_DIR,
                        LEAD_SCAN_DIR, TMP_DUCKDB, country_codes, csv_path,
                        ensure_dirs, fmt_int, log, write_json)

CSV_COLS = ("{'column0':'VARCHAR','column1':'VARCHAR',"
            "'column2':'VARCHAR','column3':'VARCHAR'}")

# token inventory: exact counts for every token, then top-limit cut.
_TOKEN_SQL = """
WITH raw AS (
  SELECT coalesce(column0, '') AS f, coalesce(column1, '') AS l
  FROM read_csv('__PATH__', header = false, quote = '"', escape = '"',
                columns = __COLS__, ignore_errors = true)
),
parts AS (
  SELECT trim(t) AS tok FROM raw, unnest(string_split(trim(f), ' ')) AS u(t)
  UNION ALL
  SELECT trim(t) AS tok FROM raw, unnest(string_split(trim(l), ' ')) AS u(t)
),
agg AS (
  SELECT tok, count(*) AS cnt
  FROM parts
  WHERE tok <> '' AND regexp_matches(tok, '\\p{L}')
  GROUP BY 1
),
ranked AS (
  SELECT tok, cnt,
         sum(cnt) OVER (ORDER BY cnt DESC, tok ROWS UNBOUNDED PRECEDING) AS cum,
         sum(cnt) OVER () AS total
  FROM agg
)
SELECT tok, cnt, cum, total FROM ranked ORDER BY cnt DESC, tok LIMIT __LIMIT__
"""

# real-name samples: top full-name pairs + pseudo-random rows from one sample scan.
_NAMES_SQL_SAMPLE = """
WITH smp AS (
  SELECT coalesce(column0, '') AS f, coalesce(column1, '') AS l,
         coalesce(column2, '') AS g
  FROM read_csv('__PATH__', header = false, quote = '"', escape = '"',
                columns = __COLS__, ignore_errors = true)
  USING SAMPLE __PCT__ PERCENT (system)
),
pairs AS (SELECT f, l, g, count(*) AS c FROM smp GROUP BY f, l, g),
pairs_top AS (
  SELECT 'pair' AS kind, f, l, g, c,
         row_number() OVER (ORDER BY c DESC, f, l) AS rk FROM pairs
),
rows_rand AS (
  SELECT 'row' AS kind, f, l, g, 0::BIGINT AS c,
         row_number() OVER (ORDER BY hash(f || chr(1) || l)) AS rk FROM smp
)
SELECT kind, f, l, g, c FROM pairs_top WHERE rk <= __N__
UNION ALL
SELECT kind, f, l, g, c FROM rows_rand WHERE rk <= __N__
"""

# whole-file variant for small countries (exact pair frequencies, no sampling)
_NAMES_SQL_FULL = """
WITH raw AS (
  SELECT coalesce(column0, '') AS f, coalesce(column1, '') AS l,
         coalesce(column2, '') AS g
  FROM read_csv('__PATH__', header = false, quote = '"', escape = '"',
                columns = __COLS__, ignore_errors = true)
),
pairs AS (SELECT f, l, g, count(*) AS c FROM raw GROUP BY f, l, g),
pairs_top AS (
  SELECT 'pair' AS kind, f, l, g, c,
         row_number() OVER (ORDER BY c DESC, f, l) AS rk FROM pairs
),
rows_rand AS (
  SELECT 'row' AS kind, f, l, g, 0::BIGINT AS c,
         row_number() OVER (ORDER BY hash(f || chr(1) || l)) AS rk FROM raw
)
SELECT kind, f, l, g, c FROM pairs_top WHERE rk <= __N__
UNION ALL
SELECT kind, f, l, g, c FROM rows_rand WHERE rk <= __N__
"""

SMALL_FILE_BYTES = 24 * 1024 * 1024   # read whole below this size
NAMES_PER_KIND = 250


def _connect(threads: int = 8, memory: str = "6GB"):
    if __package__:
        from .common import import_duckdb
    else:
        from common import import_duckdb
    duckdb = import_duckdb()
    os.makedirs(TMP_DUCKDB, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads={threads}")
    con.execute(f"SET memory_limit='{memory}'")
    con.execute(f"SET temp_directory='{os.path.join(TMP_DUCKDB, '').replace(chr(92), '/')}'")
    return con


def country_scale_tiers() -> dict[str, str]:
    """Classify countries by file size -> expected G2P share ('small'/'medium'/'large')."""
    out = {}
    for cc in country_codes():
        mb = os.path.getsize(csv_path(cc)) / 1e6
        out[cc] = "small" if mb < 20 else ("medium" if mb < 200 else "large")
    return out


def _token_query(cc: str, top_limit: int) -> str:
    return (_TOKEN_SQL.replace("__PATH__", csv_path(cc).replace("\\", "/"))
            .replace("__COLS__", CSV_COLS).replace("__LIMIT__", str(top_limit)))


def _names_query(cc: str, pct: int) -> str:
    size = os.path.getsize(csv_path(cc))
    base = _NAMES_SQL_FULL if size <= SMALL_FILE_BYTES else _NAMES_SQL_SAMPLE
    return (base.replace("__PATH__", csv_path(cc).replace("\\", "/"))
            .replace("__COLS__", CSV_COLS).replace("__PCT__", str(pct))
            .replace("__N__", str(NAMES_PER_KIND)))


def extract_countries(countries=None, top_limit: int = DEFAULT_TOP_LIMIT,
                      sample_pct: int = DEFAULT_NAME_SAMPLE_PCT,
                      force: bool = False, threads: int = 8) -> dict:
    """Run stage 1 for the given countries (default: all 105). Returns summary."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ensure_dirs()
    countries = countries or country_codes()
    con = _connect(threads=threads)
    t_all = time.time()
    summary = {"countries": {}, "top_limit": top_limit, "sample_pct": sample_pct,
               "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    for i, cc in enumerate(countries, 1):
        tok_path = os.path.join(EXTRACT_DIR, f"tokens_{cc}.parquet")
        nm_path = os.path.join(EXTRACT_DIR, f"names_{cc}.parquet")
        if not force and os.path.exists(tok_path) and os.path.exists(nm_path):
            log(f"[{i}/{len(countries)}] {cc}: cached")
            continue

        t0 = time.time()
        rows = con.execute(_token_query(cc, top_limit)).fetchall()
        n_rows = len(rows)
        total = rows[0][3] if n_rows else 0
        # coverage of the *kept* token set = last kept cum / total
        kept_cov = (rows[-1][2] / total) if n_rows and total else 0.0
        tbl = pa.table({
            "country": pa.array([cc] * n_rows),
            "token": pa.array([r[0] for r in rows]),
            "count": pa.array([r[1] for r in rows], type=pa.int64()),
            "cum_count": pa.array([r[2] for r in rows], type=pa.int64()),
            "total_tokens": pa.array([total] * n_rows, type=pa.int64()),
        })
        pq.write_table(tbl, tok_path, compression="zstd")
        t_tok = time.time() - t0

        t0 = time.time()
        nrows = con.execute(_names_query(cc, sample_pct)).fetchall()
        kind = [r[0] for r in nrows]
        ntbl = pa.table({
            "country": pa.array([cc] * len(nrows)),
            "kind": pa.array(kind),
            "first": pa.array([r[1] for r in nrows]),
            "last": pa.array([r[2] for r in nrows]),
            "gender": pa.array([r[3] for r in nrows]),
            "count": pa.array([r[4] for r in nrows], type=pa.int64()),
        })
        pq.write_table(ntbl, nm_path, compression="zstd")
        t_nm = time.time() - t0

        summary["countries"][cc] = {
            "tokens_kept": n_rows, "token_instances": total,
            "coverage_of_kept": round(kept_cov, 4),
            "names_sampled": len(nrows),
            "token_scan_s": round(t_tok, 2), "names_scan_s": round(t_nm, 2),
        }
        log(f"[{i}/{len(countries)}] {cc}: {fmt_int(n_rows)} tokens "
            f"({kept_cov*100:.1f}% coverage, {fmt_int(total)} instances) + "
            f"{len(nrows)} names in {t_tok:.1f}+{t_nm:.1f}s")

    summary["elapsed_s"] = round(time.time() - t_all, 1)
    write_json(os.path.join(EXTRACT_DIR, "extract_summary.json"), summary)
    log(f"stage-1 done: {len(countries)} countries in {summary['elapsed_s']:.0f}s")
    return summary


def load_global_freq_top(n: int = 2_000_000, field: str | None = None) -> list[tuple[str, int]]:
    """Read the Lead's global frequency table (tok, field, n) -> top-n unique tokens.

    This is the reusable 'most useful tokens in the whole dataset' artifact: the
    top-n tokens by global record count, which is exactly the set worth spending
    lexicon / manual-check effort on (see docs/METHOD.md section 1.6).
    """
    if __package__:
        from .common import import_duckdb
    else:
        from common import import_duckdb
    duckdb = import_duckdb()
    path = os.path.join(LEAD_SCAN_DIR, "global_freq.parquet").replace("\\", "/")
    if not os.path.exists(path):
        return []
    con = _connect(threads=8)
    where = f"WHERE field = '{field}'" if field else ""
    q = f"""
    SELECT tok, sum(n) AS n FROM read_parquet('{path}') {where}
    GROUP BY tok ORDER BY n DESC LIMIT {int(n)}
    """
    return con.execute(q).fetchall()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stage 1: token inventory + name samples")
    ap.add_argument("--countries", help="comma separated ISO2 list (default: all)")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_LIMIT,
                    help="max tokens kept per country (default %(default)s)")
    ap.add_argument("--sample-pct", type=int, default=DEFAULT_NAME_SAMPLE_PCT)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    cc = [c.strip().upper() for c in args.countries.split(",")] if args.countries else None
    extract_countries(cc, args.top, args.sample_pct, args.force, args.threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
