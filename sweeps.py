"""Single-point sweeps: the laser stays on one spot and the program steps the
laser power, the bias, or both (bias outer loop, power inner loop).

Each point:  set the power (lock-in SINE OUT: the 2 mV step that gives the
power closest to the target, checked with the power meter) and/or ramp
the bias -> wait until the lock-ins have settled -> take N readings of both
lock-ins and the power meter, spaced by about one time constant -> save the
mean and the spread (standard deviation).

Files: <stamp>_<sample>_sweep.h5, a _csv folder and PNG figures, like maps.
"""
import os
import time
import json
import traceback
from dataclasses import dataclass, field, asdict

import numpy as np

from scanner import (QtCore, CHANNELS, pc_scale, signal_kind, pixel_wait,
                     set_laser_power, ramp_sine)

KINDS = {"power": "Power sweep", "bias": "Bias sweep", "bias_power": "Bias × power sweep"}


@dataclass
class SweepSettings:
    kind: str = "power"             # "power", "bias" or "bias_power"
    powers_uW: list = field(default_factory=list)   # power on the sample
    biases: list = field(default_factory=list)      # V
    n_avg: int = 10                 # readings per point
    max_amp_V: float = 5.0
    laser_lockin: str = "photocurrent"
    restore_power: bool = True      # put the SINE OUT back afterwards
    x_stage: float = 0.0            # where the laser is (stage µm)
    y_stage: float = 0.0
    u: float = 0.0                  # same spot from the origin (0, 0), program axes
    v: float = 0.0
    settle_auto: bool = True
    settle_tc: float = 3.0
    extra_settle_ms: float = 0.0
    amp_mode: str = "lockin"
    amp_value: float = 1.0
    signal_as: str = "peak"
    lockin_unit: str = "V"
    sample: str = "sample"
    notes: str = ""
    conditions: dict = field(default_factory=dict)

    def bias_list(self):
        return list(self.biases) if self.kind in ("bias", "bias_power") else [None]

    def power_list(self):
        return list(self.powers_uW) if self.kind in ("power", "bias_power") else [None]


class SweepData:
    """Arrays are [bias index, power index, channel]."""

    def __init__(self, settings, hw_info):
        self.settings = settings
        self.hw_info = hw_info
        self.biases = settings.bias_list()
        self.powers = settings.power_list()
        nb, npw = len(self.biases), len(self.powers)
        self.pc = np.full((nb, npw, 4), np.nan)          # mean (raw lock-in, rms)
        self.pc_std = np.full((nb, npw, 4), np.nan)
        self.refl = np.full((nb, npw, 4), np.nan)
        self.refl_std = np.full((nb, npw, 4), np.nan)
        self.p_uW = np.full((nb, npw), np.nan)           # measured on the sample
        self.p_std_uW = np.full((nb, npw), np.nan)
        self.amp_V = np.full((nb, npw), np.nan)          # SINE OUT amplitude
        self.t_s = np.full((nb, npw), np.nan)
        self.info = {}
        self.started = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.completed = False
        self.path = None

    # ---- what to plot -------------------------------------------------
    def x_axis(self):
        """-> (name, unit, values per [ib, ip]) for the horizontal axis."""
        k = self.settings.kind
        if k == "bias":
            return "Bias", "V", np.array([[b] for b in self.biases], float)
        return "Power on sample", "µW", self.p_uW

    def signal(self, channel=0):
        """Converted signal and spread for a channel (0 X, 1 Y, 2 R, 3 θ)."""
        fac, unit, _ = pc_scale(self.settings)
        unit = unit or getattr(self.settings, "lockin_unit", "V")
        if channel == 3:
            return self.pc[..., 3], self.pc_std[..., 3], "°"
        return self.pc[..., channel] * fac, self.pc_std[..., channel] * abs(fac), unit

    def fits(self, channel=0):
        """Power law |S| = A · P^α per bias row -> list of dict or None."""
        if self.settings.kind not in ("power", "bias_power"):
            return [None] * len(self.biases)
        sig, _, _ = self.signal(channel)
        return [fit_power_law(self.p_uW[ib], sig[ib]) for ib in range(len(self.biases))]

    # ---- saving -------------------------------------------------------
    def save(self, path):
        """/info                 settings, instruments, conversion, position
        bias_V, power_set_uW     the sweep values (rows = bias, columns = power)
        power_on_sample_uW ...   2-D datasets [bias, power]
        photocurrent_X/_Y/_R/_theta_deg  (+ _std): converted, mean of N readings
        reflection_*             (+ _std)
        raw/                     lock-in readings as measured (rms)
        fit_alpha, fit_A         power law per bias row (power sweeps)"""
        import h5py
        self.path = path
        s = self.settings
        fac, unit, formula = pc_scale(s)
        unit = unit or s.lockin_unit
        kind = signal_kind(s)
        with h5py.File(path, "w") as f:
            f.attrs["format"] = "SPCM point sweep v1"
            f.attrs["type"] = "point_sweep"
            f.attrs["sweep"] = s.kind
            f.attrs["sample"] = s.sample
            f.attrs["signal"] = kind
            f.attrs["started"] = self.started
            f.attrs["saved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.attrs["completed"] = self.completed
            f.attrs["notes"] = s.notes
            f.attrs["position_um"] = [s.u, s.v]
            f.attrs["position_stage_um"] = [s.x_stage, s.y_stage]
            info = f.create_group("info")
            info.attrs["settings"] = json.dumps(asdict(s))
            info.attrs["instruments"] = json.dumps(self.hw_info)
            info.attrs["conversion"] = f"{formula} [{unit}]"
            info.attrs["lockins"] = json.dumps(self.info, default=float)
            exp = info.create_group("experiment")
            for k, v in s.conditions.items():
                exp.attrs[k] = str(v)
            exp.attrs["notes"] = s.notes
            f["bias_V"] = np.array([np.nan if b is None else b for b in self.biases], float)
            f["power_set_uW"] = np.array([np.nan if p is None else p for p in self.powers],
                                         float)
            f["power_on_sample_uW"] = self.p_uW
            f["power_on_sample_std_uW"] = self.p_std_uW
            f["sine_amplitude_V"] = self.amp_V
            f["time_s"] = self.t_s
            f.attrs["n_readings_per_point"] = s.n_avg
            for c, ch in enumerate(CHANNELS):
                nm = ch if c < 3 else "theta_deg"
                k = fac if c < 3 else 1.0
                d = f.create_dataset(f"photocurrent_{nm}", data=self.pc[..., c] * k)
                d.attrs["unit"] = unit if c < 3 else "deg"
                d.attrs["signal"] = kind
                f[f"photocurrent_{nm}_std"] = self.pc_std[..., c] * abs(k)
                f[f"reflection_{nm}"] = self.refl[..., c]
                f[f"reflection_{nm}_std"] = self.refl_std[..., c]
                f[f"raw/photocurrent_{nm}"] = self.pc[..., c]
                f[f"raw/photocurrent_{nm}_std"] = self.pc_std[..., c]
                f[f"raw/reflection_{nm}"] = self.refl[..., c]
                f[f"raw/reflection_{nm}_std"] = self.refl_std[..., c]
            f["photocurrent_X"].attrs["conversion"] = formula
            fits = self.fits(0)
            if any(fits):
                f["fit_alpha"] = [np.nan if r is None else r["alpha"] for r in fits]
                f["fit_alpha_err"] = [np.nan if r is None else r["alpha_err"] for r in fits]
                f["fit_A"] = [np.nan if r is None else r["A"] for r in fits]
                f["fit_alpha"].attrs["note"] = ("|photocurrent_X| = A · P^alpha, P in µW, "
                                                "one value per bias row")


def fit_power_law(p, s):
    """Fit |s| = A p^α on log-log axes. -> dict(alpha, alpha_err, A, n) or None."""
    p, s = np.asarray(p, float), np.abs(np.asarray(s, float))
    ok = np.isfinite(p) & np.isfinite(s) & (p > 0) & (s > 0)
    if ok.sum() < 3:
        return None
    x, y = np.log10(p[ok]), np.log10(s[ok])
    if np.ptp(x) == 0:
        return None
    a, b = np.polyfit(x, y, 1)
    n = len(x)
    res = y - (a * x + b)
    err = np.sqrt(np.sum(res ** 2) / max(n - 2, 1) / np.sum((x - x.mean()) ** 2))
    return {"alpha": float(a), "alpha_err": float(err), "A": float(10 ** b), "n": int(n)}


def load_sweep(path):
    import h5py
    with h5py.File(path, "r") as f:
        st = json.loads(f["info"].attrs["settings"])
        known = set(SweepSettings.__dataclass_fields__)
        s = SweepSettings(**{k: v for k, v in st.items() if k in known})
        d = SweepData(s, json.loads(f["info"].attrs["instruments"]))
        for c, ch in enumerate(CHANNELS):
            nm = ch if c < 3 else "theta_deg"
            d.pc[..., c] = f[f"raw/photocurrent_{nm}"][()]
            d.pc_std[..., c] = f[f"raw/photocurrent_{nm}_std"][()]
            d.refl[..., c] = f[f"raw/reflection_{nm}"][()]
            d.refl_std[..., c] = f[f"raw/reflection_{nm}_std"][()]
        d.p_uW = f["power_on_sample_uW"][()]
        d.p_std_uW = f["power_on_sample_std_uW"][()]
        d.amp_V = f["sine_amplitude_V"][()]
        d.t_s = f["time_s"][()]
        d.info = json.loads(f["info"].attrs.get("lockins", "{}"))
        d.completed = bool(f.attrs["completed"])
        d.started = str(f.attrs.get("started", ""))
        d.path = path
    return d


def is_sweep_file(path):
    import h5py
    with h5py.File(path, "r") as f:
        return f.attrs.get("type", "") == "point_sweep"


def export_csv(d):
    """<name>_csv/sweep.csv: one row per point with every column, plus
    (bias × power sweeps) photocurrent_X_matrix.csv: rows bias, columns power."""
    base = os.path.splitext(d.path)[0]
    out = base + "_csv"
    os.makedirs(out, exist_ok=True)
    s = d.settings
    fac, unit, formula = pc_scale(s)
    unit = unit or s.lockin_unit
    cols = ["bias_V", "power_set_uW", "power_on_sample_uW", "power_std_uW",
            "sine_amplitude_V", "time_s"]
    for name, u in (("photocurrent", unit), ("reflection", "V")):
        for ch in CHANNELS:
            uu = "deg" if ch == "theta" else u
            cols += [f"{name}_{ch}_{uu}", f"{name}_{ch}_std_{uu}"]
    rows = []
    for ib, b in enumerate(d.biases):
        for ip, p in enumerate(d.powers):
            r = [np.nan if b is None else b, np.nan if p is None else p,
                 d.p_uW[ib, ip], d.p_std_uW[ib, ip], d.amp_V[ib, ip], d.t_s[ib, ip]]
            for arr, sd, k in ((d.pc, d.pc_std, fac), (d.refl, d.refl_std, 1.0)):
                for c in range(4):
                    kk = k if c < 3 else 1.0
                    r += [arr[ib, ip, c] * kk, sd[ib, ip, c] * abs(kk)]
            rows.append(r)
    with open(os.path.join(out, "sweep.csv"), "w") as fh:      # numbers only
        for r in rows:
            fh.write(",".join("" if not np.isfinite(v) else f"{v:.6e}" for v in r) + "\n")
    if s.kind == "bias_power":
        m = d.pc[..., 0] * fac
        with open(os.path.join(out, "photocurrent_X_matrix.csv"), "w") as fh:
            for ib in range(len(d.biases)):          # numbers only (axes: info.txt)
                fh.write(",".join("" if not np.isfinite(v) else f"{v:.6e}"
                                  for v in m[ib]) + "\n")
    with open(os.path.join(out, "info.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{KINDS.get(s.kind, s.kind)}\nSample: {s.sample}\nStarted: {d.started}\n"
                 f"Completed: {d.completed}\n")
        fh.write(f"Position: ({s.u:.3f}, {s.v:.3f}) µm from the origin = stage "
                 f"({s.x_stage:.3f}, {s.y_stage:.3f}) µm\n")
        fh.write(f"Photocurrent conversion: {formula} [{unit}]\n")
        fh.write(f"Each point: mean of {s.n_avg} readings; *_std = their standard "
                 "deviation\n")
        fh.write("sweep.csv: numbers only, one row per point. Columns:\n")
        for i, c in enumerate(cols, 1):
            fh.write(f"  {i}: {c}\n")
        if s.kind == "bias_power":
            fh.write(f"photocurrent_X_matrix.csv: numbers only, {signal_kind(s)} X in "
                     f"{unit}; one row per bias ({', '.join(f'{b:g}' for b in d.biases)} V), "
                     f"one column per set power ({', '.join(f'{p:g}' for p in d.powers)} µW)\n")
        for ib, r in enumerate(d.fits(0)):
            if r:
                b = d.biases[ib]
                fh.write(f"Power law{'' if b is None else f' at {b:g} V'}: |X| = "
                         f"{r['A']:.4g} · P^{r['alpha']:.3f} (± {r['alpha_err']:.3f}), "
                         "P in µW\n")
        fh.write("\nExperiment:\n")
        for k, v in (s.conditions or {}).items():
            fh.write(f"  {k}: {v}\n")
        fh.write(f"\nNotes:\n{s.notes}\n\nSettings:\n")
        for k, v in asdict(s).items():
            if k not in ("conditions", "notes"):
                fh.write(f"  {k}: {v}\n")
        fh.write(f"\nLock-ins: {json.dumps(d.info, default=float)}\n")
        fh.write(f"Instruments: {json.dumps(d.hw_info)}\n")
    return out


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------
def _prefix(vmax):
    for f, p in ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                 (1, "")):
        if vmax < f * 999.5:
            return f, p
    return 1.0, ""


def _edges(c, log):
    c = np.asarray(c, float)
    if len(c) == 1:
        return (c[0] / 1.5, c[0] * 1.5) if log else (c[0] - 0.5, c[0] + 0.5)
    t = np.log10(c) if log else c
    e = np.concatenate([[t[0] - (t[1] - t[0]) / 2], (t[1:] + t[:-1]) / 2,
                        [t[-1] + (t[-1] - t[-2]) / 2]])
    return 10 ** e if log else e


def _plain_log_ticks(axis):
    """Log axis labelled 1, 2, 5, 10, 20, 50 ... instead of 2×10^1."""
    from matplotlib.ticker import LogLocator, FuncFormatter, NullFormatter
    axis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
    axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    axis.set_minor_formatter(NullFormatter())


def save_figure(d, path, channel=0, dpi=200):
    """Power: linear + log-log with the fitted slope. Bias: signal vs bias.
    Bias × power: curves per bias (linear + log-log) and a bias-power map."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = d.settings
    kind = signal_kind(s)
    sig, sd, unit = d.signal(channel)
    ch = CHANNELS[channel]
    finite = np.abs(sig[np.isfinite(sig)])
    scale, pre = _prefix(finite.max() if finite.size else 1.0)
    ylab = f"{kind} {ch} ({pre}{unit})"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    nb = len(d.biases)
    colors = plt.cm.viridis(np.linspace(0, 0.9, max(nb, 1)))
    fits = d.fits(channel)
    where = f"({s.u:.2f}, {s.v:.2f}) µm"

    def label(ib):
        b = d.biases[ib]
        return "" if b is None else f"{b:g} V"

    if s.kind == "bias":
        fig, ax = plt.subplots(figsize=(5.6, 4.2))
        x = np.array([b for b in d.biases], float)
        ax.errorbar(x, sig[:, 0] / scale, yerr=sd[:, 0] / scale, fmt="o-", ms=4,
                    capsize=2, color="#1f5fa8")
        ax.axhline(0, color="0.6", lw=0.8)
        ax.set_xlabel("Bias (V)")
        ax.set_ylabel(ylab)
        ax.set_title(f"{kind} vs bias at {where}", fontsize=11)
        axes = [ax]
    else:
        ncol = 3 if s.kind == "bias_power" else 2
        fig, axes = plt.subplots(1, ncol, figsize=(5.0 * ncol, 4.2))
        a_lin, a_log = axes[0], axes[1]
        for ib in range(nb):
            p = d.p_uW[ib]
            a_lin.errorbar(p, sig[ib] / scale, yerr=sd[ib] / scale, fmt="o-", ms=4,
                           capsize=2, color=colors[ib], label=label(ib) or None)
            ok = (p > 0) & (np.abs(sig[ib]) > 0)
            r = fits[ib]
            lab = label(ib)
            if r:
                lab = (lab + ": " if lab else "") + f"α = {r['alpha']:.2f} ± {r['alpha_err']:.2f}"
            a_log.errorbar(p[ok], np.abs(sig[ib][ok]) / scale, yerr=sd[ib][ok] / scale,
                           fmt="o", ms=4, capsize=2, color=colors[ib], label=lab or None)
            if r:
                pp = np.geomspace(np.nanmin(p[ok]), np.nanmax(p[ok]), 50)
                a_log.plot(pp, r["A"] * pp ** r["alpha"] / scale, "-", color=colors[ib],
                           lw=1)
        a_lin.axhline(0, color="0.6", lw=0.8)
        a_lin.set_xlabel("Power on sample (µW)")
        a_lin.set_ylabel(ylab)
        a_log.set_xscale("log")
        a_log.set_yscale("log")
        _plain_log_ticks(a_log.xaxis)
        _plain_log_ticks(a_log.yaxis)
        a_log.set_xlabel("Power on sample (µW)")
        a_log.set_ylabel(f"|{kind} {ch}| ({pre}{unit})")
        a_lin.set_title(f"{kind} vs power at {where}", fontsize=11)
        a_log.set_title("log-log, fit |S| = A·P$^α$", fontsize=11)
        if nb > 1 or any(fits):
            a_log.legend(fontsize=8, title="Bias" if s.kind == "bias_power" else None,
                         title_fontsize=8)
        if nb > 1:
            a_lin.legend(fontsize=8, title="Bias", title_fontsize=8)
        if s.kind == "bias_power":
            ax = axes[2]
            pw = np.array(d.powers, float)
            log = bool(np.all(pw > 0) and pw.max() / pw.min() > 10)
            bx = np.array(d.biases, float)
            order = np.argsort(bx)
            m = sig[order] / scale
            lim = np.nanpercentile(np.abs(m), 99.5) if np.isfinite(m).any() else 1
            pcm = ax.pcolormesh(_edges(pw, log), _edges(bx[order], False), m, cmap="bwr",
                                vmin=-lim, vmax=lim, shading="flat")
            if log:
                ax.set_xscale("log")
                _plain_log_ticks(ax.xaxis)
            ax.set_xlabel("Power on sample, set (µW)")
            ax.set_ylabel("Bias (V)")
            ax.set_title(f"{kind} {ch}", fontsize=11)
            cb = fig.colorbar(pcm, ax=ax)
            cb.set_label(ylab)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path


def save_all(d):
    return [save_figure(d, os.path.splitext(d.path)[0] + ".png")]


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------
class SweepWorker(QtCore.QObject):
    point = QtCore.Signal(int, int)                       # ib, ip just measured
    status = QtCore.Signal(str)
    progress = QtCore.Signal(int, int, float)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    notify = QtCore.Signal(str, str)

    def __init__(self, hw, settings, save_dir, name=None):
        super().__init__()
        self.hw, self.s, self.save_dir = hw, settings, save_dir
        self.pm = next((a for a in hw.accessories if a.name == "powermeter"), None)
        self._stop = False
        self.data = SweepData(settings, hw.describe())
        import naming
        self.path = naming.measurement_path(save_dir, name or naming.default_name(
            settings.sample, settings.kind.replace("_", "") + "sweep"))
        self.data.path = self.path

    def stop(self):
        self._stop = True

    def _sleep(self, t):
        end = time.time() + t
        while time.time() < end and not self._stop:
            time.sleep(min(0.05, max(end - time.time(), 0)))

    def _power(self):
        if self.pm is None:
            return np.nan
        try:
            return self.pm.power() / (getattr(self.pm, "sample_factor", 1.0) or 1.0) * 1e6
        except Exception:
            return np.nan

    def _measure(self, ib, ip, spacing):
        hw, d, n = self.hw, self.data, max(int(self.s.n_avg), 1)
        pcs, rfs, ps = [], [], []
        for k in range(n):
            if self._stop:
                break
            t0 = time.time()
            pcs.append(hw.lockin_pc.read())
            rfs.append(hw.lockin_refl.read())
            ps.append(self._power())
            if k < n - 1:
                self._sleep(max(0.0, spacing - (time.time() - t0)))
        if not pcs:
            return False
        pcs, rfs, ps = np.array(pcs), np.array(rfs), np.array(ps)
        d.pc[ib, ip], d.refl[ib, ip] = pcs.mean(0), rfs.mean(0)
        d.pc_std[ib, ip] = pcs.std(0, ddof=1) if len(pcs) > 1 else 0.0
        d.refl_std[ib, ip] = rfs.std(0, ddof=1) if len(rfs) > 1 else 0.0
        if np.isfinite(ps).any():
            d.p_uW[ib, ip] = np.nanmean(ps)
            d.p_std_uW[ib, ip] = np.nanstd(ps, ddof=1) if np.isfinite(ps).sum() > 1 else 0.0
        return len(pcs) == n

    def run(self):
        hw, s, d = self.hw, self.s, self.data
        biases, powers = d.biases, d.powers
        total, done, t0 = len(biases) * len(powers), 0, time.time()
        li = hw.lockin_refl if s.laser_lockin == "reflection" else hw.lockin_pc
        amp0 = None
        use_bias = any(b is not None for b in biases)
        try:
            if any(p is not None for p in powers):
                if self.pm is None:
                    raise RuntimeError("A power sweep needs the power meter (PM100). "
                                       "Turn it on in Hardware → Settings.")
                amp0 = li.sine_amplitude()
            hw.stage.set_joystick(False)       # nothing may move the spot
            try:
                x, y = hw.stage.position()
                d.info["stage_at_start_um"] = [x, y]
            except Exception:
                pass
            tau = (hw.lockin_pc.time_constant(), hw.lockin_refl.time_constant())
            try:
                poles = (hw.lockin_pc.slope_poles(), hw.lockin_refl.slope_poles())
            except Exception:
                poles = (2, 2)
            wait = pixel_wait(s, tau, poles)
            spacing = max(tau)                   # readings ~1 τ apart
            d.info.update({"tau_pc_s": tau[0], "tau_refl_s": tau[1],
                           "sens_pc": hw.lockin_pc.sensitivity(),
                           "sens_refl": hw.lockin_refl.sensitivity(),
                           "slope_poles": list(poles), "settle_wait_s": wait,
                           "reading_spacing_s": spacing,
                           "sine_amplitude_before_V": amp0})
            for ib, b in enumerate(biases):
                if self._stop:
                    break
                if b is not None:
                    self.status.emit(f"Ramping bias to {b:g} V")
                    hw.bias.ramp_to(b)
                    self._sleep(wait)
                for ip, p in enumerate(powers):
                    if self._stop:
                        break
                    where = ("" if b is None else f"{b:g} V") + \
                            ("" if p is None else (", " if b is not None else "") + f"{p:g} µW")
                    if p is not None:
                        res = set_laser_power(self.pm, li, p * 1e-6, 0,   # closest 2 mV step
                                              s.max_amp_V, status=self.status.emit,
                                              stop=lambda: self._stop)
                        if res.get("aborted") or self._stop:
                            break
                        if not res["ok"]:
                            self.notify.emit(f"{where}: {res['message']}", "warn")
                            if res["message"].startswith(("Laser power not reached",
                                                          "Laser power not set")):
                                if res["message"].startswith("Laser power not set"):
                                    raise RuntimeError(res["message"])
                                # higher powers in this row are out of reach too
                                d.amp_V[ib, ip] = res["amplitude_V"]
                                self._measure_point(ib, ip, wait, spacing, where)
                                done += len(powers) - ip
                                break
                        d.amp_V[ib, ip] = res["amplitude_V"]
                    elif amp0 is not None:
                        d.amp_V[ib, ip] = amp0
                    self._measure_point(ib, ip, wait, spacing, where)
                    done += 1
                    el = time.time() - t0
                    self.progress.emit(done, total, el / done * (total - done))
                d.save(self.path)                # autosave after every bias row
            d.completed = not self._stop
            self._finish(li, amp0, use_bias)
            d.save(self.path)
            self.finished.emit(self.path)
        except Exception:
            try:
                self._finish(li, amp0, use_bias)
            except Exception:
                pass
            try:
                d.save(self.path)
            except Exception:
                pass
            self.failed.emit(traceback.format_exc())
        finally:
            try:
                hw.stage.set_joystick(True)
            except Exception:
                pass

    def _measure_point(self, ib, ip, wait, spacing, where):
        self.status.emit(f"{where}: waiting for the lock-ins to settle…")
        self._sleep(wait)
        if self._stop:
            return
        self.status.emit(f"{where}: {self.s.n_avg} readings…")
        self.data.t_s[ib, ip] = time.time()
        self._measure(ib, ip, spacing)
        self.point.emit(ib, ip)

    def _finish(self, li, amp0, use_bias):
        if use_bias:
            self.status.emit("Ramping bias back to 0 V")
            self.hw.bias.ramp_to(0.0)
        if amp0 is not None and self.s.restore_power:
            self.status.emit(f"Laser power back: SINE OUT to {amp0:.3f} V in 2 mV steps")
            ramp_sine(li, amp0)
        t = self.data.t_s
        if np.isfinite(t).any():
            self.data.t_s = t - np.nanmin(t)
