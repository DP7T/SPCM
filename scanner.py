"""Scan engine: raster/serpentine XY scan, optional bias sweep, HDF5 saving.

Runs in a background thread (ScanWorker) so the GUI stays responsive.
Lock-in settings (sensitivity, time constant, phase) are only READ, at the
start of every map, and stored with the data -- set them on the front panel.
"""
import os
import time
import json
import traceback
from dataclasses import dataclass, field, asdict

import numpy as np

from numparse import parse_number
try:
    from pyqtgraph.Qt import QtCore
except ImportError:  # headless use / testing without Qt
    class _Sig:
        def __init__(self, *a):
            self._slots = []

        def __set_name__(self, owner, name):
            self.name = name

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            key = "_sig_" + self.name
            if key not in obj.__dict__:
                obj.__dict__[key] = _Bound()
            return obj.__dict__[key]

    class _Bound:
        def __init__(self):
            self._slots = []

        def connect(self, f):
            self._slots.append(f)

        def emit(self, *a):
            for f in self._slots:
                f(*a)

    class QtCore:  # noqa: N801
        Signal = _Sig
        QObject = object

CHANNELS = ["X", "Y", "R", "theta"]


@dataclass
class ScanSettings:
    # x_center / y_center = the position you set ("reference point").
    # origin "corner": it is the first pixel, and the map extends in the
    #                  x_dir / y_dir directions from it.
    # origin "centre": it is the middle of the map.
    x_center: float = 0.0
    y_center: float = 0.0
    origin: str = "corner"
    origin_x: float = None         # stage position of the user's (0, 0)
    origin_y: float = None
    flip_x: bool = False           # program x = -stage x (to match the joystick)
    flip_y: bool = False
    angle_deg: float = 0.0         # scan frame rotated by this angle (stage frame)
    x_dir: int = 1
    y_dir: int = 1
    x_size: float = 20.0
    y_size: float = 20.0
    step: float = 0.5
    serpentine: bool = True
    settle_tc: float = 3.0         # wait this many time constants per pixel (manual)
    settle_auto: bool = True       # wait until 99 % settled, from each lock-in's slope
    backlash_um: float = 1.0       # each line's first pixel is approached from
                                   # the scan direction (removes stage backlash)
    extra_settle_ms: float = 0.0
    biases: list = field(default_factory=lambda: [None])  # None = don't touch bias
    sample: str = "sample"
    notes: str = ""
    power_lock: bool = False       # (old) keep laser power constant during the scan
    target_power_uW: float = None  # set the power on the sample before the scan
    power_tol_pct: float = 1.0
    mode: str = "step"             # "step": stop at every pixel; "fly": continuous lines
    conditions: dict = field(default_factory=dict)  # polarization, T, p, ...
    # what sits in front of the photocurrent lock-in:
    #   "lockin"  - nothing: the lock-in value is already correct (factor 1)
    #   "sr570"   - SR570 current preamp: value x sqrt(2) x sensitivity (A/V) -> A
    #   "voltage" - voltage preamp: value x sqrt(2) / gain -> V
    amp_mode: str = "lockin"
    amp_value: float = 1.0          # SR570 sensitivity in A/V, or voltage gain
    # the SR830 reports rms of the component at the reference frequency.
    #   "rms"    x 1        "peak" (amplitude) x sqrt(2)
    #   "pp"     x 2 sqrt(2) (peak-to-peak, sine modulation)
    #   "square" x pi/sqrt(2) (on-off height of a square-wave/chopped signal)
    signal_as: str = "peak"
    lockin_unit: str = "V"          # "A" when the SR830 current input is used
    scan_type: str = "map"          # "map", or "line" (one row along a drawn line)

    def axes(self):
        nx = int(round(self.x_size / self.step)) + 1
        ny = int(round(self.y_size / self.step)) + 1
        ix, iy = np.arange(nx) * self.step, np.arange(ny) * self.step
        if self.origin == "corner":
            x0 = self.x_center if self.x_dir > 0 else self.x_center - ix[-1]
            y0 = self.y_center if self.y_dir > 0 else self.y_center - iy[-1]
        else:
            x0, y0 = self.x_center - ix[-1] / 2, self.y_center - iy[-1] / 2
        return x0 + ix, y0 + iy   # always ascending; scan order handles direction

    def start_position(self):
        return self.x_center, self.y_center

    def rotate_xy(self, x, y):
        """Grid point (unrotated stage coordinates) -> where the stage goes:
        rotated by angle_deg around the reference point (x_center, y_center)."""
        a = getattr(self, "angle_deg", 0.0) or 0.0
        if a == 0:
            return x, y
        c, s_ = np.cos(np.radians(a)), np.sin(np.radians(a))
        dx, dy = x - self.x_center, y - self.y_center
        return self.x_center + c * dx - s_ * dy, self.y_center + s_ * dx + c * dy


def _eng(v, unit):
    for f, p in ((1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1, "")):
        if abs(v) < f * 999.5:
            return f"{v / f:.3g} {p}{unit}"
    return f"{v:.3g} {unit}"


SIGNAL_AS = {  # key: (factor on the rms reading, label, formula text)
    "rms": (1.0, "rms", ""),
    "peak": (np.sqrt(2), "peak (amplitude)", "√2"),
    "pp": (2 * np.sqrt(2), "peak-to-peak", "2√2"),
    "square": (np.pi / np.sqrt(2), "square-wave on–off", "π/√2"),
}


# time to reach 99 % of a step, in time constants, for 6/12/18/24 dB/oct
# (SR830 manual: 5, 7, 9, 10 tau)
SETTLE_99 = {1: 5.0, 2: 7.0, 3: 9.0, 4: 10.0}


def pixel_wait(s, taus, poles):
    """Seconds to wait at each pixel before reading."""
    if getattr(s, "settle_auto", True):
        w = max(SETTLE_99.get(int(p), 10.0) * t for t, p in zip(taus, poles))
    else:
        w = s.settle_tc * max(taus)
    return w + s.extra_settle_ms / 1e3


def signal_kind(settings):
    """'Photocurrent' or 'Photovoltage', from what sits in front of the lock-in:
    SR570 -> current; SR560 -> voltage; lock-in alone -> its input (I or A/A-B)."""
    m = settings.amp_mode
    if m == "sr570":
        return "Photocurrent"
    if m == "voltage":
        return "Photovoltage"
    return "Photocurrent" if getattr(settings, "lockin_unit", "V") == "A" else "Photovoltage"


def pc_scale(settings):
    """-> (factor, unit, formula) to turn the raw photocurrent lock-in reading
    (rms) into the real signal."""
    m, v = settings.amp_mode, float(settings.amp_value)
    base, label, ftxt = SIGNAL_AS.get(getattr(settings, "signal_as", "peak"), SIGNAL_AS["peak"])
    mult = f" × {ftxt}" if ftxt else ""
    if m == "sr570":
        return base * v, "A", f"raw{mult} × {_eng(v, 'A/V')} (SR570) = {label}"
    if m == "voltage":
        return base / v, "V", f"raw{mult} ÷ {v:g} (SR560 gain) = {label}"
    u = getattr(settings, "lockin_unit", "V") or "V"
    return base, u, f"raw{mult} (lock-in only) = {label}"


def parse_list(text, scale=1.0):
    """'0, 0.5, 1'  or  '-1:1:0.5' (start:stop:step, inclusive)  or mixed.
    '1:100:*2' multiplies by 2 each time (1, 2, 4 ... 64, 100) - handy for
    power sweeps on a log scale. Numbers may use SI prefixes ('-500m:500m:100m');
    scale = the field's unit, as for parse_number."""
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            pieces = part.split(":")
            if len(pieces) != 3:
                raise ValueError(f"Use start:stop:step, not '{part}'")
            a, b = parse_number(pieces[0], scale), parse_number(pieces[1], scale)
            st = pieces[2].strip()
            if st[:1] in ("*", "x", "×"):
                f = float(parse_number(st[1:]))
                if f <= 1 or a <= 0 or b <= 0:
                    raise ValueError("For '*factor' use a factor above 1 and "
                                     "positive start and stop")
                lo, hi = min(a, b), max(a, b)
                vals = [lo]
                while vals[-1] * f < hi * (1 - 1e-9):
                    vals.append(vals[-1] * f)
                if vals[-1] < hi * (1 - 1e-9):
                    vals.append(hi)
                out.extend((np.round(vals if a <= b else vals[::-1], 9)).tolist())
                continue
            s = parse_number(st, scale)
            if s == 0:
                raise ValueError("Step can't be 0")
            s = abs(s) if b >= a else -abs(s)
            n = int(np.floor((b - a) / s + 1e-9)) + 1
            vals = a + s * np.arange(n)
            if abs(vals[-1] - b) > 1e-9 * max(abs(s), 1e-30) * 1e3:
                vals = np.append(vals, b)          # the stop value is always included
            out.extend(np.round(vals, 9).tolist())
        else:
            out.append(parse_number(part, scale))
    return out


def parse_bias_list(text):
    """Bias values in V (see parse_list)."""
    return parse_list(text, 1.0)


class ScanData:
    def __init__(self, settings, hw_info):
        self.settings = settings
        self.hw_info = hw_info
        self.xs, self.ys = settings.axes()
        nb, ny, nx = len(settings.biases), len(self.ys), len(self.xs)
        self.pc = np.full((nb, ny, nx, 4), np.nan)
        self.refl = np.full((nb, ny, nx, 4), np.nan)
        self.map_info = [{} for _ in range(nb)]
        self.accessories = {}   # "map0/powermeter/start" -> {key: value}
        self.power_log = []     # [map, line, time_s, power_uW, sine_amp_V, on_sample_uW]
        self.power_lock = {}    # target, start amplitude
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.completed = False

    def save(self, path):
        """Tidy file: one group per map with plain 2-D images, ready to plot.

        /info                 settings, notes, instruments (attributes)
        /map0, /map1, ...     one per bias value (attributes: bias, lock-in
                              settings, laser power ...)
            x_um, y_um            axes from the origin (0, 0), ascending
            photocurrent_X/_Y/_R  converted signal (A or V), photocurrent_theta_deg
            reflection_X/_Y/_R, reflection_theta_deg
            raw/                  lock-in readings as measured (rms)
        /power_log            laser power after every line (columns as datasets)
        Every image: rows = y, columns = x, same orientation as on screen."""
        import h5py
        self.path = path
        s = self.settings
        fac, unit, formula = pc_scale(s)
        unit = unit or getattr(s, "lockin_unit", "V")
        kind = signal_kind(s)
        ox = s.origin_x if s.origin_x is not None else s.x_center
        oy = s.origin_y if s.origin_y is not None else s.y_center
        ux = (-1 if s.flip_x else 1) * (self.xs - ox) + 0.0
        vy = (-1 if s.flip_y else 1) * (self.ys - oy) + 0.0
        ix, iy = np.argsort(ux), np.argsort(vy)
        line = getattr(s, "scan_type", "map") == "line"
        if line:                         # a line keeps its order: start -> end
            ix, iy = np.arange(len(self.xs)), np.arange(len(self.ys))
        view = np.ix_(iy, ix)
        with h5py.File(path, "w") as f:
            f.attrs["format"] = "SPCM v2 (one group per map)"
            f.attrs["scan_type"] = "line" if line else "map"
            f.attrs["sample"] = s.sample
            f.attrs["signal"] = kind
            f.attrs["started"] = self.started
            f.attrs["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.attrs["completed"] = self.completed
            f.attrs["notes"] = s.notes
            info = f.create_group("info")
            info.attrs["settings"] = json.dumps(asdict(s))
            info.attrs["instruments"] = json.dumps(self.hw_info)
            info.attrs["laser_power_setting"] = json.dumps(self.power_lock, default=float)
            info.attrs["conversion"] = f"{formula} [{unit}]"
            info.attrs["origin_stage_um"] = [ox, oy]
            exp = info.create_group("experiment")
            for k, v in s.conditions.items():
                exp.attrs[k] = str(v)
            exp.attrs["notes"] = s.notes
            for ib, b in enumerate(s.biases):
                g = f.create_group(f"map{ib}" + ("" if b is None else f"_{b:+g}V"))
                g.attrs["bias_V"] = np.nan if b is None else b
                for k, v in (self.map_info[ib] or {}).items():
                    if v is not None and not isinstance(v, (list, dict)):
                        g.attrs[k] = v
                g["x_um"], g["y_um"] = ux[ix], vy[iy]
                g["x_stage_um"], g["y_stage_um"] = self.xs[ix], self.ys[iy]
                g.attrs["angle_deg"] = getattr(s, "angle_deg", 0.0)
                if line:
                    X, Y = s.rotate_xy(self.xs, np.full(len(self.xs), self.ys[0]))
                    g["distance_um"] = self.xs - self.xs[0]
                    g["line_x_um"] = (-1 if s.flip_x else 1) * (X - ox) + 0.0
                    g["line_y_um"] = (-1 if s.flip_y else 1) * (Y - oy) + 0.0
                    g["line_x_stage_um"], g["line_y_stage_um"] = X, Y
                    g.attrs["note"] = ("line scan: images have 1 row; columns follow "
                                       "the line from start to end (distance_um)")
                if getattr(s, "angle_deg", 0.0):
                    XX, YY = np.meshgrid(self.xs[ix], self.ys[iy])
                    RX, RY = s.rotate_xy(XX, YY)
                    g["X_stage_um_2d"], g["Y_stage_um_2d"] = RX, RY   # true positions
                for c, ch in enumerate(CHANNELS):
                    nm = ch if c < 3 else "theta_deg"
                    d = g.create_dataset(f"photocurrent_{nm}",
                                         data=(self.pc[ib, :, :, c] * (fac if c < 3 else 1))[view],
                                         compression="gzip")
                    d.attrs["unit"] = unit if c < 3 else "deg"
                    d.attrs["signal"] = kind
                    d = g.create_dataset(f"reflection_{nm}",
                                         data=self.refl[ib, :, :, c][view], compression="gzip")
                    d.attrs["unit"] = "V" if c < 3 else "deg"
                    g.create_dataset(f"raw/photocurrent_{nm}",
                                     data=self.pc[ib, :, :, c][view], compression="gzip")
                    g.create_dataset(f"raw/reflection_{nm}",
                                     data=self.refl[ib, :, :, c][view], compression="gzip")
                g["photocurrent_X"].attrs["conversion"] = formula
                # laser power and camera at the start/end of this map
                for when in ("start", "end"):
                    pm = self.accessories.get(f"map{ib}/powermeter/{when}", {})
                    for k in ("power_uW", "power_on_sample_uW", "range_uW", "over_range"):
                        if k in pm:
                            g.attrs[f"laser_{k}_{when}"] = pm[k]
                    if "wavelength_nm" in pm:
                        g.attrs["laser_wavelength_nm"] = pm["wavelength_nm"]
                        g.attrs["meter_to_sample_ratio"] = pm.get("sample_factor", 1.0)
            if self.power_log:
                pl = np.array(self.power_log, float)
                g = f.create_group("power_log")
                g.attrs["note"] = "laser power after every scan line"
                for c, name in enumerate(("map", "line", "time_s", "power_uW",
                                          "sine_amplitude_V", "power_on_sample_uW")):
                    if c < pl.shape[1]:
                        g[name] = pl[:, c]


def update_experiment(path, conditions, notes):
    """Rewrite the experiment notes of a saved file (e.g. typed during the scan)."""
    import h5py
    with h5py.File(path, "a") as f:
        base = f["info"] if "info" in f else f
        if "experiment" in base:
            del base["experiment"]
        g = base.create_group("experiment")
        for k, v in conditions.items():
            g.attrs[k] = str(v)
        g.attrs["notes"] = notes
        f.attrs["notes"] = notes
        st = json.loads(base.attrs["settings"])
        st["conditions"], st["notes"] = conditions, notes
        base.attrs["settings"] = json.dumps(st)


def export_csv(d):
    """Write easy-to-open text files next to the .h5 file:
    <name>_csv/map<k>_<bias>/photocurrent_X.csv ... + info.txt
    Each CSV is a matrix: first row = x (µm), first column = y (µm).
    Photocurrent is converted (same as the map); reflection is as read."""
    base = os.path.splitext(d.path)[0]
    out = base + "_csv"
    os.makedirs(out, exist_ok=True)
    fac, unit, formula = pc_scale(d.settings)
    unit = unit or getattr(d.settings, "lockin_unit", "V")
    st = d.settings
    ox = st.origin_x if getattr(st, "origin_x", None) is not None else st.x_center
    oy = st.origin_y if getattr(st, "origin_y", None) is not None else st.y_center
    ux = (-1 if getattr(st, "flip_x", False) else 1) * (np.asarray(d.xs) - ox) + 0.0
    vy = (-1 if getattr(st, "flip_y", False) else 1) * (np.asarray(d.ys) - oy) + 0.0
    ix, iy = np.argsort(ux), np.argsort(vy)
    for ib, b in enumerate(d.settings.biases):
        sub = os.path.join(out, f"map{ib}" + ("" if b is None else f"_{b:+g}V"))
        os.makedirs(sub, exist_ok=True)
        for name, arr, f_, u in (("photocurrent", d.pc, fac, unit),
                                 ("reflection", d.refl, 1.0, "V")):
            for c, ch in enumerate(CHANNELS):
                m = (arr[ib, :, :, c] * (f_ if c < 3 else 1.0))[np.ix_(iy, ix)]
                uu = u if c < 3 else "deg"
                with open(os.path.join(sub, f"{name}_{ch}.csv"), "w") as fh:
                    fh.write(f"# {name} {ch} [{uu}]; rows: y (um), columns: x (um), "
                             f"measured from the origin (0,0) = stage ({ox:.3f}, {oy:.3f}) um\n")
                    fh.write("y\\x," + ",".join(f"{x:.4f}" for x in ux[ix]) + "\n")
                    for r, y in enumerate(vy[iy]):
                        fh.write(f"{y:.4f}," + ",".join(
                            "" if not np.isfinite(v) else f"{v:.6e}" for v in m[r]) + "\n")
    with open(os.path.join(out, "info.txt"), "w", encoding="utf-8") as fh:
        s = d.settings
        fh.write(f"Sample: {s.sample}\nStarted: {d.started}\nCompleted: {d.completed}\n")
        fh.write(f"Photocurrent conversion: {formula} [{unit}]\n\n")
        fh.write("Experiment:\n")
        for k, v in (s.conditions or {}).items():
            fh.write(f"  {k}: {v}\n")
        fh.write(f"\nNotes:\n{s.notes}\n\nScan settings:\n")
        for k, v in asdict(s).items():
            if k not in ("conditions", "notes"):
                fh.write(f"  {k}: {v}\n")
        fh.write("\nPer map (lock-in settings read from the front panels):\n")
        for ib, mi in enumerate(d.map_info):
            fh.write(f"  map{ib}: {json.dumps(mi)}\n")
        for key, vals in sorted(d.accessories.items()):
            if "powermeter" in key:
                fh.write(f"  {key}: " + ", ".join(
                    f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in vals.items() if k != "time") + "\n")
        fh.write(f"\nInstruments: {json.dumps(d.hw_info)}\n")
    if d.power_log:
        np.savetxt(os.path.join(out, "power_log.csv"), np.array(d.power_log, float),
                   delimiter=",", header="map,line,time_s,power_uW,sine_amplitude_V,"
                   "power_on_sample_uW", comments="")
    return out


def _settings_from(st):
    st.setdefault("origin", "centre")   # files from before this option
    st.setdefault("signal_as", "rms" if st.get("amp_mode", "lockin") == "lockin" else "peak")
    known = set(ScanSettings.__dataclass_fields__)
    return ScanSettings(**{k: v for k, v in st.items() if k in known})


def load(path):
    """Load a saved scan (new tidy format or the older one) -> ScanData."""
    import h5py
    with h5py.File(path, "r") as f:
        if "info" in f:                                   # ---- SPCM v2
            info = f["info"]
            d = ScanData(_settings_from(json.loads(info.attrs["settings"])),
                         json.loads(info.attrs["instruments"]))
            import re
            maps = sorted((k for k in f if re.match(r"map\d+", k)),
                          key=lambda k: int(re.match(r"map(\d+)", k).group(1)))
            g0 = f[maps[0]]
            d.xs, d.ys = g0["x_stage_um"][()], g0["y_stage_um"][()]
            nb, ny, nx = len(maps), len(d.ys), len(d.xs)
            d.pc = np.full((nb, ny, nx, 4), np.nan)
            d.refl = np.full((nb, ny, nx, 4), np.nan)
            d.map_info = []
            for ib, m in enumerate(maps):
                g = f[m]
                for c, ch in enumerate(CHANNELS):
                    nm = ch if c < 3 else "theta_deg"
                    d.pc[ib, :, :, c] = g[f"raw/photocurrent_{nm}"][()]
                    d.refl[ib, :, :, c] = g[f"raw/reflection_{nm}"][()]
                d.map_info.append({k: (v.item() if hasattr(v, "item") else v)
                                   for k, v in g.attrs.items()})
                for when in ("start", "end"):
                    pm = {k[len("laser_"):-len(when) - 1]: g.attrs[k] for k in g.attrs
                          if k.startswith("laser_") and k.endswith("_" + when)}
                    if pm:
                        d.accessories[f"map{ib}/powermeter/{when}"] = pm
                    if f"camera_{when}" in g:
                        d.accessories[f"map{ib}/camera/{when}"] = {
                            "image": g[f"camera_{when}"][()]}
            if "power_log" in f:
                pg_ = f["power_log"]
                cols = [pg_[c][()] for c in ("map", "line", "time_s", "power_uW",
                                             "sine_amplitude_V", "power_on_sample_uW")
                        if c in pg_]
                d.power_log = np.column_stack(cols).tolist() if cols else []
            d.power_lock = json.loads(info.attrs.get("laser_power_setting",
                                                     info.attrs.get("power_lock", "{}")))
        else:                                             # ---- older files
            d = ScanData(_settings_from(json.loads(f.attrs["settings"])),
                         json.loads(f.attrs["hardware"]))
            d.xs, d.ys = f["x_um"][()], f["y_um"][()]
            d.pc, d.refl = f["photocurrent"][()], f["reflection"][()]
            d.map_info = json.loads(f.attrs["map_info"])
            if "power_log" in f:
                d.power_log = f["power_log"][()].tolist()
            if "accessories" in f:
                def visit(name, obj):
                    if isinstance(obj, h5py.Group) and (len(obj.attrs) or any(
                            isinstance(v, h5py.Dataset) for v in obj.values())):
                        vals = dict(obj.attrs)
                        vals.update({k: v[()] for k, v in obj.items()
                                     if isinstance(v, h5py.Dataset)})
                        d.accessories[name] = vals
                f["accessories"].visititems(visit)
        d.completed = bool(f.attrs["completed"])
        d.path = path
        d.started = str(f.attrs.get("started", ""))
    return d


class SafeStage:
    """Wraps the stage during a scan: refuses any move outside the scan area
    (plus a small margin) and checks that the stage really arrived where it
    was sent. Stops the scan with a clear message instead of driving away."""

    def __init__(self, stage, xs, ys, step, margin_um=20.0, tol_um=3.0):
        self.st = stage
        m = margin_um + 5 * step
        self.box = (min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m)
        self.tol = tol_um

    def __getattr__(self, name):            # everything else passes through
        return getattr(self.st, name)

    def _check(self, x, y):
        x0, x1, y0, y1 = self.box
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            raise RuntimeError(
                f"Safety stop: the program wanted to move to x = {x:.2f}, y = {y:.2f} µm, "
                f"outside the scan area (x {x0:.1f}…{x1:.1f}, y {y0:.1f}…{y1:.1f} µm). "
                "Nothing was moved. Please send spcm_stage.log.")

    def move_to(self, x, y, verify=False):
        self._check(x, y)
        self.st.move_to(x, y)
        if verify:
            self.verify(x, y)

    def start_move(self, x, y):
        self._check(x, y)
        self.st.start_move(x, y)

    def verify(self, x, y):
        px, py = self.st.position()
        if abs(px - x) > self.tol or abs(py - y) > self.tol:
            self.st.stop()
            raise RuntimeError(
                f"Safety stop: the stage was sent to x = {x:.2f}, y = {y:.2f} µm but "
                f"reports x = {px:.2f}, y = {py:.2f} µm. Scan stopped. "
                "Please send spcm_stage.log from the program folder.")


def set_laser_power(pm, lockin, target_W, tol_pct=1.0, max_amp=5.0,
                    status=None, stop=None, max_iter=20):
    """Adjust the lock-in SINE OUT amplitude until the power ON THE SAMPLE
    (meter / ratio) is within tol_pct of target_W. Nothing else is touched.
    Returns dict(ok, message, power_uW, target_uW, amplitude_V)."""
    status = status or (lambda m: None)
    stop = stop or (lambda: False)
    ratio = getattr(pm, "sample_factor", 1.0) or 1.0
    n = getattr(pm, "averages", 10) or 10
    settle = 0.25 + 2 * n * 0.003

    def read():
        vals = []
        for _ in range(3):
            vals.append(pm.power() / ratio)
            time.sleep(n * 0.003)
        return float(np.median(vals))

    amp = lockin.sine_amplitude()
    p = read()
    best = (abs(p / target_W - 1), p, amp)
    for it in range(max_iter):
        if stop():
            break
        err = p / target_W - 1
        status(f"Setting laser power: {p * 1e6:.2f} µW on sample "
               f"(target {target_W * 1e6:.2f} µW, sine {amp:.3f} V)")
        if abs(err) * 100 <= tol_pct:
            return {"ok": True, "power_uW": p * 1e6, "target_uW": target_W * 1e6,
                    "amplitude_V": amp,
                    "message": f"Laser power set: {p * 1e6:.2f} µW on sample "
                               f"(target {target_W * 1e6:.2f} µW ± {tol_pct:g} %, "
                               f"sine {amp:.3f} V)"}
        if p <= 5e-9:                         # essentially no light on the meter
            return {"ok": False, "power_uW": p * 1e6, "target_uW": target_W * 1e6,
                    "amplitude_V": amp,
                    "message": "Laser power not set: the power meter reads almost "
                               "nothing. Is the laser on and the shutter open?"}
        factor = float(np.clip(target_W / p, 0.5, 2.0))
        new = float(np.clip(amp * (1 + 0.85 * (factor - 1)), 0.004, max_amp))
        if abs(new - amp) < 0.002:              # SR830 resolution reached
            break
        amp = lockin.set_sine_amplitude(new)
        time.sleep(settle)
        p = read()
        if abs(p / target_W - 1) < best[0]:
            best = (abs(p / target_W - 1), p, amp)
        if amp >= max_amp and p < target_W:
            return {"ok": False, "power_uW": p * 1e6, "target_uW": target_W * 1e6,
                    "amplitude_V": amp,
                    "message": f"Laser power not reached: {p * 1e6:.2f} µW at the maximum "
                               f"sine amplitude ({max_amp:g} V)."}
    _, p, amp = best
    if not tol_pct:
        # "closest possible": also try the neighbouring 2 mV steps, keep the best
        for nb in (amp - 0.002, amp + 0.002):
            if stop() or not (0.004 <= nb <= max_amp):
                continue
            a = lockin.set_sine_amplitude(nb)
            time.sleep(settle)
            q = read()
            if abs(q / target_W - 1) < abs(p / target_W - 1):
                p, amp = q, a
        lockin.set_sine_amplitude(amp)
        time.sleep(settle)
        return {"ok": True, "power_uW": p * 1e6, "target_uW": target_W * 1e6,
                "amplitude_V": amp,
                "message": f"Laser power set: {p * 1e6:.3g} µW on sample (target "
                           f"{target_W * 1e6:.3g} µW, {(p / target_W - 1) * 100:+.1f} %, "
                           f"sine {amp:.3f} V)"}
    lockin.set_sine_amplitude(amp)
    return {"ok": abs(p / target_W - 1) * 100 <= tol_pct, "power_uW": p * 1e6,
            "target_uW": target_W * 1e6, "amplitude_V": amp,
            "message": f"Laser power set as close as possible: {p * 1e6:.2f} µW on sample "
                       f"(target {target_W * 1e6:.2f} µW, sine {amp:.3f} V)"}


class PowerSetter(QtCore.QObject):
    """'Set power now' button, in the background."""
    status = QtCore.Signal(str)
    done = QtCore.Signal(object)

    def __init__(self, pm, lockin, target_W, tol_pct, max_amp=5.0):
        super().__init__()
        self.args = (pm, lockin, target_W, tol_pct, max_amp)

    def run(self):
        try:
            self.done.emit(set_laser_power(*self.args, status=self.status.emit))
        except Exception as e:
            self.done.emit(f"Setting the power failed: {e}")


class PowerLock:
    """Keeps the laser power constant DURING A SCAN ONLY.

    The laser is driven by a lock-in's SINE OUT. At the start of the scan the
    power meter reading becomes the target. After every line the power is
    read again and the sine amplitude is nudged to bring it back. Nothing is
    touched outside a scan, so turning on a lamp between scans can't upset it.

    Safety: never more than `max_change_pct` away from the starting
    amplitude, at most `max_step_pct` per correction, no correction when the
    reading looks wrong (beam blocked, over range).
    """

    def __init__(self, pm, lockin, cfg=None):
        cfg = cfg or {}
        self.pm, self.li = pm, lockin
        self.deadband = cfg.get("deadband_pct", 0.5) / 100
        self.gain = cfg.get("gain", 0.7)
        self.max_step = cfg.get("max_step_pct", 5) / 100
        self.max_change = cfg.get("max_change_pct", 30) / 100

    def start(self):
        self.amp0 = self.amp = self.li.sine_amplitude()
        readings = []
        for _ in range(3):
            readings.append(self.pm.power())
            time.sleep(0.1)
        self.target = float(np.mean(readings))
        if self.target <= 0 or self.pm.over_range(self.target):
            raise RuntimeError("Power lock: the starting power reading is not valid "
                               f"({self.target:.3g} W). Check the beam and the range.")
        self.lo = max(0.004, self.amp0 * (1 - self.max_change))
        self.hi = min(5.0, self.amp0 * (1 + self.max_change))
        return self.target, self.amp0

    def correct(self):
        """-> (power, amplitude, message or None)"""
        p = self.pm.power()
        if self.pm.over_range(p):
            return p, self.amp, "power meter over range - no correction"
        if p < 0.3 * self.target:
            return p, self.amp, "power dropped below 30 % of target (beam blocked?) - no correction"
        ratio = self.target / p
        if abs(ratio - 1) < self.deadband:
            return p, self.amp, None
        step = np.clip(self.gain * (ratio - 1), -self.max_step, self.max_step)
        new = float(np.clip(self.amp * (1 + step), self.lo, self.hi))
        msg = None
        if new in (self.lo, self.hi):
            msg = (f"sine amplitude at its limit ({new:.3f} V, ±"
                   f"{self.max_change * 100:.0f} % of start) - laser drifted too far")
        self.amp = self.li.set_sine_amplitude(new)
        return p, self.amp, msg


class ScanWorker(QtCore.QObject):
    pixel = QtCore.Signal(int, int, int, object, object)  # ib, iy, ix, pc, refl
    status = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, float)             # done, total, eta_s
    finished = QtCore.Signal(str)                         # saved path
    failed = QtCore.Signal(str)
    notify = QtCore.Signal(str, str)                      # message, level (info/warn)

    def __init__(self, hw, settings, save_dir, lock_cfg=None):
        super().__init__()
        self.hw, self.s, self.save_dir = hw, settings, save_dir
        self.lock_cfg = lock_cfg or {}
        self.pm = next((a for a in hw.accessories if a.name == "powermeter"), None)
        self._stop = False
        self.data = ScanData(settings, hw.describe())
        os.makedirs(save_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in settings.sample)
        self.path = os.path.join(save_dir, f"{stamp}_{safe}.h5")

    def stop(self):
        self._stop = True

    def _snapshot(self, ib, when):
        for acc in self.hw.accessories:
            if acc.name == "camera":          # camera pictures are not saved
                continue
            try:
                self.data.accessories[f"map{ib}/{acc.name}/{when}"] = acc.snapshot()
            except Exception as e:
                self.status.emit(f"{acc.name} read failed: {e}")

    def _go_reference(self):
        """Move to the scan's start position and let the power meter settle,
        so every power reading is taken on the same spot of the sample (light
        reflected back from gold etc. otherwise looks like a laser drift)."""
        self.hw.stage.move_to(*self.s.start_position())
        n = getattr(self.pm, "averages", 10) or 10
        time.sleep(0.2 + 2 * n * 0.003)

    # ------------------------------------------------------------ step mode
    def _read_both(self):
        """Read both lock-ins at the same time (saves one GPIB round trip)."""
        if not hasattr(self, "_pool"):
            from concurrent.futures import ThreadPoolExecutor
            self._pool = ThreadPoolExecutor(2)
        f1 = self._pool.submit(self.hw.lockin_pc.read)
        f2 = self._pool.submit(self.hw.lockin_refl.read)
        return f1.result(), f2.result()

    def _step_line(self, ib, iy, forward, wait):
        d, hw, nx = self.data, self.hw, len(self.data.xs)
        cols = range(nx) if forward else range(nx - 1, -1, -1)
        if self.s.backlash_um > 0:     # take up backlash before the first pixel
            first = d.xs[cols[0]]
            hw.stage.move_to(*self.s.rotate_xy(
                first - (1 if forward else -1) * self.s.backlash_um, d.ys[iy]), verify=True)
        for ix in cols:
            if self._stop:
                break
            t0 = time.time()
            hw.stage.move_to(*self.s.rotate_xy(d.xs[ix], d.ys[iy]))
            t1 = time.time()
            # first pixel of a line: the stage just came from far away and the
            # lock-in output still remembers the previous line -> wait twice
            time.sleep(wait * (2 if ix == cols[0] else 1))
            t2 = time.time()
            pc, rf = self._read_both()
            t3 = time.time()
            self._t["move"] += t1 - t0
            self._t["wait"] += t2 - t1
            self._t["read"] += t3 - t2
            self._t["n"] += 1
            d.pc[ib, iy, ix], d.refl[ib, iy, ix] = pc, rf
            self.pixel.emit(ib, iy, ix, pc, rf)
        if not self._stop:
            hw.stage.verify(*self.s.rotate_xy(d.xs[cols[-1]], d.ys[iy]))  # where planned?

    def _timing_text(self, line_s, nx):
        t = self._t
        if self.s.mode == "fly" or not t["n"]:
            return f"{line_s / max(nx, 1) * 1000:.0f} ms/pixel"
        n = t["n"]
        txt = (f"per pixel: move {t['move'] / n * 1000:.0f} ms, wait "
               f"{t['wait'] / n * 1000:.0f} ms, read {t['read'] / n * 1000:.0f} ms")
        self._t = {"move": 0.0, "wait": 0.0, "read": 0.0, "n": 0}
        return txt

    # ------------------------------------------------------------ fly mode
    def _fly_setup(self):
        """Measure the stage speed (in its own velocity units) and how fast we
        can sample, so each line can run at exactly one pixel per pixel-time."""
        hw, d, s = self.hw, self.data, self.s
        st = hw.stage
        self._vel0 = None
        vel0 = st.velocity()
        if not vel0 or vel0 <= 0:
            raise RuntimeError(f"Fly scan: the stage reported speed {vel0!r}. Use Step mode.")
        # 1) does the controller accept a speed change? (no motion yet)
        st.set_velocity(vel0 * 0.5)
        rb = st.velocity()
        st.set_velocity(vel0)
        if abs(rb - vel0 * 0.5) > 0.1 * vel0:
            raise RuntimeError(
                f"Fly scan: the stage did not accept the speed command (asked for "
                f"{vel0 * 0.5:g}, it reports {rb:g}). Fly mode can't be used with "
                "this controller setting - use Step mode, and tell me this message.")
        self._vel0 = vel0
        y = d.ys[0]
        x0 = d.xs[0]
        L = float(min(max(d.xs[-1] - d.xs[0], 2.0), 20.0))
        # 2) ONE slow test move along the first line (a second, slower one only
        #    if the first was too quick to measure)
        self.status.emit("Fly scan: one slow test move to measure the stage speed…")
        f = 0.02
        for _attempt in range(2):
            if self._stop:
                raise InterruptedError
            st.move_to(x0, y)
            st.set_velocity(vel0 * f)
            st.start_move(x0 + L, y)
            ts, xs, t_loop = [], [], []
            t_begin = time.time()
            while st.is_moving() and time.time() - t_begin < 30 and not self._stop:
                t0 = time.time()
                x, _ = st.position()
                self._read_both()
                t_loop.append(time.time() - t0)
                ts.append(t0)
                xs.append(x)
            st.set_velocity(vel0)
            if len(ts) >= 10:
                break
            f /= 10
        st.move_to(x0, y)
        if len(ts) < 5:
            raise RuntimeError("Fly scan: could not measure the stage speed "
                               "(too few samples). Use Step mode.")
        ts, xs = np.array(ts), np.array(xs)
        mid = slice(len(ts) // 5, len(ts) * 4 // 5)
        v_cal = abs(np.polyfit(ts[mid], xs[mid], 1)[0])       # µm/s at vel
        if v_cal <= 0:
            raise RuntimeError("Fly scan: the stage did not move. Use Step mode.")
        v0 = v_cal / f                                        # µm/s at normal speed
        self._k = self._vel0 / v0                             # native units per µm/s
        self._v0 = v0
        self._t_loop = float(np.median(t_loop))
        self.status.emit(f"Fly scan: stage {v0:.0f} µm/s at its normal speed, "
                         f"{self._t_loop * 1000:.0f} ms per sample")

    def _fly_line(self, ib, iy, forward, wait):
        hw, d, s = self.hw, self.data, self.s
        st, xs, nx = hw.stage, d.xs, len(d.xs)
        step = abs(xs[1] - xs[0]) if nx > 1 else 1.0
        # pixel time: what the lock-in needs, but at least ~2 samples per pixel
        t_pix = max(wait, 2.0 * self._t_loop)
        v = step / t_pix                                       # µm/s
        sgn = 1 if forward else -1
        delay = [tau * p for tau, p in zip(self._tau, self._poles)]   # lock-in lag
        runup = min(max(2 * step, v * max(delay) * 3), 4 * step + 10.0)
        xa = (xs[0] if forward else xs[-1]) - sgn * runup
        xb = (xs[-1] if forward else xs[0]) + sgn * runup
        st.set_velocity(self._vel0)
        st.move_to(xa, d.ys[iy], verify=True)
        st.set_velocity(min(v * self._k, self._vel0))
        st.start_move(xb, d.ys[iy])
        pos, tp, tr, pcs, rfs = [], [], [], [], []
        # live display: running average per pixel while the line is scanned
        v_sig = sgn * min(v, self._v0)                        # µm/s along the line
        edges = np.concatenate([xs - step / 2, [xs[-1] + step / 2]])
        sums = [np.zeros((nx, 4)), np.zeros((nx, 4))]
        cnt = [np.zeros(nx), np.zeros(nx)]
        while st.is_moving() and not self._stop:
            t0 = time.time()
            x, _ = st.position()
            t1 = time.time()
            pc, rf = self._read_both()
            t2 = time.time()
            pos.append(x)
            tp.append((t0 + t1) / 2)          # when the position was taken
            tr.append((t1 + t2) / 2)          # when the lock-ins were read
            pcs.append(pc)
            rfs.append(rf)
            xr = x + v_sig * (tr[-1] - tp[-1])
            for k, (val, lag, target) in enumerate(((pc, delay[0], d.pc),
                                                     (rf, delay[1], d.refl))):
                ix = int(np.digitize(xr - v_sig * lag, edges)) - 1
                if 0 <= ix < nx:
                    sums[k][ix] += val
                    cnt[k][ix] += 1
                    target[ib, iy, ix] = sums[k][ix] / cnt[k][ix]
                    if k == 0:
                        self.pixel.emit(ib, iy, ix, d.pc[ib, iy, ix], d.refl[ib, iy, ix])
        st.set_velocity(self._vel0)
        if len(pos) < 2:
            return
        pos, tp, tr = np.array(pos), np.array(tp), np.array(tr)
        vfit = np.polyfit(tp, pos, 1)[0] if len(pos) > 3 else sgn * v
        x_read = pos + vfit * (tr - tp)       # stage position at each reading
        pcs, rfs = np.array(pcs), np.array(rfs)
        # final, more precise pass with the measured speed (+ fill any gaps)
        for arr, lag, target in ((pcs, delay[0], d.pc), (rfs, delay[1], d.refl)):
            xc = x_read - vfit * lag                          # undo the lock-in delay
            idx = np.digitize(xc, edges) - 1
            out = np.full((nx, 4), np.nan)
            for ix in range(nx):
                sel = idx == ix
                if sel.any():
                    out[ix] = arr[sel].mean(axis=0)
            good = ~np.isnan(out[:, 0])
            if good.sum() >= 2:
                for c in range(4):
                    out[~good, c] = np.interp(xs[~good], xs[good], out[good, c])
            target[ib, iy] = out
        for ix in range(nx):
            self.pixel.emit(ib, iy, ix, d.pc[ib, iy, ix], d.refl[ib, iy, ix])

    def run(self):
        hw, s, d = self.hw, self.s, self.data
        nb, ny, nx = d.pc.shape[:3]
        total, done, t0 = nb * ny * nx, 0, time.time()
        lock = None
        raw_stage = hw.stage
        step = abs(d.xs[1] - d.xs[0]) if len(d.xs) > 1 else 1.0
        sx, sy = s.start_position()
        corners = [s.rotate_xy(x, y) for x in (d.xs[0], d.xs[-1]) for y in (d.ys[0], d.ys[-1])]
        hw.stage = SafeStage(raw_stage, [c[0] for c in corners] + [sx],
                             [c[1] for c in corners] + [sy], step)
        try:
            hw.stage.set_joystick(False)
            if s.power_lock:
                if self.pm is None:
                    raise RuntimeError("Power lock needs the power meter.")
                li = (hw.lockin_refl if self.lock_cfg.get("lockin") == "reflection"
                      else hw.lockin_pc)
                lock = PowerLock(self.pm, li, self.lock_cfg)
                self._at_ref = bool(self.lock_cfg.get("at_reference", True))
                if self._at_ref:
                    self._go_reference()
                target, amp0 = lock.start()
                d.power_lock = {"target_uW": target * 1e6, "start_amplitude_V": amp0,
                                "lockin": li.name,
                                **{k: v for k, v in self.lock_cfg.items() if k != "enabled"}}
                self.status.emit(f"Power lock on: target {target * 1e6:.2f} µW, "
                                 f"sine {amp0:.3f} V")
            if s.target_power_uW and self.pm is not None:
                li = (hw.lockin_refl if self.lock_cfg.get("lockin") == "reflection"
                      else hw.lockin_pc)
                hw.stage.move_to(*s.start_position())
                res = set_laser_power(self.pm, li, s.target_power_uW * 1e-6,
                                      s.power_tol_pct, self.lock_cfg.get("max_amplitude_V", 5.0),
                                      status=self.status.emit, stop=lambda: self._stop)
                d.power_lock = {"power_set": res}
                self.notify.emit(res["message"], "info" if res["ok"] else "warn")
                if not res["ok"] and "not" in res["message"].split(":")[0]:
                    raise RuntimeError(res["message"])
            t_start = time.time()
            self._t = {"move": 0.0, "wait": 0.0, "read": 0.0, "n": 0}
            if s.mode == "fly" and (s.angle_deg or 0) != 0:
                raise RuntimeError("Fly mode can't scan a rotated frame. "
                                   "Use Step mode, or set the angle to 0.")
            if s.mode == "fly":
                self._fly_setup()
            for ib, bias in enumerate(s.biases):
                if self._stop:
                    break
                if bias is not None:
                    self.status.emit(f"Ramping bias to {bias:g} V")
                    hw.bias.ramp_to(bias)
                # read front-panel settings for this map
                tau_pc = hw.lockin_pc.time_constant()
                tau_r = hw.lockin_refl.time_constant()
                d.map_info[ib] = {
                    "bias_V": bias, "tau_pc_s": tau_pc, "tau_refl_s": tau_r,
                    "sens_pc": hw.lockin_pc.sensitivity(),
                    "sens_refl": hw.lockin_refl.sensitivity(),
                    "overload_lines": 0}
                self._snapshot(ib, "start")
                self._tau = (tau_pc, tau_r)
                try:
                    self._poles = (hw.lockin_pc.slope_poles(), hw.lockin_refl.slope_poles())
                except Exception:
                    self._poles = (2, 2)
                wait = pixel_wait(s, self._tau, self._poles)
                d.map_info[ib]["wait_s"] = wait
                d.map_info[ib]["slope_poles"] = list(self._poles)
                label = "" if bias is None else f" @ {bias:g} V"
                rows = range(ny) if s.y_dir > 0 else range(ny - 1, -1, -1)
                for line, iy in enumerate(rows):
                    if self._stop:
                        break
                    forward = s.x_dir > 0
                    if s.serpentine and line % 2:
                        forward = not forward
                    tl = time.time()
                    if s.mode == "fly":
                        self._fly_line(ib, iy, forward, wait)
                    else:
                        self._step_line(ib, iy, forward, wait)
                    done += nx
                    line_s = time.time() - tl
                    if self.pm is not None and not self._stop:
                        try:
                            if lock is not None and self._at_ref:
                                self._go_reference()   # same spot every time
                            if lock is not None:
                                amp_before = lock.amp
                                p, amp, msg = lock.correct()
                                if msg:
                                    self.status.emit("Power lock: " + msg)
                                    self.notify.emit("Power lock: " + msg, "warn")
                                elif amp != amp_before:
                                    drift = (p / lock.target - 1) * 100
                                    self.notify.emit(
                                        f"Laser drift {drift:+.1f} % corrected (line {line + 1}): "
                                        f"sine {amp_before:.3f} → {amp:.3f} V", "info")
                            else:
                                p, amp = self.pm.power(), np.nan
                            d.power_log.append([ib, iy, time.time() - t_start, p * 1e6, amp,
                                                p / getattr(self.pm, "sample_factor", 1.0) * 1e6])
                        except Exception as e:
                            self.status.emit(f"Power meter read failed: {e}")
                    if hw.lockin_pc.overloaded():
                        d.map_info[ib]["overload_lines"] += 1
                        self.status.emit("Photocurrent lock-in OVERLOAD - "
                                         "check sensitivity on front panel")
                    el = time.time() - t0
                    self.progress.emit(done, total, el / done * (total - done))
                    self.status.emit(f"Map {ib + 1}/{nb}{label}, line {line + 1}/{ny}  ·  "
                                     + self._timing_text(line_s, nx))
                self._snapshot(ib, "end")
                d.save(self.path)  # autosave after each map
            d.completed = not self._stop
            if any(b is not None for b in s.biases):
                hw.bias.ramp_to(0.0)
            hw.stage.move_to(*s.start_position())   # back to where you started
            d.save(self.path)
            self.finished.emit(self.path)
        except InterruptedError:
            self.finished.emit(self.path)
            return
        except Exception:
            try:
                if any(b is not None for b in s.biases):
                    hw.bias.ramp_to(0.0)
            except Exception:
                pass
            try:
                d.save(self.path)
            except Exception:
                pass
            self.failed.emit(traceback.format_exc())
        finally:
            hw.stage = raw_stage
            if getattr(self, "_vel0", None) is not None:
                try:
                    hw.stage.set_velocity(self._vel0)
                except Exception:
                    pass
            try:
                hw.stage.set_joystick(True)
            except Exception:
                pass
