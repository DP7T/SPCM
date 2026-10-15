# Changelog

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
