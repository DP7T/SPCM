# Changelog

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
