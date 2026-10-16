# SPCM – scanning photocurrent microscope control

Hardware: Märzhäuser Tango Desktop stage · SR830 #1 (photocurrent, GPIB) · SR830 #2 (reflection, GPIB) · optional bias from the SR570 preamp (RS-232), an SR830 AUX OUT or a Keithley 24xx.

Only a few things are under software control. **Sensitivity, time constant, phase, reference, filters and reserve stay on the SR830 front panels.** The program only reads them, and it stores τ and sensitivity with every map. The Tango joystick works between scans and is switched off only while a scan runs.

## Getting SPCM and updates (from github.com/DP7T/SPCM)

**Option A: unzip and double-click (recommended)**
1. On github.com/DP7T/SPCM press the green **Code** button → **Download ZIP**.
2. Unzip it, for example into `Documents`. You get a folder `SPCM-main`.
3. In that folder, double-click **Install SPCM.bat**. The first time it installs Python and the libraries (a few minutes); it creates the **SPCM** icons on the Desktop.
4. To update: download the ZIP again, unzip it to the **same place** (say *Replace* when Windows asks) and double-click **Install SPCM.bat** again. Your settings (`config.yaml`) and your `data` folder are kept.

**Option B: SPCM.exe (no Python at all)**
1. Open **Releases** (right side of the repository page) and download `SPCM-v…-windows.zip`.
   Each release says in its name what it is: **stable** (use it for measurements),
   **testing** (new features to try) or **old** (replaced, no need to download).
2. Unzip it and start **SPCM.exe**. To update, unzip the new one over the same folder.

The camera works with both options, as long as ThorCam is installed on the PC: SPCM finds the Thorlabs camera package inside the ThorCam installation by itself (the first start with the camera takes a few seconds longer while it unpacks it into `%LOCALAPPDATA%\SPCM\ThorlabsSDK`).

Your own settings live in `config.yaml`, which is created on the first start from `config.default.yaml`. Updates never overwrite it.

## Install (once)

1. Install the device drivers: VISA/GPIB, Optical Power Monitor, ThorCam, the Märzhäuser driver and the USB-serial adapter driver.
2. Get the program folder, as in *Getting SPCM and updates* above.
3. Double-click **`Install SPCM.bat`**. It installs Python and all the libraries, adds the camera package if ThorCam is installed, and creates two icons on your Desktop:
   - **SPCM** for real measurements
   - **SPCM (simulation)** to try the program without any hardware
4. Start **SPCM**. The first time, it asks whether to set up the hardware. In **Settings** you pick each device's port from a drop-down list of what's connected. **Test SR570** there checks the RS-232 link.

You can open Settings again at any time from **Hardware → Settings…**. If a device isn't found at startup, the program offers **Settings**, **Try again** or **Simulation mode**.

Running `Install SPCM.bat` again is safe. Do it after installing ThorCam later, or if you move the folder.

Unexpected errors are shown in a window and written to `spcm_error.log` in the program folder.

## SR570 bias (RS-232 via a USB adapter)

The SR570 only listens on RS-232. It never answers, so the program can't read back the bias you set on the front panel. On its first use in a session, the program sets the bias to 0 V, switches it on, and then ramps. **Before running a bias sweep, set the front-panel bias to 0 V or off.** At the end of the sweep the bias is ramped to 0 V and switched off.

Getting the link working:

1. **Cable:** the SR570 has a DB-25 connector and acts as DCE. You need a *straight-through* DB-9 (female) → DB-25 (male) cable or adapter, *not* a null-modem cable.
2. **Driver (Windows):** plug in the USB adapter and open Device Manager → *Ports (COM & LPT)*.
   - If it shows as "Prolific USB-to-Serial Comm Port (COM4)" with no warning, it works. Note the COM number.
   - If it shows a yellow ⚠ with "PL2303HXA PHASED OUT SINCE 2012…" or Code 10, the adapter uses an old or counterfeit Prolific chip, and recent Windows drivers refuse it. You can install the older Prolific driver (v3.3.2.x) and select it manually: *Update driver → Browse my computer → Let me pick*. The simpler fix is to **buy an FTDI-chip adapter**, such as one with an FT232R chip. Those install automatically and are much more reliable in the lab.
3. **Test:** disconnect your device from the SR570 input. In **Hardware → Settings…**, choose the SR570's COM port and press **Test SR570**. The front-panel bias should step to +0.5 V, then −0.5 V, then turn off.
4. Set **Bias source** to *SR570* and press **Save and restart**.

If the SR570 isn't plugged in, the program still starts. It warns you and runs without bias control.

## Using it

The window: on the left the tabs **Map**, **Point sweep**, **I–V** and **Instruments** (the Start button starts what is on the open tab); on the right **Maps**, **Camera**, **Plot** (line scans, point sweeps and I–V curves) and **Experiment notes**. Small **?** buttons explain each part.

The left side has two tabs:
- **Scan**: scan area, focus, timing, bias and save.
- **Instruments**: photocurrent amplifier, lock-in readout and laser power.

**Start/Stop** and the progress bar are always visible underneath. The laser power on the sample is also always shown in the status bar at the bottom right. **View → Dark theme** switches between light and dark.

- **Number fields** show their unit in the label: position and size in **µm**, step in **nm**, waits in **ms**. A plain number is in that unit, so `300` as the step means 300 nm. `k` multiplies by 1000, so `1k` as the step means 1000 nm = 1 µm. `u`/`µ`, `n` and `m` are absolute, so `1u` as the step means 1 µm, and `500n` as the size means 0.5 µm. `1e-1`, `0.1` and a decimal comma (`0,1`) also work. Inside the bias list, commas separate the values.
- **Plot scales** pick the unit prefix by themselves (… 100 nA → 1 µA …), and the colour Min/Max boxes show values like `-2.1 nA`.

- **Origin (0, 0):** drive to the spot with the joystick and press **Set origin here (0, 0)**. That spot becomes (0, 0). The map axes, the CSV files, and the *Start x/y* fields are all measured from it. *Start 0, 0* means the scan starts exactly at the origin; type e.g. `5` to start 5 µm away. When the program opens, the origin is set to wherever the stage is.
- **Directions:** the program's x and y point the way the joystick moves, which is the opposite of the Tango's own position numbers. This only affects the program, not the joystick or the stage. You can change it in Settings under *Program axes vs stage*.
- By default the origin is the **start corner** (the first pixel), and the map extends from it in the **Scan towards** direction. Choose *Centre of the map* instead to put it in the middle.
- **Go to start position** moves the stage back to that point at any time. The stage also returns there by itself when a scan finishes or is stopped.
- **Wait at each pixel:** *Auto* (the default) waits until both lock-ins have settled to 99 %. It uses each one's τ **and filter slope** from the front panel: 5 τ at 6 dB, 7 τ at 12 dB, 9 τ at 18 dB and 10 τ at 24 dB/oct. Untick Auto to set the number of τ yourself. Waiting too short makes each pixel still show part of the previous one, which looks like a zig-zag between lines.
- **Serpentine** is off by default, so every line runs in the same direction. Each line's first pixel is also approached from the scan direction, which removes stage backlash. Turn serpentine on only if you need the speed and see no zig-zag.
- **Photocurrent amplifier:** tell the program what sits in front of the photocurrent lock-in. The map shows the converted values, and the data file keeps both the raw lock-in values (`photocurrent`) and the converted ones (`photocurrent_calibrated`, with the formula and unit attached):
  - *Lock-in only*: the lock-in value.
  - *SR570 current preamp*: value × sensitivity, in amperes. Choose the same sensitivity as on the SR570 front panel.
  - *SR560 voltage preamp*: value ÷ gain, in volts. Pick the gain from the list, matching the SR560 front panel.
  - In every case the value is also multiplied by **√2**. The SR830 reads rms, and this turns it into the peak amplitude.
- **Series** (Map tab): one map per value of
  - **Bias** (V): only with the SR570 current preamp, because the bias comes from the SR570. Enter `0, 0.5, 1` or `-2:2:0.5`. The bias ramps gently between values and returns to 0 V at the end, when you press Stop, and when an error occurs.
  - **Laser power on sample** (µW), e.g. `5, 10, 20` or `1:100:*2`: before each map the SINE OUT is moved in 2 mV steps (one direction) to the power closest to each value, at the scan's start position; the power reached is saved with that map. At the end the SINE OUT goes back in 2 mV steps. Needs the power meter.

  The same series is used for line scans.
- **Scan mode** (Timing box):
  - **Step** stops at every pixel, waits, then reads. It's the most accurate, but every stop costs time.
  - **Fly** moves the stage along each line at constant speed while both lock-ins are read continuously. The speed is set so every pixel still gets its wait time, and the lock-in delay is corrected so left-going and right-going lines line up. It is usually several times faster.
  - At the start of a fly scan the program briefly measures the stage speed. Afterwards the stage speed is put back to normal.
- While scanning, the status bar shows where the time goes, e.g. *"per pixel: move 180 ms, wait 9 ms, read 12 ms"*.
- Each map is saved to the `.h5` file as soon as it finishes, so a crash loses at most the map in progress. When the scan ends, **CSV files** are written as well (see below).
- **Experiment notes** tab: a table of conditions (polarization, temperature, pressure, objective, modulation frequency, SR570 sensitivity, gate voltage, …) plus free notes. Add or remove rows as you like and write units with the values. Everything is saved in the scan file under `experiment/` and remembered for the next scan. Changes you make while a scan is running are saved too.
- **Colour scale** (under each map):
  - **Auto** fits the colours to the data.
  - **Symmetric ±** centres the scale on zero, so positive and negative photocurrent get equal weight.
  - **Manual** uses the scale you set: click the **Min** or **Max** box, type a value (e.g. `-2e-9`) and press Enter. Dragging the colour bar also switches to Manual.

  The scale you choose stays the same for the next scan.
- **Autofocus** (Focus box, uses the Tango z axis and the camera):
  1. In the Camera tab, drag the cyan cross onto the laser spot. This is needed once; the position is remembered.
  2. Press **Autofocus**. z moves through ± the search range (5 µm by default) in coarse steps, then in fine steps around the best point, and stops where the image around the spot is sharpest. This takes a few seconds, and the button turns into **Cancel**.

  Details:
  - It works with the lamp off (laser spot only) or on.
  - It only runs when you press the button, never during a scan.
  - Every position is approached from below, so backlash in the z drive doesn't shift the result.
  - The status bar shows where focus ended up. It warns you if the best point was at the edge of the range (run it again or widen the range) or if the camera saturated (lower the exposure).
  - To protect the objective, you can set absolute `z_min` / `z_max` limits in `config.yaml`.
- **Camera view**: the *Flip left-right* and *Flip up-down* boxes in the Camera tab make the image match how you see the sample. They are remembered.
- **Angle (°)** (Scan area) rotates the scan frame counter-clockwise around the start position. This works in Step mode only.
- **Draw a frame on a map:** choose **Draw ▾ → Scan frame** above the maps. A cyan frame appears on both maps:
  - Drag it to move it.
  - Drag the square handles to resize it.
  - Drag the round handle to rotate it.

  Then press **Scan this frame** (it appears once the frame is drawn): the start position, size, direction and angle are filled in for you. Check the step and press Start scan. It also works on a scan you opened from a file, as long as the origin is the same spot.
- Double-click a map to park the laser on that spot, for example before a point sweep or a quick I–V.
- If the photocurrent lock-in overloads, a warning appears in the status bar.

## Point sweeps (power, bias, or both) on one spot

1. Park the laser on the spot: double-click a map, double-click the calibrated camera image, or use the joystick.
2. Open the **Point sweep** tab on the left and choose the sweep:
   - **Power:** a list of powers **on the sample** in µW, e.g. `1:50:5` (1, 6, 11 … 46, 50), `1:100:*2` (1, 2, 4 … 64, 100: evenly spaced on a log scale) or `1, 2, 5, 10`. Each power is set with the 2 mV SINE OUT steps (always in one direction, like *Set power now*) that come closest, checked with the power meter, and the power actually reached is recorded.
   - **Bias:** a list of biases through the SR570, like the bias maps.
   - **Bias × power:** for every bias (outer loop), the whole power list (inner loop).
3. **Readings per point** (10 by default): after the lock-ins have settled (the same wait as in Timing), this many readings are taken about one time constant apart. The mean is saved, and their standard deviation is the error bar.
4. Press **Start sweep** (the Start button changes its name on this tab). Stop & save / Stop & discard work as for maps.

The **Plot** tab on the right shows the result live:
- signal vs power (or bias), with error bars;
- for power sweeps, a **log-log plot** with a fitted power law |S| = A·P^α and the value of α (one per bias);
- for bias × power, one curve per bias (with a legend) and a **bias–power map** coloured by the photocurrent or photovoltage.

Afterwards the SINE OUT goes back to where it was in 2 mV steps (untick *Put the SINE OUT back afterwards* if you don't want that) and the bias ramps back to 0 V. Like every measurement it gets its own folder (`powersweep`, `biassweep` or `biaspowersweep` in the name) with the .h5, a PNG with the same plots, and a `_csv` folder with `sweep.csv` (numbers only, one row per point, every channel with its spread; the columns are listed in `info.txt`) and, for bias × power, `photocurrent_X_matrix.csv`.

## Line scans

1. Choose **Draw ▾ → Line to scan along** above the maps. A yellow line appears on both maps; drag its ends (or the whole line) where you want it. The status bar shows its length and the number of points.
2. Press **Scan this line** (it appears once the line is drawn). The laser steps along the line at any angle, using the **Step**, **Timing** and **Series** from the Map tab and the laser-power setting from the Instruments tab.
3. The **Plot** tab on the right shows the signal and the reflection vs the distance along the line, one curve per value. With a **Series** of biases or laser powers you also get the **distance × bias** (or **distance × power**) image.

You can draw the line on the camera image too, once the camera is calibrated. Line scans get their own folder too (`line` in the name): the .h5 has the same layout as maps, with 1-row images plus `distance_um` and the x, y of every point; a PNG; and `_csv/line.csv` (numbers only, columns listed in `info.txt`).

## Camera calibration

Once calibrated, the program knows which stage position every camera pixel shows. Then you can:
- **double-click the camera image** to move the laser to that spot;
- **draw a scan frame** or **a line** on the live camera image, and press *Scan this frame* or *Scan this line*. The drawings stay on the same spot of the sample when the stage moves.

To calibrate (once, and again after changing the objective or moving the camera):
1. Focus, and put the **cyan cross on the laser spot**.
2. Go to a place with clear structure in x **and** y, such as the corner of an electrode or a flake.
3. Set **move (µm)** so the image moves by roughly 5–20 % of its width: about 5 µm with a 50–100× objective, 20 µm with 10×.
4. Press **Calibrate camera**. The stage moves a few times in x and in y (about 10 s) and comes back. The result appears next to the button, for example *120 nm per pixel, rotated 0.3°*, and is saved.

If the image couldn't be matched (out of focus, no structure, move too small or too large), you get a message and the old calibration stays.

## Quick I–V (DC, without moving the probes)

The **I–V** tab (left) measures the DC current against the bias from the SR570, with the laser spot wherever it is.

**Set-up:** the SR570 output goes to **AUX IN 1** on the rear of the photocurrent SR830 instead of the lock-in input, and the SR570 runs with **filter OFF** and **LOW DRIFT**. The program reminds you each time you switch between maps and I–V, both ways. It can't check these settings, because the SR570 can't be read.

**Settings:**
- **Max (V)**, **Min (V)**, **Step (mV)**: one cycle is 0 → max → min → 0. **Cycles** repeats it.
  - A plain number is in the field's unit: max/min in V, step in mV. `10` as the step means 10 mV, and so does `10m`.
  - The SR570 sets the bias in 1 mV steps, so values are rounded to 1 mV.
- **Wait per point (ms)** and **Readings per point**: after each bias step the program waits, then averages that many AUX IN readings.
- **Sensitivity**: the SR570 front-panel sensitivity. The current is AUX IN voltage × sensitivity, so this must match the front panel.
- **Sign:** if a positive bias gives a negative current, use the SR570's own **INVERT** button.

Press **Start I–V**, the Start button on this tab. The **Plot** tab on the right shows the curve live, one colour per cycle.

**Safety:**
- The bias ramps back to 0 V at the end, on Stop and on errors.
- If the SR570 output goes above 10 V, the sweep stops. The AUX IN only reads up to ±10.5 V. Use a less sensitive SR570 setting or a smaller bias range.
- The laser stays as it is, so the current includes the DC photocurrent. Block the beam for a dark I–V.

**Files:** like every measurement, the I–V gets its own folder, with `IV` in the name:
- `<name>.h5` holds the dataset `iv`, one row per point: bias (V), current (A), cycle, time (s), AUX IN (V).
- `<name>_csv/iv.csv` has the same columns, numbers only.
- `<name>.png` is the plot.

## Measuring the resolution

1. Measure or open a reflection map that crosses a sharp edge, such as a gold electrode. Use a step well below the spot size, ideally ≤ 100 nm.
2. Choose **Draw ▾ → Resolution line** above the maps. An orange line appears on the reflection map. Drag it across the edge. The side handle widens the band that is averaged, which helps with noisy maps.
3. A window shows the reflection profile with an edge fit, and its derivative with the matching Gaussian, which is the spot's line profile:
   - **FWHM** of the spot and the **10–90 % edge width**;
   - the **diffraction limit** 0.51 λ/NA for comparison. Type the objective's **NA**; λ is taken from the power meter wavelength.
   - If the line crosses the edge at a slant, the program measures along the edge's normal instead, so the width isn't over-estimated.
   - It warns you if the map step is too coarse for the spot, or if the edge is too weak or noisy for a reliable fit.
4. The window updates as you move the line. **Save** writes `<map name>_resolution_1.png`, `.csv` (distance, reflection, fit; numbers only) and `.txt` (the results) into the map's folder.

## Power meter (PM100USB) and camera (CS165CU)

Both are optional. Turn them on in **Hardware → Settings…**. If one can't be opened, the program starts anyway and shows a warning.

The laser power is read at the **start and end of every map** and saved (in µW, from the meter and on the sample) as attributes of each map group (see *Data file layout*).

In the window:
- **Laser power** is always shown live, including between scans. The large number is the **power on the sample**: the meter reading divided by the **Meter / sample ratio** (for example 3.18). The raw meter reading is shown below it. The ratio is remembered, and both powers are saved with every map and every line.
- **Range:** the power meter uses a **fixed range** (1 mW by default) so that its gain doesn't change during a measurement and the readings stay linear. Pick the smallest range above your laser power from the *Range* list. Your choice is remembered. If the power goes above the range, the reading turns red with **OVER RANGE**. The saved data records the range and an `over_range` flag. *Auto* is still available, for example while you are adjusting the laser.
- The **Camera** tab shows a live view with an exposure setting. Drag the cyan cross onto the laser spot once to mark it. Camera pictures are not saved with the scans.

**Setting the laser power.** In the power box, enter **Target on sample (µW)** and a **Tolerance** (± 1 % by default):
- The program reads the power on the sample (meter ÷ ratio) and changes the lock-in **SINE OUT** in **2 mV steps, always in one direction**: up if the power is too low, down if it is too high. It never goes up and down. It stops as soon as the power is within the tolerance. If one 2 mV step jumps past the tolerance band, it keeps the closer of the last two steps.
- **Set power now** does this immediately. While it runs, the same button reads **Abort power setting**: press it to stop, and the sine stays where it is.
- Tick **Set the laser power before each scan** to do it at the scan's start position every time you press Start. The scan only begins once the power is right. In a **bias series** (maps or lines) it is set again before every bias, after the bias has changed; the power reached for each one is saved with that map. **Stop & save** aborts the power setting; then nothing is measured or saved.
- Large changes take a while, because every 2 mV step is read: about 0.1–0.2 s per step, so 1 V takes 1–2 minutes.
- Nothing is changed during the scan, but the power is still recorded after every line and at the start and end of each map.
- If the target can't be reached, the scan doesn't start and you're told why: either the meter sees no light (laser off, shutter closed), or the target is above what the maximum sine amplitude gives. The amplitude never goes above **"Never set SINE OUT above"** in Settings (5 V by default; set a lower value to protect your laser). Settings also has which SR830's SINE OUT drives the laser.
- The power meter also sees some light reflected back from the sample. So set the power with the start position on a uniform area, such as substrate.

**PM100USB setup (once):**
1. Install Thorlabs **Optical Power Monitor**, which provides the USB driver.
2. No driver switching is needed. The program first tries NI-VISA. If the meter isn't visible there, it uses Thorlabs' own driver (`TLPM_64.dll`), which comes with Optical Power Monitor. **Close the Optical Power Monitor program before starting SPCM**, because only one program can use the meter at a time.
3. Tick the power meter in **Settings** and enter your laser wavelength. The PM100USB has no front panel, so the program sets the wavelength correction.

**CS165CU setup (once):**
1. Install **ThorCam**. It comes with the camera drivers and the *Scientific Camera Interfaces* folder.
2. Run **Install SPCM.bat** (again). It finds ThorCam's `Scientific_Camera_Interfaces.zip` and installs the camera package by itself. Look for **"Camera package installed."** in its output.
3. Tick the camera in **Settings**.
4. **Close ThorCam before starting the program.** Only one program can use the camera at a time.


## Opening your data

**Every measurement gets its own folder** inside the save folder, named like the measurement. By default the name is

`sample_date_size_step_power_number`, for example `flakeA_20261015_20x20um_500nm_10uW_001`

- **File name** (Save box): pick another pattern from the list or type your own. **Add ▾** inserts fields: sample, date, time, size, step, power on the sample, bias, scan mode, type and the running number `{n}`. It also offers every quantity from the **Experiment notes** tab, such as `{Temperature}` or `{Polarization}`. The grey line under it shows the next name.
- The power in the name is the target power if *Set the laser power before each scan* is ticked, otherwise what the meter reads at the start. `{n}` counts up (001, 002 …) so a name is never used twice.

Inside each measurement folder:
- **`<name>_map0.png`**: a ready-made figure for each map. On the left is the photocurrent or photovoltage map (blue–white–red, symmetric around zero, with a colour bar in nA, µV, etc.); on the right is the reflection map in grey. The title follows your setup: **Photocurrent** with the SR570, **Photovoltage** with the SR560, and with the lock-in alone it depends on its input (current input → photocurrent, A or A−B → photovoltage). **File → Save figure of shown map…** saves the map currently shown as PNG, PDF or SVG. It uses your Manual colour limits if you set them.
- **`<name>.h5`**: everything in one file (all maps, raw and converted, settings, notes, power log). Open it with:
  - SPCM itself: **File → Open saved scan…**
  - **Origin**: *Data → Import → HDF5*.
  - **HDFView**: free from the HDF Group; double-click any dataset to see it as a table or image.
  - **Python / MATLAB**: `h5py` or `h5read`.
- **`<name>_csv/`**: the same data as plain CSV files, which open in **Origin, Excel or any program**:
  - one folder per map (per bias value)
  - `photocurrent_X.csv`, `_Y`, `_R`, `_theta`: photocurrent, already converted, in A or V
  - `reflection_*.csv`: reflection as read
  - Each file holds **only the lock-in values**, as a matrix: one row per y, one column per x, the first row being the lowest y (as on screen). The x and y positions are listed in `info.txt`.
  - `info.txt` also holds all settings, experiment notes and laser powers, and `power_log.csv` the power on the sample after every line (time, power).
  - For an older .h5 file, open it and use **File → Export shown scan to CSV…**.

## Data file layout (`.h5`)

Every image in the file is a plain 2-D array (rows = y, columns = x), in the same orientation as on screen. In HDFView you can double-click any of them and choose *Image* or *Table*.

```
/info                       settings, instruments, conversion formula (attributes)
  /experiment               your experiment notes (attributes)
/map0            (or /map0_-0.5V, /map1_+0V … for a bias sweep, one group per bias)
  attributes:               bias, τ and sensitivity of both lock-ins, wait time,
                            laser power at start/end (µW, meter and on sample) …
  x_um, y_um                axes from the origin (0, 0)
  photocurrent_X / _Y / _R  converted signal (A or V; the 'unit' attribute says which)
  photocurrent_theta_deg
  reflection_X / _Y / _R, reflection_theta_deg
  raw/                      lock-in readings exactly as measured (rms)
/power_log                  2 columns: time (s), laser power on the sample (µW),
                            one row per scan line - plot column 2 against column 1
```

```python
import h5py
f = h5py.File("data/....h5")
pc = f["map0/photocurrent_X"][()]      # 2-D image, in A (SR570) or V (SR560)
x, y = f["map0/x_um"][()], f["map0/y_um"][()]
```

SPCM still opens files saved by earlier versions.

## Check these on your hardware

- **Tango commands** (`instruments.py`, `TangoStage`): the program uses `!moa`, `?pos`, `?statusaxis`, `!dim 1 1` (µm), `!joy`, `!a` and `!autostatus 0`, at 57600 baud. Compare these with the instruction set for your firmware. If your controller runs in LEP/ASI compatibility mode, switch it back to the native Tango command set.
- **Scan direction:** if the image comes out mirrored relative to the camera, swap the x and y signs in `TangoStage.move_to`/`position`.
- **Speed:** a pixel takes about the wait plus ~20–40 ms for the move and reads. If the stage rings after small steps, raise **Extra wait**.

## Disclaimer

This program was made for my own needs, based on my own experience. I make no claims about it
compared with any other programs, and I have no knowledge of whether other systems or programs,
which may serve larger or smaller purposes, are better or worse.

## How it was built

This app was built step by step with Claude (an AI assistant), using plain-language requests.
See **[HOW_IT_WAS_BUILT.md](HOW_IT_WAS_BUILT.md)** for a summary of every request and what it added.
To continue developing it with an AI assistant, start a new chat by giving it **[AGENTS.md](AGENTS.md)**.
