# Guide for continuing SPCM development (for AI assistants and developers)

Read this before changing the code. It describes the app, how it is organized, and the decisions the
user has already made. Do not undo these decisions without asking. For the history of the requests, see
[HOW_IT_WAS_BUILT.md](HOW_IT_WAS_BUILT.md). For the user-facing manual, see [README.md](README.md).
Ideas the user has agreed on but that are not built yet are in [IDEAS.md](IDEAS.md).

## What it is

A Windows desktop program (Python 3.10+, PyQt5 + pyqtgraph) that controls a scanning photocurrent
microscope:
- **Stage:** Märzhäuser Tango Desktop, with x/y and optional z (native serial command set).
- **Lock-ins:** two SR830 over GPIB, one for photocurrent/photovoltage and one for reflection.
- **Bias:** from an SR570 (RS-232, listen-only), an SR830 AUX OUT, a Keithley 24xx, or none.
- **Optional:** a PM100USB power meter (VISA SCPI, or the TLPM DLL as fallback) and a Thorlabs CS165CU camera (thorlabs_tsi_sdk).

The user is a physicist who runs the app on a lab PC and is not a programmer. They test each version on
the real hardware and report back with screenshots. Write in plain language for them. Keep the GUI simple.

## Files

| file | contents |
|---|---|
| `main.py` | entry point: `--sim`, `--list`, theme, error hook (`spcm_error.log`), first-run and connect-failure dialogs, restart |
| `appinfo.py` | `__version__`, `app_dir()` (works both from source and as the frozen exe) |
| `instruments.py` | `TangoStage`, `SR830`, bias sources (`SR570Bias`, `LockinAuxBias`, `Keithley2400`, `NoBias`), simulators (`SimWorld`, `SimStage`, `SimSR830`, `SimBias`), `connect()` |
| `accessories.py` | `PM100USB`, `PM100TLPM`, `ThorCamera`, `SimCamera`, `SimPM100`, `ensure_thorlabs_sdk()` (finds the camera package inside the ThorCam install), `connect_accessories()` |
| `scanner.py` | `ScanSettings`, signal conversion (`pc_scale`, `signal_kind`), settling (`pixel_wait`), `ScanData` (HDF5 save/load, CSV export), `SafeStage`, `set_laser_power`, `ScanWorker` (QThread; step and fly modes, bias loop, power log) |
| `gui.py` | `MainWindow`, `MapPanel`, toasts and notifications, power poller, camera view + calibration, frame and line drawing (maps and camera), series box, point-sweep and quick I–V tabs, file-name pattern |
| `sweeps.py` | point sweeps: `SweepSettings`, `SweepData` (save/load/CSV/figure, power-law fit), `SweepWorker` |
| `linescan.py` | line scans: `line_settings()` builds a 1-row rotated `ScanSettings` (`scan_type="line"`) run by the normal `ScanWorker`; CSV and figure |
| `sweep_view.py` | live plots: `SweepView` (point sweeps), `LineView` (line scans) |
| `calibration.py` | camera ↔ stage calibration (`Calibration`, `calibrate()`, phase-correlation `image_shift()`) |
| `tools/selftest.py` | tests in simulation: names, power setting, maps (step, fly), the window off-screen with screenshots |
| `settings_dialog.py` | hardware settings window; writes `config.yaml` |
| `numparse.py` | SI-prefix number parsing and the `SISpinBox` class |
| `naming.py` | measurement names from a pattern, one folder per measurement |
| `iv.py` | quick DC I–V: `IVSettings`, `bias_points()` (cycles 0→max→min→0), `IVData` (h5/CSV/PNG), `IVWorker`, optional line fit `fit_line`, `r_text`, `rename_with_r` |
| `resolution.py` | spot size from an edge on the reflection map: profile, erf fit (no scipy), slant correction, save |
| `phase.py` | phase correction after the measurement: `find_phase`, `rotate`, `write_copy` (`<name>_phased.h5`) |
| `where.py` | `<name>_where.png` at the start of a line scan / point sweep: the map on screen with the line / spot marked (`save`, `inside`, `describe`); matplotlib without pyplot (safe next to background saving) |
| `cursors.py` | point / line cursors: nearest-pixel values (`point`, `line`) of all maps, uncorrected + phase-corrected (read by position from `<name>_phased.h5`), `save` → `<name>_cursors/*.csv` (`CursorDialog` in gui.py) |
| `figures.py` | publication-style PNG (matplotlib Agg) |
| `autofocus.py` | z sweep that maximizes the camera sharpness |
| `config.default.yaml` | default settings. `config.yaml` is the user's copy, created on the first start. It is git-ignored and never overwritten. |
| `Install SPCM.bat`, `tools/setup.ps1` | installs Python and requirements, pip-installs the camera package, creates shortcuts |
| `.github/workflows/build-windows.yml` | PyInstaller onedir build of `SPCM.exe`, attached to GitHub releases |
| `.github/workflows/release-notes.yml`, `tools/release_notes.py` | names every release stable / testing / old and fills in its CHANGELOG section |
| `.github/workflows/selftest.yml` | runs `tools/selftest.py` on Windows for every push to `main`; pushes the log, figures and screenshots to the `selftest-results` branch |

## Decisions already made (keep them)

**Lock-ins and signal**
- **Analog control:** the lock-ins are only **read** (τ, sensitivity, slope, input). Never add controls that change them. Exceptions: the SINE OUT amplitude for setting the laser power, and the optional **Auto range up** (user's request, off by default): photocurrent lock-in only, one range less sensitive when R > 80 % of full scale (`scanner.range_up`), never back down; the pixel (fly: line; sweep: point) is re-measured after one pixel wait; every change is notified and saved in `map_info[...]['range_changes']` / sweep `info['range_changes']`. Its purpose: no saturation during unattended measurements.
- **Signal conversion** gives peak amplitude:
  - SR570: × √2 × sensitivity (A/V) → photocurrent.
  - SR560: × √2 ÷ gain → photovoltage.
  - Lock-in only: × √2, with the unit taken from its input (current input → A, A or A−B → V).
- **Figure title:** *Photocurrent* or *Photovoltage*, chosen by that same rule.
- **Bias:** enabled only with the SR570.

**Units and input**
- Scan size is in **µm** and the step in **nm**. Powers are always saved in **µW**.
- **Number input:** a plain number is in the field's unit; `k`/`M` multiply; `m`, `µ`/`u`, `n`, `p` are absolute. So for the step field (nm), `1000`, `1k` and `1u` all mean 1 µm.

**Stage and scanning**
- **Axes:** typed program axes are inverted relative to the stage numbers, to match the joystick (`stage.invert_x/invert_y`). The joystick itself is not changed.
- **Origin:** **Set origin here** makes the current position (0, 0). Start positions are relative to the origin. Nothing ever silently resets the origin or the start to an absolute 0,0. That once sent the stage millimetres away.
- **Serpentine** is off by default, because it causes stripes through backlash.
- **Safety:**
  - All scan moves go through `SafeStage`, which refuses targets outside the scan area plus a margin and verifies arrival.
  - Moves longer than 500 µm need confirmation.
  - Every Tango command and reply is logged to `spcm_stage.log`.
  - **GPIB:** all SR830 queries/writes go through one bus lock (`instruments._BUS_LOCK`); the two lock-ins are read one after the other (`ScanWorker._read_both`). Reading them in parallel threads (0.9-1.2.6) mixed their replies in the lab. `SR830.read` re-reads a reply that isn't exactly 4 numbers (clear + retry, 3 tries, logged).
  - A move counts as done only when the stage is not moving **and** reports the target position. If it sits off target for `settle_s` (3 s), the move is sent once more; then ≤ `accept_um` (1 µm) off is accepted and logged, more raises an error.
  - **Stop & discard** needs a double-click (`_DoubleClickButton`), no confirmation box (user's request 1.2.15).
  - Closing the window during a measurement stops it and waits for it to finish (bias, SINE OUT, saving) before closing the instruments.
- **Laser power:** "Set the laser power before each scan" sets the SINE OUT once before each scan, and in a bias series again before every map / line, after the bias change (`ScanWorker._set_power`). `set_laser_power` moves the SINE OUT in **2 mV steps in one direction only** (never up and down, the user's request to protect the laser) until the sample power is within tolerance; it can be aborted (button, or Stop before a scan). There is **no drift correction** during the scan (a bright gold area was mistaken for drift). Power is still logged per line.

**Series, line scans, point sweeps, camera**
- Maps and line scans can run as a **series**: one per bias (`outer="bias"`, `biases`) or per laser power on the sample (`outer="power"`, `outer_values` in µW). Chopping frequency was considered and rejected by the user (not worth varying), and the lock-ins stay read-only apart from the SINE OUT.
- Every power in a series or point sweep is set with `set_laser_power(..., tol_pct=0)`: 2 mV steps in one direction, stopping at the step closest to the target. The SINE OUT is put back with `ramp_sine` (2 mV steps), never with a jump.
- Point sweeps run where the laser is (never move the stage): power, bias or bias × power (bias outer). N readings ~1 τ apart, mean ± std. Plots: linear + log-log with |S| = A·P^α, one curve per bias, bias–power map.
- Camera calibration matrix A (µm per pixel) is in `config.yaml` → `camera.calibration`. The laser is at the cyan cross. Pixel p ↔ stage `S_now + A·(p − cross)`.
- Lock-in measurements (maps, lines, sweeps) and the quick I–V remind the user to re-cable / re-set the SR570 when switching between them.

**Quick I–V and resolution**
- Quick I–V reads the SR570 output on **AUX IN 1** of the photocurrent SR830 (`OAUX? 1`); current = AUX voltage × SR570 sensitivity, which the user selects (the SR570 can't be read). SR570 filter OFF / LOW DRIFT and the cable move are done **by hand**; the program only reminds (`_switch_reminder`, on every switch map ↔ I–V, remembered in `ui.last_measurement`). Sweep = cycles of 0 → max → min → 0 (no other shapes: the user's separate Keithley program covers more). Inputs follow the usual convention: max/min in V, step in mV; values rounded to the SR570's 1 mV.
- I–V fit (1.2.13, user's choices): optional (`IVSettings.fit`, `iv` config `fit`, off by default); straight line through the **whole sweep, all cycles together** (`iv.fit_line`); h5 group `fit`, info.txt, figure, live plot. Name: with the fit ticked R is always in the name; the marker `RFIT` at start, `iv.rename_with_r` renames folder + files after the fit (`R12.3kOhm`, `Rnone` if no fit); `naming.make_name` counts `RFIT` names against `R*` folders.
- Resolution: analysis of existing maps only (no fine line scan). Fit R = A + B·erf((x−x0)/(√2σ)); FWHM = 2.355σ, 10–90 % = 2.563σ.

**Data**
- **One folder per measurement**: `<save dir>/<name>/<name>.h5` + PNG(s) + CSV folder(s).
- **Names (1.2.15, user's design, discussed in detail - don't change without asking):** made by the program, no pattern, no running number: `date_time(HHMM)_sample_wavelength_type_details[_extra]` (`naming.head/compose/unique`, `MainWindow._make_name/_name_parts/measurement_name`). Sorting by name = time order across types. Wavelength from `pm.wavelength_nm` (none without PM, never for IV). Types `map`/`line`/`point`/`IV`. Map: `size_step_power[_bias]`; bias series: range at the bias slot, power series: range at the power slot; each map's PNG and `<map name>_csv/` use the series name with the range swapped for its own value (`ScanSettings.file_name`, `map_names`, `scanner.map_bases`; old files fall back to `<name>_map<k>_<value>` and `<name>_csv/map<k>…`). Line: `length_step_power[_bias]`, series `line_bias_<range>_…` / `line_power_<range>_…`. Point: `point_power_<range>`, `point_bias_<range>_<power now>`, `point_biaspower_<brange>_<prange>`. IV: `IV_<range>[_RFIT→R12.3kOhm]`. Bias only when used (signed, `+0.5V`). Power = target or meter reading. Same minute → `_2`. Optional extra per tab (`name_extra`, config `naming.<tab>`) from Experiment-notes fields only, at the end; nothing automatic from the notes, no angle/fly/avg. Sample + Folder above the tabs. Preview per tab (`name_prev`), red above 80 characters.
- **CSV matrices hold only numbers** (no axis row/column; axes in info.txt). The user imports them into Origin.
- **HDF5 layout (v2):**
  - `/info`: settings, instruments and conversion, plus `/info/experiment`.
  - One group per map, `/map1` or `/map1_+0.5V` for bias sweeps, holding plottable 2-D datasets `photocurrent_X/_Y/_R/_theta_deg` and `reflection_*` with the `x_um`/`y_um` axes and `raw/*`. No stage-coordinate datasets (the user opens the files in Origin; stage positions follow from `info/origin_stage_um`).
  - `/power_log`: one N×2 dataset, time (s) and power on the sample (µW).
  - **No camera images in the h5.**
  - `scanner.load()` must keep reading old files.
  - **No autosave inside a single map / single sweep (user's decision 2026-10-25):** single maps and plain power sweeps are written only at the end (or on Stop / error); series after each map / line, sweeps after each bias row, I–V every 25 points. The user declined saving every line / minute (old lab PC, speed). Don't add it without asking.
  - **Saving is atomic (1.2.12):** every h5 (maps, lines, sweeps, I–V) is written through `scanner.atomic_h5` → `<path>.part` + `os.replace`; autosaves during a measurement go through `scanner.autosave` (failure = warning, measurement goes on).
- **Fly scans never invent data:** pixels without a reading stay NaN (line re-scanned once first; count in `map_info[...]['fly_empty_pixels']`). Stop halts the stage (`_halt_stage`).
- **Phase correction (1.2.4):** automatic after every map / line scan (`MainWindow._auto_phase`, `phase.py`). One angle for the whole file (all maps of a series; the user expects one phase for all pixels), found from pixels > 5× noise as the direction of the X–Y line through zero (second moments); strong pixels off the line by > 4σ + 10 % are ignored (iterated). Sign (user's choice, no reference button): the direction closest to the last used angle (`config.yaml` → `phase.last_deg`, `REF_MAX_DEG` 30°), else the strength-weighted majority positive. Series: per-map fits only as a consistency check (`inconsistent_maps`). Done on the **processed** photocurrent. Written to a **separate copy** `<name>_phased.h5` (+ `_phased_csv/`, `_phased_*.png`) in the same folder; `raw/*` there stays unrotated (`ScanData.raw_pc`), `info/phase_correction` holds angle, offset, noise, message. No copy without a clear signal. Optional substrate background: user-marked box on the map (`PhaseDialog`), else the weakest 30 % of pixels. The lock-in phase is never changed.
- **Phase window is display only (1.2.11, user's request):** no save / "No copy" buttons, no angle box, no Auto. The copy, its CSV and the `_check` / `_lag` PNGs are always written automatically (`_auto_phase`; File → Phase correction makes the copy if missing, else shows the saved one). Only the substrate tick box stays; changing it re-finds the phase and re-saves (`PhaseDialog._resave`).
- **After each scan:** CSV export and the PNG figure are written automatically. Stop & discard deletes them. Since 1.2.12 this after-scan work (CSV, figures, phase copy) runs in a background thread (`MainWindow._post` → `_PostJob`, one at a time via `_PostJob.LOCK`; results shown in the GUI thread; `_saving()`; `closeEvent` waits for it). Keep heavy saving out of the GUI thread: a 2.5 h line series froze the window at 99 % in 1.2.11.

**GUI**
- Layout (1.2): left tabs Map / Point sweep / I–V / Instruments (Start runs the open tab's measurement); right tabs Maps / Camera / Plot (one stacked widget for line scans, point sweeps, I–V: `show_plot`) / Experiment notes. Drawing tools behind one **Draw ▾** menu (`_draw_menu`; the old checkable buttons are hidden state holders). Explanations go behind **?** buttons (`_help_btn`), not as grey text: the user found the window overwhelming.
- Optional dark theme. Camera view is flipped left-right by default.
- Line tab (1.2.8): left tabs are now Map / Line / Point sweep / I–V / Instruments (indexes 0-4; `start()` routes 1 → `start_line_tab`). The Line tab has its own start/end (µm from the origin), step, readings per point (`ScanSettings.n_avg`, mean + std → `ScanData.pc_std/refl_std`, h5 `*_std`, CSV columns, error bars) and series (`_line_series`); timing, power and preamp are shared with the Map / Instruments tabs (`settings(series=False)`). 'Use this line' (map or camera) only fills the Line tab (`_fill_line_tab`).
- Finest point sweep (1.2.9): `SweepSettings.finest` (+ `p_from_uW`, `p_to_uW`, `every_steps`, `both_ways`); `SweepWorker._finest_row`: `set_laser_power` to *from*, then `set_sine_amplitude(+2 mV × every)` and a point per step until the measured power ≥ *to* (or `max_amp_V`), optionally back down measuring (`direction` −1). Arrays get room for every step (`power_list` → NaNs) and are trimmed (`SweepData.trim`); `load_sweep` takes shapes from the file. Not for line scans (user: too slow).
- Where picture (1.2.13): `MainWindow._where_pts` / `_save_where` in `start_line` and `start_sweep`: map on screen + line (dot, arrow) or spot (cross) → `<name>_where.png`; `ScanSettings.where_map` / `SweepSettings.where_map` = which map (file name, or "map not saved …" + size/place, user's case: discarded rough scans) → info.txt. Only lines and point sweeps (not cursors), map only (not camera), no picture when not on the map.
- Many curves (1.2.12): more than 8 series → saved figures use a colour bar (`figures.series_key` / `add_series_bar`, colours by value) instead of a legend; live plots hide the legend and add a colour note to the title (`sweep_view.LEGEND_MAX`). Line-scan series get no per-line phase `_check`/`_lag` PNGs (user's request).
- Scan direction (1.2.11): `ScanGlyph` = start dot + long arrow along the lines + shorter arrow to the next lines (+ return arrow for serpentine). On drawn frames (map `rois`, camera `cam_rois`) it is a child of the ROI in its local corner coordinates, the way 'Use this frame' reads them. The pink dashed **outline of the next scan** (`preview_glyphs` on both maps, `cam_preview` on the calibrated camera) is computed from `settings(series=False)` with `ScanSettings.axes()` / `rotate_xy`, so it is exactly what the scan will do; toggled by Draw ▾ → Outline of the next scan (`ui.scan_outline`); camera outline hidden while measuring.
- Live position (1.2.8): `_poll_position` every 400 ms when idle reads the stage with `position(quiet=True)` (not logged), shows it from the origin under the origin line and as a cyan cross on the maps (`_stage_to_map`).
- Cursors (1.2.6): Draw ▾ → Point / Line cursor, mirrored on both maps (`_mirror`), values in `CursorDialog`; nearest pixel only, line at the scan step, all maps of a series, raw + corrected.
- Phase window (1.2.6): Phase-lag view (`phase.lag_map`; µs from `chop_freq_Hz`, read with `FREQ?`). **Rejected by the user (2026-10-21):** phase memory per sample, the start hint, reading/saving the lock-in PHASE and the `{phase}` name field. That code is kept only on the branch `phase-memory-1.2.6`; don't bring it back without asking.

## Lessons from the 1.2.15 review (keep)
- Optional hardware (camera, power meter) may be missing: every attribute used outside its tab must exist without it (`cam_tab`, `cam_rois`, `cal` … set in `__init__`); the self-test has a window run without accessories.
- Every hardware worker counts for `_busy()`; restart / settings / File → Open check it.
- Per-map file names must be unique within a series (repeated values get `_2`).
- `map_info` lists/dicts are saved as `json:` attributes.
- All autosaves (maps, sweeps, I–V) go through `scanner.autosave`.

## How to test without hardware

```
python main.py --sim          # full GUI with a simulated stage, lock-ins, camera, power meter and sample
QT_QPA_PLATFORM=offscreen python -c "..."   # headless checks in CI or containers
```

The simulators live in `instruments.py` and `accessories.py`, with `SimWorld` holding the virtual sample, the laser and the focus. When you add hardware features, also add them to the simulators, so the app can always be tried with `--sim`.

Before handing over a version, run the self-test:

```
python tools/selftest.py out      # names, power setting, maps, series, sweeps, lines, calibration, I-V, resolution, window
```

It needs the libraries from `requirements.txt`; without PyQt5 it skips the window part. On every push to `main`, GitHub runs it on Windows (workflow *Self-test*) and pushes `selftest_log.txt`, the figures and screenshots of the window to the branch `selftest-results` (`git fetch origin selftest-results`), so the results can be looked at even without a Windows PC. Look at the screenshots, not only at "passed".

## Releasing

- Bump `__version__` in `appinfo.py` and add a section to `CHANGELOG.md`.
- Publishing a GitHub **release** (tag `vX.Y.Z`) runs the workflow, which attaches `SPCM-vX.Y.Z-windows.zip`.
- Release names and descriptions are written automatically by `tools/release_notes.py` (workflow *Release notes*, also run after each build): "SPCM x.y.z - stable / testing / old test version", a first line saying which one to use, then that version's CHANGELOG section. So keep CHANGELOG sections short and written for the user.
- Users update either by unzipping the release zip, or with Code → Download ZIP followed by `Install SPCM.bat`.
- `config.yaml` and `data/` are never in the repository, so unzipping a new version over the old one is safe.

## Status and branches

- `main`: **1.2.3** (released 2026-10-19, tested on the microscope; this is what the lab uses). Earlier: 1.0.0.
- `dev`: where new work happens; becomes one commit "SPCM x.y.z" on `main` once tested on the microscope.
- `next`: the old 1.1.x test line, fully contained in 1.2.3; no longer used.
- `experimental-0.10`: the old experimental line (0.10.0). Its useful features were rebuilt on 1.x in 1.2; don't merge it.
- Each released version is one commit on `main` ("SPCM x.y.z"), with a tag `vx.y.z`.

## Working with the user

- Keep changes small and testable, and say exactly what to check on the real setup.
- Ask before changing the conventions above.
- Explain errors and setup steps in plain words. The user sends screenshots.
- Commit as `DP7T <342493853+DP7T@users.noreply.github.com>`.
