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
    invert: bool = False        # not used (the SR570 has its own INVERT button)
    aux_in: int = 1
    fit: bool = False           # straight-line fit I = V/R + I0 over the whole sweep
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


R_FIELD_MARK = "RFIT"     # stands for {R} in the name until the fit is known


def fit_line(a):
    """Straight line I = V/R + I0 through every point of the sweep (all cycles).
    a: rows [bias_V, current_A, ...]. -> dict R_ohm, R_err_ohm, I0_A, I0_err_A, n,
    r2 (how straight: 1 = perfectly), or None with fewer than 3 points / no bias
    range."""
    a = np.asarray(a, float)
    if a.ndim != 2 or len(a) < 3:
        return None
    V, I = a[:, 0], a[:, 1]
    ok = np.isfinite(V) & np.isfinite(I)
    V, I = V[ok], I[ok]
    if len(V) < 3 or np.ptp(V) <= 0:
        return None
    A = np.column_stack([V, np.ones_like(V)])
    (g, i0), *_ = np.linalg.lstsq(A, I, rcond=None)
    res = I - (g * V + i0)
    n = len(V)
    s2 = float(res @ res) / max(n - 2, 1)
    cov = s2 * np.linalg.inv(A.T @ A)
    g_err, i0_err = float(np.sqrt(cov[0, 0])), float(np.sqrt(cov[1, 1]))
    ss = float(((I - I.mean()) ** 2).sum())
    R = 1.0 / g if g != 0 else float("inf")
    return {"R_ohm": float(R), "R_err_ohm": float(g_err / g ** 2) if g != 0 else float("inf"),
            "I0_A": float(i0), "I0_err_A": i0_err, "n": int(n),
            "r2": 1.0 - float(res @ res) / ss if ss > 0 else 1.0,
            "conductance_S": float(g)}


def r_text(R, err=None, name=False):
    """12345 -> '12.3 kΩ' (or 'R12.3kOhm' for a file name)."""
    if R is None or not np.isfinite(R):
        return "Rinf" if name else "∞ Ω"
    for f, p in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m")):
        if abs(R) >= f * 0.9995:
            break
    if name:
        return f"R{R / f:.3g}{p}Ohm"
    e = f" ± {err / f:.2g}" if err is not None and np.isfinite(err) else ""
    return f"{R / f:.4g}{e} {p}Ω"


def rename_with_r(path, R):
    """Replace the {R} marker in the measurement's folder and file names by the fitted
    resistance (e.g. R12.3kOhm). -> new path of the .h5 (unchanged if no marker)."""
    folder = os.path.dirname(os.path.abspath(path))
    old = os.path.splitext(os.path.basename(path))[0]
    if R_FIELD_MARK not in old:
        return path
    new = old.replace(R_FIELD_MARK, r_text(R, name=True) if R is not None else "Rnone")
    parent = os.path.dirname(folder)
    target = os.path.join(parent, new) if os.path.basename(folder) == old else folder
    if target != folder:
        k = 2
        base = new
        while os.path.exists(target):            # name taken: _2, _3 ...
            new = f"{base}_{k}"
            target = os.path.join(parent, new)
            k += 1
        os.rename(folder, target)
    for f in os.listdir(target):
        if f.startswith(old):
            os.rename(os.path.join(target, f), os.path.join(target, new + f[len(old):]))
    return os.path.join(target, new + ".h5")


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
        from scanner import atomic_h5
        with atomic_h5(path) as f:
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
            ft = fit_line(self.array()) if getattr(s, "fit", False) else None
            if ft:
                g = f.create_group("fit")
                for k, v in ft.items():
                    g.attrs[k] = v
                g.attrs["note"] = ("straight line I = V/R + I0 through every point of the "
                                   "sweep (all cycles), least squares")
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
        ft = fit_line(a) if getattr(s, "fit", False) else None
        if ft:
            fh.write(f"\nFit (straight line I = V/R + I0 through all points, all cycles):\n"
                     f"  R = {r_text(ft['R_ohm'], ft['R_err_ohm'])}  "
                     f"(R = {ft['R_ohm']:.6e} ± {ft['R_err_ohm']:.2e} Ohm)\n"
                     f"  I0 = {ft['I0_A']:.4e} ± {ft['I0_err_A']:.1e} A (current at 0 V)\n"
                     f"  r² = {ft['r2']:.5f} (1 = perfectly straight), {ft['n']} points\n")
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
        ft = fit_line(a) if getattr(d.settings, "fit", False) else None
        if ft:
            vv = np.array([a[:, 0].min(), a[:, 0].max()])
            ax.plot(vv, (vv / ft["R_ohm"] + ft["I0_A"]) / scale, "--", color="#c62828",
                    lw=1.4, label=f"fit: R = {r_text(ft['R_ohm'], ft['R_err_ohm'])}")
        if len(cycles) > 1 or ft:
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
                end = time.time() + s.wait_ms / 1000      # wait, but react to Stop
                while time.time() < end and not self._stop:
                    time.sleep(min(0.05, max(end - time.time(), 0)))
                if self._stop:
                    d.stop_reason = "stopped by the user"
                    break
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
                if k % 25 == 24:                 # a failed autosave only warns
                    from scanner import autosave
                    autosave(d, self.path, self.notify)
            d.completed = len(d.rows) == len(pts) and not d.stop_reason
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
