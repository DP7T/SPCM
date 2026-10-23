"""'Where' picture: the map on screen with the line of a line scan or the spot of a point
sweep marked on it, saved as <name>_where.png in that measurement's folder when it starts.
So you can see later where you measured, even if that map was a rough scan that was
discarded. Map only (never the camera image). No picture when the line / point is not
inside the map.

Uses matplotlib without pyplot (its own Figure + Agg canvas), so it is safe while
figures of an earlier measurement are being saved in the background.
"""
import os

import numpy as np

from scanner import pc_scale, signal_kind, CHANNELS


def _axes(d):
    """u, v (ascending, µm from the origin) and the index order, as on screen."""
    import cursors
    return cursors.axes(d)


def inside(d, pts, margin=0.5):
    """True if every point (u, v) lies inside the map d (± half a step)."""
    u, v, _, _ = _axes(d)
    st = d.settings
    m = margin * (st.step or 0)
    return all(u[0] - m <= a <= u[-1] + m and v[0] - m <= b <= v[-1] + m for a, b in pts)


def describe(d):
    """Which map: its file name if it is saved, else size and place."""
    u, v, _, _ = _axes(d)
    path = getattr(d, "path", None)
    place = (f"x {u[0]:.2f} … {u[-1]:.2f} µm, y {v[0]:.2f} … {v[-1]:.2f} µm "
             f"(from the origin), step {d.settings.step * 1000:.0f} nm")
    if path and os.path.exists(path):
        return f"{os.path.basename(path)} ({place})"
    return f"map not saved (stopped and discarded, or still running): {place}"


def save(d, pts, kind, path, ib=0, ch=0, rch=2, label=""):
    """Draw the map d (photocurrent channel ch, reflection channel rch, map ib of a
    series) with pts marked: kind 'line' (start dot + arrow to the end) or 'point'
    (cross). -> path of the PNG, or None if the points are not on the map."""
    if d is None or not inside(d, pts):
        return None
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from figures import _prefix
    u, v, ix, iy = _axes(d)
    st = d.settings
    ib = min(max(int(ib), 0), d.pc.shape[0] - 1)
    fac, unit, _ = pc_scale(st)
    unit = unit or getattr(st, "lockin_unit", "V")
    pcv = d.pc[ib][np.ix_(iy, ix)][..., ch] * (fac if ch < 3 else 1.0)
    rf = d.refl[ib][np.ix_(iy, ix)][..., rch]
    du = (u[1] - u[0]) if len(u) > 1 else 1.0
    dv = (v[1] - v[0]) if len(v) > 1 else 1.0
    ext = [u[0] - du / 2, u[-1] + du / 2, v[0] - dv / 2, v[-1] + dv / 2]
    fig = Figure(figsize=(10, 4.4))
    FigureCanvasAgg(fig)
    axs = fig.subplots(1, 2)
    fin = np.abs(pcv[np.isfinite(pcv)])
    if ch < 3:
        sc, pre = _prefix(fin.max() if fin.size else 1.0)
        lim = float(np.nanpercentile(fin, 99.5)) / sc if fin.size else 1.0
        im = axs[0].imshow(pcv / sc, origin="lower", extent=ext, cmap="bwr",
                           vmin=-lim, vmax=lim, interpolation="nearest")
        lab = f"{signal_kind(st)} {CHANNELS[ch]} ({pre}{unit})"
    else:
        im = axs[0].imshow(pcv, origin="lower", extent=ext, cmap="twilight",
                           interpolation="nearest")
        lab = f"{signal_kind(st)} θ (°)"
    fig.colorbar(im, ax=axs[0], shrink=0.85).set_label(lab)
    rr = rf * (1e3 if rch < 3 else 1.0)
    im2 = axs[1].imshow(rr, origin="lower", extent=ext, cmap="gray",
                        interpolation="nearest")
    fig.colorbar(im2, ax=axs[1], shrink=0.85).set_label(
        f"Reflection {CHANNELS[rch]} ({'mV' if rch < 3 else '°'})")
    for a in axs:
        a.set_xlabel("x (µm)")
        a.set_ylabel("y (µm)")
        a.set_aspect("equal")
        if kind == "line":
            (u0, v0), (u1, v1) = pts
            a.annotate("", xy=(u1, v1), xytext=(u0, v0),
                       arrowprops=dict(arrowstyle="-|>", color="#ffeb3b", lw=2.2,
                                       mutation_scale=16))
            a.plot([u0, u1], [v0, v1], "-", color="k", lw=4, alpha=0.35, zorder=1)
            a.plot([u0], [v0], "o", ms=8, mfc="#ffeb3b", mec="k")
        else:
            (u0, v0), = pts
            a.plot([u0], [v0], "+", ms=18, mew=3, color="k")
            a.plot([u0], [v0], "+", ms=16, mew=1.6, color="#ffeb3b")
    if kind == "line":
        (u0, v0), (u1, v1) = pts
        where = (f"Line ({u0:.2f}, {v0:.2f}) → ({u1:.2f}, {v1:.2f}) µm, "
                 f"{np.hypot(u1 - u0, v1 - v0):.2f} µm long")
    else:
        (u0, v0), = pts
        where = f"Point ({u0:.2f}, {v0:.2f}) µm"
    fig.suptitle(f"{label}{where}  (from the origin)\non: {describe(d)}", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor="white")
    return path
