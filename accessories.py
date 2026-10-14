"""Accessories: Thorlabs PM100USB power meter and CS165CU camera.

Both are read once at the start and once at the end of every map (saved in
the .h5 file under accessories/), and shown live in the window while idle.

Thread safety: each device has a lock, because the scan thread (snapshots)
and the window (live view) may both use it.
"""
import os
import sys
import threading
import time

import numpy as np

from instruments import Accessory


# --------------------------------------------------------------------------
# Thorlabs PM100USB
# --------------------------------------------------------------------------
class PM100USB(Accessory):
    """PM100USB via VISA/SCPI.

    Needs the Thorlabs Optical Power Monitor software (for the USB driver),
    and the meter switched to 'PM100D (NI-VISA)' mode with the Thorlabs
    'Power Meter Driver Switcher'. Then it shows up as
    USB0::0x1313::0x8072::P2xxxxxx::INSTR.

    The PM100USB has no front panel, so the wavelength correction has to be
    set from software: give `wavelength_nm` in config.yaml.
    """
    name = "powermeter"
    sample_factor = 1.0   # meter power / power on sample

    def __init__(self, resource=None, wavelength_nm=None, averages=None,
                 range_W=None):
        """range_W: fixed upper range in W (keeps the response linear and the
        gain constant during a measurement), or None / "auto" for autorange."""
        import pyvisa
        rm = pyvisa.ResourceManager()
        if not resource:
            found = [r for r in rm.list_resources() if "0x1313::0x807" in r]
            if not found:
                raise IOError("No Thorlabs power meter found on VISA. "
                              "Is it in 'PM100D (NI-VISA)' mode? See README.")
            resource = found[0]
        self.inst = rm.open_resource(resource)
        self.inst.timeout = 3000
        self.inst.read_termination = "\n"
        self.inst.write_termination = "\n"
        self.lock = threading.Lock()
        self.idn = self.inst.query("*IDN?").strip()
        try:
            self.sensor = self.inst.query("SYST:SENS:IDN?").strip()
        except Exception:
            self.sensor = ""
        if wavelength_nm:
            self.inst.write(f"SENS:CORR:WAV {float(wavelength_nm)}")
        self.averages = 1
        if averages:
            self.set_averages(averages)
        self.inst.write("CONF:POW")
        self.wavelength_nm = float(self.inst.query("SENS:CORR:WAV?"))
        self.set_range(range_W)

    def set_range(self, range_W):
        """Fixed range (W) or None/'auto'. The meter picks the nearest range
        at or above the value; the range actually used is read back."""
        with self.lock:
            if range_W in (None, "", "auto", 0):
                self.inst.write("SENS:POW:RANG:AUTO ON")
                self.auto = True
            else:
                self.inst.write("SENS:POW:RANG:AUTO OFF")
                self.inst.write(f"SENS:POW:RANG:UPP {float(range_W):.6E}")
                self.auto = False
            self.range_W = float(self.inst.query("SENS:POW:RANG:UPP?"))
        return self.range_W

    def set_averages(self, n):
        """Samples averaged per reading (~3 ms each on the PM100USB)."""
        with self.lock:
            self.inst.write(f"SENS:AVER:COUN {int(n)}")
        self.averages = int(n)

    def power(self):
        with self.lock:
            return float(self.inst.query("READ?"))

    def over_range(self, p):
        return (not self.auto) and p > self.range_W

    def snapshot(self):
        p = self.power()
        # saved in µW (internally everything is in W)
        return {"power_uW": p * 1e6, "wavelength_nm": self.wavelength_nm,
                "range_uW": self.range_W * 1e6, "autorange": self.auto,
                "over_range": self.over_range(p), "time": time.time(),
                "sample_factor": self.sample_factor,
                "power_on_sample_uW": p / self.sample_factor * 1e6}

    def close(self):
        self.inst.close()


class PM100TLPM(PM100USB):
    """Same power meter through Thorlabs' own TLPM driver (TLPM_64.dll from
    Optical Power Monitor). Used automatically when the meter is in the
    default Thorlabs driver mode and NI-VISA can't see it, so no Driver
    Switcher is needed."""
    DLL_DIRS = [r"C:\Program Files\IVI Foundation\VISA\Win64\Bin",
                r"C:\Program Files (x86)\IVI Foundation\VISA\WinNT\Bin",
                r"C:\Program Files\IVI Foundation\VISA\Win64\Bin\TLPM",
                r"C:\Program Files\Thorlabs\Optical Power Monitor"]

    def __init__(self, wavelength_nm=None, averages=None, range_W=None):
        import ctypes as C
        self.C = C
        lib = None
        for d in [""] + self.DLL_DIRS:
            try:
                lib = C.CDLL(os.path.join(d, "TLPM_64.dll") if d else "TLPM_64.dll")
                break
            except OSError:
                continue
        if lib is None:
            raise IOError("Thorlabs TLPM driver (TLPM_64.dll) not found. Is Optical "
                          "Power Monitor installed?")
        self.lib = lib
        self.lock = threading.Lock()
        self.h = C.c_long(0)
        n = C.c_uint32(0)
        self._ok(lib.TLPM_findRsrc(self.h, C.byref(n)), "findRsrc")
        if n.value == 0:
            raise IOError("No Thorlabs power meter found (TLPM). Is it plugged in, "
                          "and is the Optical Power Monitor program closed?")
        name = C.create_string_buffer(1024)
        self._ok(lib.TLPM_getRsrcName(self.h, C.c_int(0), name), "getRsrcName")
        self._ok(lib.TLPM_init(name.value, C.c_bool(True), C.c_bool(False),
                               C.byref(self.h)), "init")
        self.idn = f"PM100 via TLPM ({name.value.decode(errors='ignore')})"
        if wavelength_nm:
            self._ok(lib.TLPM_setWavelength(self.h, C.c_double(float(wavelength_nm))),
                     "setWavelength")
        self.averages = 1
        if averages:
            self.set_averages(averages)
        wl = C.c_double(0)
        self._ok(lib.TLPM_getWavelength(self.h, C.c_int16(0), C.byref(wl)), "getWavelength")
        self.wavelength_nm = wl.value
        self.set_range(range_W)

    def set_averages(self, n):
        with self.lock:
            self._ok(self.lib.TLPM_setAvgCnt(self.h, self.C.c_int16(int(n))), "setAvgCnt")
        self.averages = int(n)

    def _ok(self, err, what):
        if err != 0:
            raise IOError(f"TLPM {what} failed (error {err})")

    def set_range(self, range_W):
        C = self.C
        with self.lock:
            if range_W in (None, "", "auto", 0):
                self._ok(self.lib.TLPM_setPowerAutoRange(self.h, C.c_int16(1)), "autorange")
                self.auto = True
            else:
                self._ok(self.lib.TLPM_setPowerAutoRange(self.h, C.c_int16(0)), "autorange")
                self._ok(self.lib.TLPM_setPowerRange(self.h, C.c_double(float(range_W))),
                         "setPowerRange")
                self.auto = False
            r = C.c_double(0)
            self._ok(self.lib.TLPM_getPowerRange(self.h, C.c_int16(0), C.byref(r)),
                     "getPowerRange")
            self.range_W = r.value
        return self.range_W

    def power(self):
        C = self.C
        with self.lock:
            p = C.c_double(0)
            self._ok(self.lib.TLPM_measPower(self.h, C.byref(p)), "measPower")
            return p.value

    def close(self):
        try:
            self.lib.TLPM_close(self.h)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Thorlabs CS165CU (and other Zelux / Kiralux / Quantalux) camera
# --------------------------------------------------------------------------
DEFAULT_THORCAM_DIRS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "SPCM", "ThorlabsSDK"),
    r"C:\Program Files\Thorlabs\Scientific Imaging",
    r"C:\Program Files\Thorlabs",
]
CAM_DLL = "thorlabs_tsi_camera_sdk.dll"
COLOR_DLL = "thorlabs_tsi_mono_to_color_processing.dll"


def _dll_folders(root):
    """All 64-bit folders under root holding Thorlabs camera DLLs, best first
    (a folder with both the camera and the colour DLL comes first)."""
    if not root or not os.path.isdir(root):
        return []
    hits = []
    for r, _sub, files in os.walk(root):
        low = {f.lower() for f in files}
        if CAM_DLL in low or COLOR_DLL in low:
            if "32" in os.path.basename(r) and "64" not in r:
                continue                       # skip 32-bit library folders
            hits.append((-(CAM_DLL in low) - (COLOR_DLL in low), r))
    return [r for _k, r in sorted(hits)]


def _add_dll_dir(dll_dir):
    """Make the camera SDK DLLs (and their dependencies) loadable."""
    folders = []
    for d in ([dll_dir] if dll_dir else []) + DEFAULT_THORCAM_DIRS:
        folders += [f for f in _dll_folders(d) if f not in folders]
        if any(os.path.isfile(os.path.join(f, COLOR_DLL)) and
               os.path.isfile(os.path.join(f, CAM_DLL)) for f in folders):
            break
    for f in reversed(folders):
        os.environ["PATH"] = f + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(f)
            except OSError:
                pass
    return folders[0] if folders else None


SDK_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "SPCM", "ThorlabsSDK")
THORLABS_ROOTS = [r"C:\Program Files\Thorlabs", r"C:\Program Files (x86)\Thorlabs"]


def _find_pkg_dir(root):
    """Folder that contains the 'thorlabs_tsi_sdk' Python package, or None."""
    for r, dirs, files in os.walk(root):
        if "thorlabs_tsi_sdk" in dirs and os.path.isfile(
                os.path.join(r, "thorlabs_tsi_sdk", "tl_camera.py")):
            return r
    return None


def ensure_thorlabs_sdk():
    """Make 'import thorlabs_tsi_sdk' work without pip: the Python package that
    comes with ThorCam is found in the ThorCam installation, unpacked once into
    %LOCALAPPDATA%\\SPCM\\ThorlabsSDK and added to the import path. Works the same
    from SPCM.exe and from the source folder."""
    import sys
    import zipfile
    try:
        import thorlabs_tsi_sdk  # noqa: F401  (already installed with pip)
        return True
    except ImportError:
        pass
    pkg = _find_pkg_dir(SDK_CACHE)
    if pkg is None:
        roots = [d for d in THORLABS_ROOTS if os.path.isdir(d)]
        zips = []
        for root in roots:
            for r, _d, files in os.walk(root):
                for fn in files:
                    low = fn.lower()
                    if low.endswith(".zip") and ("python_sdk" in low or
                                                 ("camera" in low and "interfaces" in low)):
                        zips.append(os.path.join(r, fn))
        # unpack: the outer SDK zip contains the Python package zip
        os.makedirs(SDK_CACHE, exist_ok=True)
        for z in sorted(zips, key=lambda p: "python_sdk" not in p.lower()):
            try:
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(os.path.join(SDK_CACHE, os.path.splitext(os.path.basename(z))[0]))
            except Exception:
                continue
            for r, _d, files in os.walk(SDK_CACHE):        # inner package zip(s)
                for fn in files:
                    if fn.lower().endswith(".zip") and "python_sdk" in fn.lower():
                        dest = os.path.join(r, os.path.splitext(fn)[0])
                        if not os.path.isdir(dest):
                            try:
                                with zipfile.ZipFile(os.path.join(r, fn)) as zf:
                                    zf.extractall(dest)
                            except Exception:
                                pass
            pkg = _find_pkg_dir(SDK_CACHE)
            if pkg:
                break
    if pkg is None:
        return False
    if pkg not in sys.path:
        sys.path.insert(0, pkg)
    import importlib
    importlib.invalidate_caches()
    try:
        import thorlabs_tsi_sdk  # noqa: F401
    except Exception:
        return False
    return True


def debayer_superpixel(raw, h, w, phase, bit_depth):
    """Simple colour image (half resolution, then doubled) from raw Bayer data,
    used when Thorlabs' colour-processing DLL can't be loaded."""
    a = np.asarray(raw, np.float32).reshape(h, w)
    a = a[: h // 2 * 2, : w // 2 * 2]
    p00, p01, p10, p11 = a[0::2, 0::2], a[0::2, 1::2], a[1::2, 0::2], a[1::2, 1::2]
    # phase = colour of the top-left pixel: 0 red, 1 blue, 2 green-left-of-red,
    # 3 green-left-of-blue
    ph = int(phase)
    if ph == 0:
        r, g, b = p00, (p01 + p10) / 2, p11
    elif ph == 1:
        r, g, b = p11, (p01 + p10) / 2, p00
    elif ph == 2:
        r, g, b = p01, (p00 + p11) / 2, p10
    else:
        r, g, b = p10, (p00 + p11) / 2, p01
    rgb = np.stack([r, g, b], -1) / (2 ** bit_depth - 1) * 255
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return rgb.repeat(2, axis=0).repeat(2, axis=1)


class ThorCamera(Accessory):
    """Thorlabs scientific camera via the official Python SDK
    (thorlabs_tsi_sdk, shipped with ThorCam). Colour cameras such as the
    CS165CU are converted to RGB with Thorlabs' own colour processor.

    Only one program can use the camera at a time: close ThorCam first.
    """
    name = "camera"

    def __init__(self, exposure_ms=20.0, dll_dir=None, serial=None):
        if not ensure_thorlabs_sdk():
            raise ImportError("the Thorlabs camera package was not found. Is ThorCam "
                              "installed (with 'Scientific Camera Interfaces')?")
        _add_dll_dir(dll_dir)
        from thorlabs_tsi_sdk.tl_camera import TLCameraSDK
        from thorlabs_tsi_sdk.tl_camera_enums import SENSOR_TYPE
        self.lock = threading.Lock()
        self.sdk = TLCameraSDK()
        cams = self.sdk.discover_available_cameras()
        if not cams:
            self.sdk.dispose()
            raise IOError("No Thorlabs camera found (is ThorCam still open?)")
        self.cam = self.sdk.open_camera(serial or cams[0])
        c = self.cam
        self.idn = f"{c.model} s/n {c.serial_number}"
        c.exposure_time_us = int(exposure_ms * 1000)
        c.frames_per_trigger_zero_for_unlimited = 0   # free-running
        c.image_poll_timeout_ms = 0                   # non-blocking poll
        self.w, self.h = c.image_width_pixels, c.image_height_pixels
        self.mtc_sdk = self.mtc = None
        self.bayer = c.camera_sensor_type == SENSOR_TYPE.BAYER
        self.phase = c.color_filter_array_phase if self.bayer else 0
        self.color_note = ""
        if self.bayer:
            try:   # Thorlabs' own colour processing (best colours)
                from thorlabs_tsi_sdk.tl_mono_to_color_processor import \
                    MonoToColorProcessorSDK
                self.mtc_sdk = MonoToColorProcessorSDK()
                self.mtc = self.mtc_sdk.create_mono_to_color_processor(
                    SENSOR_TYPE.BAYER, c.color_filter_array_phase,
                    c.get_color_correction_matrix(),
                    c.get_default_white_balance_matrix(), c.bit_depth)
            except Exception as e:   # fall back to a simple built-in conversion
                self.mtc_sdk = self.mtc = None
                self.color_note = f"simple colour (Thorlabs colour DLL not loaded: {e})"
        c.arm(2)
        c.issue_software_trigger()
        self.last = None

    # exposure
    @property
    def exposure_ms(self):
        return self.cam.exposure_time_us / 1000

    @exposure_ms.setter
    def exposure_ms(self, ms):
        with self.lock:
            self.cam.exposure_time_us = int(ms * 1000)

    def latest(self):
        """Newest frame as uint8 (H, W, 3) RGB or (H, W) mono, or None."""
        with self.lock:
            frame = self.cam.get_pending_frame_or_null()
            if frame is None:
                return self.last
            if self.mtc is not None:
                rgb = self.mtc.transform_to_24(frame.image_buffer, self.w, self.h)
                img = rgb.reshape(self.h, self.w, 3).copy()
            elif self.bayer:
                img = debayer_superpixel(frame.image_buffer, self.h, self.w,
                                         self.phase, self.cam.bit_depth)
            else:
                buf = frame.image_buffer
                img = (buf >> max(self.cam.bit_depth - 8, 0)).astype(np.uint8)
                img = img.reshape(self.h, self.w).copy()
            self.last = img
            return img

    def snapshot(self):
        # wait for a fresh frame so the image belongs to this moment
        t0, img = time.time(), None
        old = self.last
        timeout = 2 + 3 * self.exposure_ms / 1000
        while time.time() - t0 < timeout:
            img = self.latest()
            if img is not None and img is not old:
                break
            time.sleep(0.01)
        return {"image": img if img is not None else np.zeros((1, 1), np.uint8),
                "exposure_ms": self.exposure_ms}

    def close(self):
        try:
            self.cam.disarm()
            if self.mtc is not None:
                self.mtc.dispose()
                self.mtc_sdk.dispose()
            self.cam.dispose()
        finally:
            self.sdk.dispose()


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
class SimCamera(Accessory):
    """Renders the fake sample around the current stage position,
    with the laser spot in the middle. Field of view ~60 x 45 um."""
    name = "camera"
    idn = "Simulated CS165CU"

    def __init__(self, world, exposure_ms=20.0):
        self.w = world
        self.exposure_ms = exposure_ms
        self.lock = threading.Lock()
        self.um_per_px = 0.12

    def latest(self):
        h, w = 180, 240          # rendered at half size, then doubled (fast)
        px = self.um_per_px * 2
        ys = (np.arange(h) - h / 2) * px
        xs = (np.arange(w) - w / 2) * px
        X, Y = np.meshgrid(xs + self.w.x, ys + self.w.y)
        r = self.w.reflection(X, Y)
        f = self.w.flake(X, Y)
        l, rr = self.w.electrode(X, Y)
        gold = np.clip(l + rr, 0, 1)[..., None]
        fl = (f[..., None]) * (1 - gold)
        base = np.stack([0.42 * r, 0.36 * r, 0.62 * r], -1)          # SiO2 purple
        base = base * (1 - gold) + gold * np.array([0.95, 0.78, 0.35])
        base = base * (1 - 0.6 * fl) + 0.6 * fl * np.array([0.30, 0.42, 0.75])
        base *= getattr(self, "lamp", 0.55)                           # lamp level
        # defocus blurs everything (Gaussian blur via FFT)
        dz = getattr(self.w, "z", 0.0) - getattr(self.w, "z_focus", 0.0)
        blur_um = 0.6 * abs(dz)
        if blur_um > 0.05:
            ky = np.fft.fftfreq(h, d=px)[:, None]
            kx = np.fft.fftfreq(w, d=px)[None, :]
            g = np.exp(-2 * (np.pi * blur_um) ** 2 * (kx ** 2 + ky ** 2))
            base = np.real(np.fft.ifft2(np.fft.fft2(base, axes=(0, 1)) * g[..., None],
                                        axes=(0, 1)))
        sig = np.sqrt(0.5 ** 2 + blur_um ** 2)                        # laser spot
        spot = np.exp(-(((X - self.w.x) ** 2 + (Y - self.w.y) ** 2) / (2 * sig ** 2)))
        spot *= 0.4 * (0.5 / sig) ** 2                                 # same total light
        base = base + spot[..., None] * np.array([0.3, 1.0, 0.35])     # green laser
        base *= self.exposure_ms / 20.0
        base += self.w.rng.normal(0, 0.01, base.shape)
        img = (np.clip(base, 0, 1) * 255).astype(np.uint8)
        return img.repeat(2, axis=0).repeat(2, axis=1)

    def snapshot(self):
        return {"image": self.latest(), "exposure_ms": self.exposure_ms}


class SimPM100(Accessory):
    name = "powermeter"
    idn = "Simulated PM100USB"
    sample_factor = 1.0

    def __init__(self, world, wavelength_nm=532.0, range_W=None):
        self.w = world
        self.wavelength_nm = wavelength_nm
        self.lock = threading.Lock()
        self._t0 = time.time()
        self.set_range(range_W)

    def set_range(self, range_W):
        self.auto = range_W in (None, "", "auto", 0)
        if self.auto:
            self.range_W = 1e-3
        else:  # decade ranges like a photodiode sensor
            self.range_W = 10 ** np.ceil(np.log10(float(range_W)) - 1e-9)
        return self.range_W

    def over_range(self, p):
        return (not self.auto) and p > self.range_W

    def set_averages(self, n):
        self.averages = int(n)

    def power(self):
        return self.w.laser_power() * (1 + 0.003 * self.w.rng.normal())

    def snapshot(self):
        p = self.power()
        # saved in µW (internally everything is in W)
        return {"power_uW": p * 1e6, "wavelength_nm": self.wavelength_nm,
                "range_uW": self.range_W * 1e6, "autorange": self.auto,
                "over_range": self.over_range(p), "time": time.time(),
                "sample_factor": self.sample_factor,
                "power_on_sample_uW": p / self.sample_factor * 1e6}


def connect_accessories(config, world=None):
    """Returns (list_of_accessories, warnings). Failures are not fatal."""
    out, warnings = [], []
    pcfg = config.get("powermeter", {}) or {}
    ccfg = config.get("camera", {}) or {}

    if world is not None:  # simulation: always include both
        out.append(SimPM100(world, pcfg.get("wavelength_nm") or 532.0,
                            pcfg.get("range_W")))
        out[-1].sample_factor = float(pcfg.get("sample_factor", 1.0))
        out.append(SimCamera(world, ccfg.get("exposure_ms", 20.0)))
        return out, warnings

    if pcfg.get("enabled", False):
        try:
            try:      # 1) NI-VISA (meter in "PM100D (NI-VISA)" mode, or visible to VISA)
                pm = PM100USB(pcfg.get("resource"), pcfg.get("wavelength_nm"),
                              pcfg.get("averages", 10), pcfg.get("range_W"))
            except Exception as e_visa:
                try:  # 2) Thorlabs' own driver (default mode, no switcher needed)
                    pm = PM100TLPM(pcfg.get("wavelength_nm"), pcfg.get("averages", 10),
                                   pcfg.get("range_W"))
                except Exception as e_tlpm:
                    raise IOError(f"via NI-VISA: {e_visa}; via Thorlabs TLPM: {e_tlpm}")
            out.append(pm)
            out[-1].sample_factor = float(pcfg.get("sample_factor", 1.0))
        except Exception as e:
            warnings.append(f"Power meter not available ({e}). Continuing without it.")
    if ccfg.get("enabled", False):
        try:
            out.append(ThorCamera(ccfg.get("exposure_ms", 20.0), ccfg.get("dll_dir"),
                                  ccfg.get("serial")))
        except ImportError as e:
            warnings.append(f"Camera: {e} Continuing without the camera.")
        except Exception as e:
            warnings.append(f"Camera not available ({e}). Close ThorCam if it is "
                            "open. Continuing without the camera.")
    return out, warnings
