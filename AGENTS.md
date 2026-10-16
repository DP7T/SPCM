# Guide for continuing SPCM development (for AI assistants and developers)

Read this before changing the code. It describes the app, how it is organized, and the decisions the
user has already made. Do not undo these decisions without asking. For the history of the requests, see
[HOW_IT_WAS_BUILT.md](HOW_IT_WAS_BUILT.md). For the user-facing manual, see [README.md](README.md).

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
| `iv.py` | quick DC I–V: `IVSettings`, `bias_points()` (cycles 0→max→min→0), `IVData` (h5/CSV/PNG), `IVWorker` |
| `resolution.py` | spot size from an edge on the reflection map: profile, erf fit (no scipy), slant correction, save |
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
  - A move counts as done only when the stage is not moving **and** reports the target position. If it sits off target for `settle_s` (3 s), the move is sent once more; then ≤ `accept_um` (1 µm) off is accepted and logged, more raises an error.
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
- Resolution: analysis of existing maps only (no fine line scan). Fit R = A + B·erf((x−x0)/(√2σ)); FWHM = 2.355σ, 10–90 % = 2.563σ.

**Data**
- **One folder per measurement**: `<save dir>/<name>/<name>.h5` + PNG(s) + `<name>_csv/`. Names come from a pattern (`naming.py`), default `{sample}_{date}_{size}_{step}_{power}_{n}`; experiment-note quantities can be fields.
- **CSV matrices hold only numbers** (no axis row/column; axes in info.txt). The user imports them into Origin.
- **HDF5 layout (v2):**
  - `/info`: settings, instruments and conversion, plus `/info/experiment`.
  - One group per map, `/map1` or `/map1_+0.5V` for bias sweeps, holding plottable 2-D datasets `photocurrent_X/_Y/_R/_theta_deg` and `reflection_*` with the `x_um`/`y_um` axes and `raw/*`. No stage-coordinate datasets (the user opens the files in Origin; stage positions follow from `info/origin_stage_um`).
  - `/power_log`: one N×2 dataset, time (s) and power on the sample (µW).
  - **No camera images in the h5.**
  - `scanner.load()` must keep reading old files.
- **Fly scans never invent data:** pixels without a reading stay NaN (line re-scanned once first; count in `map_info[...]['fly_empty_pixels']`). Stop halts the stage (`_halt_stage`).
- **After each scan:** CSV export and the PNG figure are written automatically. Stop & discard deletes them.

**GUI**
- Layout (1.2): left tabs Map / Point sweep / I–V / Instruments (Start runs the open tab's measurement); right tabs Maps / Camera / Plot (one stacked widget for line scans, point sweeps, I–V: `show_plot`) / Experiment notes. Drawing tools behind one **Draw ▾** menu (`_draw_menu`; the old checkable buttons are hidden state holders). Explanations go behind **?** buttons (`_help_btn`), not as grey text: the user found the window overwhelming.
- Optional dark theme. Camera view is flipped left-right by default.

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

- `main`: **1.0** (0.9.0 + the fixes from the first lab test). Released; this is what the lab uses.
- `next`: 1.1.x in testing (quick I–V, resolution, bug fixes). Becomes one commit "SPCM 1.1.x" on `main` once tested.
- `dev`: 1.2 in testing (camera calibration, line scans, bias / power series, point sweeps), built on `next`.
- `experimental-0.10`: the old experimental line (0.10.0). Its useful features were rebuilt on 1.x in 1.2; don't merge it.
- Each released version is one commit on `main` ("SPCM x.y.z"), with a tag `vx.y.z`.

## Working with the user

- Keep changes small and testable, and say exactly what to check on the real setup.
- Ask before changing the conventions above.
- Explain errors and setup steps in plain words. The user sends screenshots.
- Commit as `DP7T <342493853+DP7T@users.noreply.github.com>`.
