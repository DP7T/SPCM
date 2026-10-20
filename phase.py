"""Phase correction after the measurement (instead of autophasing the lock-in).

All real photocurrent of a map (and of every map of a series) has the same lock-in
phase, so in the X-Y plane its pixels lie on one straight line through zero
(positive and negative photocurrent on opposite sides). Noise is a round blob around
zero. The phase is the direction of that line, found from the pixels clearly above
the noise. One angle is used for the whole file.

Works on the PROCESSED (converted) photocurrent X and Y. The rotated data go into a
separate copy next to the measurement, <name>_phased.h5 (+ CSV folder and PNG); the
original file is never changed, and the copy keeps the raw lock-in readings unchanged
in raw/*.

Angle convention: X' = X cos(phi) + Y sin(phi), Y' = -X sin(phi) + Y cos(phi),
theta' = theta - phi. That is the same as raising the lock-in's PHASE by phi.
Sign: X' is positive at the strongest pixel (like autophasing there by hand).
"""
import copy
import os

import numpy as np

SIGNAL_SIGMA = 5.0        # a pixel counts as signal above 5 x the noise
QUIET_FRACTION = 0.3      # the weakest 30 % of pixels give the noise / background
MIN_SIGNAL_PIXELS = 5


def copy_path(path):
    base, ext = os.path.splitext(path)
    return base + "_phased" + (ext or ".h5")


def rotate(X, Y, phi_deg, offset=(0.0, 0.0)):
    p = np.deg2rad(phi_deg)
    x, y = X - offset[0], Y - offset[1]
    return x * np.cos(p) + y * np.sin(p), -x * np.sin(p) + y * np.cos(p)


def find_phase(X, Y, subtract_background=False, substrate=None):
    """X, Y: processed photocurrent of all pixels (any shape, NaN allowed).
    subtract_background: subtract the mean X, Y of the bare substrate, which is
    `substrate` (a True/False mask of the same shape, marked by the user on the map)
    or, without a mask, the weakest 30 % of the pixels.
    -> dict(ok, phase_deg, offset, noise, n_signal, spread, message)."""
    X, Y = np.asarray(X, float), np.asarray(Y, float)
    sub = None if substrate is None else np.asarray(substrate, bool).ravel()
    X, Y = X.ravel(), Y.ravel()
    good = np.isfinite(X) & np.isfinite(Y)
    if sub is not None:
        sub = sub[good]
    X, Y = X[good], Y[good]
    out = {"ok": False, "phase_deg": 0.0, "offset": [0.0, 0.0], "noise": np.nan,
           "n_signal": 0, "n_pixels": int(X.size), "spread": np.nan,
           "subtract_background": bool(subtract_background)}
    if X.size < MIN_SIGNAL_PIXELS * 2:
        out["message"] = "Too few pixels for a phase correction."
        return out
    R = np.hypot(X, Y)
    quiet = R <= np.quantile(R, QUIET_FRACTION)
    area = sub if (sub is not None and sub.sum() >= 3) else quiet
    off = (float(X[area].mean()), float(Y[area].mean())) if subtract_background \
        else (0.0, 0.0)
    out["background_from"] = ("marked substrate area" if area is sub else
                              "weakest 30 % of the pixels") if subtract_background else ""
    x, y = X - off[0], Y - off[1]
    r = np.hypot(x, y)
    rq = r[r <= np.quantile(r, QUIET_FRACTION)]
    # noise per component from the weakest pixels: for pure noise, the mean of R^2
    # over the lowest 30 % is 0.333 sigma^2 (Rayleigh distribution)
    sigma = float(np.sqrt(np.mean(rq ** 2) / 0.333)) if rq.size else np.nan
    sig = r > SIGNAL_SIGMA * sigma
    n = int(sig.sum())
    out.update(offset=list(off), noise=sigma, n_signal=n)
    if n < MIN_SIGNAL_PIXELS:
        out["message"] = (f"No clear signal: only {n} pixel(s) above {SIGNAL_SIGMA:g} x the "
                          "noise. No phase correction.")
        return out
    xs, ys = x[sig], y[sig]
    sxx, syy, sxy = np.sum(xs * xs), np.sum(ys * ys), np.sum(xs * ys)
    phi = 0.5 * np.degrees(np.arctan2(2 * sxy, sxx - syy))    # line through zero
    xr, yr = rotate(xs, ys, phi)
    k = int(np.argmax(np.hypot(xs, ys)))
    if xr[k] < 0:                                   # X' positive at the strongest pixel
        phi += 180.0
        xr, yr = -xr, -yr
    phi = (phi + 180.0) % 360.0 - 180.0
    spread = float(np.sqrt(np.mean(yr ** 2)) / max(np.sqrt(np.mean(xr ** 2)), 1e-300))
    yrms_noise = float(np.sqrt(np.mean(yr ** 2)) / sigma) if sigma > 0 else np.nan
    out.update(ok=True, phase_deg=float(phi), spread=spread, y_rms_over_noise=yrms_noise)
    if yrms_noise <= 3:
        q = "one clear phase (Y after the correction is only noise)"
    elif spread < 0.2:
        q = "clear phase, Y after the correction is a little above the noise"
    else:
        q = ("CHECK: Y after the correction is well above the noise - not all signal has "
             "the same phase (background pickup, or the phase changed during the scan?)")
    out["message"] = (f"Phase {phi:+.1f}° from {n} pixels above {SIGNAL_SIGMA:g}× noise: {q}. "
                      f"(Same as raising the lock-in PHASE by {phi:.1f}°.)")
    return out


def processed_xy(d):
    """Converted photocurrent X, Y of all maps of a ScanData (the data you see)."""
    from scanner import pc_scale
    fac = pc_scale(d.settings)[0]
    return d.pc[..., 0] * fac, d.pc[..., 1] * fac


def make_copy(d, res):
    """-> new ScanData with the photocurrent rotated by res['phase_deg'] (one angle
    for all maps), saved later to copy_path(d.path). The raw lock-in readings are
    kept unchanged (raw_pc)."""
    from scanner import pc_scale
    fac = pc_scale(d.settings)[0]
    c = copy.deepcopy(d)
    c.raw_pc = getattr(d, "raw_pc", None)
    if c.raw_pc is None:
        c.raw_pc = d.pc.copy()
    X, Y = processed_xy(d)
    Xr, Yr = rotate(X, Y, res["phase_deg"], res.get("offset", (0.0, 0.0)))
    c.pc = d.pc.copy()
    c.pc[..., 0], c.pc[..., 1] = Xr / fac, Yr / fac
    c.pc[..., 2] = np.hypot(Xr, Yr) / fac
    c.pc[..., 3] = np.degrees(np.arctan2(Yr, Xr))
    c.phase = {k: res[k] for k in ("phase_deg", "offset", "noise", "n_signal", "spread",
                                   "subtract_background", "background_from", "message") if k in res}
    c.phase["source_file"] = os.path.basename(d.path or "")
    c.phase["offset_unit"] = pc_scale(d.settings)[1] or getattr(d.settings, "lockin_unit", "V")
    c.path = copy_path(d.path)
    return c


def write_copy(d, res, export=True):
    """Rotate, save <name>_phased.h5 and (export=True) its CSV folder and PNG(s).
    -> path of the copy."""
    c = make_copy(d, res)
    c.save(c.path)
    if export:
        import linescan
        if linescan.is_line(c):
            linescan.export_csv(c)
            linescan.save_all(c)
        else:
            from scanner import export_csv
            from figures import save_all
            export_csv(c)
            save_all(c)
    return c.path


def remove_copy(path):
    """Delete <name>_phased.h5, its CSV folder and PNGs (when no correction is wanted)."""
    import glob
    import shutil
    cp = copy_path(path)
    base = os.path.splitext(cp)[0]
    for f in [cp] + glob.glob(base + "*.png"):
        if os.path.exists(f):
            os.remove(f)
    if os.path.isdir(base + "_csv"):
        shutil.rmtree(base + "_csv")
