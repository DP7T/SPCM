# Ideas for later (agreed with the user, not built yet)

Read together with [AGENTS.md](AGENTS.md). Each item says what the user decided. Build them
one at a time on `dev`, test on the microscope, then release as usual.

Built in 1.2.6: point / line cursors, phase-lag view in the phase window.
Rejected by the user (2026-10-21): phase memory per sample, the start hint, reading / saving
the lock-in PHASE and the `{phase}` file-name field (that code is only on the branch
`phase-memory-1.2.6`; don't bring it back without asking).

## 1. Phase window: clearer wording (agreed earlier, not built)
Instead of "Y after the correction is only noise" (a statistical test that can look wrong when
Y still shows structure), give the number that matters, e.g. "Y left over: 3 % of the signal
(≈ 1.5° phase spread across the map)". Background: on the real sample the residual Y was ~2-3 %
of X with recognisable features; that is a small phase difference between features
(sin 1.5° ≈ 2.6 % in Y, X keeps cos 1.5° = 99.97 %), not lost signal. Ask before building:
the user chose to keep 1.2.5's phase behaviour and only add the phase-lag view.

## 2. Check the saved data
The user will look at saved files (h5, CSV, PNG, info.txt) and list what should change.
Collect that list here before changing the file formats (Origin import must keep working,
`scanner.load()` must keep reading old files).

## 3. Drift alignment of series by the reflection (option 1, agreed as an idea 2026-10-21)
Seen in the lab (1.2.5): in a long bias series of line scans under low pressure (vacuum
chamber), the sample drifted ~0.5-1 µm over 41 lines; the reflection features shifted
steadily from line to line while the stage went to the same coordinates every time.
Idea: after the measurement, find each line's shift against the first line by
cross-correlating the reflection profiles, shift the photocurrent of that line by the
same amount, and save a drift-corrected copy (like `_phased`) together with the measured
shift per line (= a drift curve). For map series the same with a 2-D shift. No
correction during the measurement (option 2 was not chosen). Ask before building.

## 4. Quick I–V: optional fit, resistance in the file name (idea 2026-10-23, not built)
The user wants an optional fit of the I–V and the resistance in the file name. Open
questions (asked, not answered yet): straight line over the whole sweep or only around
zero bias (range, e.g. ±50 mV) for non-linear devices; all cycles together or per cycle;
the name is fixed before the sweep, so the folder / files would be renamed after the fit
(field `{R}`, written as e.g. `R12.3kOhm`, no Ω) - or R only in the figure / files; fit
line + R ± error on the live plot and figure, R, I0 and the fit range in h5 / info.txt;
tick box, off = as today. Ask before building.
