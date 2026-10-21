#!/usr/bin/env python3
"""Standalone v2 of the sensor-240 analysis script.

Origin: data/analyze_sensor_240.py (the draft whose run crashed). Its printed
execution failed at the B-outlier table with
    ValueError: Sign not allowed in string format specifier
(saved verbatim in data/previous-execution-failure.txt lines 38-45, pointing at
original line 181 "{:>+9}" applied to a string). This v2 fixes that and
awkward defaults; it does NOT add new analysis claims.

What is different from data/analyze_sensor_240.py (line numbers are in that file)
  F1  line 181  "{:>+9}" (a sign-aware spec) is applied to fmt(...) output, which
               is a str. In v2 the numeric column is formatted with a NUMERIC
               sign-aware spec "{:>+9.6f}" applied to the float, and every
               string column keeps a plain width spec ("{:>6}", ...).
  F2  lines 45-48, 60-63  the default data path was derived from the *script's
               own* directory plus a hard-coded run id (run-20261021-074455), so a
               copy placed anywhere else pointed at nothing. v2 defaults to the
               data/ directory sitting next to this script (its parent directory),
               i.e. <parent-of-script>/data/repeated-measurements.md, and accepts
               --data-file (and an optional positional path, kept for
               compatibility) to override it.
  F3  line 244  dead variable `expected_first = ids[0] + (b_out[0])` removed; it
               was computed and never used and mixed an ID with a row index.
  F4  line 257  a dangling comment ("expected clock value") that described an
               expectation the code never computes was removed; no per-row
               expectation column is produced, so nothing implies it is checked.
  F5  argparse --help / --version and an explicit "not a verdict" banner so the
               output cannot be mistaken for a verified result on its own.

Stdlib only, Python 3.8+ (uses f-strings nowhere; .format() throughout so it
also runs on 3.6+).

Usage
  python analyze_sensor_240_v2.py
  python analyze_sensor_240_v2.py --data-file <path/to/repeated-measurements.md>
  python analyze_sensor_240_v2.py <path/to/repeated-measurements.md>   # positional

Exit codes: 0 = report printed, 1 = file present but unparsable, 2 = file missing
or argument error. A successful exit only means the script ran; it is not a
verification of the numbers.
"""

import argparse
import math
import os
import re
import statistics
import sys

VERSION = "2.0"

REFERENCE_MV = 4.00
TOLERANCE_MV = 0.15
DEFAULT_DATA_NAME = "repeated-measurements.md"
DEFAULT_DATA_DIR = "data"          # sibling of the directory holding this file

# A data row is three pipe-separated cells whose first cell is a bare integer
# and whose other two cells are decimal numbers, so "1 | 3.99 | 7.96" matches
# while the header and the "--- | --- | ---" separator do not.
ROW_RE = re.compile(r"^\s*(\d+)\s*\|\s*(-?\d+(?:\.\d+)?)\s*\|\s*(-?\d+(?:\.\d+)?)\s*$")

# Decimals for printed derived numbers. Computations use full float precision;
# columns are sized so the printed width is constant.
DP = 6
COL_SIGNED = "{:>+9.6f}"             # NUMERIC, always signed  (F1)
COL_PLAIN = "{:>9.6f}"               # NUMERIC, unsigned
COL_TEXT = "{:>9}"                   # STRING columns never get a sign spec


def default_data_path():
    """<dir-of-this-script>/../data/repeated-measurements.md (F2)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, DEFAULT_DATA_DIR,
                                         DEFAULT_DATA_NAME))


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="Recompute the 240-sample sensor descriptives and outlier "
                    "table from repeated-measurements.md.",
        epilog="Default data file: " + default_data_path(),
    )
    p.add_argument("path", nargs="?", default=None,
                   help="optional positional path to the data file "
                        "(overrides the default; same as --data-file)")
    p.add_argument("--data-file", default=None,
                   help="path to repeated-measurements.md")
    p.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    args = p.parse_args(argv)
    if args.data_file and args.path:
        p.error("give the data file either positionally or with --data-file, not both")
    chosen = args.data_file or args.path or default_data_path()
    return os.path.abspath(chosen)


def parse(path):
    rows = []
    skipped = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            m = ROW_RE.match(line)
            if not m:
                if line.strip():
                    skipped += 1
                continue
            rows.append({
                "id": int(m.group(1)),
                "a": float(m.group(2)),
                "b": float(m.group(3)),
                "line": line_no,
            })
    return rows, skipped


def stats(values):
    n = len(values)
    mean = statistics.fmean(values)
    ss = sum((x - mean) ** 2 for x in values)
    return {
        "n": n,
        "mean": mean,
        "pstd": math.sqrt(ss / n),                       # population, /n
        "sstd": math.sqrt(ss / (n - 1)) if n > 1 else float("nan"),  # sample, /(n-1)
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def f(x):
    return "{:.{}f}".format(x, DP)


def report_stats(label, values):
    s = stats(values)
    print("  " + label)
    print("    n                = {}".format(s["n"]))
    print("    mean             = {}".format(f(s["mean"])))
    print("    std (population) = {}".format(f(s["pstd"])))
    print("    std (sample, n-1)= {}".format(f(s["sstd"])))
    print("    min              = {}".format(f(s["min"])))
    print("    median           = {}".format(f(s["median"])))
    print("    max              = {}".format(f(s["max"])))


def main(argv):
    path = parse_args(argv)
    if not os.path.exists(path):
        print("ERROR: data file not found: {}".format(path), file=sys.stderr)
        print("  Pass --data-file <path> to override.", file=sys.stderr)
        return 2

    rows, skipped = parse(path)
    if not rows:
        print("ERROR: no 'ID | A | B' rows parsed from {}".format(path),
              file=sys.stderr)
        return 1

    ids = [r["id"] for r in rows]
    a_vals = [r["a"] for r in rows]
    b2_vals = [r["b"] / 2.0 for r in rows]   # calibration.md: B is on a x2 scale
    n = len(rows)

    print("=" * 78)
    print("Sensor 240-sample analysis  (script v{})".format(VERSION))
    print("NOTE: this is a recomputation of the recorded table only. Printing "
          "it does not")
    print("      verify the numbers; check them independently.")
    print("=" * 78)
    print("File: {}".format(path))
    print("Parsed {} 'ID | A | B' rows ({} non-data non-blank lines skipped)"
          .format(n, skipped))
    print("B/2 = B / 2 for every row (calibration.md line 1: B uses a x2 scale)")
    print("Reference {} mV, per-reading tolerance +/- {} mV "
          "(repeated-measurements.md line 2)"
          .format(f(REFERENCE_MV), f(TOLERANCE_MV)))
    print("=" * 78)

    # [1] descriptives -------------------------------------------------------
    print()
    print("[1] Descriptives (mV)")
    report_stats("A (as recorded)", a_vals)
    print()
    report_stats("B/2 (B divided by 2)", b2_vals)

    lo = REFERENCE_MV - TOLERANCE_MV
    hi = REFERENCE_MV + TOLERANCE_MV

    # [2] outliers -----------------------------------------------------------
    # outside means strictly beyond the band; the 1e-12 guard only absorbs
    # binary representation noise (e.g. 4.15 arriving as 4.1499999...).
    a_out = [i for i in range(n)
             if abs(a_vals[i] - REFERENCE_MV) > TOLERANCE_MV + 1e-12]
    b_out = [i for i in range(n)
             if abs(b2_vals[i] - REFERENCE_MV) > TOLERANCE_MV + 1e-12]

    print()
    print("[2] Readings outside {} +/- {} mV  (x < {} or x > {}):"
          .format(f(REFERENCE_MV), f(TOLERANCE_MV), f(lo), f(hi)))
    print()
    print("  A outliers: {}".format(len(a_out)))
    for i in a_out:
        excess = a_vals[i] - REFERENCE_MV
        # F1-class fix: the sign-aware spec takes the float, never a str.
        print("    A ID {:>3} line {:>3}: A = {}  excess {:+.6f} mV".format(
            rows[i]["id"], rows[i]["line"], f(a_vals[i]), excess))
    if not a_out:
        print("    (none)")

    print()
    print("  B outliers (B/2): {}".format(len(b_out)))
    # header: STRING columns -> plain width only (F1)
    print("    {:>4} {:>5} {:>9} {:>9} {:>9} {:>9}".format(
        "ID", "line", "B raw", "B/2", "A", "excess"))
    for i in b_out:
        excess = b2_vals[i] - REFERENCE_MV
        # F1: ID/line/derived-string columns use plain widths; only the numeric
        # excess gets the signed spec, applied to the float.
        print("    {:>4} {:>5} {} {} {} {}".format(
            rows[i]["id"],                 # int  -> plain width
            rows[i]["line"],               # int  -> plain width
            COL_PLAIN.format(rows[i]["b"]),
            COL_PLAIN.format(b2_vals[i]),
            COL_PLAIN.format(a_vals[i]),
            COL_SIGNED.format(excess),     # float -> "{:>+9.6f}"
        ))

    # [3] ID deltas between consecutive B outliers ---------------------------
    print()
    print("[3] ID deltas between consecutive B outliers")
    deltas = []
    if len(b_out) < 2:
        print("    need at least two B outliers; none printed")
    else:
        for k in range(1, len(b_out)):
            prev_id = rows[b_out[k - 1]]["id"]
            cur_id = rows[b_out[k]]["id"]
            d = cur_id - prev_id
            deltas.append(d)
            print("    ID {:>3} -> ID {:>3}: delta {:>3}".format(prev_id, cur_id, d))
        print("    deltas: {}".format(", ".join(str(d) for d in deltas)))
        print("    distinct deltas: {}".format(sorted(set(deltas))))
        print("    mean delta: {}".format(f(statistics.fmean(deltas))))

    # [4] residuals for each B outlier ---------------------------------------
    print()
    print("[4] Per B-outlier residuals to neighbours (B/2, mV)")
    print("    prev  = B/2 of the row immediately before the outlier")
    print("    next  = B/2 of the row immediately after the outlier")
    print("    nb6   = mean of the six nearest NON-outlier rows (k-3..k-1, k+1..k+3)")
    print("    dev to X = outlier value minus X")
    outlier_set = set(b_out)
    print()
    print("    {:>4} {:>9} {:>9} {:>9} {:>9} {:>10} {:>10} {:>10}".format(
        "ID", "B/2", "prev", "next", "nb6 mean",
        "dev to nb6", "dev to prev", "dev to next"))
    nb_counts = set()
    for i in b_out:
        prev_val = b2_vals[i - 1] if i - 1 >= 0 else None
        next_val = b2_vals[i + 1] if i + 1 < n else None
        nb_vals = [b2_vals[k] for k in (i - 3, i - 2, i - 1, i + 1, i + 2, i + 3)
                   if 0 <= k < n and k not in outlier_set]
        nb_counts.add(len(nb_vals))
        nb_mean = sum(nb_vals) / len(nb_vals) if nb_vals else float("nan")
        obs = b2_vals[i]
        # every column here is numeric -> sign-aware spec is legal for each
        print("    {:>4} {:>9.6f} {:>9.6f} {:>9.6f} {:>9.6f} {:>+10.6f} {:>+10.6f} {:>+10.6f}"
              .format(
                  rows[i]["id"], obs,
                  prev_val if prev_val is not None else float("nan"),
                  next_val if next_val is not None else float("nan"),
                  nb_mean,
                  obs - nb_mean if nb_vals else float("nan"),
                  obs - prev_val if prev_val is not None else float("nan"),
                  obs - next_val if next_val is not None else float("nan"),
              ))
    if b_out:
        print()
        print("    neighbour counts used per outlier: {} of 6"
              .format(sorted(nb_counts)))

    # [5] lattice check (descriptive only) -----------------------------------
    print()
    print("[5] Lattice check (descriptive; no cause is claimed)")
    if len(b_out) >= 2:
        first_delta = rows[b_out[0]]["id"] - ids[0]
        step = deltas[0] if len(set(deltas)) == 1 else None
        print("    first B-outlier ID = {}, ID of first parsed row = {}, offset = {}"
              .format(rows[b_out[0]]["id"], ids[0], first_delta))
        if step:
            predicted = [rows[b_out[0]]["id"] + k * step for k in range(len(b_out))]
            actual = [rows[i]["id"] for i in b_out]
            print("    strict {}-step lattice from the first outlier matches every "
                  "observed ID: {}".format(step, predicted == actual))
        else:
            print("    deltas are not constant; no single-step lattice fits")
        print("    min/mean/max excess over {} across B outliers: {} / {} / {} mV"
              .format(f(REFERENCE_MV),
                      f(min(b2_vals[i] - REFERENCE_MV for i in b_out)),
                      f(statistics.fmean(b2_vals[i] - REFERENCE_MV for i in b_out)),
                      f(max(b2_vals[i] - REFERENCE_MV for i in b_out))))
        print("    all A values at B-outlier rows within {} +/- {}: {}"
              .format(f(REFERENCE_MV), f(TOLERANCE_MV),
                      all(i not in a_out for i in b_out)))
        print("    share of rows that are B outliers: {}/{} = {:.2f} %"
              .format(len(b_out), n, 100.0 * len(b_out) / n))

    # [6] trend check over rows (descriptive) --------------------------------
    print()
    print("[6] Trend / bias check (endpoints, descriptive)")
    for label, vals in (("A", a_vals), ("B/2", b2_vals)):
        half = n // 2
        first = vals[:half]
        second = vals[half:]
        print("    {} first {} rows mean = {}, last {} rows mean = {}, "
              "difference = {} mV".format(
                  label, half, f(statistics.fmean(first)),
                  len(second), f(statistics.fmean(second)),
                  f(statistics.fmean(second) - statistics.fmean(first))))
    print("    ID monotonically non-decreasing {0}..{0}+n-1: {1}".format(
        ids[0], ids == list(range(ids[0], ids[0] + n))))

    print()
    print("Done. (Recomputation only; no verification implied by this output.)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
