"""Spatial resolution from a sharp edge on the reflection map.

A line is drawn across an edge (e.g. a gold electrode). Along it the
reflection is   R(x) = A + B·erf((x − x0) / (√2·σ)),
the edge blurred by a Gaussian laser spot with standard deviation σ.
The derivative of that profile is the spot's line profile (a Gaussian).

  FWHM          = 2.355 σ
  10–90 % width = 2.563 σ

The fit needs no extra libraries: for each (x0, σ) on a grid, A and B follow
from a linear least-squares fit; the best grid point is refined twice.
"""
import math
import os

import numpy as np

FWHM_PER_SIGMA = 2 * math.sqrt(2 * math.log(2))      # 2.3548
W1090_PER_SIGMA = 2 * 1.2815515655446004             # 2.5631

def _erf(z):
    """Error function, numpy-vectorized (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7)."""
    z = np.asarray(z, float)
    t = 1.0 / (1.0 + 0.3275911 * np.abs(z))
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-z * z)
    return np.sign(z) * y


def bilinear(img, xs, ys, px, py):
    """Values of img (rows = y, columns = x, axes xs, ys ascending) at the
    points (px, py); NaN outside the map."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    fx = np.interp(px, xs, np.arange(len(xs)), left=np.nan, right=np.nan)
    fy = np.interp(py, ys, np.arange(len(ys)), left=np.nan, right=np.nan)
    out = np.full(np.shape(px), np.nan)
    ok = np.isfinite(fx) & np.isfinite(fy)
    if len(xs) < 2 or len(ys) < 2 or not ok.any():
        return out
    fx, fy = fx[ok], fy[ok]
    i0 = np.clip(np.floor(fx).astype(int), 0, len(xs) - 2)
    j0 = np.clip(np.floor(fy).astype(int), 0, len(ys) - 2)
    tx, ty = fx - i0, fy - j0
    v = (img[j0, i0] * (1 - tx) * (1 - ty) + img[j0, i0 + 1] * tx * (1 - ty)
         + img[j0 + 1, i0] * (1 - tx) * ty + img[j0 + 1, i0 + 1] * tx * ty)
    out[ok] = v
    return out


def profile(img, xs, ys, p0, p1, width=0.0, spacing=None):
    """Profile along p0 -> p1 (map coordinates, µm), averaged over a band of
    `width` µm across the line. -> (distance µm, values)."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    L = float(np.hypot(*(p1 - p0)))
    if L <= 0:
        raise ValueError("The line has zero length.")
    step = min(abs(xs[1] - xs[0]), abs(ys[1] - ys[0])) if len(xs) > 1 and len(ys) > 1 else 1
    spacing = spacing or step / 4
    n = max(int(L / spacing) + 1, 10)
    t = np.linspace(0, 1, n)
    u = (p1 - p0) / L                              # along
    nrm = np.array([-u[1], u[0]])                  # across
    k = max(int(width / step) * 2 + 1, 1) if width > 0 else 1
    offs = np.linspace(-width / 2, width / 2, k) if k > 1 else [0.0]
    rows = []
    for o in offs:
        px = p0[0] + t * (p1[0] - p0[0]) + o * nrm[0]
        py = p0[1] + t * (p1[1] - p0[1]) + o * nrm[1]
        rows.append(bilinear(img, xs, ys, px, py))
    with np.errstate(all="ignore"):
        vals = np.nanmean(np.array(rows), axis=0)
    return t * L, vals


def _fit_at(x, y, x0, s):
    e = _erf((x - x0) / (math.sqrt(2) * s))
    M = np.column_stack([np.ones_like(x), e])
    coef, *_ = np.linalg.lstsq(M, y, rcond=None)
    res = float(np.sum((M @ coef - y) ** 2))
    return res, coef


def fit_edge(x, y):
    """-> dict(x0, sigma, A, B, fwhm, w1090, rms, n) or raises ValueError."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 8:
        raise ValueError("Too few points on the line (is it on the map?).")
    L = x.max() - x.min()
    dx = np.median(np.diff(np.sort(x)))
    x0s = np.linspace(x.min() + 0.05 * L, x.max() - 0.05 * L, 60)
    sigmas = np.geomspace(max(dx / 4, 1e-4), L / 3, 40)
    best = (np.inf, None, None, None)
    for _round in range(3):
        for x0 in x0s:
            for s in sigmas:
                r, c = _fit_at(x, y, x0, s)
                if r < best[0]:
                    best = (r, x0, s, c)
        _, bx, bs, _ = best
        span = (x0s[1] - x0s[0]) * 2
        x0s = np.linspace(bx - span, bx + span, 25)
        f = sigmas[1] / sigmas[0]
        sigmas = np.geomspace(bs / f ** 2, bs * f ** 2, 25)
    r, x0, s, (A, B) = best
    return {"x0": float(x0), "sigma": float(s), "A": float(A), "B": float(B),
            "fwhm": float(s * FWHM_PER_SIGMA), "w1090": float(s * W1090_PER_SIGMA),
            "rms": float(math.sqrt(r / len(x))), "n": int(len(x)),
            "contrast_to_noise": float(abs(2 * B) / (math.sqrt(r / len(x)) + 1e-30))}


def model(x, fit):
    return fit["A"] + fit["B"] * _erf((np.asarray(x) - fit["x0"]) / (math.sqrt(2) * fit["sigma"]))


def derivative(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return np.gradient(y, x)


def gaussian(x, fit):
    """Derivative of the fitted edge: the spot's line profile."""
    s = fit["sigma"]
    return fit["B"] * math.sqrt(2 / math.pi) / s * np.exp(-((np.asarray(x) - fit["x0"]) ** 2)
                                                          / (2 * s ** 2))


def save(path_base, x, y, fit, info):
    """<base>.png (profile + fit, derivative + Gaussian) and <base>.csv
    (numbers only: distance µm, reflection V, fit V) + <base>.txt (results)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(5.8, 6.0), sharex=True)
    xx = np.linspace(np.nanmin(x), np.nanmax(x), 400)
    a1.plot(x, y * 1e3, "o", ms=3, color="0.35", label="reflection")
    a1.plot(xx, model(xx, fit) * 1e3, "-", color="#c62828", lw=1.5, label="edge fit")
    a1.set_ylabel("Reflection (mV)")
    a1.legend(fontsize=9)
    a1.set_title(f"FWHM = {fit['fwhm'] * 1000:.0f} nm   (10–90 %: "
                 f"{fit['w1090'] * 1000:.0f} nm)", fontsize=11)
    a2.plot(x, derivative(x, y) * 1e3, "o", ms=3, color="0.35", label="derivative")
    a2.plot(xx, gaussian(xx, fit) * 1e3, "-", color="#1f5fa8", lw=1.5, label="Gaussian")
    a2.set_ylabel("dR/dx (mV/µm)")
    a2.set_xlabel("Distance along the line (µm)")
    a2.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(path_base + ".png", dpi=200, facecolor="white")
    plt.close(fig)
    with open(path_base + ".csv", "w") as fh:
        for a, b, c in zip(x, y, model(x, fit)):
            fh.write(f"{a:.6e},{b:.6e},{c:.6e}\n")
    with open(path_base + ".txt", "w", encoding="utf-8") as fh:
        fh.write("Resolution from a reflection edge\n\n")
        fh.write(f"FWHM          {fit['fwhm'] * 1000:.1f} nm\n")
        fh.write(f"10-90 % width {fit['w1090'] * 1000:.1f} nm\n")
        fh.write(f"sigma         {fit['sigma'] * 1000:.1f} nm\n")
        for k, v in info.items():
            fh.write(f"{k}: {v}\n")
        fh.write("\nFit: R(x) = A + B·erf((x − x0)/(√2·σ)); FWHM = 2.355σ, 10–90 % = 2.563σ\n"
                 f"A = {fit['A']:.6g} V, B = {fit['B']:.6g} V, x0 = {fit['x0']:.4f} µm, "
                 f"rms residual {fit['rms']:.3g} V, {fit['n']} points\n")
        fh.write("\nCSV columns (numbers only): distance along the line (µm), "
                 "reflection (V), fit (V)\n")
    return [path_base + e for e in (".png", ".csv", ".txt")]


def next_base(folder, stem):
    """<folder>/<stem>_resolution_1, _2 ... (first free)."""
    k = 1
    while os.path.exists(os.path.join(folder, f"{stem}_resolution_{k}.png")):
        k += 1
    return os.path.join(folder, f"{stem}_resolution_{k}")


def edge_angle(img, xs, ys, p0, p1, fit):
    """Angle (degrees) between the drawn line and the edge's normal (the
    direction the reflection changes fastest), at the fitted edge position.
    A line that crosses the edge at a slant sees a wider edge by 1/cos(angle);
    multiply widths by cos(angle) to correct."""
    img = np.where(np.isfinite(img), img, np.nanmean(img))
    gy, gx = np.gradient(img, ys, xs)
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    u = (p1 - p0) / np.hypot(*(p1 - p0))
    c = p0 + fit["x0"] * u
    sx = bilinear(gx, xs, ys, np.array([c[0]]), np.array([c[1]]))[0]
    sy = bilinear(gy, xs, ys, np.array([c[0]]), np.array([c[1]]))[0]
    g = np.hypot(sx, sy)
    if not np.isfinite(g) or g == 0:
        return 0.0
    cos = min(abs(u[0] * sx + u[1] * sy) / g, 1.0)
    return float(np.degrees(np.arccos(cos)))


def measure(img, xs, ys, p0, p1, width=0.0):
    """Profile + fit along the drawn line; if the line crosses the edge at a
    slant, measure again along the edge's normal through the same edge point
    (same length), so the width is not over-estimated.
    -> dict(distance, values, fit, angle_deg, p0, p1) of the final profile."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d, v = profile(img, xs, ys, p0, p1, width)
    fit = fit_edge(d, v)
    ang = edge_angle(img, xs, ys, p0, p1, fit)
    if ang > 5.0:
        L = float(np.hypot(*(p1 - p0)))
        u = (p1 - p0) / L
        c = p0 + fit["x0"] * u
        imgf = np.where(np.isfinite(img), img, np.nanmean(img))
        gy, gx = np.gradient(imgf, ys, xs)
        g = np.array([bilinear(gx, xs, ys, np.array([c[0]]), np.array([c[1]]))[0],
                      bilinear(gy, xs, ys, np.array([c[0]]), np.array([c[1]]))[0]])
        n = g / np.hypot(*g)
        if n @ u < 0:
            n = -n
        q0, q1 = c - n * L / 2, c + n * L / 2
        d2, v2 = profile(img, xs, ys, q0, q1, width)
        try:
            fit2 = fit_edge(d2, v2)
            return {"distance": d2, "values": v2, "fit": fit2, "angle_deg": ang,
                    "p0": q0, "p1": q1, "realigned": True}
        except ValueError:
            pass
    return {"distance": d, "values": v, "fit": fit, "angle_deg": ang, "p0": p0, "p1": p1,
            "realigned": False}
