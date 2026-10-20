"""Line scans: the laser steps along a straight line (drawn on a map or on the
calibrated camera image), at any angle. With a series of biases or laser
powers, each value gives one line, so the result is a position × bias (or
position × power) image.

A line scan is a normal scan with one row (ScanSettings.scan_type = "line",
y_size = 0, rotated to the line's direction), so it uses the same scan engine,
safety checks, laser-power setting and file format as maps. In the .h5 each
/mapN group has 1-row images plus distance_um and the (x, y) of every point.
"""
import os
import json
from dataclasses import asdict

import numpy as np

from scanner import ScanSettings, pc_scale, signal_kind, CHANNELS, outer_name


def line_settings(start, end, step_um, biases=None, origin=(0.0, 0.0),
                  flips=(False, False), outer="bias", values=None, **kw):
    """start, end: stage coordinates (µm) of the two ends of the line.
    The step is adjusted slightly so the last point lands exactly on the end.
    outer: what changes from line to line, "bias" (biases, V) or "power"
    (values = laser power on the sample, µW)."""
    (x0, y0), (x1, y1) = start, end
    L = float(np.hypot(x1 - x0, y1 - y0))
    if L <= 0:
        raise ValueError("The line has zero length.")
    n = max(int(round(L / step_um)), 1) + 1
    step = L / (n - 1)
    ang = float(np.degrees(np.arctan2(y1 - y0, x1 - x0)))
    kw.setdefault("serpentine", False)
    kw["mode"] = "step"
    return ScanSettings(x_center=x0, y_center=y0, origin="corner", x_dir=1, y_dir=1,
                        x_size=L, y_size=0.0, step=step, angle_deg=ang,
                        origin_x=origin[0], origin_y=origin[1],
                        flip_x=bool(flips[0]), flip_y=bool(flips[1]),
                        biases=list(biases) if (biases and outer == "bias") else [None],
                        outer=outer,
                        outer_values=list(values or []) if outer == "power" else [],
                        scan_type="line", **kw)


def is_line(d):
    return getattr(d.settings, "scan_type", "map") == "line"


def geometry(d):
    """-> distance (µm), user x, user y of every point along the line."""
    s = d.settings
    xs = np.asarray(d.xs, float)
    X, Y = s.rotate_xy(xs, np.full(len(xs), d.ys[0]))
    ox = s.origin_x if s.origin_x is not None else s.x_center
    oy = s.origin_y if s.origin_y is not None else s.y_center
    u = (-1 if s.flip_x else 1) * (X - ox) + 0.0
    v = (-1 if s.flip_y else 1) * (Y - oy) + 0.0
    return xs - xs[0], u, v


def signal(d, channel=0):
    """[bias, point] arrays of the converted signal and the reflection."""
    fac, unit, _ = pc_scale(d.settings)
    unit = unit or getattr(d.settings, "lockin_unit", "V")
    if channel == 3:
        return d.pc[:, 0, :, 3], "°"
    return d.pc[:, 0, :, channel] * fac, unit


def export_csv(d):
    """<name>_csv/line.csv: numbers only, one row per point; columns: distance,
    x, y, then per line (bias / power) the photocurrent X, Y, R, theta and the
    reflection X, Y, R, theta. + photocurrent_X_matrix.csv for a series (rows =
    lines, columns = points). Everything is described in info.txt."""
    base = os.path.splitext(d.path)[0]
    out = base + "_csv"
    os.makedirs(out, exist_ok=True)
    s = d.settings
    fac, unit, formula = pc_scale(s)
    unit = unit or s.lockin_unit
    dist, u, v = geometry(d)
    vals = s.outer_list()
    pname, punit = outer_name(s)
    cols, data = ["distance (µm)", "x (µm)", "y (µm)"], [dist, u, v]
    for ib, b in enumerate(vals):
        tag = "" if b is None else f" at {b:g} {punit}"
        for name, arr, k, uu in (("photocurrent", d.pc, fac, unit),
                                 ("reflection", d.refl, 1.0, "V")):
            for c, ch in enumerate(CHANNELS):
                cols.append(f"{name} {ch}{tag} ({uu if c < 3 else 'deg'})")
                data.append(arr[ib, 0, :, c] * (k if c < 3 else 1.0))
    with open(os.path.join(out, "line.csv"), "w") as fh:
        for row in np.array(data).T:
            fh.write(",".join("" if not np.isfinite(x) else f"{x:.6e}" for x in row) + "\n")
    series = any(b is not None for b in vals)
    if series:
        m = d.pc[:, 0, :, 0] * fac
        with open(os.path.join(out, "photocurrent_X_matrix.csv"), "w") as fh:
            for ib in range(len(vals)):
                fh.write(",".join("" if not np.isfinite(x) else f"{x:.6e}"
                                  for x in m[ib]) + "\n")
    with open(os.path.join(out, "info.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Line scan\nSample: {s.sample}\nStarted: {d.started}\n"
                 f"Completed: {d.completed}\n")
        fh.write(f"From ({u[0]:.3f}, {v[0]:.3f}) to ({u[-1]:.3f}, {v[-1]:.3f}) µm "
                 f"(from the origin), {dist[-1]:.3f} µm long, {len(dist)} points, "
                 f"step {s.step * 1000:.1f} nm\n")
        fh.write(f"Photocurrent conversion: {formula} [{unit}]\n\n")
        ph = getattr(d, "phase", None)
        if ph:
            fh.write(f"PHASE-CORRECTED COPY of {ph.get('source_file', '')}: photocurrent X, Y, "
                     f"theta rotated by {ph.get('phase_deg', 0):.2f}° (X' = X cos + Y sin), "
                     f"background subtracted: {ph.get('offset', [0, 0])} "
                     f"{ph.get('offset_unit', '')}\n  {ph.get('message', '')}\n\n")
        fh.write("line.csv: numbers only, one row per point. Columns:\n")
        for i, c in enumerate(cols, 1):
            fh.write(f"  {i}: {c}\n")
        if series:
            fh.write(f"\nphotocurrent_X_matrix.csv: numbers only, {signal_kind(s)} X in "
                     f"{unit}; one row per {pname.lower()} "
                     f"({', '.join(f'{b:g}' for b in vals)} {punit}), one column per point "
                     "along the line\n")
        fh.write("\nExperiment:\n")
        for k, val in (s.conditions or {}).items():
            fh.write(f"  {k}: {val}\n")
        fh.write(f"\nNotes:\n{s.notes}\n\nSettings:\n")
        for k, val in asdict(s).items():
            if k not in ("conditions", "notes"):
                fh.write(f"  {k}: {val}\n")
        fh.write("\nPer line (lock-in settings read from the front panels; for a power "
                 "series also the power reached):\n")
        for ib, mi in enumerate(d.map_info):
            fh.write(f"  line{ib}: {json.dumps(mi, default=str)}\n")
        fh.write(f"\nInstruments: {json.dumps(d.hw_info)}\n")
    return out


def _prefix(vmax):
    for f, p in ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                 (1, "")):
        if vmax < f * 999.5:
            return f, p
    return 1.0, ""


def save_figure(d, path, channel=0, dpi=200):
    """Signal and reflection vs distance (one curve per bias); with several
    biases also the distance × bias image."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = d.settings
    kind = signal_kind(s)
    sig, unit = signal(d, channel)
    ch = CHANNELS[channel]
    dist, u, v = geometry(d)
    fin = np.abs(sig[np.isfinite(sig)])
    scale, pre = _prefix(fin.max() if fin.size else 1.0)
    biases = s.outer_list()
    pname, punit = outer_name(s)
    multi = len(biases) > 1
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    if multi:
        fig = plt.figure(figsize=(11, 5.6))
        a1 = fig.add_axes([0.07, 0.42, 0.40, 0.50])
        a2 = fig.add_axes([0.07, 0.10, 0.40, 0.25], sharex=a1)
        a3 = fig.add_axes([0.58, 0.10, 0.33, 0.82])
        cax = fig.add_axes([0.925, 0.10, 0.015, 0.82])
    else:
        fig, (a1, a2) = plt.subplots(2, 1, figsize=(6.4, 5.6), sharex=True,
                                     gridspec_kw={"height_ratios": [2, 1]})
    colors = plt.cm.viridis(np.linspace(0, 0.9, max(len(biases), 1)))
    for ib, b in enumerate(biases):
        a1.plot(dist, sig[ib] / scale, "o-", ms=3, lw=1.2, color=colors[ib],
                label=None if b is None else f"{b:g} {punit}")
        a2.plot(dist, d.refl[ib, 0, :, 2] * 1e3, "-", lw=1, color=colors[ib])
    a1.axhline(0, color="0.6", lw=0.8)
    a1.set_ylabel(f"{kind} {ch} ({pre}{unit})")
    a1.set_title(f"{kind} along the line ({u[0]:.1f}, {v[0]:.1f}) → "
                 f"({u[-1]:.1f}, {v[-1]:.1f}) µm", fontsize=10)
    a2.set_ylabel("Reflection R (mV)")
    a2.set_xlabel("Distance along the line (µm)")
    plt.setp(a1.get_xticklabels(), visible=False)
    if multi:
        a1.legend(fontsize=8, title=pname, title_fontsize=8, ncol=2 if len(biases) > 6
                  else 1)
        bx = np.array(biases, float)
        order = np.argsort(bx)
        m = sig[order] / scale
        lim = np.nanpercentile(np.abs(m), 99.5) if np.isfinite(m).any() else 1
        step = dist[1] - dist[0] if len(dist) > 1 else 1
        be = bx[order]
        log = bool(punit == "µW" and np.all(be > 0) and be.max() / be.min() > 10)
        t = np.log10(be) if log else be
        if len(t) > 1:
            e = np.concatenate([[t[0] - (t[1] - t[0]) / 2], (t[1:] + t[:-1]) / 2,
                                [t[-1] + (t[-1] - t[-2]) / 2]])
        else:
            e = np.array([t[0] - 0.5, t[0] + 0.5]) if not log else t[0] + np.array([-0.2, 0.2])
        if log:
            e = 10 ** e
        de = np.concatenate([dist - step / 2, [dist[-1] + step / 2]])
        pcm = a3.pcolormesh(de, e, m, cmap="bwr", vmin=-lim, vmax=lim, shading="flat")
        if log:
            a3.set_yscale("log")
        a3.set_xlabel("Distance along the line (µm)")
        a3.set_ylabel(f"{pname} ({punit})")
        a3.set_title(f"{kind} {ch}", fontsize=10)
        cb = fig.colorbar(pcm, cax=cax)
        cb.set_label(f"{kind} {ch} ({pre}{unit})")
    else:
        fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return path


def save_all(d):
    return [save_figure(d, os.path.splitext(d.path)[0] + ".png")]
