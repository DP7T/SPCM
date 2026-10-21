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
Sign (which of the two directions of the line): the one closer to the angle used
last time (same convention across files while the lock-in phase is untouched); without
one, the direction in which most of the signal is positive. Strong pixels whose phase
doesn't fit the line (spikes) are ignored. For a series, each map is also fitted alone,
only to check that one angle fits all.
"""
import copy
import os

import numpy as np

SIGNAL_SIGMA = 5.0        # a pixel counts as signal above 5 x the noise
QUIET_FRACTION = 0.3      # the weakest 30 % of pixels give the noise / background
MIN_SIGNAL_PIXELS = 5
REF_MAX_DEG = 30.0        # farther from the last angle: something was changed


def copy_path(path):
    base, ext = os.path.splitext(path)
    return base + "_phased" + (ext or ".h5")


def rotate(X, Y, phi_deg, offset=(0.0, 0.0)):
    p = np.deg2rad(phi_deg)
    x, y = X - offset[0], Y - offset[1]
    return x * np.cos(p) + y * np.sin(p), -x * np.sin(p) + y * np.cos(p)


def _line_angle(xs, ys):
    """Angle (deg, -90..90) of the best line through zero (weights R^2)."""
    sxx, syy, sxy = np.sum(xs * xs), np.sum(ys * ys), np.sum(xs * ys)
    return 0.5 * np.degrees(np.arctan2(2 * sxy, sxx - syy))


def _wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def find_phase(X, Y, subtract_background=False, substrate=None, ref_deg=None):
    """X, Y: processed photocurrent, shape (maps, ...) or any shape, NaN allowed.
    subtract_background: subtract the mean X, Y of the bare substrate, which is
    `substrate` (a True/False mask of the same shape, marked by the user on the map)
    or, without a mask, the weakest 30 % of the pixels.
    ref_deg: the angle used last time (lock-in phase untouched -> about the same):
    of the two directions of the line, the one closer to it is taken (same sign
    convention as before). Without it, or if it is more than REF_MAX_DEG away: the
    direction in which most of the signal (weighted by strength) is positive.
    -> dict(ok, phase_deg, offset, noise, n_signal, n_rejected, rejected (mask of the
    input shape), per_map, sign_rule, message, ...)."""
    X, Y = np.asarray(X, float), np.asarray(Y, float)
    shape = X.shape
    nmaps = shape[0] if X.ndim >= 3 else 1
    mapno = (np.broadcast_to(np.arange(nmaps).reshape((-1,) + (1,) * (X.ndim - 1)), shape)
             .ravel() if X.ndim >= 3 else np.zeros(X.size, int))
    sub = None if substrate is None else np.asarray(substrate, bool).ravel()
    X, Y = X.ravel(), Y.ravel()
    good = np.isfinite(X) & np.isfinite(Y)
    where = np.flatnonzero(good)
    if sub is not None:
        sub = sub[good]
    X, Y, mapno = X[good], Y[good], mapno[good]
    rejected = np.zeros(shape, bool)
    out = {"ok": False, "phase_deg": 0.0, "offset": [0.0, 0.0], "noise": np.nan,
           "n_signal": 0, "n_rejected": 0, "n_pixels": int(X.size), "spread": np.nan,
           "subtract_background": bool(subtract_background), "rejected": rejected,
           "per_map": [], "sign_rule": ""}
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
    if subtract_background and area is sub:
        # noise = scatter of X, Y on the marked substrate
        sigma = float(np.sqrt((np.var(x[sub]) + np.var(y[sub])) / 2))
    else:
        rq = r[r <= np.quantile(r, QUIET_FRACTION)]
        # noise per component from the weakest pixels: for pure noise, the mean of R^2
        # over the lowest 30 % is 0.333 sigma^2 (Rayleigh distribution)
        sigma = float(np.sqrt(np.mean(rq ** 2) / 0.333)) if rq.size else np.nan
    sig = r > SIGNAL_SIGMA * sigma
    out.update(offset=list(off), noise=sigma)
    if sig.sum() < MIN_SIGNAL_PIXELS:
        out["n_signal"] = int(sig.sum())
        out["message"] = (f"No clear signal: only {int(sig.sum())} pixel(s) above "
                          f"{SIGNAL_SIGMA:g} x the noise. No phase correction.")
        return out
    # fit the line, then drop pixels whose phase doesn't fit (spikes, glitches) and
    # fit again. A real pixel is off the line only by its noise; allow 4 sigma + 10 %.
    use = sig.copy()
    for _ in range(4):
        phi = _line_angle(x[use], y[use])
        xr, yr = rotate(x, y, phi)
        bad = sig & (np.abs(yr) > 4 * sigma + 0.1 * np.abs(xr))
        if np.array_equal(bad, sig & ~use):
            break
        use = sig & ~bad
        if use.sum() < MIN_SIGNAL_PIXELS:
            use = sig.copy()                  # don't throw everything away
            phi = _line_angle(x[use], y[use])
            break
    n, n_bad = int(use.sum()), int((sig & ~use).sum())
    flat = np.zeros(int(np.prod(shape)), bool)
    flat[where[sig & ~use]] = True
    rejected = flat.reshape(shape)
    # which of the two directions: stay close to the last angle, else majority
    xr, yr = rotate(x[use], y[use], phi)
    if ref_deg is not None and np.isfinite(ref_deg):
        if abs(_wrap(phi + 180.0 - ref_deg)) < abs(_wrap(phi - ref_deg)):
            phi += 180.0
        if abs(_wrap(phi - ref_deg)) <= REF_MAX_DEG:
            rule = f"sign as in your earlier measurements (last angle {ref_deg:+.1f}°)"
        else:
            phi = _majority(phi, x[use], y[use])
            rule = (f"FAR from your last angle {ref_deg:+.1f}° - was the lock-in phase, a "
                    "cable, the SR570 setting or the chopping frequency changed? Sign: most "
                    "of the signal positive")
    else:
        phi = _majority(phi, x[use], y[use])
        rule = "sign: most of the signal positive (no earlier angle to follow)"
    phi = float(_wrap(phi))
    xr, yr = rotate(x[use], y[use], phi)
    spread = float(np.sqrt(np.mean(yr ** 2)) / max(np.sqrt(np.mean(xr ** 2)), 1e-300))
    yrms_noise = float(np.sqrt(np.mean(yr ** 2)) / sigma) if sigma > 0 else np.nan
    # angle uncertainty (deg) from the noise: sigma / sqrt(sum R^2)
    err = float(np.degrees(sigma / np.sqrt(np.sum(xr ** 2)))) if sigma > 0 else np.nan
    out.update(ok=True, phase_deg=phi, spread=spread, y_rms_over_noise=yrms_noise,
               n_signal=n, n_rejected=n_bad, rejected=rejected, phase_err_deg=err,
               sign_rule=rule)
    # each map on its own, only to check that one angle fits the whole series
    per, inconsistent = [], []
    if nmaps > 1:
        for k in range(nmaps):
            m = use & (mapno == k)
            if m.sum() < MIN_SIGNAL_PIXELS:
                per.append(None)
                continue
            a = _line_angle(x[m], y[m])
            d = _wrap(2 * (a - phi)) / 2            # line angles: compare modulo 180
            xm, _ = rotate(x[m], y[m], a)
            e = float(np.degrees(sigma / np.sqrt(np.sum(xm ** 2))))
            per.append({"map": k, "angle_deg": float(phi + d), "err_deg": e})
            if abs(d) > 3 * np.hypot(e, err) + 1.0:
                inconsistent.append(k)
    out["per_map"] = per
    out["inconsistent_maps"] = inconsistent
    if yrms_noise <= 3:
        q = "one clear phase (Y after the correction is only noise)"
    elif spread < 0.2:
        q = "clear phase, Y after the correction is a little above the noise"
    else:
        q = ("CHECK: Y after the correction is well above the noise - not all signal has "
             "the same phase (background pickup, or the phase changed during the scan?)")
    msg = (f"Phase {phi:+.1f}° ± {err:.1f}° from {n} pixels above {SIGNAL_SIGMA:g}× noise: {q}. "
           f"(Same as raising the lock-in PHASE by {phi:.1f}°.)")
    if n_bad:
        msg += (f" {n_bad} strong pixel(s) ignored: their phase doesn't fit (spikes?), "
                "red in the X–Y plot.")
    if inconsistent:
        msg += (" CHECK: the phase differs between maps of the series ("
                + ", ".join(f"map {k}: {per[k]['angle_deg']:+.1f}°" for k in inconsistent)
                + ") - it may change with bias / power.")
    elif nmaps > 1 and any(per):
        msg += " Same phase in every map of the series."
    out["message"] = msg + " " + rule[0].upper() + rule[1:] + "."
    return out


def lag_map(X, Y, phi, offset=(0.0, 0.0), noise=None):
    """Phase lag of every pixel relative to the common phase (deg, -90..90): atan(Y'/X').
    Only where the signal is above SIGNAL_SIGMA x noise, NaN elsewhere."""
    Xr, Yr = rotate(X, Y, phi, offset)
    lag = np.degrees(np.arctan(Yr / np.where(Xr == 0, np.nan, Xr)))
    if noise is not None and np.isfinite(noise) and noise > 0:
        lag = np.where(np.hypot(Xr, Yr) > SIGNAL_SIGMA * noise, lag, np.nan)
    return lag


def _majority(phi, x, y):
    """Of phi and phi + 180: the one in which most of the signal, weighted by
    strength (X'|X'|), is positive."""
    xr, _ = rotate(x, y, phi)
    return phi if np.sum(xr * np.abs(xr)) >= 0 else phi + 180.0


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
    c.phase = {k: res[k] for k in ("phase_deg", "phase_err_deg", "offset", "noise",
                                   "n_signal", "n_rejected", "spread", "subtract_background",
                                   "background_from", "sign_rule", "message") if k in res}
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
