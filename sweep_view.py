"""Live plots for single-point sweeps (right-hand 'Point sweep' tab)."""
import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets

from scanner import CHANNELS, signal_kind


def _log_ticks(axis, vals):
    """Label a log axis 1, 2, 5, 10, 20 ... (pyqtgraph's own labels overlap)."""
    v = np.asarray(vals, float)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0:
        axis.setTicks(None)
        return
    lo, hi = np.floor(np.log10(v.min())) - 1, np.ceil(np.log10(v.max())) + 1
    major, minor = [], []
    for e in range(int(lo), int(hi) + 1):
        for m in range(1, 10):
            x = m * 10.0 ** e
            if m in (1, 2, 5):
                major.append((np.log10(x), f"{x:g}"))
            else:
                minor.append((np.log10(x), ""))
    axis.setTicks([major, minor])


def _si_scale(v):
    """-> (factor, prefix) so that max(v)/factor is between 1 and 1000."""
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    m = np.abs(v).max() if v.size else 1.0
    for f, p in ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                 (1, "")):
        if m < f * 999.5:
            return f, p
    return 1.0, ""


def _colors(n):
    cm = pg.colormap.get("viridis")
    return cm.map(np.linspace(0.0, 0.9, max(n, 1)), mode="qcolor")


class SweepView(QtWidgets.QWidget):
    """Linear plot (with error bars), log-log plot (with the fitted slope) and,
    for bias × power sweeps, a bias–power map."""

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.info = QtWidgets.QLabel("No sweep yet. Set it up in the 'Point sweep' tab "
                                     "on the left.")
        self.info.setStyleSheet("color:gray")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        self.lin = self.glw.addPlot(row=0, col=0)
        self.log = self.glw.addPlot(row=0, col=1)
        self.log.setLogMode(True, True)
        for p in (self.lin, self.log):
            p.showGrid(x=True, y=True, alpha=0.25)
            p.getAxis("bottom").enableAutoSIPrefix(False)
        self.log.getAxis("left").enableAutoSIPrefix(False)
        self.map = self.glw.addPlot(row=1, col=0, colspan=2)
        self.img = pg.ImageItem()
        self.map.addItem(self.img)
        self.bar = pg.ColorBarItem(colorMap=pg.colormap.get("CET-D1"), width=14,
                                   interactive=False)
        self.bar.setImageItem(self.img, insert_in=self.map)
        self.map.setLabel("left", "Bias (V)")
        self.map.setLabel("bottom", "Power on sample, set (µW)")
        self.map.getAxis("bottom").enableAutoSIPrefix(False)
        self.map.getAxis("left").enableAutoSIPrefix(False)
        self.lin_leg = self.lin.addLegend(offset=(10, 10))
        self.log_leg = self.log.addLegend(offset=(10, 10))
        self._shape = None
        self.show_map(False)

    def show_map(self, on):
        self.map.setVisible(on)
        self.glw.ci.layout.setRowStretchFactor(1, 2 if on else 0)
        self.map.setMaximumHeight(10000 if on else 0)

    def clear(self):
        for p in (self.lin, self.log):
            p.clear()
        for leg in (self.lin_leg, self.log_leg):
            leg.clear()
        self.img.clear()

    def set_data(self, d, channel=0):
        """Redraw everything from a SweepData (cheap: a sweep has few points)."""
        self.clear()
        if d is None:
            return
        s = d.settings
        kind = signal_kind(s)
        sig, sd, unit = d.signal(channel)
        ch = CHANNELS[channel]
        nb = len(d.biases)
        cols = _colors(nb)
        xname, xunit, xv = d.x_axis()
        self.lin.setLabel("bottom", xname, units=xunit)
        self.lin.setLabel("left", f"{kind} {ch}", units=unit)
        self.lin.setTitle(f"{kind} vs {'bias' if s.kind == 'bias' else 'power'}"
                          f"  at ({s.u:.2f}, {s.v:.2f}) µm")
        power = s.kind in ("power", "bias_power")
        self.log.setVisible(power and channel != 3)
        self.show_map(s.kind == "bias_power")
        if s.kind == "bias":
            rows = [(None, xv[:, 0], sig[:, 0], sd[:, 0], cols[0])]
        else:
            rows = [(d.biases[ib], xv[ib], sig[ib], sd[ib], cols[ib]) for ib in range(nb)]
        fits = d.fits(channel) if channel != 3 else [None] * nb
        lscale, lpre = _si_scale(sig)
        for k, (b, x, y, e, c) in enumerate(rows):
            ok = np.isfinite(x) & np.isfinite(y)
            if not ok.any():
                continue
            name = None if b is None else f"{b:g} V"
            pen = pg.mkPen(c, width=1.5)
            self.lin.plot(x[ok], y[ok], pen=pen, symbol="o", symbolSize=6, symbolPen=None,
                          symbolBrush=c, name=name)
            ee = np.where(np.isfinite(e[ok]), e[ok], 0)
            self.lin.addItem(pg.ErrorBarItem(x=x[ok], y=y[ok], height=2 * ee, beam=0,
                                             pen=pg.mkPen(c, width=1)))
            if power and channel != 3:
                pos = ok & (x > 0) & (np.abs(y) > 0)
                if pos.any():
                    r = fits[k] if s.kind != "bias" else None
                    label = name or ""
                    if r:
                        label = (label + ": " if label else "") + \
                            f"α = {r['alpha']:.2f} ± {r['alpha_err']:.2f}"
                    self.log.plot(x[pos], np.abs(y[pos]) / lscale, pen=None, symbol="o",
                                  symbolSize=6, symbolPen=None, symbolBrush=c,
                                  name=label or None)
                    if r:
                        xx = np.geomspace(x[pos].min(), x[pos].max(), 40)
                        self.log.plot(xx, r["A"] * xx ** r["alpha"] / lscale,
                                      pen=pg.mkPen(c, width=1))
        if power and channel != 3:
            fin = np.isfinite(xv) & np.isfinite(sig)
            _log_ticks(self.log.getAxis("bottom"), xv[fin])
            _log_ticks(self.log.getAxis("left"), np.abs(sig[fin]) / lscale)
        if power:
            self.log.setLabel("bottom", "Power on sample", units="µW")
            self.log.setLabel("left", f"|{kind} {ch}| ({lpre}{unit})")
            self.log.setTitle("log-log, fit |S| = A·P^α")
        if s.kind == "bias_power":
            self._draw_map(d, sig, unit, kind, ch)
        n_done = int(np.isfinite(sig).sum())
        self.info.setText(f"{n_done} of {sig.size} points  ·  each point = mean of "
                          f"{s.n_avg} readings, error bars = their standard deviation")

    def _draw_map(self, d, sig, unit, kind, ch):
        bx = np.array(d.biases, float)
        order = np.argsort(bx)
        m = sig[order]
        if not np.isfinite(m).any():
            return
        lim = float(np.nanpercentile(np.abs(m), 99.5)) or 1e-30
        img = np.where(np.isfinite(m), m, 0.0)
        self.img.setImage(img, autoLevels=False)
        self.img.setRect(-0.5, -0.5, m.shape[1], m.shape[0])   # index grid
        self.bar.setLevels((-lim, lim))
        self.bar.axis.setLabel(f"{kind} {ch}", units=unit)
        pw = list(d.powers)
        every = max(1, len(pw) // 8)
        self.map.getAxis("bottom").setTicks(
            [[(i, f"{p:g}") for i, p in enumerate(pw) if i % every == 0]])
        bs = bx[order]
        every = max(1, len(bs) // 10)
        self.map.getAxis("left").setTicks(
            [[(i, f"{b:g}") for i, b in enumerate(bs) if i % every == 0]])
        self.map.setTitle(f"{kind} {ch}: bias vs power")


class LineView(QtWidgets.QWidget):
    """Line scans: signal and reflection vs distance (one curve per bias) and,
    with several biases, the distance × bias image."""

    def __init__(self):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.info = QtWidgets.QLabel("No line scan yet. On the Maps tab press 'Draw line', "
                                     "drag its ends, then 'Scan this line'.")
        self.info.setStyleSheet("color:gray")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, 1)
        self.sig = self.glw.addPlot(row=0, col=0)
        self.refl = self.glw.addPlot(row=1, col=0)
        self.refl.setXLink(self.sig)
        self.refl.setMaximumHeight(220)
        self.map = self.glw.addPlot(row=0, col=1, rowspan=2)
        self.img = pg.ImageItem()
        self.map.addItem(self.img)
        self.bar = pg.ColorBarItem(colorMap=pg.colormap.get("CET-D1"), width=14,
                                   interactive=False)
        self.bar.setImageItem(self.img, insert_in=self.map)
        for p in (self.sig, self.refl, self.map):
            p.getAxis("bottom").enableAutoSIPrefix(False)
            p.setLabel("bottom", "Distance along the line (µm)")
        for p in (self.sig, self.refl):
            p.showGrid(x=True, y=True, alpha=0.25)
        self.map.getAxis("left").enableAutoSIPrefix(False)
        self.map.setLabel("left", "Bias (V)")
        self.refl.setLabel("left", "Reflection R", units="V")
        self.leg = self.sig.addLegend(offset=(10, 10))

    def set_data(self, d, channel=0):
        import linescan
        for p in (self.sig, self.refl):
            p.clear()
        self.leg.clear()
        self.img.clear()
        if d is None:
            return
        s = d.settings
        kind = signal_kind(s)
        sig, unit = linescan.signal(d, channel)
        dist, u, v = linescan.geometry(d)
        nb = len(s.biases)
        cols = _colors(nb)
        self.sig.setLabel("left", f"{kind} {CHANNELS[channel]}", units=unit)
        self.sig.setTitle(f"{kind} along ({u[0]:.1f}, {v[0]:.1f}) → ({u[-1]:.1f}, "
                          f"{v[-1]:.1f}) µm")
        for ib, b in enumerate(s.biases):
            ok = np.isfinite(sig[ib])
            if not ok.any():
                continue
            c = cols[ib]
            self.sig.plot(dist[ok], sig[ib][ok], pen=pg.mkPen(c, width=1.5), symbol="o",
                          symbolSize=4, symbolPen=None, symbolBrush=c,
                          name=None if b is None else f"{b:g} V")
            r = d.refl[ib, 0, :, 2]
            okr = np.isfinite(r)
            self.refl.plot(dist[okr], r[okr], pen=pg.mkPen(c, width=1.2))
        multi = nb > 1
        self.map.setVisible(multi)
        if multi:
            bx = np.array(s.biases, float)
            order = np.argsort(bx)
            m = sig[order]
            if np.isfinite(m).any():
                lim = float(np.nanpercentile(np.abs(m), 99.5)) or 1e-30
                step = dist[1] - dist[0] if len(dist) > 1 else 1.0
                self.img.setImage(np.where(np.isfinite(m), m, 0.0), autoLevels=False)
                self.img.setRect(-step / 2, -0.5, step * len(dist), len(bx))
                self.bar.setLevels((-lim, lim))
                self.bar.axis.setLabel(f"{kind} {CHANNELS[channel]}", units=unit)
                bs = bx[order]
                every = max(1, len(bs) // 10)
                self.map.getAxis("left").setTicks(
                    [[(i, f"{b:g}") for i, b in enumerate(bs) if i % every == 0]])
                self.map.setTitle(f"{kind}: distance × bias")
        n = int(np.isfinite(sig).sum())
        self.info.setText(f"{n} of {sig.size} points  ·  {dist[-1]:.2f} µm, "
                          f"{len(dist)} points per line, step {s.step * 1000:.0f} nm")
