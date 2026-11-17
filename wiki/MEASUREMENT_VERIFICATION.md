# Spice Sifter 0.5 — Automatic Measurements

## Environment and Integration

Windows, Python 3.14.5, Altium Designer 24.4.1.13; October 30, 2026. Measurements were added on top of the existing SDF parser. The runner, temporary DelphiScript, and independent Sweep/Temperature runs were retained. A measurement does not create a new circuit, edit a model, or rerun simulation.

## Actual MixedSim Results

| Test | Measured | Reference |
|---|---|---|
| RC, R=1 kΩ, C=1 µF: 10–90% rise | 2.197219880057017 ms | RC·ln(9)≈2.197224577 ms |
| Same RC: 90–10% fall | 2.19721701995022 ms | Same theoretical result |
| Delay at 50% | 0.6931484905720209 ms | RC·ln(2)≈0.693147181 ms |
| Settling to 1% band | 4.605163563967684 ms | RC·ln(100)≈4.605170186 ms |
| RC at t=2 ms, with step at t=1 ms | 3.1606008385624094 V | 5·(1−exp(−1))≈3.160602794 V |
| RC low-pass cutoff relative to 1 Hz | 159.15494484242754 Hz | 1/(2πRC)≈159.154943 Hz; grid/reference frequency limits accuracy |
| Ideal C=1 µF | ≈1e−6 F at 100/1000/10000 Hz | −1/(2πf·Im(Z)) |
| Ideal L=1 mH | ≈1e−3 H at those frequencies | Im(Z)/(2πf) |
| Series RLC: minimum impedance | 5011.872336272697 Hz | Theory≈5032.921 Hz; ≈0.42% difference, DEC 100 resolution |

Mean, RMS, and ripple calculated from the same RC SDF in the 5–9 ms window were 4.9775249521360445 V, 4.977579480081974 V, and 0.08990042897968298 V. These values were returned as `MEASURED` without datasheet limits. All eight transient measurements used one simulation; the graph was reduced to ten points only after calculations.

## MOSFET and Temperature

The example uses a real SPICE LEVEL=1 analytical model in MixedSim: VTO=2 V, KP=0.02 A/V², LAMBDA=0, W/L=1, TNOM=25 °C. It is not a model of a particular commercial MOSFET; datasheet compliance is not claimed.

VGS=[2,2.1,2.2,4.5] V, VDS=[0.01,5] V, temperatures −40/+25/+85 °C: 24 independent runs. At VDS=5 V, threshold was extracted at ID=0.25 mA. RDS(on) was measured at VGS=4.5 V and VDS=0.01 V.

| Temperature | VGS at ID=0.25 mA | RDS(on) |
|---|---|---|
| −40 °C | 2.1924023858648773 V | 14.220584423688212 Ω |
| +25 °C | 2.1499999983299998 V | 20.040080159789547 Ω |
| +85 °C | 2.1171026061641447 V | 25.760921343028016 Ω |

At +25 °C, the analytical threshold from ID=KP/2·(VGS−VTO)² is ≈2.158114 V. Linear interpolation between 2.1 and 2.2 V gives 2.15 V; the difference is caused by the coarse grid and nonlinear characteristic. The measured value is not presented as the internal VTO=2 V. Theoretical static RDS(on)=VDS/[KP·((VGS−VTO)·VDS−VDS²/2)]≈20.0400801603 Ω, matching MixedSim.

Temperature families are kept separate. Each family-level result lists the four `run_id` values used; RDS(on) belongs to its own OP operating point. Multiple compatible measurements reuse the same 24 simulations.

## Verification

- **79 standalone tests:** the previous 58 unchanged plus 21 measurement tests. These include synthetic linear edges, multiple delay events, unevenly sampled sine waves, trapezoids/windows, exact threshold/plateau/duplicate X values, settling, RC cutoff, capacitance/inductance, resonance, tolerances, isolated failures, temperature families, ambiguous/incomplete crossings, and real RC/RLC SDFs.
- **7 live measurement scenarios:** 30 independent simulations and 27 measurement results. Six positive examples returned PASS. One negative scenario correctly returned ERROR: a missing signal yielded ERROR, an independent mean yielded MEASURED, an out-of-limit check yielded FAIL, and the graph was retained.
- Final algorithms were rechecked against all saved real SDFs without new simulations. Real RC/RLC samples are included under `tests/fixtures`.

Reproduce with `python -m unittest discover -s tests -v`; with Altium open, run `python tests/acceptance_measurements.py`. The local `tests/measurement_acceptance_report.json` is excluded from Git.

## Regression

The original JSONs remained unchanged. `tests/acceptance.py` passed **7/7**, `tests/acceptance_ac.py` **11/11**, `tests/acceptance_temperature.py` **9/9**, and `tests/acceptance_sweep.py` **11/11**. All suites exited with code 0. OP/DC, Transient, AC/impedance, temperature, Parameter Sweep, and Graph Export behavior remained intact.

## Accuracy Limitations

Interpolation does not create information between simulated points. Resonance frequency is selected from the sampled grid. Sweep threshold values use linear interpolation; tighter parameter spacing is needed for precision. Edges with ambiguous threshold plateaus or direction reversals between specified levels are rejected. Real-valued signal X coordinates must increase; conflicting duplicate X values are not silently corrected.

Minimum impedance, Cs/Ls/ESR, and static VDS/ID are meaningful only for the specified circuit and operating conditions. The user specifies levels, windows, current direction, threshold criteria, and limits. Self-heating, FFT, model fitting, and automatic datasheet extraction were not implemented.
