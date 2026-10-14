# How this app was built

This app was built with **Claude** (an AI assistant by Anthropic) over a few days, by describing what was
needed in plain language and testing each version on the real microscope. No code was written by hand.
The requests are summarized below in the order they were given, so others can see how the app grew and
build something similar for their own setup.

Requests are lightly paraphrased. Short quotes are the original wording.

If you want to continue developing SPCM with an AI assistant, give it **[AGENTS.md](AGENTS.md)** first.
That file describes the code, the conventions and the decisions behind them.

---

## 1. Describing the setup

> "I work with scanning photocurrent microscope spcm. I need a fresh program."

Claude asked a few questions first. The answers were:
- Python
- a Märzhäuser Tango Desktop stage
- lock-in detection
- a live map, a reflection image, bias/gate sweeps and a simulation mode

Then the hardware details:
- *"I have 2 sr830 for reflection and photocurrent"*
- *"I still prefer mostly analog controls"*: the program only **reads** the lock-in settings (τ, sensitivity, slope) and never changes them.
- An optional **SR570** current preamp that can apply bias over RS-232 through a USB adapter, and later a **CS165CU** camera and a **PM100USB** power meter.

**Result:** a PyQt app with a live photocurrent and reflection map and line profiles, step scans, bias sweeps through the SR570, HDF5 saving and a simulation mode with a virtual sample.

## 2. Getting it onto the lab PC

> "I need runnable app not code"

**Result:** `Install SPCM.bat`, which installs Python and the libraries if needed and creates Desktop icons for **SPCM** and **SPCM (simulation)**. Claude also explained which drivers were needed: NI-VISA (already there with LabVIEW), the Optical Power Monitor software and ThorCam.

Problems solved along the way, from screenshots:
- **Camera package not found:** the ThorCam zip has underscores in its name, so the installer's search pattern was fixed.
- **Colour-processing DLL not found:** the DLL folder is now found automatically, and a simple debayer is used as a fallback.
- **No "Driver Switcher" in the newer Optical Power Monitor software:** the power meter can now also be read through Thorlabs' own TLPM driver.
- **Cramped, unreadable layout:** scrollable tabs, a cleaner style and high-DPI support.

## 3. Scan area and navigation

> "Set center. Is it the corner as starting point or center of map? I also want button that says go back to initial scan position"

**Result:**
- The start is the scan corner.
- **Go to start position** button.
- Later, **Set origin here (0, 0)**, which makes the current stage position the origin. The program axes were also inverted for typed positions to match the joystick (*"only in typing tho, joystick is correct"*).

## 4. Correct units everywhere

> "If current preamp 570 is used, data must be multiplied by sqrt(2)·sensitivity of preamp. If voltage preamp is used must be divided by gain and multiplied by sqrt(2). And bias option is only available in current preamp"

**Result:**
- The maps show **photocurrent (A)** or **photovoltage (V)** as peak amplitude.
- The SR570 sensitivity and the SR560 gain are chosen from drop-down lists.
- The raw lock-in values are also kept in the file.

> "All number inputs should accept like 0.1, 1e-1 and 100m"

**Result:** every number field accepts SI prefixes. It took a few iterations to agree on the rules:
- The scan size is in µm and the step in nm.
- A plain number is in the field's own unit.
- `k` and `M` multiply.
- `m`, `µ`, `n` and `p` are absolute.

So `1000`, `1k` and `1u` all mean a 1 µm step.

The colour scale of the map got **Auto / Symmetric / Manual** (click Min or Max to type a value). The colourbar switches its prefix automatically (nA, µA, …).

## 5. Power meter and laser power

> "We gotta fix the range in powermeter cuz it is not linear otherwise"

**Result:**
- A fixed range and averaging for the PM100USB.
- A meter-to-sample ratio (*"divide by custom number like 3.18"*).
- Power is always saved in µW.
- Readings run in the background, because the first version read slowly.

Laser power control went through two versions:
- **First version:** drift correction during the scan, by changing the lock-in **SINE OUT** that drives the laser. In practice, a bright gold area in the beam path was read as drift.
- **Final version:** *"we should have power input and it should set sine out voltage based on pm reading … no drift correction, just check at the beginning and still keep recording the power"*. This became **Scan at this power** with a tolerance, and the power is logged line by line.

Also added: **experiment notes** (polarization, temperature, pressure …) saved with every scan.

## 6. Autofocus

> "Would it be possible to add autofocus in z" … "only button on demand"

**Result:** a button that sweeps the Tango z axis, coarse then fine, and maximises the sharpness of the laser spot on the camera.

## 7. Speed, artefacts and safety (testing on the real stage)

Each of these was found by scanning a real sample and sending a screenshot:
- **"Scan is too slow regardless of time constant"**: the wait per pixel now follows the lock-in filter slope (99 % settling: 5/7/9/10 τ for 6/12/18/24 dB). The two lock-ins are read in parallel. A **Fly** mode was added that scans each line at constant speed and bins pixels live.
- **Grating-like stripes in step mode**: the cause was serpentine scanning combined with stage backlash. Serpentine is now off by default and a backlash take-up was added.
- **Fly mode "does crazy moves"** and **"it goes to different far away place"**: the stage now refuses any move outside the scan area (plus a margin). It checks arrival, asks for confirmation before long moves, and logs every stage command to `spcm_stage.log`.
- **Last pixel of one line showing up at the start of the next**: a move now only counts as finished when the stage reports the target position, and the first pixel of each line waits twice as long.
- **Line profile not always updating**: fixed, and the profile can follow the current scan line.

Also added: **Stop & discard** next to **Stop & save**, and pop-up notifications.

## 8. Tidy data and figures

> "Data saving under bias. No plottable data and even in normal single scans h5 is not tidy."

**Result:**
- **HDF5 layout:** one group per map (or per bias), plottable 2-D datasets with their x/y axes, converted values next to the raw ones, all settings in `/info`, and no camera images.
- **CSV export:** written next to the `.h5`, with axes relative to the origin.

> [a reference figure] "I also want output picture like this, whether it is pv or pc measurement should be obvious"

**Result:** a publication-style PNG after each scan, with the signal in blue–white–red on a symmetric colourbar plus the reflection image in grey. The title is *Photocurrent* or *Photovoltage*, chosen from the preamp in use or the lock-in input.

## 9. Rotated scans and drawing a frame

> "Lets think about if we can add rotation to scan or is it possible to draw frame and scan"

**Result:** a scan **angle**, and **Draw scan frame** on a previous map. The frame can be moved, resized and rotated, then **Scan this frame** starts a scan of exactly that area.

## 10. Repository, releases and updates

> "From now onwards we will make a new repository for spcm … for good versions we can release executables"

> "Either exe must have camera, or it should work as easy as unzipping the zip and double clicking bat file"

**Result:**
- The code moved to GitHub.
- **Download ZIP**, unzip and double-click `Install SPCM.bat` is the update route. Your `config.yaml` settings and your data are kept, because the settings are created from `config.default.yaml` on the first start.
- A GitHub Action builds **SPCM.exe** for each release.
- The camera package is found automatically inside the ThorCam installation, so the camera also works in the exe.

---

## 11. Point sweeps, line scans and camera calibration

The three ideas from earlier, built in the order 3 → 2 → 1. Before starting, Claude asked four short questions:
- Power plot: both linear and log-log with a fitted slope.
- Each point: the average of N readings with the spread as an error bar.
- Combined sweep: *"Bias outer is fine but it should give out plot as both in same figure with legend labels of bias. And 2d plot where one axis is bias other is power and color is pc or pv intensity"*.
- Order: point sweeps first.

And one rule added while it was being built:
> "2mV in sine out in power sweep is good enough step size. finer is not possible. coarser is not good. so don't give option in that"

**Result:**
- **Point sweeps** (power, bias, bias × power) on the spot where the laser is, with live plots, the fitted exponent α, and a bias–power map.
- **Line scans** drawn on a map or on the camera, with a distance × bias image for bias lists.
- **Camera calibration** with one button. Afterwards you can double-click the camera image to move the laser there, and draw frames and lines directly on the live image.

Claude also added a **self-test** that runs everything on simulated hardware, including the window (off-screen, with screenshots). GitHub runs it on Windows after every change.

## Tips for building your own

- **Describe your exact hardware** (models, how each one is connected), and **say which settings you want to keep on the front panels**.
- **Ask for a simulation mode first.** Most of the GUI can be tested without the microscope.
- **Test on the real setup after each version and send screenshots.** Stripes, wrong moves and slow scans were all found that way.
- **Agree on units and conventions early**: signal conversion, axis directions and number input.
- **Keep a stage log and safety limits** whenever software moves a motorised stage.
