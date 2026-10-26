# Changelog

## 1.2.17 (in testing)
- **Stop & discard** now really removes the measurement's folder: if Windows still holds a
  file that was just written (virus scanner, an Explorer window on the folder), it tries
  again for a few seconds, and tells you if the folder can't be deleted
- A measurement stopped (or failed) **before anything was measured** - e.g. while the
  laser power was still being set - no longer leaves a folder behind (map, line, point
  sweep, I–V). The status bar says that nothing was saved

## 1.2.16 (2026-10-25)
Bug fixes only (from an outside code review), each with a self-test:
- The program crashed at start **without a camera** (camera off or not found) since 1.2.11
- Map series with a value twice (e.g. bias `0, 1, 0`): the third map's PNG / CSV no longer
  overwrites the first (`…_+0V` and `…_+0V_2`)
- Bias series with "Set the laser power": the first map's power is now set **after** its
  bias, like all the others; Stop during that first power setting ramps the bias back to 0
- Fly scan: a read error during a line now halts the stage
- Autofocus: a single z limit (only `z_max` or only `z_min`) is no longer ignored
- Restart / Hardware settings are refused while autofocus, camera calibration or a power
  setting runs (before, only a measurement blocked it)
- File → Open is refused during a measurement (it mixed up the live display)
- Auto-range changes and the lock-in filter slopes are now saved in the map / line .h5
- Quick I–V: a failed autosave only warns, the I–V goes on; an I–V stopped by an overrange
  is no longer marked complete
- Point sweep: an unreachable power no longer skips the lower powers after it in the list;
  such a sweep is marked not complete and info.txt lists the powers not reached
- Phase-corrected copy of averaged line scans: the error bars now turn with the signal
- Point sweep: θ is the phase of the mean X, Y (averaging +179° and −179° gave 0°)
- The phase window re-saves in the background after the substrate tick

## 1.2.15 (pre-release)
- **New file names, made by the program:** `date_time_sample_wavelength_type_details`,
  e.g. `20261024_1432_flakeA_532nm_map_20x20um_500nm_10uW`. Sorting by name now gives
  the order you measured in, for every type (map, line, point, IV). Wavelength from the
  power meter (not for the I–V); bias only when used; a series shows its range, and each
  map of a map series gets its own PNG / CSV named with its own bias or power. Lines:
  `line_bias_…` / `line_power_…`; point sweeps: `point_power_…` / `point_bias_…` /
  `point_biaspower_…`; I–V: `IV_-1to1V` (+ `R12.3kOhm` with the fit). Two in the same
  minute: `_2`. The running number and the pattern are gone
- Each tab (Map, Line, Point sweep, I–V) has its own **File name** box: the next name,
  and an optional part from the Experiment notes added at the end (e.g. `{Temperature}`)
- **Sample** and **Folder** moved above the tabs (shared by all measurements)
- **Stop & discard** works with a double-click, without a question
- Old files keep their names and open as before

## 1.2.14 (pre-release)
- Quick I–V: with "Fit a straight line" ticked, the resistance now always goes into the
  file name (folder and files renamed after the fit, e.g. `sample_IV_R12.3kOhm_001`).
  Before, it only did with `{R}` in the file-name pattern; `{R}` still chooses where

## 1.2.13 (pre-release)
- **Where you measured:** line scans and point sweeps save `<name>_where.png` when they
  start: the map shown (photocurrent and reflection) with the line (start dot + arrow) or
  a cross on the spot. info.txt says which map it was: its file name, or "map not saved"
  with its size and place (e.g. a rough scan you discarded). No picture when the line or
  spot is not on the map shown. Map only, not the camera
- **Quick I–V: optional straight-line fit** (tick box "Fit a straight line (resistance R)",
  off by default): I = V/R + I0 through all points, all cycles. Fit line and R ± error on
  the plot and in the PNG; R, I0, r² in the h5 and info.txt. With `{R}` in the file-name
  pattern, the folder and files are renamed after the fit, e.g. `..._R12.3kOhm_001`

## 1.2.12 (pre-release)
- **No more freezing at the end of a long measurement:** CSV, figures and the phase copy
  are now made in the background after a map, line scan or point sweep. The window stays
  usable; the status bar says "Saving …" until they are done. Closing the program waits
  for the saving to finish (before, a long series could show "Not responding" at 99 %)
- **Safer saving:** files are written to `<name>.h5.part` first and then swapped in, so a
  crash or power cut while saving no longer destroys the measurement saved so far. If an
  autosave fails during a measurement (e.g. the file is open in another program), you get
  a warning and the measurement goes on
- Line-scan series: no more phase PNGs for every single line (the corrected figure of the
  whole series is still saved)
- Plots with many curves (more than 8 lines / biases): a colour bar instead of a legend,
  so nothing covers the data (saved figures); in the live plots the legend is hidden and
  the title says which colour is which

## 1.2.11 (pre-release)
- **Scan direction shown before the scan:** a pink dashed outline of the next scan (Map tab
  values) on both maps and on the calibrated camera image, with a dot on the start pixel,
  an arrow along the lines and a shorter arrow towards the next lines (and the way back
  for serpentine). Drawn frames (maps and camera) get the same dot and arrows.
  Draw ▾ → Outline of the next scan switches it off
- Phase window is for looking only: the "Save corrected copy" / "No copy" buttons, the
  angle box and Auto are gone (the copy and PNGs are always saved automatically; "No copy"
  gave an error). Ticking the substrate background saves the copy again by itself

## 1.2.10 (pre-release)
- The phase window's two views are saved as PNGs with every corrected copy, without
  asking: `<name>_phased_check.png` (Y after the correction) and `<name>_phased_lag.png`
  (phase lag of each pixel), one pair per map of a series
- One cross on the maps: while idle the cyan cross shows where the laser is; during a
  measurement the orange mark shows the pixel being measured (before, both showed at once)

## 1.2.9 (pre-release)
- Point sweep: **Finest: every SINE OUT step** - from a lowest to a highest power on the
  sample, a point at every 2 mV SINE OUT step (or every Nth), going up only; optionally
  measured on the way back down too. Readings per point set to 1 when ticked (back to
  before when unticked); the power list is greyed out. Point sweeps only
- Fixed (1.2.8): switching to the Point sweep tab updates the laser position again

## 1.2.8 (pre-release)
- **Line tab** (left, after Map): line scans with their own start / end (µm from the
  origin), step, **readings per point** (1 = like a map; more = mean + spread, error bars)
  and series (bias / power). A line drawn on a map or the camera image ("Use this line")
  fills it in. Timing, laser power and preamp come from the Map and Instruments tabs
- **Live position**: the Map tab shows where the laser is now from the origin (updated while
  you move with the joystick), and a cyan cross on the maps follows it

## 1.2.7 (pre-release)
- Fixed: a scan could stop with "could not convert string to float" (seen in 1.2.5, the
  problem was there since 0.9): the two lock-ins were read at the same time and their
  replies on the shared GPIB bus could mix. They are now read one after the other, and a
  reply that still comes out garbled is read again (up to 3 times, noted in
  spcm_error.log) instead of stopping the scan

## 1.2.6 (pre-release)
- **Point and line cursors** (Draw ▾): values of the nearest measured pixel at a point or
  along a line (sampled at the scan step), shown on both maps, for every map of a series,
  uncorrected and phase-corrected; Save → CSV + info.txt in `<name>_cursors/`
- Phase window: **Phase lag** view - how many degrees each pixel is behind / ahead of the
  common phase (where the signal is clear), and what 1° means in µs at the chopping
  frequency (read from the lock-in)
- Fixed: the phase sign rule now follows the previous map right away (1.2.5 only picked up
  the last angle after restarting the program)

## 1.2.5 (pre-release)
- Phase: strong pixels whose phase doesn't fit are ignored (spikes); the sign follows the
  angle used last time (same convention across files while the lock-in phase is untouched),
  else most of the signal positive; series: each map checked on its own, warning if the
  phase differs between maps

## 1.2.4 (pre-release)
- **Phase correction after every map and line scan** (instead of autophasing the lock-in):
  the phase is found from the whole measurement (pixels well above the noise lie on one
  line through zero in the X–Y plane), one angle for the map / the whole series, X positive
  at the strongest pixel. Saved as a separate copy `<name>_phased.h5` + CSV + PNG in the
  same folder; the measurement is unchanged. A window shows the X–Y cloud and Y after the
  correction (should be noise); optional substrate background from a box you put on the map;
  angle can be typed. File → *Phase correction of the shown map / line…* to redo it

## 1.2.3 (2026-10-19)
- **First pixel of each line** (step scans): the stage now stops one step before the line,
  waits there, and then steps onto the first pixel like onto any other pixel. Before, the
  first pixel came right after the long move back from the end of the previous line and
  could still carry some of it
- **Auto range up** (Instruments tab → Lock-ins, off by default): when the photocurrent
  signal goes above 80 % of full scale, the photocurrent lock-in switches to the next less
  sensitive range and measures that pixel again (fly scans: the line, point sweeps: the
  point). Only ever up, never back down; each change pops up and is saved with the data.
  For long measurements you're not watching
- **Set the laser power before each scan** now also works in bias series (maps and
  lines): the power is set again after every bias change, before that map or line; the
  power reached is saved with each map

## 1.2.2 (pre-release)
Safety and data fixes after a code review:
- **Stage arrival**: a move that stops off target is sent once more; if the stage is still
  more than 1 µm away, the measurement stops with a clear message (before, it was accepted
  after 3 s and only the line-end check could catch it)
- **Closing the window during a measurement** waits until it has really stopped (bias back
  to 0, SINE OUT back, data saved) before disconnecting the instruments
- **Fly scans**: Stop halts the stage at once; a pixel that got no reading is no longer
  filled in from its neighbours: the line is scanned again once, and if pixels are still
  missing they stay empty and you get a warning
- Stop reacts at once during pixel waits (step scans) and I–V waits
- Bias ramps remember the last voltage really sent, also when a step fails

## 1.2.1 (pre-release)
- **Calmer window**: left tabs Map / Point sweep / I–V / Instruments (Start runs the open
  tab); right tabs Maps / Camera / one **Plot** tab (line scans, point sweeps, I–V) /
  Experiment notes
- One **Draw ▾** menu (scan frame, line, resolution line) next to the tab names; "Scan this
  frame / line" only appear once something is drawn
- Explanations behind small **?** buttons instead of grey text
- Plus everything in 1.2.0 (camera calibration, line scans, bias / power series, point
  sweeps) and 1.1.1 (quick I–V, resolution, fixes)

## 1.2.0 (pre-release)
- **Camera calibration**: one button; then double-click the camera image to move the laser
  there, and draw scan frames and lines on the live image (they stay on the sample when
  the stage moves)
- **Line scans**: draw a line on a map or on the camera image, scan along it at any angle
- **Series**: one map or line per **bias** or per **laser power on the sample** (each set
  with 2 mV SINE OUT steps in one direction, power reached saved; SINE OUT back in 2 mV
  steps afterwards) - replaces the bias box; gives a distance × bias / power image for lines
- **Point sweeps** on one spot: power, bias or bias × power; mean and spread of N
  readings; linear and log-log plots with the fitted exponent α; bias–power map
- Lists accept `1:100:*2` (multiply each step); the stop value is always included
- **Measure resolution** refuses lines that don't cross a clear edge
- All new measurements are saved like maps: own folder, name pattern, numbers-only CSV

## 1.1.1 (pre-release)
- **Set power now** respects *Never set SINE OUT above* from Settings (it used the
  default 5 V before - also in 1.0)
- While a measurement runs, the window no longer talks to the stage or the lock-ins
  (Refresh, Set origin, Read z, double-click a map): two programs asking at once can mix
  up the replies
- Double-click on a map and *Go to start position* ask before moving more than 0.5 mm
- Settings window: *Save and restart* keeps everything else remembered in config.yaml
- Focus (z) moves wait until z is really at the target (more reliable autofocus)
- *Stop & discard* no error if the measurement finishes meanwhile; no empty .h5 when a
  scan stops before its first line
- Quick I–V: plot follows the light/dark theme; no invert option (use the SR570's INVERT)
- Fits smaller screens: map tools on a second row, side panel scrolls sideways if needed

## 1.1.0 (pre-release)
- **Quick I–V**: DC current vs SR570 bias through AUX IN 1 of the photocurrent SR830;
  cycles 0 → max → min → 0; SR570 sensitivity entered by hand; reminders to re-cable and
  re-set the SR570 when switching between maps and I–V; stops above 10 V SR570 output
- **Measure resolution**: line across an edge on the reflection map → edge fit, FWHM,
  10–90 % width, derivative + Gaussian, diffraction limit; re-measures perpendicular to
  the edge; saves PNG/CSV/TXT next to the map

## 1.0.0 (2026-10-15)
First full release: 0.9.1, tested on the microscope. No changes to the program since 0.9.1;
adds the licence (MIT) and the documentation (HOW_IT_WAS_BUILT.md, AGENTS.md).

## 0.9.1 (2026-10-15) - fixes after testing 0.9.0 in the lab (pre-release)
- **Laser power setting**: 2 mV steps in one direction only (never up and down) until the
  power is within the tolerance; **Abort** button for *Set power now*; *Stop & save*
  aborts the power setting before a scan (nothing is saved)
- **One folder per measurement** with the .h5, the PNG(s) and the CSV folder inside
- **File names** from a pattern, default `sample_date_size_step_power_number`
  (e.g. `flakeA_20261015_20x20um_500nm_10uW_001`); fields can be added from a list,
  including the Experiment notes quantities
- **CSV**: only the lock-in values (no x/y row and column); the axes are in info.txt
- **.h5** (for Origin): no stage-coordinate datasets; `/power_log` is one 2-column dataset
  (time in s, power on the sample in µW). Older files still open.

## 0.9.0 (2026-10-14) - pre-release
First version in the repository. Includes:
- Settings: your own `config.yaml` is created on first start from `config.default.yaml`
  and is never touched by updates (unzip or pull over the old folder safely)
- Camera: the Thorlabs camera package is found automatically in the ThorCam installation,
  so the camera now also works in SPCM.exe and without the pip step
- README: updating = Download ZIP, unzip over the old folder, double-click Install SPCM.bat
- Step and Fly scans with the Märzhäuser Tango; two SR830s (photocurrent/photovoltage + reflection)
- SR570 (with bias sweeps) / SR560 / lock-in-only signal conversion (× √2, peak amplitude)
- Origin (0, 0) at the current stage position; axes matching the joystick; rotated scans;
  draw a frame on a map and scan it
- Safety: no moves outside the scan area, arrival check, distance confirmation, stage log
- PM100USB power meter: live reading, fixed range, meter/sample ratio, set the laser power
  before a scan via the lock-in SINE OUT
- CS165CU camera: live view, flip, autofocus on the laser spot (Tango z)
- Tidy .h5 files (one group per map), CSV export, publication-style PNG per map
- Experiment notes, dark theme, number fields with SI prefixes
