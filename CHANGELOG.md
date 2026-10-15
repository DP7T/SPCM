# Changelog

## Unreleased (after testing 0.9.0 in the lab)
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

## 0.10.0 (2026-10-15) - pre-release, in testing
- **Point sweeps** on one spot: power, bias, or bias × power (bias outer). Powers set with the
  2 mV SINE OUT step closest to the target; N readings per point with mean and spread;
  live linear and log-log plots with the fitted exponent α; bias–power map; .h5 + CSV + PNG
- **Line scans**: draw a line on a map or on the camera image, scan along it at any angle;
  with a bias list you get a distance × bias image
- **Camera calibration**: one button; afterwards double-click the camera to move the laser,
  and draw scan frames and lines on the live camera image
- Lists accept `1:100:*2` (multiply each step) and always include the stop value
- `tools/selftest.py`: tests everything in simulation; runs on GitHub for every change

## 0.9.0 (2026-10-14) - in testing
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
