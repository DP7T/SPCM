"""Point and line cursors: read the measured values of a map (nearest pixel).

Everything here works on a ScanData in the map's own axes (u, v: µm from the origin,
the axes shown on screen). Values are the nearest measured pixel, never interpolated.
A line is sampled at the scan step. For a series, every map is read.

If the measurement has a phase-corrected copy (<name>_phased.h5, see phase.py), the
corrected photocurrent X', Y' is given too, next to the uncorrected values.
"""
import os

import numpy as np

from scanner import pc_scale, outer_name, CHANNELS


def axes(d):
    """-> u, v (ascending, µm from the origin) and the index order ix, iy."""
    st = d.settings
    ox = st.origin_x if getattr(st, "origin_x", None) is not None else st.x_center
    oy = st.origin_y if getattr(st, "origin_y", None) is not None else st.y_center
    u = (-1 if getattr(st, "flip_x", False) else 1) * (np.asarray(d.xs) - ox) + 0.0
    v = (-1 if getattr(st, "flip_y", False) else 1) * (np.asarray(d.ys) - oy) + 0.0
    ix, iy = np.argsort(u), np.argsort(v)
    return u[ix], v[iy], ix, iy


_copy_cache = {}


def corrected(d):
    """The phase-corrected data for d: d itself if it is a corrected copy, else its
    <name>_phased.h5 (loaded once, cached), else None. -> (ScanData, angle_deg) or None.
    Read it with nearest() in its OWN axes: a loaded file and a live scan can store
    the pixels in different orders."""
    import phase
    if getattr(d, "phase", None):
        return d, d.phase.get("phase_deg")
    path = getattr(d, "path", None)
    if not path:
        return None
    cp = phase.copy_path(path)
    if not os.path.exists(cp):
        return None
    key = (cp, os.path.getmtime(cp))
    if key not in _copy_cache:
        from scanner import load
        try:
            c = load(cp)
        except Exception:
            return None
        _copy_cache.clear()
        _copy_cache[key] = (c, (c.phase or {}).get("phase_deg"))
    c, a = _copy_cache[key]
    return (c, a) if c.pc.shape[0] == d.pc.shape[0] else None


def _corr_at(cor, us, vs):
    """Corrected X', Y' (converted) at the pixels nearest to (us, vs): [map, point, 2]."""
    c, _ = cor
    fac = pc_scale(c.settings)[0]
    idx = [nearest(c, a, b)[:2] for a, b in zip(np.atleast_1d(us), np.atleast_1d(vs))]
    I = np.array([k[0] for k in idx])
    J = np.array([k[1] for k in idx])
    return c.pc[:, I, J, :2] * fac


def raw_readings(d):
    """Lock-in readings as measured (the unrotated ones for a corrected copy)."""
    raw = getattr(d, "raw_pc", None)
    return raw if raw is not None else d.pc


def nearest(d, u0, v0):
    """-> (i, j) indices into the data arrays [iy, ix] of the pixel nearest to (u0, v0),
    and that pixel's u, v."""
    u, v, ix, iy = axes(d)
    a, b = int(np.argmin(np.abs(u - u0))), int(np.argmin(np.abs(v - v0)))
    return iy[b], ix[a], float(u[a]), float(v[b])


def _labels(d):
    st = d.settings
    vals = st.outer_list()
    name, unit = outer_name(st)
    return vals, name, unit


def point(d, u0, v0):
    """Values at the pixel nearest to (u0, v0), for every map of the series."""
    i, j, u, v = nearest(d, u0, v0)
    fac, unit, _ = pc_scale(d.settings)
    unit = unit or getattr(d.settings, "lockin_unit", "V")
    raw = raw_readings(d)
    pc = raw[:, i, j, :].copy()                 # converted below
    pc[:, :3] *= fac
    cor = corrected(d)
    vals, oname, ounit = _labels(d)
    return {"u": u, "v": v, "i": int(i), "j": int(j), "unit": unit,
            "outer": vals, "outer_name": oname, "outer_unit": ounit,
            "pc": pc, "lockin": raw[:, i, j, :2].copy(), "refl": d.refl[:, i, j, :].copy(),
            "corr": None if cor is None else _corr_at(cor, u, v)[:, 0, :],
            "phase_deg": None if cor is None else cor[1]}


def line(d, a, b):
    """Values along the line a -> b (map axes, µm), sampled at the scan step, nearest
    pixel at each point, for every map of the series."""
    (u0, v0), (u1, v1) = a, b
    u, v, _, _ = axes(d)
    steps = [abs(u[1] - u[0]) if len(u) > 1 else np.inf,
             abs(v[1] - v[0]) if len(v) > 1 else np.inf]
    step = min(steps) if np.isfinite(min(steps)) else 1.0
    L = float(np.hypot(u1 - u0, v1 - v0))
    n = max(int(round(L / step)), 1) + 1
    t = np.linspace(0.0, 1.0, n)
    us, vs = u0 + t * (u1 - u0), v0 + t * (v1 - v0)
    idx = [nearest(d, a_, b_) for a_, b_ in zip(us, vs)]
    I = np.array([k[0] for k in idx])
    J = np.array([k[1] for k in idx])
    fac, unit, _ = pc_scale(d.settings)
    unit = unit or getattr(d.settings, "lockin_unit", "V")
    raw = raw_readings(d)
    pc = raw[:, I, J, :].copy()
    pc[..., :3] *= fac
    cor = corrected(d)
    vals, oname, ounit = _labels(d)
    pu, pv = np.array([k[2] for k in idx]), np.array([k[3] for k in idx])
    return {"distance": t * L, "u": us, "v": vs, "pix_u": pu,
            "pix_v": pv, "unit": unit, "step": step,
            "outer": vals, "outer_name": oname, "outer_unit": ounit,
            "pc": pc, "lockin": raw[:, I, J, :2].copy(), "refl": d.refl[:, I, J, :].copy(),
            "corr": None if cor is None else _corr_at(cor, pu, pv),
            "phase_deg": None if cor is None else cor[1], "a": a, "b": b}


def _next(folder, kind):
    k = 1
    while os.path.exists(os.path.join(folder, f"{kind}_{k:03d}.csv")):
        k += 1
    return os.path.join(folder, f"{kind}_{k:03d}")


def _columns(r, tag=""):
    u = r["unit"]
    cols = [f"photocurrent X{tag} ({u})", f"photocurrent Y{tag} ({u})",
            f"photocurrent R{tag} ({u})", f"photocurrent theta{tag} (deg)"]
    if r["corr"] is not None:
        cols += [f"corrected X'{tag} ({u})", f"corrected Y'{tag} ({u})"]
    cols += [f"lock-in X{tag} (as read)", f"lock-in Y{tag} (as read)",
             f"reflection X{tag} (V)", f"reflection Y{tag} (V)", f"reflection R{tag} (V)",
             f"reflection theta{tag} (deg)"]
    return cols


def _parts(r, k):
    """The arrays of map k in column order (point: 1-D, line: [point, column])."""
    parts = [r["pc"][k]]
    if r["corr"] is not None:
        parts.append(r["corr"][k])
    return parts + [r["lockin"][k], r["refl"][k]]


def save(d, r, kind):
    """Write the cursor values next to the measurement:
    <name>_cursors/point_001.csv (one row per map of the series) or line_001.csv (one row
    per point along the line), numbers only, + point_001_info.txt describing the columns.
    -> path of the CSV."""
    base = os.path.splitext(d.path)[0]
    folder = base + "_cursors"
    os.makedirs(folder, exist_ok=True)
    out = _next(folder, kind)
    series = any(b is not None for b in r["outer"])
    if kind == "point":
        cols = ([f"{r['outer_name']} ({r['outer_unit']})"] if series else []) + _columns(r)
        rows = []
        for k, b in enumerate(r["outer"]):
            vals = ([np.nan if b is None else float(b)] if series else [])
            for p in _parts(r, k):
                vals += [float(x) for x in np.ravel(p)]
            rows.append(vals)
        data = np.array(rows, float)
    else:
        cols = ["distance (µm)", "x (µm)", "y (µm)", "pixel x (µm)", "pixel y (µm)"]
        blocks = [r["distance"], r["u"], r["v"], r["pix_u"], r["pix_v"]]
        for k, b in enumerate(r["outer"]):
            cols += _columns(r, "" if b is None else f" at {b:g} {r['outer_unit']}")
            blocks += _parts(r, k)
        data = np.column_stack(blocks)
    with open(out + ".csv", "w") as fh:
        for row in np.atleast_2d(data):
            fh.write(",".join("" if not np.isfinite(x) else f"{x:.6e}" for x in row) + "\n")
    with open(out + "_info.txt", "w", encoding="utf-8") as fh:
        fh.write(f"Cursor values from {os.path.basename(d.path)} (nearest measured pixel, "
                 "not interpolated)\n")
        if kind == "point":
            fh.write(f"Point: ({r['u']:.4f}, {r['v']:.4f}) µm from the origin; one row per "
                     "map of the series\n")
        else:
            (u0, v0), (u1, v1) = r["a"], r["b"]
            fh.write(f"Line: ({u0:.4f}, {v0:.4f}) -> ({u1:.4f}, {v1:.4f}) µm from the origin, "
                     f"sampled every {r['step'] * 1000:.1f} nm (the scan step); one row per "
                     "point; 'pixel x/y' = the pixel that was read\n")
        if r["corr"] is not None:
            fh.write(f"corrected X', Y' = phase-corrected copy, rotated by "
                     f"{r['phase_deg']:+.2f}°\n")
        fh.write("photocurrent = converted (as in the maps); lock-in = as read; "
                 "reflection = as read\n\nColumns:\n")
        for i, c in enumerate(cols, 1):
            fh.write(f"  {i}: {c}\n")
    return out + ".csv"
