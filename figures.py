"""Publication-style figure: signal map (blue-white-red, symmetric) next to the
reflection map (grey), like:

    Photovoltage                      Reflection
    [ map ] [colour bar: Photovoltage (µV)]   [ map ]

Title and unit follow the amplifier: SR570 -> Photocurrent (A),
SR560 -> Photovoltage (V), lock-in alone -> its input (current or voltage).
"""
import os

import numpy as np

from scanner import pc_scale, signal_kind, CHANNELS

_PREFIX = ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1, ""))


def _prefix(vmax):
    for f, p in _PREFIX:
        if vmax < f * 999.5:
            return f, p
    return 1.0, ""


def _nice(v):
    """Round up to 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10 x 10^n."""
    if v <= 0 or not np.isfinite(v):
        return 1.0
    e = np.floor(np.log10(v))
    for m in (1, 1.2, 1.4, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 10):
        if v <= m * 10 ** e * (1 + 1e-9):
            return m * 10 ** e
    return 10 ** (e + 1)


def save_figure(d, ib, path, channel=0, refl_channel=2, limits=None,
                refl_limits=None, dpi=300):
    """d: ScanData; ib: map index; channel: 0=X,1=Y,2=R of the signal.
    limits: (lo, hi) of the signal in its unit, or None for symmetric auto."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kind = signal_kind(d.settings)
    fac, unit, _ = pc_scale(d.settings)
    unit = unit or ("A" if kind == "Photocurrent" else "V")
    st = d.settings
    ox = st.origin_x if getattr(st, "origin_x", None) is not None else st.x_center
    oy = st.origin_y if getattr(st, "origin_y", None) is not None else st.y_center
    ux = (-1 if getattr(st, "flip_x", False) else 1) * (np.asarray(d.xs) - ox) + 0.0
    vy = (-1 if getattr(st, "flip_y", False) else 1) * (np.asarray(d.ys) - oy) + 0.0
    oix, oiy = np.argsort(ux), np.argsort(vy)
    view = np.ix_(oiy, oix)
    sig = (d.pc[ib, :, :, channel] * fac)[view]
    refl = d.refl[ib, :, :, refl_channel][view]
    xs_v, ys_v = ux[oix], vy[oiy]

    finite = sig[np.isfinite(sig)]
    if limits is None:
        m = np.percentile(np.abs(finite), 99.5) if finite.size else 1.0
        m = _nice(m)
        lo, hi = -m, m
    else:
        lo, hi = limits
    scale, pre = _prefix(max(abs(lo), abs(hi)))

    dx = xs_v[1] - xs_v[0] if len(xs_v) > 1 else 1
    dy = ys_v[1] - ys_v[0] if len(ys_v) > 1 else 1
    ext = (xs_v[0] - dx / 2, xs_v[-1] + dx / 2, ys_v[0] - dy / 2, ys_v[-1] + dy / 2)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12})
    fig = plt.figure(figsize=(9.6, 4.6))
    ax1 = fig.add_axes([0.01, 0.10, 0.42, 0.78])
    cax = fig.add_axes([0.455, 0.10, 0.018, 0.78])
    ax2 = fig.add_axes([0.565, 0.10, 0.42, 0.78])

    im = ax1.imshow(sig / scale, origin="lower", extent=ext, cmap="bwr",
                    vmin=lo / scale, vmax=hi / scale, interpolation="nearest",
                    aspect="equal")
    ax1.set_title(kind, pad=10)
    cb = fig.colorbar(im, cax=cax)
    if limits is None:
        t = hi / scale
        cb.set_ticks([-t, -t / 2, 0, t / 2, t])
        cb.ax.set_yticklabels([f"{v:g}".replace("-", "\u2212")
                               for v in (-t, -t / 2, 0, t / 2, t)])
    cb.set_label(f"{kind} ({pre}{unit})")

    rf = refl[np.isfinite(refl)]
    if refl_limits is None and rf.size:
        rl = np.percentile(rf, [0.5, 99.5])
    else:
        rl = refl_limits or (0, 1)
    ax2.imshow(refl, origin="lower", extent=ext, cmap="gray", vmin=rl[0], vmax=rl[1],
               interpolation="nearest", aspect="equal")
    ax2.set_title("Reflection", pad=10)

    for ax in (ax1, ax2):
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_linewidth(1.0)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def save_all(d):
    """One PNG per map next to the .h5 file: <name>_map0.png, ..."""
    base = os.path.splitext(d.path)[0]
    out = []
    for ib, b in enumerate(d.settings.biases):
        if not np.isfinite(d.pc[ib, :, :, 0]).any():
            continue
        suffix = f"_map{ib}" + ("" if b is None else f"_{b:+g}V")
        out.append(save_figure(d, ib, base + suffix + ".png"))
    return out
