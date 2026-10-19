# Changelog

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
