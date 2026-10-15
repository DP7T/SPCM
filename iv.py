"""Quick I-V: DC current vs bias through the SR570, without touching the probes.

Set-up (by hand, the program reminds you):
  * SR570 filter OFF, gain mode LOW DRIFT
  * SR570 output cable moved from the lock-in input to AUX IN 1 of the
    photocurrent SR830 (the SR830 only digitizes it, ±10.5 V)
The bias comes from the SR570 (RS-232, 1 mV steps). Current = AUX IN voltage ×
SR570 sensitivity (A/V), which you type in because the SR570 can't be read.

Sweep: cycles of 0 -> max -> min -> 0.
Files: like every measurement, its own folder with .h5, CSV and PNG.
"""
import os
import time
import json
import traceback
from dataclasses import dataclass, field, asdict

import numpy as np

from scanner import QtCore

COLUMNS = ("bias_V", "current_A", "cycle", "time_s", "aux_in_V")
AUX_LIMIT_V = 10.0          # SR830 AUX IN range is ±10.5 V


@dataclass
class IVSettings:
    v_max: float = 1.0          # V
    v_min: float = -1.0         # V
    step: float = 0.01          # V (shown in mV)
    cycles: int = 1
    wait_ms: float = 100.0      # after each bias step, before reading
    n_avg: int = 5              # AUX IN readings averaged per point
    sensitivity: float = 1e-6   # SR570 A/V (front panel; can't be read)
    invert: bool = False        # SR570 output sign
    aux_in: int = 1
    sample: str = "sample"
    notes: str = ""
    conditions: dict = field(default_factory=dict)


def _seg(a, b, step):
    """a -> b in steps of `step` (the end included, the start not)."""
    if a == b:
        return []
    n = int(np.ceil(abs(b - a) / step - 1e-9))
    return list(np.linspace(a, b, n + 1)[1:])


def bias_points(s):
    """[(bias_V, cycle), ...] for cycles of 0 -> max -> min -> 0, on the
    SR570's 1 mV grid."""
    if s.step <= 0:
        raise ValueError("The step must be above 0.")
    if s.v_max < 0 or s.v_min > 0:
        raise ValueError("Use Max ≥ 0 and Min ≤ 0 (a cycle is 0 → max → min → 0).")
    pts = [(0.0, 1)]
    for c in range(1, int(s.cycles) + 1):
        for a, b in ((0.0, s.v_max), (s.v_max, s.v_min), (s.v_min, 0.0)):
            pts += [(v, c) for v in _seg(a, b, s.step)]
    return [(round(v * 1000) / 1000, c) for v, c in pts]


class IVData:
    def __init__(self, settings, hw_info):
        self.settings = settings
        self.hw_info = hw_info
        self.rows = []              # [bias_V, current_A, cycle, time_s, aux_in_V]
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.completed = False
        self.stop_reason = ""
        self.path = None

    def array(self):
        return np.array(self.rows, float).reshape(-1, len(COLUMNS))

    def save(self, path):
        """/iv            N x 5: bias (V), current (A), cycle, time (s), AUX IN (V)
        /info          settings, instruments, experiment notes"""
        import h5py
        self.path = path
        s = self.settings
        with h5py.File(path, "w") as f:
            f.attrs["format"] = "SPCM quick I-V v1"
            f.attrs["type"] = "iv"
            f.attrs["sample"] = s.sample
            f.attrs["started"] = self.started
            f.attrs["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.attrs["completed"] = self.completed
            f.attrs["stopped_because"] = self.stop_reason
            f.attrs["notes"] = s.notes
            info = f.create_group("info")
            info.attrs["settings"] = json.dumps(asdict(s))
            info.attrs["instruments"] = json.dumps(self.hw_info)
            info.attrs["conversion"] = (f"current = {'-' if s.invert else ''}AUX IN {s.aux_in} "
                                        f"voltage × {s.sensitivity:g} A/V (SR570)")
            exp = info.create_group("experiment")
            for k, v in s.conditions.items():
                exp.attrs[k] = str(v)
            exp.attrs["notes"] = s.notes
            ds = f.create_dataset("iv", data=self.array())
            ds.attrs["columns"] = ", ".join(COLUMNS)
            ds.attrs["note"] = ("one row per bias point: column 1 bias (V), column 2 "
                                "current (A), 3 cycle, 4 time (s), 5 AUX IN voltage (V)")


def is_iv_file(path):
    import h5py
    with h5py.File(path, "r") as f:
        return f.attrs.get("type", "") == "iv"


def load_iv(path):
    import h5py
    with h5py.File(path, "r") as f:
        st = json.loads(f["info"].attrs["settings"])
        known = set(IVSettings.__dataclass_fields__)
        d = IVData(IVSettings(**{k: v for k, v in st.items() if k in known}),
                   json.loads(f["info"].attrs["instruments"]))
        d.rows = np.asarray(f["iv"][()], float).tolist()
        d.completed = bool(f.attrs["completed"])
        d.started = str(f.attrs.get("started", ""))
        d.stop_reason = str(f.attrs.get("stopped_because", ""))
        d.path = path
    return d


def export_csv(d):
    """<name>_csv/iv.csv: numbers only, columns as in info.txt."""
    base = os.path.splitext(d.path)[0]
    out = base + "_csv"
    os.makedirs(out, exist_ok=True)
    a = d.array()
    with open(os.path.join(out, "iv.csv"), "w") as fh:
        for r in a:
            fh.write(",".join(f"{v:.6e}" for v in r) + "\n")
    s = d.settings
    with open(os.path.join(out, "info.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Quick I-V\nSample: {s.sample}\nStarted: {d.started}\n"
                 f"Completed: {d.completed}\n")
        if d.stop_reason:
            fh.write(f"Stopped because: {d.stop_reason}\n")
        fh.write("\niv.csv: numbers only, one row per bias point. Columns:\n"
                 "  1 bias (V), 2 current (A), 3 cycle, 4 time (s), 5 AUX IN voltage (V)\n")
        fh.write(f"Current = {'-' if s.invert else ''}AUX IN {s.aux_in} voltage × "
                 f"{s.sensitivity:g} A/V (SR570 sensitivity)\n")
        fh.write(f"Sweep: {s.cycles} cycle(s) of 0 → {s.v_max:g} → {s.v_min:g} → 0 V, "
                 f"step {s.step * 1000:g} mV, wait {s.wait_ms:g} ms, "
                 f"{s.n_avg} readings averaged per point\n")
        fh.write("\nExperiment:\n")
        for k, v in (s.conditions or {}).items():
            fh.write(f"  {k}: {v}\n")
        fh.write(f"\nNotes:\n{s.notes}\n\nInstruments: {json.dumps(d.hw_info)}\n")
    return out


def _prefix(vmax):
    for f, p in ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                 (1, "")):
        if vmax < f * 999.5:
            return f, p
    return 1.0, ""


def save_figure(d, path, dpi=200):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    a = d.array()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, ax = plt.subplots(figsize=(5.6, 4.4))
    if len(a):
        scale, pre = _prefix(np.nanmax(np.abs(a[:, 1])) or 1.0)
        cycles = sorted(set(a[:, 2].astype(int)))
        colors = plt.cm.viridis(np.linspace(0, 0.85, max(len(cycles), 1)))
        for c, col in zip(cycles, colors):
            sel = a[:, 2] == c
            ax.plot(a[sel, 0], a[sel, 1] / scale, "-o", ms=2.5, lw=1.2, color=col,
                    label=f"cycle {c}" if len(cycles) > 1 else None)
        ax.set_ylabel(f"Current ({pre}A)")
        if len(cycles) > 1:
            ax.legend(fontsize=9)
    ax.axhline(0, color="0.7", lw=0.8)
    ax.axvline(0, color="0.7", lw=0.8)
    ax.set_xlabel("Bias (V)")
    ax.set_title(f"I–V  {d.settings.sample}", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path


def save_all(d):
    return [save_figure(d, os.path.splitext(d.path)[0] + ".png")]


class IVWorker(QtCore.QObject):
    point = QtCore.Signal()
    status = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, float)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    notify = QtCore.Signal(str, str)

    def __init__(self, hw, settings, save_dir, name=None):
        super().__init__()
        self.hw, self.s = hw, settings
        self._stop = False
        self.data = IVData(settings, hw.describe())
        import naming
        self.path = naming.measurement_path(save_dir, name or naming.default_name(
            settings.sample, "IV"))
        self.data.path = self.path

    def stop(self):
        self._stop = True

    def _read(self):
        li, s = self.hw.lockin_pc, self.s
        vals = [li.aux_in(s.aux_in) for _ in range(max(int(s.n_avg), 1))]
        return float(np.mean(vals))

    def run(self):
        hw, s, d = self.hw, self.s, self.data
        pts = bias_points(s)
        sign = -1.0 if s.invert else 1.0
        t0 = time.time()
        try:
            for k, (v, cyc) in enumerate(pts):
                if self._stop:
                    d.stop_reason = "stopped by the user"
                    break
                hw.bias.ramp_to(v)
                time.sleep(s.wait_ms / 1000)
                aux = self._read()
                d.rows.append([v, sign * aux * s.sensitivity, cyc, time.time() - t0, aux])
                self.point.emit()
                if abs(aux) > AUX_LIMIT_V:
                    d.stop_reason = (f"SR570 output {aux:+.2f} V at {v:+.3f} V bias: above "
                                     f"the AUX IN range. Use a less sensitive SR570 setting "
                                     "or a smaller bias range.")
                    self.notify.emit("I–V stopped: " + d.stop_reason, "warn")
                    break
                el = time.time() - t0
                self.progress.emit(k + 1, len(pts), el / (k + 1) * (len(pts) - k - 1))
                if k % 25 == 24:
                    d.save(self.path)
            d.completed = len(d.rows) == len(pts)
            self.status.emit("Ramping bias back to 0 V")
            hw.bias.ramp_to(0.0)
            d.save(self.path)
            self.finished.emit(self.path)
        except Exception:
            try:
                hw.bias.ramp_to(0.0)
            except Exception:
                pass
            try:
                d.save(self.path)
            except Exception:
                pass
            self.failed.emit(traceback.format_exc())
