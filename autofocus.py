"""Autofocus on the laser spot seen by the camera (on demand only).

Moves the Tango z axis through a coarse range, then a fine range around the
best point, and settles where the laser spot on the camera is sharpest.

Sharpness = mean squared intensity gradient / mean intensity^2, inside a box
around the laser spot. It is largest when the spot is smallest and edges in
the image are crispest, so it works with the lamp off (laser spot only) and
with the lamp on. It does not depend on laser power or exposure.
Saturated pixels make it unreliable -> lower the exposure if warned.

Every z position is approached from below (same direction), so backlash in
the z drive doesn't shift the result.
"""
import time

import numpy as np


def spot_sharpness(img, cx, cy, half=80):
    """-> (sharpness, saturated_fraction)"""
    a = np.asarray(img, float)
    if a.ndim == 3:
        sat = float(np.mean(a.max(axis=2) >= 250))
        a = a.mean(axis=2)
    else:
        sat = float(np.mean(a >= 250))
    h, w = a.shape
    x0, x1 = int(max(cx - half, 0)), int(min(cx + half, w))
    y0, y1 = int(max(cy - half, 0)), int(min(cy + half, h))
    roi = a[y0:y1, x0:x1]
    if roi.size == 0:
        raise ValueError("The laser-spot marker is outside the camera image")
    if a.ndim == 2 and np.asarray(img).ndim == 2:
        sat = float(np.mean(roi >= 250))
    if roi.shape[0] < 3 or roi.shape[1] < 3:
        return 0.0, sat
    gx = roi[:, 2:] - roi[:, :-2]
    gy = roi[2:, :] - roi[:-2, :]
    mean = roi.mean()
    if mean <= 1e-9:
        return 0.0, sat
    return float((np.mean(gx ** 2) + np.mean(gy ** 2)) / mean ** 2), sat


def fresh_frame(cam, n=2, timeout=5.0):
    """Wait for n new frames so the image was taken after the stage stopped."""
    old, got, t0 = cam.latest(), 0, time.time()
    img = old
    while got < n and time.time() - t0 < timeout:
        img = cam.latest()
        if img is not None and img is not old:
            got += 1
            old = img
        else:
            time.sleep(0.005)
    return img


def run_autofocus(stage, cam, spot_xy, range_um=5.0, step_um=0.5,
                  backlash_um=2.0, z_limits=None, status=None, stop=None):
    """Returns dict(z_start, z_best, ok, message, z, metric).
    stop: callable returning True to cancel (stage goes back to z_start)."""
    status = status or (lambda m: None)
    stop = stop or (lambda: False)
    cx, cy = spot_xy
    z0 = stage.position_z()
    lo_lim, hi_lim = (z_limits or (-np.inf, np.inf))
    zs_all, ms_all, sat_max = [], [], 0.0

    def clamp(z):
        return float(np.clip(z, max(lo_lim, z0 - range_um - backlash_um),
                             min(hi_lim, z0 + range_um)))

    def sweep(zs, label):
        nonlocal sat_max
        stage.move_z(clamp(zs[0] - backlash_um))       # approach from below
        out = []
        for k, z in enumerate(zs):
            if stop():
                raise InterruptedError
            stage.move_z(clamp(z))
            m, sat = spot_sharpness(fresh_frame(cam), cx, cy)
            sat_max = max(sat_max, sat)
            out.append(m)
            zs_all.append(float(z))
            ms_all.append(m)
            status(f"Autofocus {label}: z = {z:.2f} µm ({k + 1}/{len(zs)})")
        return np.array(out)

    try:
        # 1) coarse
        zc = np.arange(z0 - range_um, z0 + range_um + step_um / 2, step_um)
        zc = np.unique([clamp(z) for z in zc])
        mc = sweep(zc, "coarse")
        ib = int(np.argmax(mc))
        if mc[ib] <= 0:
            raise RuntimeError("The camera image is dark near the marker. Check the "
                               "laser/lamp and that the cyan cross sits on the spot.")
        edge = ib in (0, len(zc) - 1)
        # 2) fine, around the best coarse point
        fine = step_um / 5
        zf = np.arange(zc[ib] - 1.5 * step_um, zc[ib] + 1.5 * step_um + fine / 2, fine)
        zf = np.unique([clamp(z) for z in zf])
        mf = sweep(zf, "fine")
        j = int(np.argmax(mf))
        z_best = zf[j]
        # parabola through the top points for sub-step precision
        sel = slice(max(j - 2, 0), min(j + 3, len(zf)))
        if len(zf[sel]) >= 3:
            a, b, _ = np.polyfit(zf[sel] - zf[j], mf[sel], 2)
            if a < 0:
                z_best = float(np.clip(zf[j] - b / (2 * a), zf[sel][0], zf[sel][-1]))
        # 3) go there from below
        stage.move_z(clamp(z_best - backlash_um))
        stage.move_z(clamp(z_best))
        msg = f"Focus at z = {z_best:.2f} µm ({z_best - z0:+.2f} µm)"
        if edge:
            msg += ". Best point was at the edge of the range: run it again or widen the range"
        if sat_max > 0.01:
            msg += ". Camera saturated on the spot: lower the exposure for a reliable result"
        return {"z_start": z0, "z_best": z_best, "ok": not edge, "message": msg,
                "z": np.array(zs_all), "metric": np.array(ms_all)}
    except InterruptedError:
        stage.move_z(clamp(z0 - backlash_um))
        stage.move_z(z0)
        return {"z_start": z0, "z_best": z0, "ok": False,
                "message": "Autofocus cancelled, z returned to start",
                "z": np.array(zs_all), "metric": np.array(ms_all)}
