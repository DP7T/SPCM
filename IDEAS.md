# Ideas for later (agreed with the user, not built yet)

Read together with [AGENTS.md](AGENTS.md). Each item says what the user decided. Build them
one at a time on `dev`, test on the microscope, then release as usual.

## 1. Point and line cursors for reading data
- **Draw ▾ → Point cursor**: crosshair, drawn on either map (photocurrent or reflection),
  shown on both. Reads the **nearest measured pixel** (no interpolation): x, y (µm from the
  origin), photocurrent X / Y / R / θ (converted units) and reflection.
- **Draw ▾ → Line cursor**: line with two draggable ends, any angle, on either map, shown on
  both. Photocurrent and reflection along it on one distance axis, sampled at the **scan step**
  (nearest pixel at each point, no band averaging). Updates live while dragging.
- **Series** (bias / power): read **all maps** - point: value vs bias / power; line: one curve
  per bias / power.
- **Phase-corrected copy present**: show **both** raw and corrected X′ / Y′.
- **Save** button: numbers-only CSV + info.txt next to the measurement (usual conventions).
- Keep separate from the existing dashed line cut, "Line to scan along" and the resolution line.

## 2. Phase window: clearer wording
Instead of "Y after the correction is only noise" (a statistical test that can look wrong when
Y still shows structure), give the number that matters, e.g. "Y left over: 3 % of the signal
(≈ 1.5° phase spread across the map)". Background: on the real sample the residual Y was ~2-3 %
of X with recognisable features; that is a small phase difference between features
(sin 1.5° ≈ 2.6 % in Y, X keeps cos 1.5° = 99.97 %), not lost signal. The user worried that
signal was removed; explain this in the window or its "?" help.

## 3. Phase-lag map
In the phase window, optionally show θ′ = atan2(Y′, X′) as a map (only where the signal is
well above the noise): which features lag and by how many degrees / microseconds at the
chopping frequency (e.g. fast photovoltaic vs slower photothermal response).

## 4. Check the saved data
The user will look at saved files (h5, CSV, PNG, info.txt) and list what should change.
Collect that list here before changing the file formats (Origin import must keep working,
`scanner.load()` must keep reading old files).

## 5. Phase memory per sample (total phase)
The right angle depends on the device (R x C of contacts and cables, slow photoresponse), so
it can be completely different on another sample. Idea: remember per **sample name** the
total phase = lock-in phase (now saved, `phase_pc_deg`) + correction angle. The sign rule and
the "FAR from your last angle" warning then compare that total for the same sample only (a
reset knob or an autophase no longer looks like a change; a new sample uses the majority rule
quietly). Optional: when starting on a known sample, a hint "last time: total phase 127.4° -
set the lock-in PHASE to 127.4°" (the user sets it by hand; writing it would be a new
exception to the read-only rule - ask first).
