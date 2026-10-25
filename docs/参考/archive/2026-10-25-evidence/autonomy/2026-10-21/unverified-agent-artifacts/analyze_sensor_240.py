#!/usr/bin/env python3
"""Analyse the 240 repeated sensor measurements in data/repeated-measurements.md.

Stdlib only (Python 3). Nothing is executed by the agent that wrote this file;
run it yourself and every figure in sensor-240-findings.md can be recomputed.

What it does
  1. Reads the markdown table, keeps only rows of the form  ID | A | B
     (header, separator and prose lines are skipped).
  2. Builds B/2 (calibration.md: "B is recorded on a x2 scale") and A in mV.
  3. Prints n, mean, population std, sample std, min, median, max for A and B/2.
  4. Lists every reading outside 4.00 +/- 0.15 mV with its signed excess over
     4.00.
  5. Lists the ID deltas between consecutive B outliers.
  6. For each B outlier, prints residuals to the immediately preceding row,
     the following row, and the mean of its six nearest non-outlier
     neighbours (all six are non-outliers because neighbouring outliers are
     17 IDs apart).

Assumptions (documented, not hidden)
  - Units: the table header says both A and B are in mV and that B uses a x2
    scale; the reference is 4.00 mV and the per-reading tolerance is
    +/-0.15 mV. Those three facts come from the repeated-measurements.md
    header (and observations.md), NOT from calibration.md, which gives only
    the x2 scale and says the error range is unknown.
  - "Outside 4.00 +/- 0.15 mV" means |x - 4.00| > 0.15, i.e. x < 3.85 or
    x > 4.15. The boundary itself is inside the band.
  - "Nearest neighbours" are taken by row order (the table is the only
    ordering available); 3 rows before and 3 rows after each outlier.

Usage
  python analyze_sensor_240.py [path/to/repeated-measurements.md]
  The default path is <repo>/.artifacts/autonomy-real/run-20261021-074455/data/
  repeated-measurements.md; pass a path if the script is moved.
"""

import math
import os
import re
import statistics
import sys

REFERENCE_MV = 4.00
TOLERANCE_MV = 0.15
DATA_REL = os.path.join(
    ".artifacts", "autonomy-real", "run-20261021-074455", "data",
    "repeated-measurements.md",
)

# A data row is three pipe-separated cells whose first cell is a bare integer
# and whose other two cells are numbers (so "1 | 3.99 | 7.96" matches but the
# header "编号 | A 原始值 | B 原始值" and the separator "--- | --- | ---" do not).
ROW_RE = re.compile(r"^\s*(\d+)\s*\|\s*(-?\d+(?:\.\d+)?)\s*\|\s*(-?\d+(?:\.\d+)?)\s*$")

# decimals fixed by the inputs (A has 2, B/2 has 3); printed with fixed width
# so residuals are clean, but computed from full float precision.
DP = 6


def default_path():
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, DATA_REL)
    return candidate


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
            ident = int(m.group(1))
            a = float(m.group(2))
            b = float(m.group(3))
            rows.append({"id": ident, "a": a, "b": b, "line": line_no})
    return rows, skipped


def stats(values):
    n = len(values)
    mean = statistics.fmean(values)
    pvar = sum((x - mean) ** 2 for x in values) / n
    svar = sum((x - mean) ** 2 for x in values) / (n - 1) if n > 1 else float("nan")
    return {
        "n": n,
        "mean": mean,
        "pstd": math.sqrt(pvar),
        "sstd": math.sqrt(svar),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def fmt(x):
    return "{:.{}f}".format(x, DP)


def report_stats(label, values):
    s = stats(values)
    print("  " + label)
    print("    n                = {}".format(s["n"]))
    print("    mean             = {}".format(fmt(s["mean"])))
    print("    std (population) = {}".format(fmt(s["pstd"])))
    print("    std (sample, n-1)= {}".format(fmt(s["sstd"])))
    print("    min              = {}".format(fmt(s["min"])))
    print("    median           = {}".format(fmt(s["median"])))
    print("    max              = {}".format(fmt(s["max"])))


def residuals(values, idx, lo, hi):
    """Mean of the non-outlier values in values[lo:hi] (hi exclusive)."""
    vals = [values[k] for k in range(lo, hi) if values[k] is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def main(argv):
    path = argv[1] if len(argv) > 1 else default_path()
    if not os.path.exists(path):
        print("ERROR: data file not found: {}".format(path), file=sys.stderr)
        print("  Pass the path explicitly: analyze_sensor_240.py <path>", file=sys.stderr)
        return 2

    rows, skipped = parse(path)
    if not rows:
        print("ERROR: no 'ID | A | B' rows parsed from {}".format(path), file=sys.stderr)
        return 1

    ids = [r["id"] for r in rows]
    a_vals = [r["a"] for r in rows]
    b2_vals = [r["b"] / 2.0 for r in rows]
    n = len(rows)

    print("=" * 78)
    print("Sensor 240-sample analysis")
    print("File: {}".format(path))
    print("Parsed {} 'ID | A | B' rows ({} non-data lines skipped)".format(n, skipped))
    print("B/2 = B / 2 applied to every row (calibration.md: B is on a x2 scale)")
    print("Reference {} mV, per-reading tolerance +/- {} mV (table header)"
          .format(REFERENCE_MV, TOLERANCE_MV))
    print("=" * 78)

    # ----- 1. descriptives -------------------------------------------------
    print()
    print("[1] Descriptives (mV)")
    report_stats("A (as recorded)", a_vals)
    print()
    report_stats("B/2 (B divided by 2)", b2_vals)

    lo = REFERENCE_MV - TOLERANCE_MV
    hi = REFERENCE_MV + TOLERANCE_MV

    # ----- 2. outliers -----------------------------------------------------
    a_out = [i for i in range(n) if abs(a_vals[i] - REFERENCE_MV) > TOLERANCE_MV + 1e-12]
    b_out = [i for i in range(n) if abs(b2_vals[i] - REFERENCE_MV) > TOLERANCE_MV + 1e-12]

    print()
    print("[2] Readings outside {} +/- {} mV  (i.e. x < {} or x > {}):"
          .format(REFERENCE_MV, TOLERANCE_MV, lo, hi))
    print()
    print("  A outliers: {}".format(len(a_out)))
    for i in a_out:
        excess = a_vals[i] - REFERENCE_MV
        print("    A ID {:>3} line {:>3}: A = {}  excess {:+,} mV".format(
            rows[i]["id"], rows[i]["line"], fmt(a_vals[i]), fmt(excess)))
    if not a_out:
        print("    (none)")

    print()
    print("  B outliers (B/2): {}".format(len(b_out)))
    print("    {:>4} {:>5} {:>6} {:>6} {:>6} {:>9}".format(
        "ID", "line", "B raw", "B/2", "A", "excess"))
    for i in b_out:
        excess = b2_vals[i] - REFERENCE_MV
        print("    {:>4} {:>5} {:>6} {:>6} {:>6} {:>+9}".format(
            rows[i]["id"], rows[i]["line"], fmt(rows[i]["b"]),
            fmt(b2_vals[i]), fmt(a_vals[i]), fmt(excess)))

    # ----- 3. ID deltas between consecutive B outliers ---------------------
    print()
    print("[3] ID deltas between consecutive B outliers")
    if len(b_out) < 2:
        print("    need at least two B outliers; none printed")
    else:
        deltas = []
        for k in range(1, len(b_out)):
            prev_id = rows[b_out[k - 1]]["id"]
            cur_id = rows[b_out[k]]["id"]
            d = cur_id - prev_id
            deltas.append(d)
            print("    ID {:>3} -> ID {:>3}: delta {:>3}".format(prev_id, cur_id, d))
        print("    deltas: {}".format(", ".join(str(d) for d in deltas)))
        print("    distinct deltas: {}".format(sorted(set(deltas))))
        if deltas:
            print("    mean delta: {:.6f}".format(statistics.fmean(deltas)))

    # ----- 4. residuals for each B outlier ---------------------------------
    print()
    print("[4] Per B-outlier residuals to neighbours (B/2, mV)")
    print("    prev = same measurement as the row before the outlier (r_{k-1})")
    print("    next = same measurement as the row after the outlier (r_{k+1})")
    print("    nb6  = mean of six nearest non-outlier rows (k-3..k-1, k+1..k+3)")
    print("    deviation-to-nb6 = outlier minus that mean")
    outlier_set = set(b_out)
    print()
    print("    {:>4} {:>7} {:>10} {:>10} {:>10} {:>12} {:>10} {:>11}".format(
        "ID", "B/2", "prev", "next", "nb6 mean", "dev to nb6", "dev to prev", "dev to next"))
    nb_counts = set()
    for i in b_out:
        prev_val = b2_vals[i - 1] if i - 1 >= 0 else None
        next_val = b2_vals[i + 1] if i + 1 < n else None
        # three before and three after, skipping outliers if any lie that close
        nb_vals = []
        for k in (i - 3, i - 2, i - 1, i + 1, i + 2, i + 3):
            if 0 <= k < n and k not in outlier_set:
                nb_vals.append(b2_vals[k])
        nb_counts.add(len(nb_vals))
        nb_mean = sum(nb_vals) / len(nb_vals) if nb_vals else float("nan")
        obs = b2_vals[i]
        print("    {:>4} {:>7} {:>10} {:>10} {:>10} {:>+12} {:>+10} {:>+11}".format(
            rows[i]["id"], fmt(obs),
            fmt(prev_val) if prev_val is not None else "-",
            fmt(next_val) if next_val is not None else "-",
            fmt(nb_mean),
            fmt(obs - nb_mean) if nb_vals else "-",
            fmt(obs - prev_val) if prev_val is not None else "-",
            fmt(obs - next_val) if next_val is not None else "-",
        ))
    if b_out:
        print()
        print("    neighbour counts used per outlier: {} of 6"
              .format(sorted(nb_counts)))

    # ----- 5. lattice check (descriptive only, no cause claimed) -----------
    print()
    print("[5] Lattice check (descriptive; no cause is claimed)")
    if len(b_out) >= 2:
        expected_first = ids[0] + (b_out[0])
        first_delta = rows[b_out[0]]["id"] - ids[0]
        step = deltas[0] if len(set(deltas)) == 1 else None
        print("    first B-outlier ID = {}, ID of first parsed row = {}, offset = {}"
              .format(rows[b_out[0]]["id"], ids[0], first_delta))
        if step:
            predicted = [rows[b_out[0]]["id"] + k * step for k in range(len(b_out))]
            actual = [rows[i]["id"] for i in b_out]
            print("    strict {}-step lattice ID0 + k*{} == observed IDs: {}"
                  .format(step, step, predicted == actual))
        else:
            print("    deltas are not constant; no single-step lattice fits")
        # how much the outlier exceeds the same-lattice expected clock value?
        # "expected clock value" = the B/2 pattern continues (here ID mod 4 pattern).
        # We only report the excess over 4.00 plus the band and the nb6 mean.
        print("    min/mean/max excess over 4.00 across B outliers: {} / {} / {} mV"
              .format(fmt(min(b2_vals[i] - REFERENCE_MV for i in b_out)),
                      fmt(statistics.fmean(b2_vals[i] - REFERENCE_MV for i in b_out)),
                      fmt(max(b2_vals[i] - REFERENCE_MV for i in b_out))))
        print("    all A values at B-outlier rows within {} +/- {}: {}"
              .format(REFERENCE_MV, TOLERANCE_MV,
                      all(i not in a_out for i in b_out)))

    # ----- 6. trend check over rows (descriptive) --------------------------
    print()
    print("[6] Trend / bias check (endpoints, descriptive)")
    for label, vals in (("A", a_vals), ("B/2", b2_vals)):
        half = n // 2
        first = vals[:half]
        second = vals[half:]
        print("    {} first {} rows mean = {}, last {} rows mean = {}, difference = {} mV"
              .format(label, half, fmt(statistics.fmean(first)),
                      len(second), fmt(statistics.fmean(second)),
                      fmt(statistics.fmean(second) - statistics.fmean(first))))
    print("    ID monotonic non-decreasing 1..{}: {}".format(
        n, ids == list(range(ids[0], ids[0] + n))))

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
