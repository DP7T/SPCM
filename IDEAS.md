# Ideas for later (agreed with the user, not built yet)

Read together with [AGENTS.md](AGENTS.md). Each item says what the user decided. Build them
one at a time on `dev`, test on the microscope, then release as usual.

Built in 1.2.6: point / line cursors, phase-lag view in the phase window.
Built in 1.2.13: I–V straight-line fit with R in the file name.
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


## Known small issues (outside code reviews; user: keep in mind)
- Autofocus: `autofocus.fresh_frame` returns the last (old) image after its 5 s timeout
  without saying so, so a stalled camera can give a wrong focus. Fix: warn and stop.
- Fixed in 1.2.16: point-sweep / I–V "completed" flags, and the 12 findings of the
  1.2.15 review (see CHANGELOG). Not done (judged not needed at the user's data sizes):
  per-map HDF5 checkpoints instead of re-saving the whole series, density plots for the
  phase pictures, cached live-plot items, CI on tags / pinned dependencies.
