"""Camera calibration: which stage position each camera pixel shows.

The program moves the stage by a few µm in x and in y and measures how far
the camera image shifts (phase correlation, with the laser spot masked out
because it doesn't move). From that it gets a 2×2 matrix A (µm of stage per
camera pixel), which holds the scale, the rotation and any mirroring.

The laser always sits at the cyan cross (pixel p_laser). With the stage at S,
the sample point seen at pixel p is the one the laser reaches when the stage
goes to   S + A · (p − p_laser).

Saved in config.yaml under camera → calibration. Redo it after changing the
objective or moving the camera.
"""
import time

import numpy as np


class Calibration:
    def __init__(self, A, residual_px=0.0, step_um=0.0, when=""):
        self.A = np.asarray(A, float).reshape(2, 2)
        self.B = np.linalg.inv(self.A)          # pixels per µm of stage
        self.residual_px = float(residual_px)
        self.step_um = float(step_um)
        self.when = when

    # p = (x, y) = (column, row) of the camera image; laser = cross position
    def px_to_stage(self, px, py, cur=(0.0, 0.0), laser=(0.0, 0.0)):
        d = self.A @ np.array([px - laser[0], py - laser[1]])
        return float(cur[0] + d[0]), float(cur[1] + d[1])

    def stage_to_px(self, x, y, cur=(0.0, 0.0), laser=(0.0, 0.0)):
        d = self.B @ np.array([x - cur[0], y - cur[1]])
        return float(laser[0] + d[0]), float(laser[1] + d[1])

    def scale_um_per_px(self):
        return float(np.sqrt(abs(np.linalg.det(self.A))))

    def angle_deg(self):
        """Rotation of the camera's x axis relative to the stage x axis."""
        return float(np.degrees(np.arctan2(self.A[1, 0], self.A[0, 0])))

    def mirrored(self):
        return bool(np.linalg.det(self.A) < 0)

    def describe(self):
        return (f"{self.scale_um_per_px() * 1000:.1f} nm per pixel, rotated "
                f"{self.angle_deg():.1f}°" + (", mirrored" if self.mirrored() else "")
                + (f", fit error {self.residual_px:.1f} px" if self.residual_px else ""))

    def to_dict(self):
        return {"A_um_per_px": self.A.tolist(), "residual_px": self.residual_px,
                "step_um": self.step_um, "date": self.when}

    @classmethod
    def from_dict(cls, d):
        if not d or "A_um_per_px" not in d:
            return None
        try:
            return cls(d["A_um_per_px"], d.get("residual_px", 0.0), d.get("step_um", 0.0),
                       d.get("date", ""))
        except Exception:
            return None


def _gray(img):
    a = np.asarray(img, float)
    return a.mean(axis=2) if a.ndim == 3 else a


def _blur(g, sigma):
    h, w = g.shape
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    k = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(g) * k))


def _prepare(img, laser, mask_r):
    """Grey image, band-passed (no lamp gradient, no pixel noise), with the
    laser spot and the image edges faded to zero so they can't match."""
    g = _gray(img)
    h, w = g.shape
    g = _blur(g, 1.5) - _blur(g, 12.0)
    yy, xx = np.mgrid[0:h, 0:w]
    weight = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    if laser is not None:
        r = np.hypot(xx - laser[0], yy - laser[1])
        weight = weight * np.clip((r - mask_r) / mask_r, 0, 1)
    return g * weight


def image_shift(a, b, laser=None, mask_r=20, exclude_zero=True):
    """How far b is moved relative to a, as (dx, dy) in pixels (columns,
    rows), sub-pixel, by phase correlation. Also returns a quality number
    (peak height over the background; above ~5 is a clear match)."""
    A = np.fft.fft2(_prepare(a, laser, mask_r))
    Bf = np.fft.fft2(_prepare(b, laser, mask_r))
    R = Bf * np.conj(A)
    R /= np.abs(R) ** 0.5 + 1e-12          # partly whitened: sharp but robust
    c = np.real(np.fft.ifft2(R))
    h, w = c.shape
    if exclude_zero:        # the stage did move: ignore a match at "no shift"
        for dy_ in (-1, 0, 1):
            for dx_ in (-1, 0, 1):
                c[dy_ % h, dx_ % w] = c.min()
    iy, ix = np.unravel_index(np.argmax(c), c.shape)

    def sub(cm, c0, cp):
        den = cm - 2 * c0 + cp
        return 0.0 if den == 0 else 0.5 * (cm - cp) / den
    dy = iy + sub(c[(iy - 1) % h, ix], c[iy, ix], c[(iy + 1) % h, ix])
    dx = ix + sub(c[iy, (ix - 1) % w], c[iy, ix], c[iy, (ix + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    quality = float(c[iy, ix] / (np.std(c) + 1e-12))
    return float(dx), float(dy), quality


def _frame(cam, settle):
    time.sleep(settle)
    snap = cam.snapshot()               # waits for a new frame
    img = snap["image"]
    snap2 = cam.snapshot()              # average two frames: less noise
    return (np.asarray(img, float) + np.asarray(snap2["image"], float)) / 2


def calibrate(stage, cam, step_um=5.0, laser_px=None, status=None, stop=None):
    """Move the stage around its current position and measure the image shifts.
    Always approaches from the same side so stage backlash doesn't matter.
    Returns a Calibration; the stage ends where it started."""
    status = status or (lambda m: None)
    stop = stop or (lambda: False)
    x0, y0 = stage.position()
    first = cam.snapshot()["image"]
    h, w = np.asarray(first).shape[:2]
    if laser_px is None:
        laser_px = (w / 2, h / 2)
    mask_r = 0.06 * min(h, w)
    settle = max(0.3, 3 * getattr(cam, "exposure_ms", 20) / 1000)
    D, S, Q = [], [], []
    try:
        for axis, name in ((0, "x"), (1, "y")):
            def at(k):
                return (x0 + k * step_um, y0) if axis == 0 else (x0, y0 + k * step_um)
            stage.move_to(*at(-1))                      # take up backlash
            stage.move_to(*at(0))
            ref = _frame(cam, settle)
            for k in (1, 2):
                if stop():
                    raise InterruptedError("Calibration cancelled")
                status(f"Camera calibration: moving {k * step_um:g} µm in {name}…")
                stage.move_to(*at(k))
                img = _frame(cam, settle)
                dx, dy, q = image_shift(ref, img, laser_px, mask_r)
                D.append([k * step_um if axis == 0 else 0.0,
                          k * step_um if axis == 1 else 0.0])
                S.append([dx, dy])
                Q.append(q)
    finally:
        stage.move_to(x0 - step_um, y0)
        stage.move_to(x0, y0)
    D, S = np.array(D).T, np.array(S).T             # 2 × n
    # S = −B · D  (the image moves opposite to the stage, in camera pixels)
    B = -S @ np.linalg.pinv(D)
    if abs(np.linalg.det(B)) < 1e-9:
        raise RuntimeError("Calibration failed: the image did not move. Is the camera "
                           "showing the sample (lamp on, in focus)?")
    res = float(np.sqrt(np.mean((S + B @ D) ** 2)))
    moved = float(np.median(np.hypot(*S)))
    if min(Q) < 4:
        raise RuntimeError(
            f"Calibration failed: the camera image could not be matched reliably "
            f"(quality {min(Q):.1f}). Make sure the sample is in focus, the lamp is on "
            "and there is some structure in view (an edge or a flake).")
    if moved < 3:
        raise RuntimeError(f"The image moved only {moved:.1f} pixels. Use a larger "
                           "calibration move.")
    if moved > 0.35 * min(h, w):
        raise RuntimeError(f"The image moved {moved:.0f} pixels, too far to measure "
                           "well. Use a smaller calibration move.")
    sv = np.linalg.svd(B, compute_uv=False)
    if res > 3 or res > 0.08 * moved or sv.max() / sv.min() > 1.15:
        raise RuntimeError(
            "Calibration not consistent: the measured image shifts don't fit together. "
            "Go to a place with clear structure in x AND y (a flake corner, an "
            "electrode corner), check the focus, and try again.")
    return Calibration(np.linalg.inv(B), res, step_um,
                       time.strftime("%Y-%m-%d %H:%M"))
