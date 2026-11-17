# Spice Sifter 0.2 — AC Analysis & Graph Export

Historical 0.2 report. For temperature extensions and more recent regression results, see [TEMPERATURE_VERIFICATION.md](TEMPERATURE_VERIFICATION.md).

## Compatibility and SDF Format

The existing `spice_sifter.py` utility was extended without replacing its CLI, temporary DelphiScript, or result-saving workflow. New fields are optional; checks without `analysis` still use transient analysis.

The complex-data structure was verified against a real SDF from Altium Designer 24.4.1.13. For complex X/Y, each sample contains float64 Re(X), Im(X), Re(Y), Im(Y), followed by the corresponding auxiliary float32 block. The parser derives block sizes from complex flags and sample counts, and validates boundaries and `TotalPoints`. Original frequency values are preserved; the imaginary part of frequency must be zero. A real RC fixture is included as `tests/fixtures/rc_ac_ad24_4.sdf`.

The VSENSE source current is called `i(vsense)` in SDF. For `VSENSE src dut 0`, current flows from the source to the DUT and `current_sign=1`. Changing `current_sign` explicitly reverses the impedance sign; no automatic correction is applied.

## Real Simulation Results

**Environment:** Windows, Altium Designer 24.4.1.13, Python 3.14.5; October 30, 2026.

| Test | Result |
|---|---|
| 1 kΩ resistor, DEC | Re(Z)=1000 Ω, Im(Z)=0, magnitude 1000 Ω; PASS |
| 1 µF capacitor, OCT | Negative Im(Z), decreasing magnitude; analytical tolerances passed |
| 1 mH inductor, LIN | Im(Z)=6.283185307179587 Ω at 1 kHz; PASS |
| Series RC, DEC | Z≈1000−j159.1549430918962 Ω at 1 kHz; PASS |
| Arbitrary frequency 1011 Hz | Interpolation of original V/I; PASS |
| Out-of-range frequency | ERROR / AC_FREQUENCY_OUT_OF_RANGE |
| Full graph arrays | All 501 voltage and impedance points |
| `max_points=100` | 100 of 501 points, endpoints preserved |
| FAIL with graph request | FAIL; arrays still returned |
| AC without `output.graphs` | No `graphs` field |
| Transient/DC signal | Arrays with s/V and V/V units; PASS |

AC acceptance: **11/11** scenarios. The machine-readable local report, `tests/ac_acceptance_report.json`, is excluded from Git because it includes local artifact paths. Reproduce with `python tests/acceptance_ac.py` while Altium is running.

An ideal inductor connected to an ideal voltage source caused a degenerate DC circuit. The test adds `RDRV=1 Ω` between the source and VSENSE, outside the measured DUT. The measured V(dut)/I(VSENSE) ratio still applies only to the ideal inductor.

## MVP 1 Regression

The original `tests/acceptance.py` was run unchanged: **7/7** passed (divider, wrong tolerance, missing signal, invalid JSON, repeated run, faulty model, Cyrillic characters/spaces). Report: `tests/acceptance_report.json`.

## Automated Tests

**31 tests:** the original 18 with unchanged assertions, plus 13 AC tests. Coverage includes real and synthetic complex data, analytical RC results, separate interpolation of V and I before division, exact frequency matching, `current_sign`, mismatched/unordered grids, zero/low current, NaN, missing/ambiguous Chart/Wave, input validation, AC/Transient/DC graphs, and full/decimated arrays. A case with a value between selected graph points confirms that decimation cannot affect tolerance checks.

Run: `python -m unittest discover -s tests -v`.

## Limitations

- Frequency interpolation is linear in hertz and approximate; the frequency grid must resolve resonances. Uniform graph decimation can hide narrow peaks.
- `max_points` must be at least 2 to retain both endpoints. Without a limit, every point is exported.
- V/I grids must match; there is no automatic alignment. Multiple matching analyses are rejected as ambiguous.
- `.OP` was neither part of MVP 1 nor added in 0.2; no OP regression is claimed. DC is now supported for basic measurements and signal exports.
- Limitations on MixedSim panel messages and Cyrillic model paths still apply; see the [User Guide](User-Guide.md).
