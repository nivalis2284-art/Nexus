# Sensor 240-sample findings

Source data: `data/repeated-measurements.md` (240 rows, IDs 1–240, file lines 5–244),
`data/calibration.md` (2 lines), `data/observations.md` (6 lines).
Companion script: `output/analyze_sensor_240.py` (stdlib only).

**Method statement.** No interpreter was run. Every figure below is
hand-derived from the file rows, with the arithmetic shown so it can be
recomputed row by row. Any figure that only the script can produce — standard
deviations (population and sample), medians, full-precision means — is marked
**"to be produced by running the script"** and is *not* guessed here.

---

## 1. Where the units, model and tolerance come from

- `repeated-measurements.md` line 2 (table header) states: A and B raw values
  are both in mV; B is recorded on a **two-times scale** and must be divided by
  2 first; the two units are the **same model** at **adjacent positions**; the
  independent reference channel nominal value is **4.00 mV**; the allowed error
  of a single measurement is **+/-0.15 mV**.
- `calibration.md` line 1 gives only the **x2 scale** ("B's records use a
  two-times scale; 9 corresponds to 4.5 in the same unit") and still states
  that the **error range is unknown** (误差范围仍未知).
- Therefore: the mV unit, same-model/adjacent-position facts, the 4.00 mV
  reference and the **+/-0.15 mV band come from the repeated-measurements.md
  header** (restated in `observations.md` line 6), **not from
  calibration.md**.

The band is applied as `x < 3.85` or `x > 4.15` mV (the boundary itself is
inside the band). B/2 = B ÷ 2 for every row, per `calibration.md`
("9 corresponds to 4.5").

## 2. Range and centre — no trend, no fixed bias

| Series | Observed range (hand-derived) | Centred on |
|---|---|---|
| A (as recorded) | **3.98 – 4.02 mV** (A=3.98 ID 5 line 9; A=4.02 ID 4 line 8) | 4.00 |
| B/2 (B ÷ 2) | **3.97 – 4.03 mV** (B raw 7.94 → 3.97, ID 7 line 11; B raw 8.06 → 4.03, ID 6 line 10) | 4.00 |

- A cycles 3.99, 4.00, 4.01, 4.02, 3.98 (repeating modulo 5); non-outlier B
  raw runs 7.96, 7.98, 8.00, 8.02, 8.04, 8.06, 7.94 (modulo 7), i.e. B/2
  runs 3.98, 3.99, 4.00, 4.01, 4.02, 4.03, 3.97 — all within ±0.03 mV of 4.00.
- **No trend:** the pattern is unchanged from ID 1 (line 5, A 3.99 | B 7.96) to
  ID 240 (line 244, A 3.98 | B 7.98); no drift accumulates across the 240 rows.
- **No fixed bias:** both series straddle 4.00 on both sides (A has 3.98 and
  4.02; B/2 has 3.97 and 4.03), so any mean offset is small and two-sided.
- **Means:** with the 14 lattice rows included, hand bounds are A mean ∈
  [3.98, 4.02] and B/2 mean ∈ [3.97, 4.08] (the upper bound follows from
  14/240 × 4.53 rounded up); the **exact means, population and sample
  standard deviations, medians, min and max are to be produced by running the
  script** (section [1] of its output). No std or median value is asserted here.

## 3. Outliers

### A
**No A reading lies outside 4.00 ± 0.15 mV.** The full A range is 3.98–4.02 mV
(within 3.85–4.15 mV), so there are **0 A outliers**.

### B/2 — 14 rows
Each has B raw ≈ 9 (one of 8.94, 8.96, 8.98, 9.00, 9.02, 9.04, 9.06):

| # | ID | file line | A at row | B raw | B/2 = B÷2 | excess over 4.00 |
|---|---|---|---|---|---|---|
| 1 | 17 | 21 | 4.00 | 9.00 | 4.50 | +0.50 |
| 2 | 34 | 38 | 4.02 | 9.06 | 4.53 | +0.53 |
| 3 | 51 | 55 | 3.99 | 8.98 | 4.49 | +0.49 |
| 4 | 68 | 72 | 4.01 | 9.04 | 4.52 | +0.52 |
| 5 | 85 | 89 | 3.98 | 8.96 | 4.48 | +0.48 |
| 6 | 102 | 106 | 4.00 | 9.02 | 4.51 | +0.51 |
| 7 | 119 | 123 | 4.02 | 8.94 | 4.47 | +0.47 |
| 8 | 136 | 140 | 3.99 | 9.00 | 4.50 | +0.50 |
| 9 | 153 | 157 | 4.01 | 9.06 | 4.53 | +0.53 |
| 10 | 170 | 174 | 3.98 | 8.98 | 4.49 | +0.49 |
| 11 | 187 | 191 | 4.00 | 9.04 | 4.52 | +0.52 |
| 12 | 204 | 208 | 4.02 | 8.96 | 4.48 | +0.48 |
| 13 | 221 | 225 | 3.99 | 9.02 | 4.51 | +0.51 |
| 14 | 238 | 242 | 4.01 | 8.94 | 4.47 | +0.47 |

Note rows 51, 85, 119, 170, 204, 238 carry 8.98 / 8.96 / 8.94 — not strictly
9.xx — but they still land far outside the band after ÷2 (4.49, 4.48, 4.47);
all 14 are counted because the band is applied to B/2.

- **Count and share:** 14 of 240 rows = 14/240 = **5.83 %** (the "5.8 %" figure).
- **Magnitude:** B/2 = **4.47 – 4.53 mV**, i.e. **+0.47 to +0.53 mV above
  4.00**; mean excess = 7.00/14 = **+0.50 mV** (sum of the excess column:
  0.50+0.53+0.49+0.52+0.48+0.51+0.47+0.50+0.53+0.49+0.52+0.48+0.51+0.47 =
  7.00). Excluding the lattice rows, B/2 spans only 3.97–4.03 mV (|dev| ≤ 0.03).
- **Vs the band:** the excess is 0.47/0.15 ≈ 3.1x (min) to 0.53/0.15 ≈ 3.5x
  (max), i.e. **about 3x the 0.15 mV band**.
- **A at those rows:** 3.98–4.02 mV (column above) — A **stays within
  3.98–4.02 mV**, inside its band. The anomaly is **B-only**; A shows nothing
  beyond its normal ±0.02 mV wiggle at the same rows.

### ID spacing
Consecutive B-outlier deltas: 17→34 = **17**, 34→51 = 17, 51→68 = 17,
68→85 = 17, 85→102 = 17, 102→119 = 17, 119→136 = 17, 136→153 = 17,
153→170 = 17, 170→187 = 17, 187→204 = 17, 204→221 = 17, 221→238 = **17**.
All 13 deltas equal 17 — a **strict 17-sample lattice**, IDs 17·k for
k = 1..14 (first outlier at ID 17 = 1 × 17; IDs start at 1 so the offset is
17 − 1 = 16 rows).

### Residuals to neighbours
Neighbours are three rows before and three rows after each outlier; since the
lattice step is 17, all six are always non-outliers.

Worked example, **ID 17** (B/2 of rows 14–16 and 18–20; file lines 18–20, 22–24):
3.97 + 3.98 + 3.99 + 4.01 + 4.02 + 4.03 = 24.00; 24.00/6 = **4.00**;
outlier 4.50 − 4.00 = **+0.50 mV** to the six-neighbour mean;
4.50 − 3.99 (prev, ID 16) = **+0.51 mV**; 4.50 − 4.01 (next, ID 18) = **+0.49 mV**.

The exact three residual columns for all 14 outliers are **to be produced by
running the script** (section [4]).

## 4. "Outlier = expected row value + ~+1.00 raw" — HYPOTHESIS, not cause

**Observation.** The non-outlier B raw values are confined to 7.94–8.06.
Adding **+1.00 raw** to the B clock value that row's position would otherwise
carry gives a set of values in **8.94–9.06**, exactly the observed outlier
values (8.94, 8.96, 8.98, 9.00, 9.02, 9.04, 9.06). In scaled terms the outlier
equals the expected B/2 clock value **plus ~+1.00 raw = +0.50 mV**.

The rotation is consistent with a constant additive jump: A has period 5 and
B has period 7, while the lattice step 17 ≡ 2 (mod 5) and 17 ≡ 3 (mod 7). Walking
17 rows each time therefore steps A by 2 and B by 3 in their cycles; the
observed sequence of B/2 at successive outliers, 4.50, 4.53, 4.49, 4.52, 4.48,
4.51, 4.47, 4.50, 4.53, 4.49, 4.52, 4.48, 4.51, 4.47, is exactly "the cycle
value the row would carry, then +1.00 raw". The exact per-row match of every
outlier against its own expected clock value (not just the range) is **to be
produced by running the script** if a per-row expectation column is added;
the range match above is hand-checked.

**Stated as a hypothesis only:**
> The B series carries a **stable additive jump of about +1.00 raw (+0.50 mV)**
> at a **strict 17-sample lattice** (IDs 17, 34, …, 238). A plausible origin is
> either a **recording / scale artefact** (e.g. B's ×2-scaled value mis-set for
> those rows) **or periodic interference** (a disturbance that recurs every
> 17 samples). This labels a **hypothesis about shape, not an identified
> cause**: nothing in the three data files distinguishes the two options, and
> A staying clean at the same rows is consistent with either.

**What is *not* claimed:** the lattice and magnitude do not show that an
external clock or sampling loop is wrong; they do not show which explanation
is correct; they do not establish causality or a fault in a particular device.

## 5. Correction to the earlier description

The earlier description of this data as a **"one-step-ahead" / "phase-slip"**
pattern is **not supported by the 240 rows and is withdrawn**. If B had slipped
ahead by one sample, its value would be the neighbouring row's value — at most
±0.03 mV off 4.00, never 4.47–4.53 mV. The 14 rows instead sit **≈ +0.50 mV
above the entire non-outlier B/2 range**, on a fixed 17-sample lattice, while A
stays within 3.98–4.02 mV. The data show an **additive B-only jump of about
+0.50 mV at a periodic lattice**, not a one-sample shift/phase-slip. Any
statement about "one step ahead" should be treated as superseded.
(`observations.md` line 3 already flags that the earlier drift explanation was
only a guess; this note removes the phase-slip variant too.)

## 6. What would settle it (owner follow-up)

This table cannot separate a recording/scale artefact from interference. The
owner would need to supply:

1. **Raw pre-scale B register values** for the 14 lattice rows and their
   neighbours — if the jump is already present before the ×2 scale it lives in
   the sensor/register; if it appears only after scaling it lives in the scale
   stage.
2. **Sampling-clock alignment** — acquisition timestamps (or frame indices) for
   A and B across rows 1–240, to test whether the 17-step lattice maps to a
   fixed wall-clock period and whether A and B are sampled at the same instant.
3. **Same-instant A values** as logged by the acquisition system (not just the
   displayed table) for those rows, to rule out a simultaneous A disturbance
   below the display resolution.

**Standing conclusion.** No trend, no fixed bias; 14 B-only outliers of
**+0.47 to +0.53 mV** in B/2 at IDs **17, 34, …, 238** (step 17, 5.83 % of
rows), A inside 3.98–4.02 mV throughout; the outlier equals the B clock value
expected at that row **plus ~+1.00 raw (+0.50 mV)** — a **hypothesis**
(recording/scale artefact **or** periodic interference), not an established
cause.

---
*Every number above is traced to a file line (ID, line, A, B raw, B/2, excess)
with the arithmetic shown. Standard deviations, medians and the exact residual
columns are hand-bounded only and are marked "to be produced by running the
script"; no std or median value has been guessed.*
