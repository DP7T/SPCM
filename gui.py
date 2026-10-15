"""SPCM main window.

Kept deliberately small: lock-in sensitivity, time constant, phase etc. are
set on the SR830 front panels; the stage can be driven with the Tango joystick
between scans. The software does the scan, shows the maps live and saves.
"""
import os
import sys
import time

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

import instruments
from numparse import parse_number, make_spinbox_class
from scanner import ScanSettings, ScanWorker, parse_bias_list, CHANNELS, load

SISpinBox = make_spinbox_class(QtWidgets, QtGui, QtCore)

pg.setConfigOptions(imageAxisOrder="row-major", antialias=True)


def si(v, unit="", digits=3):
    """1.5e-9, 'A' -> '1.5 nA'"""
    if v is None or not np.isfinite(v):
        return "-"
    if v == 0:
        return f"0 {unit}".strip()
    for f, p in ((1e-15, "f"), (1e-12, "p"), (1e-9, "n"), (1e-6, "µ"), (1e-3, "m"),
                 (1, ""), (1e3, "k"), (1e6, "M")):
        if abs(v) < f * 999.5:
            return f"{v / f:.{digits}g} {p}{unit}".strip()
    return f"{v:.{digits}g} {unit}".strip()


class MapPanel(QtWidgets.QWidget):
    """Image + colour bar + horizontal line cut."""
    clicked = QtCore.Signal(float, float)

    def __init__(self, title, cmap):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw)
        self.plot = self.glw.addPlot(title=title)
        self.plot.setAspectLocked(True)
        self.plot.setLabel("bottom", "x (µm)")
        self.plot.setLabel("left", "y (µm)")
        for name in ("bottom", "left"):
            self.plot.getAxis(name).enableAutoSIPrefix(False)
        self.plot.invertY(False)
        self.img = pg.ImageItem()
        self.plot.addItem(self.img)
        self.bar = pg.ColorBarItem(colorMap=pg.colormap.get(cmap), interactive=True,
                                   width=14)
        self.bar.setImageItem(self.img, insert_in=self.plot)
        self.marker = pg.ScatterPlotItem(size=12, pen=pg.mkPen("#ff9800", width=2),
                                         brush=None, symbol="+")
        self.plot.addItem(self.marker)
        self.hline = pg.InfiniteLine(angle=0, movable=True,
                                     pen=pg.mkPen((140, 140, 140, 200), dash=[4, 4]))
        self.plot.addItem(self.hline)
        self.glw.nextRow()
        self.cut = self.glw.addPlot()
        self.cut.setMaximumHeight(130)
        self.cut.setLabel("bottom", "x (µm)")
        self.cut.getAxis("bottom").enableAutoSIPrefix(False)
        self.cut_curve = self.cut.plot(pen=pg.mkPen("#4fc3f7", width=1.5),
                                       symbol="o", symbolSize=3,
                                       symbolPen=None, symbolBrush="#4fc3f7",
                                       connect="finite")   # gaps where not scanned yet
        self.hline.sigPositionChanged.connect(self.update_cut)
        self.plot.scene().sigMouseClicked.connect(self._click)
        self.data, self.xs, self.ys = None, None, None

        # colour scale: Auto / Symmetric (±) / Manual min-max
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        row.addWidget(QtWidgets.QLabel("Colour:"))
        self.mode = "auto"
        self.mode_btns = {}
        grp = QtWidgets.QButtonGroup(self)
        for key, label, tip in (("auto", "Auto", "Fit the colour scale to the data"),
                                ("sym", "Symmetric ±", "Centre the scale on zero: −max … +max"),
                                ("manual", "Manual", "Use the Min/Max typed below")):
            b = QtWidgets.QRadioButton(label)
            b.setToolTip(tip)
            grp.addButton(b)
            b.toggled.connect(lambda on, k=key: on and self.set_mode(k))
            row.addWidget(b)
            self.mode_btns[key] = b
        self.mode_btns["auto"].setChecked(True)
        row.addStretch(1)
        lay.addLayout(row)
        row = QtWidgets.QHBoxLayout()          # second line: limits + line cut
        row.setContentsMargins(6, 0, 6, 4)
        self.min_e, self.max_e = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
        for lab, e in (("Min", self.min_e), ("Max", self.max_e)):
            e.setFixedWidth(95)
            e.setToolTip("Click, type a value (e.g. -2e-9, -2n, 0.5µ) and press Enter")
            e.editingFinished.connect(self._typed)
            row.addWidget(QtWidgets.QLabel(lab))
            row.addWidget(e)
        row.addSpacing(10)
        self.follow = QtWidgets.QCheckBox("Cut follows scan")
        self.follow.setChecked(True)
        self.follow.setToolTip("During a scan the line cut shows the line being scanned. "
                               "Drag the dashed line to look at another line "
                               "(this switches following off).")
        row.addWidget(self.follow)
        row.addStretch(1)
        lay.addLayout(row)
        self.hline.sigDragged.connect(lambda *_: self.follow.setChecked(False))
        self.manual = None
        self.unit = ""
        self.bar.sigLevelsChangeFinished.connect(self._bar_dragged)

    def set_unit(self, unit, name=""):
        """Axis labels with automatic SI prefixes: 1, 10, 100 nA -> 1 µA ..."""
        if unit == self.unit and name == getattr(self, "_uname", None):
            return
        self.unit, self._uname = unit, name
        auto = unit not in ("°", "")
        for ax in (self.bar.axis, self.cut.getAxis("left")):
            ax.enableAutoSIPrefix(auto or unit == "")
            ax.setLabel(text=name, units=unit)

    # -- colour scale --
    @property
    def autoscale(self):
        return self.mode == "auto"

    @autoscale.setter
    def autoscale(self, on):
        if on:
            self.mode_btns["auto"].setChecked(True)

    def set_mode(self, mode):
        self.mode = mode
        if mode == "manual" and self.manual is None:
            self.manual = tuple(self.bar.levels())
        self._apply_levels()

    def _typed(self):
        try:
            lo = parse_number(self.min_e.text())
            hi = parse_number(self.max_e.text())
        except ValueError:
            self._show_levels(self.bar.levels())
            return
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            hi = lo + abs(lo) * 1e-3 + 1e-30
        self.manual = (lo, hi)
        self.mode_btns["manual"].setChecked(True)
        self._apply_levels()

    def _bar_dragged(self):
        self.manual = tuple(self.bar.levels())
        self.mode_btns["manual"].blockSignals(True)
        self.mode_btns["manual"].setChecked(True)
        self.mode_btns["manual"].blockSignals(False)
        self.mode = "manual"
        self._show_levels(self.manual)

    def _show_levels(self, lv):
        for e, v in ((self.min_e, lv[0]), (self.max_e, lv[1])):
            if not e.hasFocus():
                e.setText(si(v, "" if self.unit == "°" else self.unit, 4).strip()
                          if self.unit != "°" else f"{v:.4g}")

    def _apply_levels(self):
        if self.data is None:
            return
        finite = self.data[np.isfinite(self.data)]
        if finite.size == 0:
            return
        if self.mode == "manual" and self.manual is not None:
            lo, hi = self.manual
        elif self.mode == "sym":
            m = float(np.percentile(np.abs(finite), 99.5)) or 1e-30
            lo, hi = -m, m
        else:
            lo, hi = np.percentile(finite, [0.5, 99.5])
            if hi <= lo:
                hi = lo + 1e-30
        self.bar.blockSignals(True)
        self.bar.setLevels((float(lo), float(hi)))
        self.bar.blockSignals(False)
        self._show_levels((lo, hi))

    def set_axes(self, xs, ys):
        self.xs, self.ys = xs, ys
        dx = xs[1] - xs[0] if len(xs) > 1 else 1
        dy = ys[1] - ys[0] if len(ys) > 1 else 1
        self.rect = QtCore.QRectF(xs[0] - dx / 2, ys[0] - dy / 2,
                                  dx * len(xs), dy * len(ys))
        self.hline.setBounds((ys[0], ys[-1]))
        if not (ys[0] <= self.hline.value() <= ys[-1]):
            self.hline.setValue(ys[len(ys) // 2])

    def follow_row(self, y):
        """Move the cut to the line being scanned (if following)."""
        if self.follow.isChecked():
            self.hline.blockSignals(True)
            self.hline.setValue(y)
            self.hline.blockSignals(False)

    def show_map(self, arr, pos=None):
        self.data = arr
        if arr is None or np.all(np.isnan(arr)):
            self.img.clear()
            self.cut_curve.setData([], [])
            return
        finite = arr[np.isfinite(arr)]
        img = np.where(np.isfinite(arr), arr, np.nanmin(finite))
        self.img.setImage(img, autoLevels=False)
        self.img.setRect(self.rect)
        self._apply_levels()
        if pos is not None:
            self.marker.setData([pos[0]], [pos[1]])
        self.update_cut()

    def update_cut(self, *_):
        if self.data is None or self.ys is None or self.xs is None:
            return
        if self.data.shape != (len(self.ys), len(self.xs)):
            return
        iy = int(np.clip(np.argmin(np.abs(self.ys - self.hline.value())),
                         0, len(self.ys) - 1))
        row = np.asarray(self.data[iy], float)
        try:
            if np.isfinite(row).any():
                self.cut_curve.setData(self.xs, row)
            else:
                self.cut_curve.setData([], [])
        except Exception:
            return
        self.cut.setTitle(f"line cut at y = {self.ys[iy]:.2f} µm", size="9pt")
        if np.isfinite(row).any():
            self.cut.enableAutoRange(axis="y")

    def _click(self, ev):
        if ev.double():
            p = self.plot.vb.mapSceneToView(ev.scenePos())
            self.clicked.emit(p.x(), p.y())


def apply_theme(app, dark):
    """Fusion style; dark or light palette."""
    app.setStyle("Fusion")
    P, C = QtGui.QPalette, QtGui.QColor
    pal = P()
    if dark:
        for role, col in ((P.ColorRole.Window, "#202124"), (P.ColorRole.WindowText, "#e8eaed"),
                          (P.ColorRole.Base, "#2b2c30"), (P.ColorRole.AlternateBase, "#25262a"),
                          (P.ColorRole.Text, "#e8eaed"), (P.ColorRole.Button, "#303136"),
                          (P.ColorRole.ButtonText, "#e8eaed"), (P.ColorRole.ToolTipBase, "#303136"),
                          (P.ColorRole.ToolTipText, "#e8eaed"), (P.ColorRole.Highlight, "#3d7bd9"),
                          (P.ColorRole.HighlightedText, "#ffffff"), (P.ColorRole.Link, "#8ab4f8"),
                          (P.ColorRole.PlaceholderText, "#8a8d93")):
            pal.setColor(role, C(col))
        for role in (P.ColorRole.Text, P.ColorRole.ButtonText, P.ColorRole.WindowText):
            pal.setColor(P.ColorGroup.Disabled, role, C("#6f7277"))
    else:
        pal = app.style().standardPalette()
    app.setPalette(pal)
    bg, fg = ("#16171a", "#c8cbd0") if dark else ("#ffffff", "#303030")
    pg.setConfigOptions(background=bg, foreground=fg)


def style_plots(glw, dark):
    """Plot background follows the theme."""
    bg, fg = ("#16171a", "#c8cbd0") if dark else ("#ffffff", "#303030")
    glw.setBackground(bg)
    for item in glw.ci.items.keys() if hasattr(glw, "ci") else []:
        if isinstance(item, pg.PlotItem):
            for name in ("left", "bottom", "right", "top"):
                ax = item.getAxis(name)
                ax.setPen(fg)
                ax.setTextPen(fg)
            item.titleLabel.setText(item.titleLabel.text, color=fg)


class PowerPoller(QtCore.QThread):
    """Reads the power meter continuously in the background (~5 readings/s,
    or as fast as the averaging allows)."""
    reading = QtCore.Signal(float)
    failed = QtCore.Signal(str)

    def __init__(self, pm, interval=0.2):
        super().__init__()
        self.pm, self.interval, self._run = pm, interval, True

    def stop(self):
        self._run = False

    def run(self):
        import time as _t
        errors = 0
        while self._run:
            t0 = _t.time()
            try:
                self.reading.emit(float(self.pm.power()))
                errors = 0
            except Exception as e:
                errors += 1
                if errors in (1, 10):
                    self.failed.emit(str(e))
                _t.sleep(1.0)
            _t.sleep(max(0.0, self.interval - (_t.time() - t0)))


class AutofocusWorker(QtCore.QObject):
    status = QtCore.Signal(str)
    done = QtCore.Signal(object)      # result dict, or error text

    def __init__(self, stage, cam, spot, rng, step, backlash, limits):
        super().__init__()
        self.args = (stage, cam, spot, rng, step, backlash, limits)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import traceback
        from autofocus import run_autofocus
        stage, cam, spot, rng, step, backlash, limits = self.args
        lim = None if None in limits else limits
        try:
            stage.set_joystick(False)
            r = run_autofocus(stage, cam, spot, rng, step, backlash, lim,
                              status=self.status.emit, stop=lambda: self._stop)
            self.done.emit(r)
        except Exception as e:
            self.done.emit(f"{e}\n\n{traceback.format_exc()}")
        finally:
            try:
                stage.set_joystick(True)
            except Exception:
                pass


class CalibrationWorker(QtCore.QObject):
    status = QtCore.Signal(str)
    done = QtCore.Signal(object)      # Calibration, or error text

    def __init__(self, stage, cam, step_um, laser_px):
        super().__init__()
        self.args = (stage, cam, step_um, laser_px)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        from calibration import calibrate
        stage, cam, step, laser = self.args
        try:
            stage.set_joystick(False)
            self.done.emit(calibrate(stage, cam, step, laser, status=self.status.emit,
                                     stop=lambda: self._stop))
        except Exception as e:
            self.done.emit(str(e))
        finally:
            try:
                stage.set_joystick(True)
            except Exception:
                pass


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, hw, config, config_path=None, restart=None):
        super().__init__()
        self.hw, self.config = hw, config
        self.config_path, self._restart = config_path, restart
        self._hw_closed = False
        self.worker = self.thread = None
        self.data = None
        sim = " [SIMULATION]" if hw.simulated else ""
        from appinfo import __version__
        self.setWindowTitle(f"SPCM {__version__}" + sim)
        self.resize(1500, 850)
        self.build_menu()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        # ---------------- left: controls ----------------
        # left column: "Scan" and "Instruments" tabs (each scrolls if the window
        # is short) with Start/Stop and progress always visible underneath
        side = QtWidgets.QWidget()
        side.setFixedWidth(430)
        side_lay = QtWidgets.QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        self.side_tabs = QtWidgets.QTabWidget()
        side_lay.addWidget(self.side_tabs, 1)
        root.addWidget(side, 0)

        def page(title):
            inner = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(inner)
            lay.setSpacing(8)
            sa = QtWidgets.QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            sa.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            sa.setWidget(inner)
            self.side_tabs.addTab(sa, title)
            return lay

        scan_lay = page("Map")
        sweep_lay = page("Point sweep")
        iv_lay = page("I–V")
        inst_lay = page("Instruments")
        left = scan_lay

        g = QtWidgets.QGroupBox("Scan area")
        f = QtWidgets.QFormLayout(g)

        def spin(v, lo, hi, dec=2, step=1.0, scale=1e-6, unit_name="µm"):
            w = SISpinBox(scale, unit_name)
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setSingleStep(step)
            w.setValue(v)
            w.setKeyboardTracking(False)
            return w

        d = config.get("scan_defaults", {})
        # positions typed here are relative to the origin (0, 0) set with the
        # button, in the program's axes (stage axes flipped if set in config)
        st_cfg = config.get("stage", {}) or {}
        self.sx = -1.0 if st_cfg.get("invert_x", True) else 1.0
        self.sy = -1.0 if st_cfg.get("invert_y", True) else 1.0
        self.origin_xy = None                       # stage coordinates of (0, 0)
        self.xc = spin(0.0, -1e6, 1e6)
        self.yc = spin(0.0, -1e6, 1e6)
        self.xs_ = spin(d.get("x_size", 20), 0, 1e5)
        self.ys_ = spin(d.get("y_size", 20), 0, 1e5)
        # step is typed in nm (stored in µm in the scan settings)
        self.step = spin(d.get("step", 0.5) * 1000, 10, 1e6, 1, 100, 1e-9, "nm")
        self.origin = QtWidgets.QComboBox()
        self.origin.addItem("Start corner of the map", "corner")
        self.origin.addItem("Centre of the map", "centre")
        self.origin.setCurrentIndex(max(self.origin.findData(d.get("origin", "corner")), 0))
        self.direction = QtWidgets.QComboBox()
        for label, dirs in (("+x and +y", (1, 1)), ("−x and +y", (-1, 1)),
                            ("+x and −y", (1, -1)), ("−x and −y", (-1, -1))):
            self.direction.addItem(label, dirs)
        self.direction.setCurrentIndex(max(self.direction.findText(
            d.get("direction", "+x and +y").replace("-", "−")), 0))
        f.addRow("Position is the", self.origin)
        self.xc_lab, self.yc_lab = QtWidgets.QLabel(), QtWidgets.QLabel()
        f.addRow(self.xc_lab, self.xc)
        f.addRow(self.yc_lab, self.yc)
        here = QtWidgets.QPushButton("Set origin here (0, 0)")
        here.setToolTip("Drive to the spot with the joystick, then press this. That spot "
                        "becomes (0, 0); positions and map axes are measured from it.")
        here.clicked.connect(self.centre_here)
        back = QtWidgets.QPushButton("Go to start position")
        back.setToolTip("Move the stage to the position above (the scan's start point)")
        back.clicked.connect(self.go_start)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(here)
        row.addWidget(back)
        f.addRow(row)
        self.origin_lab = QtWidgets.QLabel("origin: not set")
        self.origin_lab.setStyleSheet("color:gray")
        f.addRow(self.origin_lab)
        f.addRow("Scan towards", self.direction)
        self.angle = spin(0.0, -180, 180, 2, 1, 1.0, "°")
        self.angle.setToolTip("Rotate the scan frame (degrees, counter-clockwise, around "
                              "the start position). Step mode only.")
        f.addRow("Angle (°)", self.angle)
        f.addRow("Size x (µm)", self.xs_)
        f.addRow("Size y (µm)", self.ys_)
        f.addRow("Step (nm)", self.step)
        self.serp = QtWidgets.QCheckBox("Serpentine (faster)")
        self.serp.setChecked(d.get("serpentine", False))
        self.serp.setToolTip("Scan every other line backwards. Faster, but any stage "
                             "backlash or lock-in lag shows up as a zig-zag between "
                             "lines. Off = every line in the same direction.")
        f.addRow(self.serp)
        left.addWidget(g)
        self.origin.currentIndexChanged.connect(self._origin_changed)
        self._origin_changed()

        self.af_thread = self.af_worker = None
        self.cam = self.accessory("camera")
        if self.cam is not None and getattr(hw.stage, "has_z", False):
            af = config.get("autofocus", {}) or {}
            g = QtWidgets.QGroupBox("Focus (z)")
            f = QtWidgets.QFormLayout(g)
            row = QtWidgets.QHBoxLayout()
            self.z_lab = QtWidgets.QLabel("-")
            zr = QtWidgets.QPushButton("Read")
            zr.setToolTip("Read the current z position from the stage")
            zr.clicked.connect(self.read_z)
            row.addWidget(self.z_lab, 1)
            row.addWidget(zr)
            f.addRow("z (µm)", row)
            self.af_range = spin(af.get("range_um", 5), 0.2, 50, 2, 1, 1e-6, "µm")
            self.af_step = spin(af.get("step_um", 0.5) * 1000, 20, 5000, 0, 100, 1e-9, "nm")
            f.addRow("Search ± (µm)", self.af_range)
            f.addRow("Coarse step (nm)", self.af_step)
            self.af_btn = QtWidgets.QPushButton("Autofocus")
            self.af_btn.setToolTip(
                "Moves z through ± range around the current focus and stops where the "
                "laser spot on the camera is sharpest. Put the cyan cross (Camera tab) "
                "on the laser spot first. Takes a few seconds.")
            self.af_btn.clicked.connect(self.autofocus)
            f.addRow(self.af_btn)
            left.addWidget(g)
            QtCore.QTimer.singleShot(200, self.read_z)

        g = QtWidgets.QGroupBox("Timing")
        f = QtWidgets.QFormLayout(g)
        self.settle = spin(d.get("settle_tc", 3), 0, 50, 1, 0.5, 1.0, "")
        self.extra = spin(d.get("extra_settle_ms", 0), 0, 1e4, 0, 10, 1e-3, "ms")
        self.settle.setToolTip("Wait this many lock-in time constants at each pixel")
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Step: stop at every pixel", "step")
        self.mode.addItem("Fly: continuous lines (much faster)", "fly")
        self.mode.setCurrentIndex(max(self.mode.findData(d.get("mode", "step")), 0))
        self.mode.setToolTip(
            "Step: the stage stops at each pixel, waits, then reads (most accurate).\n"
            "Fly: the stage moves along each line at constant speed while the lock-ins "
            "are read continuously; the speed is set so each pixel still gets the wait "
            "time, and the lock-in delay is corrected. No time lost stopping and starting.")
        f.addRow("Scan mode", self.mode)
        self.settle_auto = QtWidgets.QCheckBox("Auto: wait until 99 % settled")
        self.settle_auto.setChecked(bool(d.get("settle_auto", True)))
        self.settle_auto.setToolTip(
            "Uses each lock-in's τ AND filter slope: 5 τ (6 dB), 7 τ (12 dB), "
            "9 τ (18 dB), 10 τ (24 dB/oct).\nUntick to set the number of τ yourself.")
        self.settle_auto.toggled.connect(lambda on: self.settle.setEnabled(not on))
        self.settle.setEnabled(not self.settle_auto.isChecked())
        f.addRow(self.settle_auto)
        f.addRow("Wait (× τ)", self.settle)
        f.addRow("Extra wait (ms)", self.extra)
        self.eta = QtWidgets.QLabel("-")
        f.addRow("Est. time", self.eta)
        left.addWidget(g)

        amp = config.get("amplifier", {}) or {}
        left = inst_lay
        g = QtWidgets.QGroupBox("Photocurrent amplifier")
        f = QtWidgets.QFormLayout(g)
        self.amp_mode = QtWidgets.QComboBox()
        self.amp_mode.addItem("Lock-in only", "lockin")
        self.amp_mode.addItem("SR570 current preamp", "sr570")
        self.amp_mode.addItem("SR560 voltage preamp", "voltage")
        self.amp_mode.setCurrentIndex(max(self.amp_mode.findData(amp.get("mode", "lockin")), 0))
        self.amp_sens = QtWidgets.QComboBox()
        for dec in range(-12, -3):
            for m in (1, 2, 5):
                v = m * 10.0 ** dec
                self.amp_sens.addItem(si(v, "A/V"), v)
        self.amp_sens.addItem("1 mA/V", 1e-3)
        i = self.amp_sens.findData(float(amp.get("sr570_sensitivity", 1e-6)))
        self.amp_sens.setCurrentIndex(i if i >= 0 else self.amp_sens.findData(1e-6))
        self.amp_sens.setToolTip("Set this to the SR570 front-panel sensitivity")
        self.amp_gain = QtWidgets.QComboBox()       # SR560 front-panel gains
        for g_ in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000,
                   10000, 20000, 50000):
            self.amp_gain.addItem(f"{g_:,}".replace(",", " "), float(g_))
        i = self.amp_gain.findData(float(amp.get("voltage_gain", 100)))
        self.amp_gain.setCurrentIndex(i if i >= 0 else self.amp_gain.findData(100.0))
        self.amp_gain.setToolTip("Set this to the SR560 front-panel gain")
        self.amp_formula = QtWidgets.QLabel()
        self.amp_formula.setWordWrap(True)
        self.amp_formula.setStyleSheet("color:gray")
        f.addRow("In front of lock-in", self.amp_mode)
        f.addRow("SR570 sensitivity", self.amp_sens)
        f.addRow("SR560 gain", self.amp_gain)
        f.addRow(self.amp_formula)
        left.addWidget(g)

        left = scan_lay
        g = QtWidgets.QGroupBox("Series: one map (or line) per value")
        f = QtWidgets.QFormLayout(g)
        ser = config.get("series", {}) or {}
        self.ser_kind = QtWidgets.QComboBox()
        self.ser_kind.addItem("Nothing (one map)", "none")
        self.ser_kind.addItem("Bias (V)", "bias")
        self.ser_kind.addItem("Laser power on sample (µW)", "power")
        self.ser_kind.setCurrentIndex(max(self.ser_kind.findData(ser.get("kind", "none")), 0))
        self.ser_vals = QtWidgets.QLineEdit()
        self._ser_texts = {"bias": str(ser.get("bias", d.get("biases", "0"))),
                           "power": str(ser.get("power", "1:100:*2"))}
        self._ser_shown = None
        self.has_bias = not isinstance(hw.bias, instruments.NoBias)
        f.addRow("Change", self.ser_kind)
        f.addRow("Values", self.ser_vals)
        self.ser_note = QtWidgets.QLabel()
        self.ser_note.setWordWrap(True)
        self.ser_note.setStyleSheet("color:gray")
        f.addRow(self.ser_note)
        f.addRow("Bias source", QtWidgets.QLabel(hw.bias.idn))
        left.addWidget(g)
        self.ser_kind.currentIndexChanged.connect(self._ser_changed)
        self.ser_vals.editingFinished.connect(self._remember_series)

        left = inst_lay
        g = QtWidgets.QGroupBox("Lock-ins (front panel, read only)")
        f = QtWidgets.QFormLayout(g)
        self.li_pc = QtWidgets.QLabel("-")
        self.li_r = QtWidgets.QLabel("-")
        for lab_ in (self.li_pc, self.li_r):
            lab_.setWordWrap(True)
        f.addRow("Photocurrent", self.li_pc)
        f.addRow("Reflection", self.li_r)
        rd = QtWidgets.QPushButton("Refresh")
        rd.clicked.connect(self.refresh_lockins)
        f.addRow(rd)
        left.addWidget(g)

        self.pm = self.accessory("powermeter")
        if self.pm is not None:
            g = QtWidgets.QGroupBox("Laser power (PM100)")
            f = QtWidgets.QFormLayout(g)
            self.pm_lab = QtWidgets.QLabel("-")
            self.pm_lab.setStyleSheet("font-size:16pt; font-weight:bold")
            self.pm_lab.setToolTip("Power on the sample = meter reading ÷ ratio")
            f.addRow("On sample", self.pm_lab)
            self.pm_raw = QtWidgets.QLabel("-")
            f.addRow("Meter reads", self.pm_raw)
            self.pm_factor = SISpinBox(1.0)
            self.pm_factor.setRange(1e-4, 1e6)
            self.pm_factor.setDecimals(4)
            self.pm_factor.setSingleStep(0.01)
            self.pm_factor.setKeyboardTracking(False)
            self.pm_factor.setValue(float((config.get("powermeter", {}) or {})
                                          .get("sample_factor", 1.0)))
            self.pm_factor.setToolTip("Meter power ÷ power on the sample "
                                      "(e.g. 3.18 → sample gets 1/3.18 of the meter reading)")
            self.pm_factor.valueChanged.connect(self._factor_changed)
            self.pm.sample_factor = self.pm_factor.value()
            f.addRow("Meter / sample ratio", self.pm_factor)
            f.addRow("λ correction", QtWidgets.QLabel(f"{self.pm.wavelength_nm:g} nm"))
            self.pm_range = QtWidgets.QComboBox()
            self.pm_range.addItem("Auto (not linear across range changes)", None)
            for w in (1e-6, 10e-6, 100e-6, 1e-3, 10e-3, 100e-3, 1.0):
                self.pm_range.addItem(f"Fixed {si(w, 'W')}", w)
            cur = None if self.pm.auto else self.pm.range_W
            i = self.pm_range.findData(cur) if cur is not None else 0
            if i < 0:   # meter chose a range not in the list
                self.pm_range.addItem(f"Fixed {si(cur, 'W')}", cur)
                i = self.pm_range.count() - 1
            self.pm_range.setCurrentIndex(i)
            self.pm_range.setToolTip("Use a fixed range for measurements: the meter "
                                     "then keeps the same gain, so readings stay linear. "
                                     "Pick the smallest range above your laser power.")
            self.pm_range.currentIndexChanged.connect(self.set_pm_range)
            f.addRow("Range", self.pm_range)
            self.pm_avg = QtWidgets.QComboBox()
            for n in (1, 3, 10, 30, 100, 300):
                self.pm_avg.addItem(f"{n}  (≈ {n * 3:g} ms per reading)", n)
            i = self.pm_avg.findData(int(getattr(self.pm, "averages", 10)))
            self.pm_avg.setCurrentIndex(i if i >= 0 else 2)
            self.pm_avg.setToolTip("More averaging = less noise but slower readings")
            self.pm_avg.currentIndexChanged.connect(self._avg_changed)
            f.addRow("Averaging", self.pm_avg)
            ps = config.get("power_set", {}) or {}
            self.pm_set = QtWidgets.QCheckBox("Set the laser power before each scan")
            self.pm_set.setChecked(bool(ps.get("enabled", False)))
            self.pm_set.setToolTip(
                "Before the scan starts, the lock-in SINE OUT amplitude is adjusted "
                "until the power on the sample is within the tolerance of the target. "
                "Nothing is changed during the scan; the power is still recorded.")
            f.addRow(self.pm_set)
            self.pm_target = SISpinBox(1e-6, "µW")
            self.pm_target.setRange(0.001, 1e6)
            self.pm_target.setDecimals(3)
            self.pm_target.setValue(float(ps.get("target_uW", 10.0)))
            self.pm_target.setToolTip("Power on the sample (meter reading ÷ ratio). "
                                      "Plain numbers are µW; 1m = 1 mW.")
            f.addRow("Target on sample (µW)", self.pm_target)
            self.pm_tol = SISpinBox(1.0, "%")
            self.pm_tol.setRange(0.1, 20)
            self.pm_tol.setDecimals(1)
            self.pm_tol.setValue(float(ps.get("tol_pct", 1.0)))
            f.addRow("Tolerance (± %)", self.pm_tol)
            self.pm_set_btn = QtWidgets.QPushButton("Set power now")
            self.pm_set_btn.clicked.connect(self.set_power_now)
            f.addRow(self.pm_set_btn)
            for w_ in (self.pm_target, self.pm_tol):
                w_.valueChanged.connect(self._remember_power_set)
            self.pm_set.toggled.connect(self._remember_power_set)
            left.addWidget(g)
            self.pm_status = QtWidgets.QLabel()
            self.pm_status.setStyleSheet("font-weight:bold")
            self.statusBar().addPermanentWidget(self.pm_status)
            # read the meter in a background thread so the window never waits for it
            self.pm_poller = PowerPoller(self.pm)
            self.pm_poller.reading.connect(self.show_power)
            self.pm_poller.failed.connect(
                lambda e: (self.pm_lab.setText("read error"),
                           self.statusBar().showMessage(f"Power meter: {e}")))
            self.pm_poller.start()

        inst_lay.addStretch(1)
        self.build_iv_tab(iv_lay)
        self.build_sweep_tab(sweep_lay)
        for cb in side.findChildren(QtWidgets.QComboBox):     # narrow drop-downs
            cb.setSizeAdjustPolicy(
                QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(10)
        left = scan_lay
        g = QtWidgets.QGroupBox("Save")
        f = QtWidgets.QFormLayout(g)
        self.sample = QtWidgets.QLineEdit(d.get("sample", "sample"))
        self.folder = QtWidgets.QLineEdit(config.get("save_dir", "data"))
        f.addRow("Sample", self.sample)
        f.addRow("Folder", self.folder)
        import naming
        row = QtWidgets.QHBoxLayout()
        self.name_pat = QtWidgets.QComboBox()
        self.name_pat.setEditable(True)
        self.name_pat.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.name_pat.setMinimumContentsLength(10)
        pats = [d.get("file_pattern") or naming.DEFAULT_PATTERN, naming.DEFAULT_PATTERN,
                "{sample}_{date}_{n}", "{date}_{sample}_{size}_{step}_{n}",
                "{sample}_{date}_{size}_{step}_{power}_{bias}_{n}"]
        for p_ in dict.fromkeys(pats):
            self.name_pat.addItem(p_)
        self.name_pat.setToolTip(
            "How measurements are named. Each one gets its own folder with this name, "
            "holding the .h5, the PNG and the CSV folder.\nUse 'Add' to insert fields, "
            "including the quantities from the Experiment notes tab (e.g. {Temperature}). "
            "{n} is a running number: 001, 002 …")
        add = QtWidgets.QToolButton()
        add.setText("Add ▾")
        add.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.name_menu = QtWidgets.QMenu(add)
        add.setMenu(self.name_menu)
        self.name_menu.aboutToShow.connect(self._fill_name_menu)
        row.addWidget(self.name_pat, 1)
        row.addWidget(add)
        f.addRow("File name", row)
        self.name_preview = QtWidgets.QLabel()
        self.name_preview.setWordWrap(True)
        self.name_preview.setStyleSheet("color:gray")
        f.addRow(self.name_preview)
        self.name_pat.editTextChanged.connect(self._name_changed)
        self.sample.textChanged.connect(self._name_changed)
        left.addWidget(g)

        row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("Start scan")
        self.start_btn.setStyleSheet("font-weight:bold; padding:8px")
        self.stop_btn = QtWidgets.QPushButton("Stop && save")
        self.stop_btn.setToolTip("Stop after the current pixel and keep what was measured. "
                                 "While the laser power is being set before a scan, this "
                                 "aborts the power setting (the sine stays where it is).")
        self.stop_btn.setEnabled(False)
        self.discard_btn = QtWidgets.QPushButton("Stop && discard")
        self.discard_btn.setToolTip("Stop and delete this scan (nothing is saved)")
        self.discard_btn.setEnabled(False)
        self.discard_btn.clicked.connect(self.stop_discard)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.start_btn, 2)
        row.addWidget(self.stop_btn, 1)
        row.addWidget(self.discard_btn, 1)
        scan_lay.addStretch(1)
        side_lay.addLayout(row)
        self.prog = QtWidgets.QProgressBar()
        side_lay.addWidget(self.prog)

        # ---------------- right: maps ----------------
        right = QtWidgets.QVBoxLayout()
        root.addLayout(right, 1)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Photocurrent channel"))
        self.ch_pc = QtWidgets.QComboBox()
        self.ch_pc.addItems(CHANNELS)
        bar.addWidget(self.ch_pc)
        bar.addSpacing(20)
        bar.addWidget(QtWidgets.QLabel("Reflection channel"))
        self.ch_r = QtWidgets.QComboBox()
        self.ch_r.addItems(CHANNELS)
        self.ch_r.setCurrentText("R")
        bar.addWidget(self.ch_r)
        bar.addSpacing(20)
        bar.addWidget(QtWidgets.QLabel("Map"))
        self.map_sel = QtWidgets.QComboBox()
        self.map_sel.setMinimumWidth(110)
        bar.addWidget(self.map_sel)
        auto = QtWidgets.QPushButton("Auto colour")
        auto.clicked.connect(self.reset_levels)
        bar.addWidget(auto)
        bar.addStretch(1)
        right.addLayout(bar)
        self.map_tools = QtWidgets.QWidget()    # second row: tools drawn on the maps
        bar = QtWidgets.QHBoxLayout(self.map_tools)
        bar.setContentsMargins(0, 0, 0, 0)
        self.rois, self._roi_sync = [], False
        self.frame_btn = QtWidgets.QPushButton("Draw scan frame")
        self.frame_btn.setCheckable(True)
        self.frame_btn.setToolTip("Show a frame on the maps. Move, resize and rotate it "
                                  "over the area you want, then press 'Scan this frame'.")
        self.frame_btn.toggled.connect(self.toggle_frame)
        self.use_frame_btn = QtWidgets.QPushButton("Scan this frame")
        self.use_frame_btn.setEnabled(False)
        self.use_frame_btn.clicked.connect(self.use_frame)
        bar.addWidget(self.frame_btn)
        bar.addWidget(self.use_frame_btn)
        bar.addSpacing(12)
        self.line_rois = []
        self.line_btn = QtWidgets.QPushButton("Draw line")
        self.line_btn.setCheckable(True)
        self.line_btn.setToolTip("Show a line on the maps. Drag its two ends where you "
                                 "want, then press 'Scan this line'. Step, timing, series "
                                 "(bias / laser power) and laser power come from the Scan "
                                 "and Instruments tabs.")
        self.line_btn.toggled.connect(self.toggle_line)
        self.use_line_btn = QtWidgets.QPushButton("Scan this line")
        self.use_line_btn.setEnabled(False)
        self.use_line_btn.clicked.connect(self.scan_line)
        bar.addWidget(self.line_btn)
        bar.addWidget(self.use_line_btn)
        bar.addSpacing(12)
        self.res_rois, self.res_dialog = [], None
        self.res_btn = QtWidgets.QPushButton("Measure resolution")
        self.res_btn.setCheckable(True)
        self.res_btn.setToolTip(
            "Draws a line on the reflection map. Put it across a sharp edge (e.g. a gold "
            "electrode); the reflection profile is fitted and the spot size (FWHM) shown. "
            "Drag the side handle to average over a wider band.")
        self.res_btn.toggled.connect(self.toggle_resolution)
        bar.addWidget(self.res_btn)
        # the three toggle buttons stay as state holders, the user sees one menu
        for b_ in (self.frame_btn, self.line_btn, self.res_btn):
            b_.setVisible(False)
        self.draw_btn = self._draw_menu([
            ("Scan frame (move, resize, rotate)", self.frame_btn),
            ("Line to scan along", self.line_btn),
            ("Resolution line (reflection map)", self.res_btn)])
        bar.insertWidget(0, self.draw_btn)
        self.use_frame_btn.setVisible(False)
        self.use_line_btn.setVisible(False)
        bar.addSpacing(8)
        bar.addWidget(self._help_btn(
            "Maps",
            "• Double-click a map to move the laser there (when idle).\n"
            "• Drag the dashed line for a line cut; drag the colour bar to set the scale.\n"
            "• Draw ▸ Scan frame: move, resize and rotate the frame over the area you want, "
            "then press 'Scan this frame' (fills in the Map tab).\n"
            "• Draw ▸ Line to scan along: drag its two ends, then 'Scan this line'. Step, "
            "timing and series come from the Map tab.\n"
            "• Draw ▸ Resolution line: put it across a sharp edge on the reflection map; the "
            "spot size (FWHM) is fitted."))

        self.tabs = QtWidgets.QTabWidget()
        # the drawing tools sit next to the tab names, so nothing jumps when they hide
        self.tabs.setCornerWidget(self.map_tools, QtCore.Qt.TopRightCorner)
        right.addWidget(self.tabs, 1)
        maps_w = QtWidgets.QWidget()
        maps = QtWidgets.QHBoxLayout(maps_w)
        maps.setContentsMargins(0, 0, 0, 0)
        self.pc_panel = MapPanel("Photocurrent", "CET-D1")
        self.r_panel = MapPanel("Reflection", "CET-L1")
        maps.addWidget(self.pc_panel)
        maps.addWidget(self.r_panel)
        self.tabs.addTab(maps_w, "Maps")
        if self.cam is not None:
            self.tabs.addTab(self.build_camera_tab(), "Camera")
        self.plot_stack = QtWidgets.QStackedWidget()
        ph = QtWidgets.QLabel("Line scans, point sweeps and I–V curves appear here.")
        ph.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        ph.setStyleSheet("color:gray")
        self.plot_stack.addWidget(ph)
        self.tabs.addTab(self.plot_stack, "Plot")
        self.tabs.addTab(self.build_experiment_tab(), "Experiment notes")
        # a GraphicsLayoutWidget (like the maps) so it follows the light/dark theme
        self.iv_glw = pg.GraphicsLayoutWidget()
        self.iv_plot = self.iv_glw.addPlot()
        self.iv_plot.setLabel("bottom", "Bias", units="V")
        self.iv_plot.setLabel("left", "Current", units="A")
        self.iv_plot.showGrid(x=True, y=True, alpha=0.25)
        self.iv_legend = self.iv_plot.addLegend(offset=(10, 10))
        self.iv_data = None
        self.plot_stack.addWidget(self.iv_glw)
        from sweep_view import SweepView, LineView
        self.line_view = LineView()
        self.plot_stack.addWidget(self.line_view)
        self.line_data = None
        self.sweep_view = SweepView()
        self.plot_stack.addWidget(self.sweep_view)
        self.sweep_data = None
        self.side_tabs.currentChanged.connect(self._side_tab_changed)
        # the map drawing tools only show with the maps
        self.tabs.currentChanged.connect(
            lambda *_: self.map_tools.setVisible(self.tabs.currentIndex() == 0))
        for p in (self.pc_panel, self.r_panel):
            p.clicked.connect(self.goto)

        for w in (self.ch_pc, self.ch_r, self.map_sel):
            w.currentIndexChanged.connect(self.redraw)
        for w in (self.ch_r, self.map_sel):
            w.currentIndexChanged.connect(lambda *_: self.update_resolution())
        for w in (self.xs_, self.ys_, self.step, self.settle, self.extra):
            w.valueChanged.connect(self.update_eta)
        self.ser_kind.currentIndexChanged.connect(self.update_eta)
        self.settle_auto.toggled.connect(self.update_eta)
        self.serp.toggled.connect(self.update_eta)
        self.mode.currentIndexChanged.connect(self.update_eta)
        self.mode.currentIndexChanged.connect(
            lambda *_: self._save_setting("scan_defaults", "mode", self.mode.currentData()))
        self.ser_vals.editingFinished.connect(self.update_eta)

        self.statusBar().showMessage("Ready" + sim)
        if hw.warnings:
            QtCore.QTimer.singleShot(300, lambda: QtWidgets.QMessageBox.warning(
                self, "SPCM", "\n\n".join(hw.warnings)))
        for w in (self.amp_mode, self.amp_sens):
            w.currentIndexChanged.connect(self._amp_changed)
        self.amp_gain.currentIndexChanged.connect(self._amp_changed)
        self._amp_changed()
        for w in (self.xs_, self.ys_, self.step):
            w.valueChanged.connect(self._name_changed)
        self.ser_kind.currentIndexChanged.connect(self._name_changed)
        self.ser_vals.editingFinished.connect(self._name_changed)
        if self.pm is not None:
            self.pm_set.toggled.connect(self._name_changed)
            self.pm_target.valueChanged.connect(self._name_changed)
        self.folder.textChanged.connect(self._name_changed)
        QtCore.QTimer.singleShot(400, self._name_changed)
        QtCore.QTimer.singleShot(300, self._start_here_on_launch)
        dark = bool((self.config.get("ui", {}) or {}).get("dark", False))
        for w in self.findChildren(pg.GraphicsLayoutWidget):
            style_plots(w, dark)
        self.refresh_lockins()
        self.update_eta()

    # ----------------------------------------------------------------
    def build_menu(self):
        m = self.menuBar().addMenu("&File")
        m.addAction("Open saved scan…", self.open_file)
        m.addAction("Export shown scan to CSV…", self.export_csv)
        m.addAction("Save figure of shown map…", self.save_figure)
        m.addAction("Open data folder", lambda: self.open_path(self.folder.text()))
        m.addSeparator()
        m.addAction("Quit", self.close)
        m = self.menuBar().addMenu("&View")
        act = m.addAction("Dark theme")
        act.setCheckable(True)
        act.setChecked(bool((self.config.get("ui", {}) or {}).get("dark", False)))
        act.toggled.connect(self._toggle_dark)
        m = self.menuBar().addMenu("&Hardware")
        m.addAction("Settings…", self.open_settings)
        if self.hw.simulated:
            m.addAction("Restart with real hardware", lambda: self.do_restart(False))
        else:
            m.addAction("Restart in simulation mode", lambda: self.do_restart(True))
        m.addAction("Reconnect (restart)", lambda: self.do_restart(self.hw.simulated))
        m = self.menuBar().addMenu("&Help")
        from appinfo import app_dir, __version__
        here = app_dir()
        m.addAction("Read me", lambda: self.open_path(os.path.join(here, "README.md")))
        m.addAction("About SPCM", lambda: QtWidgets.QMessageBox.information(
            self, "SPCM", f"SPCM version {__version__}\nScanning photocurrent microscope control"))
        m.addAction("Connected instruments", lambda: QtWidgets.QMessageBox.information(
            self, "SPCM", "\n".join(f"{k}: {v}" for k, v in self.hw.describe().items())))

    @staticmethod
    def open_path(path):
        from pyqtgraph.Qt import QtGui
        os.makedirs(path, exist_ok=True) if not os.path.splitext(path)[1] else None
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.abspath(path)))

    def open_settings(self):
        if self.thread is not None:
            self.error("Stop the scan before changing the hardware settings.")
            return
        from settings_dialog import SettingsDialog
        if SettingsDialog(self.config, self.config_path, self).exec():
            self.do_restart(False)

    def do_restart(self, simulate):
        if self.thread is not None:
            self.error("Stop the scan first.")
            return
        if self._restart is None:
            return
        for t in self.findChildren(QtCore.QTimer):
            t.stop()
        self._stop_poller()
        self.hw.close()          # free the COM/GPIB ports for the new copy
        self._hw_closed = True
        self._restart(simulate)

    def accessory(self, name):
        for a in self.hw.accessories:
            if a.name == name:
                return a
        return None

    def _toggle_dark(self, on):
        self.config.setdefault("ui", {})["dark"] = bool(on)
        self._save_setting("ui", "dark", bool(on))
        apply_theme(QtWidgets.QApplication.instance(), bool(on))
        for w in self.findChildren(pg.GraphicsLayoutWidget):
            style_plots(w, bool(on))

    def _cross_moved(self, *_):
        p = self.cam_cross.pos()
        self.config.setdefault("camera", {}).update(spot_x=float(p.x()), spot_y=float(p.y()))
        self._save_setting("camera", "spot_x", float(p.x()))
        self._save_setting("camera", "spot_y", float(p.y()))

    def read_z(self):
        if self._busy():
            return
        try:
            self.z_lab.setText(f"{self.hw.stage.position_z():.3f}")
        except Exception as e:
            self.z_lab.setText("?")
            self.statusBar().showMessage(f"Could not read z: {e}")

    def autofocus(self):
        if self.af_thread is not None:            # button acts as Cancel
            self.af_worker.stop()
            self.af_btn.setText("Cancelling…")
            return
        if self.thread is not None:
            self.error("Autofocus is not available during a scan.")
            return
        if not getattr(self, "_cross_placed", False):
            self.error("Open the Camera tab and drag the cyan cross onto the laser "
                       "spot first.")
            return
        p = self.cam_cross.pos()
        rng, step = self.af_range.value(), self.af_step.value() / 1000
        for k, v in (("range_um", rng), ("step_um", step)):
            self._save_setting("autofocus", k, v)
        af = self.config.get("autofocus", {}) or {}
        self.af_worker = AutofocusWorker(
            self.hw.stage, self.cam, (p.x(), p.y()), rng, step,
            af.get("backlash_um", 2.0),
            (af.get("z_min"), af.get("z_max")))
        self.af_thread = QtCore.QThread()
        self.af_worker.moveToThread(self.af_thread)
        self.af_thread.started.connect(self.af_worker.run)
        self.af_worker.status.connect(self.statusBar().showMessage)
        self.af_worker.done.connect(self._af_done)
        self.af_btn.setText("Cancel autofocus")
        self.start_btn.setEnabled(False)
        self.af_thread.start()

    def _af_done(self, result):
        self.af_thread.quit()
        self.af_thread.wait()
        self.af_thread = self.af_worker = None
        self.af_btn.setText("Autofocus")
        self.start_btn.setEnabled(True)
        if isinstance(result, str):
            self.error("Autofocus failed:\n" + result)
            return
        self.z_lab.setText(f"{result['z_best']:.3f}")
        self.statusBar().showMessage(result["message"])
        self.last_autofocus = result

    def _busy(self):
        """True while a scan, I-V, autofocus or power setting runs in the background:
        then only that thread talks to the stage and the lock-ins (two threads asking
        at once can mix up the replies)."""
        return (self.thread is not None or getattr(self, "af_thread", None) is not None
                or getattr(self, "cal_thread", None) is not None
                or getattr(self, "ps_thread", None) is not None)

    def _lockin_unit(self):
        """'A' if the photocurrent SR830 uses its current input, else 'V'.
        While busy, the value read before is used."""
        if self._busy():
            return getattr(self, "_lockin_unit_now", "V")
        try:
            self._lockin_unit_now = self.hw.lockin_pc.input_unit()
        except Exception:
            self._lockin_unit_now = "V"
        return self._lockin_unit_now

    def _amp_value(self):
        m = self.amp_mode.currentData()
        if m == "sr570":
            return float(self.amp_sens.currentData())
        if m == "voltage":
            return float(self.amp_gain.currentData())
        return 1.0

    def _amp_changed(self, *_):
        m = self.amp_mode.currentData()
        self.amp_sens.setEnabled(m == "sr570")
        self.amp_gain.setEnabled(m == "voltage")
        if hasattr(self, "ser_kind"):
            self._ser_changed()
        from scanner import pc_scale
        fac, unit, formula = pc_scale(ScanSettings(amp_mode=m, amp_value=self._amp_value(),
                                                   signal_as="peak"))
        self.amp_formula.setText("Maps and data are converted:\n"
                                 + formula.replace("raw", "lock-in reading")
                                 + (f"  [{unit}]" if unit else ""))
        amp = {"mode": m, "sr570_sensitivity": float(self.amp_sens.currentData()),
               "voltage_gain": float(self.amp_gain.currentData()),
               }
        self.config["amplifier"] = amp
        for k, v in amp.items():
            self._save_setting("amplifier", k, v)
        if self.thread is None:
            self.redraw()
        self.update_eta()

    def _factor_changed(self, v):
        self.pm.sample_factor = v
        self.config.setdefault("powermeter", {})["sample_factor"] = v
        self._save_setting("powermeter", "sample_factor", v)

    def _remember_power_set(self, *_):
        ps = {"enabled": self.pm_set.isChecked(), "target_uW": self.pm_target.value(),
              "tol_pct": self.pm_tol.value()}
        self.config["power_set"] = ps
        for k, v in ps.items():
            self._save_setting("power_set", k, v)

    def set_power_now(self):
        """Adjust the sine amplitude now (outside a scan), in the background.
        While it runs, the same button aborts it."""
        if getattr(self, "ps_thread", None) is not None:
            self.ps_worker.stop()
            self.pm_set_btn.setText("Aborting…")
            return
        if self.thread is not None:
            return
        from scanner import PowerSetter
        li = self._laser_lockin()
        self.ps_worker = PowerSetter(self.pm, li, self.pm_target.value() * 1e-6,
                                     self.pm_tol.value(),
                                     (self.config.get("power_lock", {}) or {}).get(
                                         "max_amplitude_V", 5.0))
        self.ps_thread = QtCore.QThread()
        self.ps_worker.moveToThread(self.ps_thread)
        self.ps_thread.started.connect(self.ps_worker.run)
        self.ps_worker.status.connect(self.statusBar().showMessage)
        self.ps_worker.done.connect(self._power_set_done)
        self.pm_set_btn.setText("Abort power setting")
        self.pm_set_btn.setStyleSheet("color:#c62828; font-weight:bold")
        self.start_btn.setEnabled(False)
        self.ps_thread.start()

    def _power_set_done(self, res):
        self.ps_thread.quit()
        self.ps_thread.wait()
        self.ps_thread = self.ps_worker = None
        self.pm_set_btn.setText("Set power now")
        self.pm_set_btn.setStyleSheet("")
        self.start_btn.setEnabled(True)
        if isinstance(res, str):
            self.toast(res, "warn")
        else:
            self.toast(res["message"], "info" if res["ok"] else "warn")

    def _laser_lockin(self):
        lc = self.config.get("power_lock", {}) or {}
        return (self.hw.lockin_refl if lc.get("lockin") == "reflection"
                else self.hw.lockin_pc)

    def _remember_lock(self, on):
        self.config.setdefault("power_lock", {})["enabled"] = bool(on)
        self._save_setting("power_lock", "enabled", bool(on))

    def _save_setting(self, section, key, value):
        """Write one value to config.yaml so it is remembered next time."""
        if not self.config_path:
            return
        try:
            import yaml
            with open(self.config_path, encoding="utf-8-sig") as fh:
                disk = yaml.safe_load(fh) or {}
            disk.setdefault(section, {})[key] = value
            with open(self.config_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(disk, fh, sort_keys=False, allow_unicode=True)
        except Exception as e:
            self.statusBar().showMessage(f"Could not save setting: {e}")

    def set_pm_range(self, *_):
        if self.thread is not None:
            self.error("Don't change the power meter range during a scan.")
            return
        want = self.pm_range.currentData()
        try:
            got = self.pm.set_range(want)
            self.statusBar().showMessage(
                "Power meter: autorange" if want is None else
                f"Power meter range fixed at {si(got, 'W')}")
            # remember for next time
            self.config.setdefault("powermeter", {})["range_W"] = want
            self._save_setting("powermeter", "range_W", want)
        except Exception as e:
            self.error(f"Could not set the power meter range:\n{e}")

    def _stop_poller(self):
        p = getattr(self, "pm_poller", None)
        if p is not None:
            p.stop()
            p.wait(3000)

    def _avg_changed(self, *_):
        n = int(self.pm_avg.currentData())
        try:
            self.pm.set_averages(n)
            self.config.setdefault("powermeter", {})["averages"] = n
            self._save_setting("powermeter", "averages", n)
        except Exception as e:
            self.error(f"Could not set averaging:\n{e}")

    def show_power(self, p):
        self._last_power_W = p
        try:
            self.pm_raw.setText(si(p, "W"))
            if self.pm.over_range(p):
                self.pm_lab.setText("OVER RANGE")
                self.pm_status.setText("  Laser: OVER RANGE  ")
                self.pm_lab.setStyleSheet("font-size:16pt; font-weight:bold; color:#e53935")
                return
            self.pm_lab.setStyleSheet("font-size:16pt; font-weight:bold")
            self.pm_lab.setText(si(p / self.pm.sample_factor, "W"))
            self.pm_status.setText(f"  Laser on sample: {si(p / self.pm.sample_factor, 'W')}  ")
        except Exception as e:
            self.statusBar().showMessage(f"Power meter: {e}")

    DEFAULT_FIELDS = [["Polarization", ""], ["Temperature", ""], ["Pressure", ""],
                      ["Laser wavelength", ""], ["Objective", ""],
                      ["Modulation frequency", ""],
                      ["Gate voltage", ""]]

    def build_experiment_tab(self):
        exp = self.config.get("experiment", {}) or {}
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lab = QtWidgets.QLabel(
            "Saved with every scan. Write units with the value (e.g. 4.2 K, 1e-6 mbar, "
            "45°). Values are remembered for the next scan; edit what changed.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.exp_table = QtWidgets.QTableWidget(0, 2)
        self.exp_table.setHorizontalHeaderLabels(["Quantity", "Value"])
        self.exp_table.horizontalHeader().setStretchLastSection(True)
        self.exp_table.horizontalHeader().resizeSection(0, 220)
        self.exp_table.verticalHeader().setVisible(False)
        for k, v in exp.get("fields") or self.DEFAULT_FIELDS:
            self._add_field(k, v)
        lay.addWidget(self.exp_table, 2)
        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add row")
        add.clicked.connect(lambda: (self._add_field("", ""),
                                     self.exp_table.editItem(self.exp_table.item(
                                         self.exp_table.rowCount() - 1, 0))))
        rem = QtWidgets.QPushButton("Remove selected row")
        rem.clicked.connect(lambda: self.exp_table.removeRow(self.exp_table.currentRow()))
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QtWidgets.QLabel("Notes"))
        self.exp_notes = QtWidgets.QPlainTextEdit(exp.get("notes", ""))
        self.exp_notes.setPlaceholderText("Anything else: what you are testing, "
                                          "things you noticed, ...")
        lay.addWidget(self.exp_notes, 1)
        return w

    def _add_field(self, k, v):
        r = self.exp_table.rowCount()
        self.exp_table.insertRow(r)
        self.exp_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(k)))
        self.exp_table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(v)))

    def experiment_fields(self):
        out = []
        for r in range(self.exp_table.rowCount()):
            k = self.exp_table.item(r, 0)
            v = self.exp_table.item(r, 1)
            k = k.text().strip() if k else ""
            if k:
                out.append([k, v.text().strip() if v else ""])
        return out

    def conditions(self):
        return {k: v for k, v in self.experiment_fields()}

    def remember_experiment(self):
        exp = {"fields": self.experiment_fields(), "notes": self.exp_notes.toPlainText()}
        self.config["experiment"] = exp
        if not self.config_path:
            return
        try:
            import yaml
            with open(self.config_path, encoding="utf-8-sig") as fh:
                disk = yaml.safe_load(fh) or {}
            disk["experiment"] = exp
            with open(self.config_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(disk, fh, sort_keys=False, allow_unicode=True)
        except Exception:
            pass

    def build_camera_tab(self):
        w = QtWidgets.QWidget()
        self.cam_tab = w
        lay = QtWidgets.QVBoxLayout(w)
        bar = QtWidgets.QHBoxLayout()
        cc0 = self.config.get("camera", {}) or {}
        self.cam_flipx = QtWidgets.QCheckBox("Flip left-right")
        self.cam_flipx.setChecked(bool(cc0.get("flip_x", True)))
        self.cam_flipy = QtWidgets.QCheckBox("Flip up-down")
        self.cam_flipy.setChecked(bool(cc0.get("flip_y", False)))
        self.cam_live = QtWidgets.QCheckBox("Live")
        self.cam_live.setChecked(True)
        bar.addWidget(self.cam_live)
        bar.addSpacing(10)
        bar.addWidget(self.cam_flipx)
        bar.addWidget(self.cam_flipy)
        bar.addSpacing(15)
        bar.addWidget(QtWidgets.QLabel("Exposure (ms)"))
        self.cam_exp = SISpinBox(1e-3, "ms")
        self.cam_exp.setRange(0.05, 5000)
        self.cam_exp.setDecimals(2)
        self.cam_exp.setValue(self.cam.exposure_ms)
        self.cam_exp.setKeyboardTracking(False)
        self.cam_exp.valueChanged.connect(self.set_exposure)
        bar.addWidget(self.cam_exp)
        bar.addSpacing(15)
        self.cam_info = QtWidgets.QLabel(self.cam.idn + (
            "  ·  " + getattr(self.cam, "color_note", "").split(" (")[0]
            if getattr(self.cam, "color_note", "") else ""))
        self.cam_info.setStyleSheet("color:gray")
        bar.addWidget(self.cam_info)
        bar.addStretch(1)
        lay.addLayout(bar)
        # calibration + drawing on the camera image
        from calibration import Calibration
        self.cal = Calibration.from_dict(cc0.get("calibration"))
        bar2 = QtWidgets.QHBoxLayout()
        self.cal_btn = QtWidgets.QPushButton("Calibrate camera")
        self.cal_btn.setToolTip(
            "Moves the stage a few µm in x and in y and measures how the image moves, "
            "so the program knows which stage position every camera pixel shows.\n"
            "Before: put the cyan cross on the laser spot, focus, and go to a place "
            "with clear structure (an electrode or flake corner). Takes ~10 s.\n"
            "Redo it after changing the objective.")
        self.cal_btn.clicked.connect(self.calibrate_camera)
        bar2.addWidget(self.cal_btn)
        bar2.addWidget(QtWidgets.QLabel("move (µm)"))
        self.cal_step = SISpinBox(1e-6, "µm")
        self.cal_step.setRange(0.2, 200)
        self.cal_step.setDecimals(1)
        self.cal_step.setValue(float(cc0.get("calibration_move_um", 5.0)))
        self.cal_step.setToolTip("How far the stage moves for the calibration. The image "
                                 "should move by roughly 5-20 % of its width: about 5 µm "
                                 "with a 50-100× objective, 20 µm with 10×.")
        bar2.addWidget(self.cal_step)
        self.cal_lab = QtWidgets.QLabel()
        self.cal_lab.setWordWrap(True)
        self.cal_lab.setMinimumWidth(120)
        self.cal_lab.setStyleSheet("color:gray")
        bar2.addWidget(self.cal_lab, 1)
        lay.addLayout(bar2)
        bar2 = QtWidgets.QHBoxLayout()          # third row: frame and line on the image
        self.cam_rois, self.cam_line = [], []
        self.cam_frame_btn = QtWidgets.QPushButton("Draw scan frame")
        self.cam_frame_btn.setCheckable(True)
        self.cam_frame_btn.toggled.connect(self.toggle_cam_frame)
        self.cam_use_frame = QtWidgets.QPushButton("Scan this frame")
        self.cam_use_frame.clicked.connect(self.use_cam_frame)
        self.cam_line_btn = QtWidgets.QPushButton("Draw line")
        self.cam_line_btn.setCheckable(True)
        self.cam_line_btn.toggled.connect(self.toggle_cam_line)
        self.cam_use_line = QtWidgets.QPushButton("Scan this line")
        self.cam_use_line.clicked.connect(self.use_cam_line)
        self.cam_draw = self._draw_menu([("Scan frame (move, resize, rotate)",
                                          self.cam_frame_btn),
                                         ("Line to scan along", self.cam_line_btn)])
        for b_ in (self.cam_draw, self.cam_frame_btn, self.cam_use_frame, self.cam_line_btn,
                   self.cam_use_line):
            bar2.addWidget(b_)
        for b_ in (self.cam_frame_btn, self.cam_line_btn, self.cam_use_frame,
                   self.cam_use_line):
            b_.setVisible(False)
        bar2.addStretch(1)
        bar2.addWidget(self._help_btn(
            "Camera",
            "• Drag the cyan cross onto the laser spot once; it marks where the scan "
            "pixel is.\n"
            "• Calibrate camera (once, and after changing the objective): focus, put the "
            "cross on the laser spot, go to a place with clear structure (an electrode or "
            "flake corner) and press the button. The stage moves a few µm and comes back.\n"
            "• After calibrating: double-click the image to move the laser there, and use "
            "Draw ▾ for a scan frame or a line on the live image (they stay on the sample "
            "when the stage moves), then 'Scan this frame' / 'Scan this line'."))
        lay.addLayout(bar2)
        self._show_cal()
        glw = pg.GraphicsLayoutWidget()
        self.cam_plot = glw.addPlot()
        self.cam_plot.setAspectLocked(True)
        cc0 = self.config.get("camera", {}) or {}
        self.cam_plot.invertY(not bool(cc0.get("flip_y", False)))
        self.cam_plot.invertX(bool(cc0.get("flip_x", True)))
        self.cam_plot.hideAxis("left")
        self.cam_plot.hideAxis("bottom")
        self.cam_img = pg.ImageItem()
        self.cam_plot.addItem(self.cam_img)
        # draggable cross to mark where the laser spot sits on the image
        self.cam_cross = pg.TargetItem(movable=True, size=24,
                                       pen=pg.mkPen("#00e5ff", width=1.5))
        self.cam_plot.addItem(self.cam_cross)
        cc = self.config.get("camera", {}) or {}
        self._cross_placed = "spot_x" in cc and cc.get("spot_x") is not None
        if self._cross_placed:
            self.cam_cross.setPos(float(cc["spot_x"]), float(cc["spot_y"]))
        self.cam_cross.sigPositionChangeFinished.connect(self._cross_moved)
        lay.addWidget(glw, 1)
        self.cam_timer = QtCore.QTimer(self)
        self.cam_timer.timeout.connect(self.update_camera)
        self.cam_timer.start(100)
        self.cam_live.toggled.connect(self.update_camera)
        self.cam_flipx.toggled.connect(self._cam_flip)
        self.cam_flipy.toggled.connect(self._cam_flip)
        self.map_sel.currentIndexChanged.connect(self.update_camera)
        self.cam_plot.scene().sigMouseClicked.connect(self._cam_clicked)
        self._cam_stage = None              # stage position the overlays belong to
        self.cam_track = QtCore.QTimer(self)
        self.cam_track.timeout.connect(self._track_overlays)
        self.cam_track.start(1000)
        return w

    # -- camera calibration and drawing on the camera image --
    def _show_cal(self):
        ok = self.cal is not None
        self.cal_lab.setText(("calibrated: " + self.cal.describe()) if ok
                             else "not calibrated")
        for b_ in (self.cam_frame_btn, self.cam_line_btn):
            b_.setEnabled(ok)
            b_.setToolTip("" if ok else "Calibrate the camera first")
        self.cam_use_frame.setEnabled(ok and self.cam_frame_btn.isChecked())
        self.cam_use_line.setEnabled(ok and self.cam_line_btn.isChecked())
        self.cam_draw.setEnabled(ok)
        self.cam_draw.setToolTip("" if ok else "Calibrate the camera first")

    def calibrate_camera(self):
        if getattr(self, "cal_thread", None) is not None:
            self.cal_worker.stop()
            return
        if self._busy():
            self.error("Wait until the scan / autofocus has finished.")
            return
        if not getattr(self, "_cross_placed", False):
            self.error("Drag the cyan cross onto the laser spot first.")
            return
        p = self.cam_cross.pos()
        self._save_setting("camera", "calibration_move_um", self.cal_step.value())
        self.cal_worker = CalibrationWorker(self.hw.stage, self.cam, self.cal_step.value(),
                                            (p.x(), p.y()))
        self.cal_thread = QtCore.QThread()
        self.cal_worker.moveToThread(self.cal_thread)
        self.cal_thread.started.connect(self.cal_worker.run)
        self.cal_worker.status.connect(self.statusBar().showMessage)
        self.cal_worker.done.connect(self._cal_done)
        self.cal_btn.setText("Cancel calibration")
        self.start_btn.setEnabled(False)
        self.cal_thread.start()

    def _cal_done(self, result):
        self.cal_thread.quit()
        self.cal_thread.wait()
        self.cal_thread = self.cal_worker = None
        self.cal_btn.setText("Calibrate camera")
        self.start_btn.setEnabled(True)
        if isinstance(result, str):
            self.error("Camera calibration did not work:\n\n" + result)
            return
        self.cal = result
        self.config.setdefault("camera", {})["calibration"] = result.to_dict()
        self._save_setting("camera", "calibration", result.to_dict())
        self._cam_stage = None
        self._show_cal()
        self.toast("Camera calibrated: " + result.describe(), "info")

    def _laser_px(self):
        p = self.cam_cross.pos()
        return (p.x(), p.y())

    def _cam_clicked(self, ev):
        """Double-click on the camera image: move the laser to that spot."""
        if not ev.double() or self.cal is None:
            return
        if self._busy():
            return
        p = self.cam_plot.vb.mapSceneToView(ev.scenePos())
        try:
            cur = self.hw.stage.position()
        except Exception as e:
            self.error(f"Could not read the stage:\n{e}")
            return
        x, y = self.cal.px_to_stage(p.x(), p.y(), cur, self._laser_px())
        if np.hypot(x - cur[0], y - cur[1]) > 2000:
            return
        u, v = self.stage_to_user(x, y) if self.origin_xy else (x, y)
        self.goto_stage(x, y, label=f"({u:.2f}, {v:.2f}) µm (clicked on the camera)")
        self._track_overlays(force=True)

    def _track_overlays(self, force=False):
        """Keep frames/lines drawn on the camera on the same spot of the sample
        when the stage moves (the camera image moves with the stage)."""
        if self.cal is None or not (self.cam_rois or self.cam_line):
            return
        if not force and (self.tabs.currentWidget() is not self.cam_tab or self._busy()):
            return
        try:
            cur = np.array(self.hw.stage.position())
        except Exception:
            return
        if self._cam_stage is not None:
            d = cur - self._cam_stage
            if np.hypot(*d) > 0.02:
                shift = -(self.cal.B @ d)
                for r in self.cam_rois + self.cam_line:
                    r.setPos(r.pos() + QtCore.QPointF(*shift), finish=False)
        self._cam_stage = cur

    def _cam_size(self):
        img = self.cam_img.image
        if img is None:
            return 480, 360
        return img.shape[1], img.shape[0]

    def toggle_cam_frame(self, on):
        if on and not self.cam_rois:
            w, h = self._cam_size()
            cx, cy = self._laser_px()
            roi = pg.ROI((cx - w / 6, cy - h / 6), (w / 3, h / 3),
                         pen=pg.mkPen("#00e5ff", width=2),
                         hoverPen=pg.mkPen("#ffeb3b", width=2))
            roi.addScaleHandle([1, 1], [0, 0])
            roi.addScaleHandle([0, 0], [1, 1])
            roi.addScaleHandle([1, 0.5], [0, 0.5])
            roi.addScaleHandle([0.5, 1], [0.5, 0])
            roi.addRotateHandle([1, 0], [0.5, 0.5])
            self.cam_plot.addItem(roi)
            self.cam_rois.append(roi)
            self._track_overlays(force=True)
        for r in self.cam_rois:
            r.setVisible(on)
        self.cam_use_frame.setEnabled(on)
        self.cam_use_frame.setVisible(on)

    def toggle_cam_line(self, on):
        if on and not self.cam_line:
            w, _ = self._cam_size()
            cx, cy = self._laser_px()
            roi = pg.LineSegmentROI([(cx - w / 6, cy), (cx + w / 6, cy)],
                                    pen=pg.mkPen("#ffeb3b", width=2),
                                    hoverPen=pg.mkPen("#00e5ff", width=2))
            self.cam_plot.addItem(roi)
            self.cam_line.append(roi)
            self._track_overlays(force=True)
        for r in self.cam_line:
            r.setVisible(on)
        self.cam_use_line.setEnabled(on)
        self.cam_use_line.setVisible(on)

    def _cam_to_stage(self, pts):
        cur = self.hw.stage.position()
        return [self.cal.px_to_stage(x, y, cur, self._laser_px()) for x, y in pts]

    def use_cam_frame(self):
        if not self.cam_rois or self.cal is None or self._busy():
            return
        if self.origin_xy is None:
            self.error("Set the origin first (Map tab).")
            return
        try:
            self._apply_frame(*self._cam_to_stage(self._roi_corners(self.cam_rois[0])))
        except Exception as e:
            self.error(f"Could not use the frame:\n{e}")

    def use_cam_line(self):
        if not self.cam_line or self.cal is None or self._busy():
            return
        r = self.cam_line[0]
        pts = [r.mapToParent(h.pos()) for h in r.getHandles()]
        try:
            a, b = self._cam_to_stage([(p.x(), p.y()) for p in pts])
        except Exception as e:
            self.error(f"Could not read the stage:\n{e}")
            return
        self.start_line(a, b)

    def _cam_flip(self, *_):
        fx, fy = self.cam_flipx.isChecked(), self.cam_flipy.isChecked()
        self.cam_plot.invertX(fx)
        self.cam_plot.invertY(not fy)          # images are drawn top row first
        self._save_setting("camera", "flip_x", fx)
        self._save_setting("camera", "flip_y", fy)

    def set_exposure(self, ms):
        try:
            self.cam.exposure_ms = ms
        except Exception as e:
            self.statusBar().showMessage(f"Camera: {e}")

    def _show_cam(self, img):
        if img is None:
            return
        self.cam_img.setImage(img, autoLevels=False, levels=(0, 255))
        if not self._cross_placed:
            self.cam_cross.setPos(img.shape[1] / 2, img.shape[0] / 2)
            self._cross_placed = True

    def update_camera(self, *_):
        if self.tabs.currentWidget() is not getattr(self, "cam_tab", None):
            return
        if self.cam_live.isChecked():
            try:
                self._show_cam(self.cam.latest())
            except Exception as e:
                self.statusBar().showMessage(f"Camera: {e}")
        elif self.data is not None:
            ib = max(self.map_sel.currentIndex(), 0)
            snap = self.data.accessories.get(f"map{ib}/camera/start", {})
            if "image" in snap:
                self._show_cam(np.asarray(snap["image"]))

    # -- series: what changes from one map / line to the next --
    SER_NOTES = {
        "none": "One map (or line) at the present bias and laser power.",
        "bias": "One map per bias, e.g. 0, 0.5, 1 or -1:1:250m. Needs the SR570 current "
                "preamp (Instruments tab) and the SR570 as bias source (Hardware → "
                "Settings). Ramped back to 0 V at the end.",
        "power": "One map per laser power on the sample, e.g. 5, 10, 20 or 1:100:*2. Each "
                 "is set at the scan's start position with the 2 mV SINE OUT steps (one "
                 "direction) that come closest; the power reached is saved. Needs the power "
                 "meter. The SINE OUT goes back in 2 mV steps at the end.",
    }

    def _ser_changed(self, *_):
        k = self.ser_kind.currentData()
        if self._ser_shown in self._ser_texts:        # keep what was typed for each kind
            self._ser_texts[self._ser_shown] = self.ser_vals.text()
        self._ser_shown = k
        self.ser_vals.setEnabled(k != "none")
        self.ser_vals.setText(self._ser_texts.get(k, ""))
        note = self.SER_NOTES[k]
        if k == "bias" and not (self.has_bias and self.amp_mode.currentData() == "sr570"):
            note = "<span style='color:#c62828'>Bias needs the SR570 current preamp " \
                   "(Instruments tab) and the SR570 as bias source (Hardware → Settings)." \
                   "</span>"
        if k == "power" and self.accessory("powermeter") is None:
            note = "<span style='color:#c62828'>A power series needs the power meter " \
                   "(Hardware → Settings).</span>"
        warn = note.startswith("<span")
        self.ser_note.setText(note if warn else "")
        self.ser_note.setVisible(warn)
        self.ser_kind.setToolTip(self.SER_NOTES[k])
        self._remember_series()

    def _remember_series(self):
        k = self.ser_kind.currentData()
        if k in self._ser_texts:
            self._ser_texts[k] = self.ser_vals.text()
        vals = {"kind": k, **self._ser_texts}
        self.config["series"] = vals
        for key, v in vals.items():
            self._save_setting("series", key, v)

    def _series(self):
        """-> (outer, values) for the next map or line scan, checked.
        ('bias', [None]) = a single map."""
        from scanner import parse_list
        k = self.ser_kind.currentData()
        if k == "none":
            return "bias", [None]
        text = self.ser_vals.text()
        if k == "bias":
            if not (self.has_bias and self.amp_mode.currentData() == "sr570"):
                raise ValueError("A bias series needs the SR570 current preamp (Instruments "
                                 "tab) and the SR570 as bias source (Hardware → Settings).")
            vals = parse_bias_list(text)
            lim = self.hw.bias.max_abs_V
            bad = [b for b in vals if abs(b) > lim]
            if bad:
                raise ValueError(f"Bias {bad[0]} V exceeds the limit of {lim} V.")
        else:
            if self.accessory("powermeter") is None:
                raise ValueError("A laser power series needs the power meter. Turn it on "
                                 "in Hardware → Settings.")
            vals = parse_list(text, 1e-6)
            if vals and min(vals) <= 0:
                raise ValueError("Powers must be above 0.")
        if not vals:
            raise ValueError("The list of values for the series is empty.")
        return k, vals

    # ----------------------------------------------------------------
    def settings(self):
        if self.origin_xy is None:
            raise ValueError("Set the origin first (Set origin here).")
        outer, values = self._series()
        biases = values if outer == "bias" else [None]
        return ScanSettings(
            outer=outer, outer_values=values if outer == "power" else [],
            x_center=self.to_stage(self.xc.value(), self.yc.value())[0],
            y_center=self.to_stage(self.xc.value(), self.yc.value())[1],
            origin_x=self.origin_xy[0], origin_y=self.origin_xy[1],
            flip_x=self.sx < 0, flip_y=self.sy < 0,
            origin=self.origin.currentData(),
            angle_deg=self.angle.value() * self.sx * self.sy,
            x_dir=int(self.direction.currentData()[0] * self.sx),
            y_dir=int(self.direction.currentData()[1] * self.sy),
            x_size=self.xs_.value(), y_size=self.ys_.value(), step=self.step.value() / 1000,
            serpentine=self.serp.isChecked(), settle_tc=self.settle.value(),
            settle_auto=self.settle_auto.isChecked(),
            extra_settle_ms=self.extra.value(), biases=biases,
            sample=self.sample.text() or "sample", notes=self.exp_notes.toPlainText(),
            conditions=self.conditions(),
            power_lock=False,
            target_power_uW=(self.pm_target.value()
                             if self.pm is not None and self.pm_set.isChecked() else None),
            power_tol_pct=(self.pm_tol.value() if self.pm is not None else 1.0),
            mode=self.mode.currentData(),
            amp_mode=self.amp_mode.currentData(), amp_value=self._amp_value(),
            lockin_unit=self._lockin_unit(),
            signal_as="peak")

    def refresh_lockins(self):
        if self._busy():          # the running measurement is reading the lock-ins
            self.statusBar().showMessage("The lock-ins are read when the measurement "
                                         "has finished.")
            return
        try:
            self._poles_now = []
            for li, lab in ((self.hw.lockin_pc, self.li_pc), (self.hw.lockin_refl, self.li_r)):
                self._tau = getattr(self, "_tau", {})
                tau = li.time_constant()
                self._tau[id(li)] = tau
                try:
                    poles = li.slope_poles()
                except Exception:
                    poles = 4
                self._poles_now.append(poles)
                lab.setText(f"τ = {si(tau, 's')}, {6 * poles} dB/oct\n"
                            f"sens = {si(li.sensitivity(), 'V/A')}")
        except Exception as e:
            self.statusBar().showMessage(f"Lock-in read failed: {e}")
        self.update_eta()

    def update_eta(self, *_):
        try:
            s = self.settings()
        except Exception:
            self.eta.setText("-")
            return
        xs, ys = s.axes()
        n = len(xs) * len(ys) * len(s.outer_list())
        tau = max(getattr(self, "_tau", {0: 0}).values())
        from scanner import pixel_wait
        taus = list(getattr(self, "_tau", {0: 0}).values()) or [0]
        poles = getattr(self, "_poles_now", [4] * len(taus))
        wait = pixel_wait(s, taus, poles[:len(taus)] + [4] * (len(taus) - len(poles)))
        if s.mode == "fly":
            per = max(wait, 0.03)                 # ~2 samples per pixel minimum
            t = n * per + len(ys) * len(s.outer_list()) * 0.5   # + line turnaround
        else:
            per = wait + getattr(self, "_step_overhead", 0.1)   # move + read
            t = n * per
        self.eta.setText(f"{len(xs)}×{len(ys)} px × {len(s.outer_list())} map(s) "
                         f"≈ {t / 60:.1f} min")

    def _learn_overhead(self, msg):
        """Use the measured move+read time per pixel for later time estimates."""
        import re
        m = re.search(r"move (\d+) ms, wait \d+ ms, read (\d+) ms", msg)
        if m:
            self._step_overhead = (int(m.group(1)) + int(m.group(2))) / 1000

    def _origin_changed(self, *_):
        corner = self.origin.currentData() == "corner"
        self.xc_lab.setText("Start x (µm)" if corner else "Centre x (µm)")
        self.yc_lab.setText("Start y (µm)" if corner else "Centre y (µm)")
        for w in (self.xc, self.yc):
            w.setToolTip("Measured from the origin (0, 0). 0 = start exactly at the origin.")
        self.direction.setEnabled(True)

    # -- coordinates: program (u, v) relative to the origin <-> stage (X, Y) --
    def to_stage(self, u, v, frame=None):
        ox, oy, sx, sy = frame or (*self.origin_xy, self.sx, self.sy)
        return ox + sx * u, oy + sy * v

    def set_origin(self, x, y):
        self.origin_xy = (x, y)
        self.xc.setValue(0.0)
        self.yc.setValue(0.0)
        self.origin_lab.setText(f"origin (0, 0) = stage x {x:.2f}, y {y:.2f} µm")

    def _start_here_on_launch(self):
        """When the program opens, (0, 0) is where the stage is now
        (never an old or default position far away)."""
        try:
            x, y = self.hw.stage.position()
        except Exception:
            return
        self.set_origin(x, y)
        self.statusBar().showMessage(
            f"Origin (0, 0) set to the current stage position ({x:.1f}, {y:.1f} µm)")

    def remember_scan(self, s):
        """Keep size, step, mode etc. for next time (not the position)."""
        keep = {"x_size": s.x_size, "y_size": s.y_size, "step": s.step,
                "serpentine": s.serpentine, "settle_auto": s.settle_auto,
                "settle_tc": s.settle_tc, "extra_settle_ms": s.extra_settle_ms,
                "origin": s.origin, "mode": s.mode, "sample": s.sample}
        for k, v in keep.items():
            self._save_setting("scan_defaults", k, v)

    def go_start(self):
        if self.origin_xy is None:
            self.error("Set the origin first.")
            return
        self.goto_stage(*self.to_stage(self.xc.value(), self.yc.value()),
                        label=f"({self.xc.value():.2f}, {self.yc.value():.2f}) µm")

    def centre_here(self):
        if self._busy():
            self.error("Wait until the measurement / autofocus has finished.")
            return
        try:
            x, y = self.hw.stage.position()
            self.set_origin(x, y)
            self.statusBar().showMessage("Origin (0, 0) set here")
        except Exception as e:
            self.error(f"Could not read stage position:\n{e}")

    def goto(self, u, v):
        """Double-click on a map: (u, v) in that map's axes."""
        if self.data is None:
            return
        self.goto_stage(*self.map_to_stage(self.data, u, v),
                        label=f"({u:.2f}, {v:.2f}) µm", mark=(u, v))

    def map_to_stage(self, d, u, v):
        """A point on a map (its axes) -> stage position, incl. scan rotation."""
        X, Y = self.to_stage(u, v, self._frame(d))
        return d.settings.rotate_xy(X, Y)

    def stage_to_user(self, X, Y):
        ox, oy = self.origin_xy
        return self.sx * (X - ox) + 0.0, self.sy * (Y - oy) + 0.0

    # -- scan frame drawn on a map --
    def toggle_frame(self, on):
        if on and self.data is None:
            self.frame_btn.setChecked(False)
            self.error("Open or measure a map first, then draw the frame on it.")
            return
        if on and not self.rois:
            u, v, _, _ = self._view_axes(self.data)
            w, h = (u[-1] - u[0]) / 2 or 1, (v[-1] - v[0]) / 2 or 1
            for p in (self.pc_panel, self.r_panel):
                roi = pg.ROI((u[0] + w / 2, v[0] + h / 2), (w, h),
                             pen=pg.mkPen("#00e5ff", width=2),
                             hoverPen=pg.mkPen("#ffeb3b", width=2))
                roi.addScaleHandle([1, 1], [0, 0])
                roi.addScaleHandle([0, 0], [1, 1])
                roi.addScaleHandle([1, 0.5], [0, 0.5])
                roi.addScaleHandle([0.5, 1], [0.5, 0])
                roi.addRotateHandle([1, 0], [0.5, 0.5])
                p.plot.addItem(roi)
                self.rois.append(roi)
            a, b = self.rois

            def sync(src, dst):
                if self._roi_sync:
                    return
                self._roi_sync = True
                dst.setState(src.getState())
                self._roi_sync = False
                self._frame_info()
            a.sigRegionChanged.connect(lambda r: sync(a, b))
            b.sigRegionChanged.connect(lambda r: sync(b, a))
            self._frame_info()
        for r in self.rois:
            r.setVisible(on)
        self.use_frame_btn.setEnabled(on)
        self.use_frame_btn.setVisible(on)

    def _frame_info(self):
        if not self.rois:
            return
        r = self.rois[0]
        self.statusBar().showMessage(
            f"Frame: {r.size()[0]:.2f} × {r.size()[1]:.2f} µm, angle {r.angle():.1f}°  "
            "(drag the frame, its corners, or the round handle to rotate)")

    def use_frame(self):
        """Fill the scan settings from the frame drawn on the map."""
        if not self.rois or self.data is None:
            return
        d = self.data
        corners = self._roi_corners(self.rois[0])
        self._apply_frame(*[self.map_to_stage(d, u, v) for u, v in corners])

    @staticmethod
    def _roi_corners(r):
        """Corner at the ROI's origin, the end of its width side and the end of
        its height side, in the coordinates of the plot it sits on."""
        w, h = r.size()[0], r.size()[1]
        return [(p.x(), p.y()) for p in (r.mapToParent(QtCore.QPointF(0, 0)),
                                         r.mapToParent(QtCore.QPointF(w, 0)),
                                         r.mapToParent(QtCore.QPointF(0, h)))]

    def _apply_frame(self, s0, s1, s2):
        """Scan tab <- a frame given by three stage positions: start corner,
        end of the first line, start of the last line."""
        p0, p1, p2 = (np.array(self.stage_to_user(*p)) for p in (s0, s1, s2))
        ex, ey = p1 - p0, p2 - p0
        ydir = 1 if (ex[0] * ey[1] - ex[1] * ey[0]) >= 0 else -1
        ang = float(np.degrees(np.arctan2(ex[1], ex[0])))
        self.origin.setCurrentIndex(self.origin.findData("corner"))
        self.xc.setValue(float(p0[0]))
        self.yc.setValue(float(p0[1]))
        self.xs_.setValue(float(np.hypot(*ex)))
        self.ys_.setValue(float(np.hypot(*ey)))
        for i in range(self.direction.count()):
            if tuple(self.direction.itemData(i)) == (1, ydir):
                self.direction.setCurrentIndex(i)
        self.angle.setValue(round(ang, 2))
        if abs(ang) > 0.01 and self.mode.currentData() == "fly":
            self.mode.setCurrentIndex(self.mode.findData("step"))
        self.side_tabs.setCurrentIndex(0)
        self.statusBar().showMessage(
            f"Scan set from the frame: start ({p0[0]:.2f}, {p0[1]:.2f}) µm, "
            f"{np.hypot(*ex):.2f} × {np.hypot(*ey):.2f} µm, angle {ang:.1f}°. "
            "Check the step, then press Start scan.")

    def goto_stage(self, x, y, label="", mark=None):
        if self._busy():
            return
        try:
            px, py = self.hw.stage.position()
            dist = float(np.hypot(x - px, y - py))
            if dist > 500 and QtWidgets.QMessageBox.question(
                    self, "SPCM",
                    f"This moves the stage {dist / 1000:.2f} mm.\n\n"
                    f"Stage now: x = {px:.1f}, y = {py:.1f} µm\n"
                    f"Target: x = {x:.1f}, y = {y:.1f} µm\n\nMove there?") != \
                    QtWidgets.QMessageBox.StandardButton.Yes:
                return
            self.hw.stage.move_to(x, y)
            if mark is not None:
                for p in (self.pc_panel, self.r_panel):
                    p.marker.setData([mark[0]], [mark[1]])
            self.statusBar().showMessage(f"Moved to {label}")
        except Exception as e:
            self.error(f"Move failed:\n{e}")

    def _frame(self, d):
        """(origin x, origin y, sign x, sign y) of a scan, for its map axes."""
        st = d.settings
        ox = getattr(st, "origin_x", None)
        oy = getattr(st, "origin_y", None)
        if ox is None or oy is None:
            ox, oy = st.x_center, st.y_center
        return (ox, oy, -1.0 if getattr(st, "flip_x", False) else 1.0,
                -1.0 if getattr(st, "flip_y", False) else 1.0)

    def _view_axes(self, d):
        """Map axes in program coordinates (ascending) + index order."""
        ox, oy, sx, sy = self._frame(d)
        u = sx * (np.asarray(d.xs) - ox) + 0.0
        v = sy * (np.asarray(d.ys) - oy) + 0.0
        ix, iy = np.argsort(u), np.argsort(v)
        return u[ix], v[iy], ix, iy

    # ---------------------------------------------------------------- line scans
    def toggle_line(self, on):
        if on and self.data is None:
            self.line_btn.setChecked(False)
            self.error("Open or measure a map first, then draw the line on it.")
            return
        if on and not self.line_rois:
            u, v, _, _ = self._view_axes(self.data)
            a = (u[0] + (u[-1] - u[0]) * 0.25, v[0] + (v[-1] - v[0]) * 0.5)
            b = (u[0] + (u[-1] - u[0]) * 0.75, v[0] + (v[-1] - v[0]) * 0.5)
            for p in (self.pc_panel, self.r_panel):
                roi = pg.LineSegmentROI([a, b], pen=pg.mkPen("#ffeb3b", width=2),
                                        hoverPen=pg.mkPen("#00e5ff", width=2))
                p.plot.addItem(roi)
                self.line_rois.append(roi)
            r1, r2 = self.line_rois

            def sync(src, dst):
                if self._roi_sync:
                    return
                self._roi_sync = True
                dst.setState(src.getState())
                self._roi_sync = False
                self._line_info()
            r1.sigRegionChanged.connect(lambda r: sync(r1, r2))
            r2.sigRegionChanged.connect(lambda r: sync(r2, r1))
            self._line_info()
        for r in self.line_rois:
            r.setVisible(on)
        self.use_line_btn.setEnabled(on)
        self.use_line_btn.setVisible(on)

    def _line_ends(self):
        """Ends of the drawn line in the map's axes (u, v)."""
        r = self.line_rois[0]
        pts = [r.mapToParent(h.pos()) for h in r.getHandles()]
        return [(p.x(), p.y()) for p in pts]

    def _line_info(self):
        if not self.line_rois:
            return
        (u0, v0), (u1, v1) = self._line_ends()
        n = int(round(np.hypot(u1 - u0, v1 - v0) / (self.step.value() / 1000))) + 1
        self.statusBar().showMessage(
            f"Line ({u0:.2f}, {v0:.2f}) → ({u1:.2f}, {v1:.2f}) µm, "
            f"{np.hypot(u1 - u0, v1 - v0):.2f} µm, {n} points at the current step "
            "(drag the ends or the whole line)")

    def scan_line(self):
        if not self.line_rois or self.data is None:
            return
        (u0, v0), (u1, v1) = self._line_ends()
        self.start_line(self.map_to_stage(self.data, u0, v0),
                        self.map_to_stage(self.data, u1, v1))

    def start_line(self, start_stage, end_stage):
        """Scan along a line between two stage positions (µm)."""
        if self._busy():
            return
        import linescan
        try:
            base = self.settings()
            s = linescan.line_settings(
                start_stage, end_stage, base.step,
                biases=None if base.biases == [None] else base.biases,
                outer=base.outer, values=base.outer_values,
                origin=self.origin_xy, flips=(self.sx < 0, self.sy < 0),
                settle_tc=base.settle_tc, settle_auto=base.settle_auto,
                backlash_um=base.backlash_um, extra_settle_ms=base.extra_settle_ms,
                sample=base.sample, notes=base.notes, conditions=base.conditions,
                target_power_uW=base.target_power_uW, power_tol_pct=base.power_tol_pct,
                amp_mode=base.amp_mode, amp_value=base.amp_value,
                lockin_unit=base.lockin_unit, signal_as=base.signal_as)
            px, py = self.hw.stage.position()
            dist = float(np.hypot(start_stage[0] - px, start_stage[1] - py))
        except Exception as e:
            self.error(str(e))
            return
        if dist > 500 and QtWidgets.QMessageBox.question(
                self, "SPCM", f"The line starts {dist / 1000:.2f} mm away from where the "
                "stage is now. Move there and scan?") != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if not self._switch_reminder("map"):     # lock-in measurement
            return
        self.refresh_lockins()
        self.remember_experiment()
        self.worker = ScanWorker(self.hw, s, self.folder.text() or "data",
                                 self.config.get("power_lock", {}) or {},
                                 name=self.measurement_name("line", s))
        self.line_data = self.worker.data
        self.line_data.path = self.worker.path
        self.show_plot(self.line_view)
        self._redraw_line()
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.pixel.connect(self._line_pixel)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_line_finished)
        self.worker.failed.connect(self.on_line_failed)
        self.worker.notify.connect(self.toast)
        self.start_btn.setEnabled(False)
        self.use_line_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        self._discard = False
        self._last_line_draw = 0
        self.thread.start()

    def _line_pixel(self, *_):
        now = time.time()
        if now - self._last_line_draw > 0.15:
            self._last_line_draw = now
            self._redraw_line()
        elif not getattr(self, "_line_queued", False):
            self._line_queued = True
            QtCore.QTimer.singleShot(200, self._flush_line)

    def _flush_line(self):
        self._line_queued = False
        self._last_line_draw = time.time()
        self._redraw_line()

    def _redraw_line(self, *_):
        if getattr(self, "line_data", None) is None:
            return
        try:
            self.line_view.set_data(self.line_data, self.ch_pc.currentIndex())
        except Exception as e:
            self.statusBar().showMessage(f"Line plot: {e}")

    def on_line_finished(self, path):
        if self._nothing_saved(path):
            self.use_line_btn.setEnabled(self.line_btn.isChecked())
            return
        done = self.line_data.completed
        self._cleanup()
        self.use_line_btn.setEnabled(self.line_btn.isChecked())
        if getattr(self, "_discard", False):
            self._delete_scan_files(path)
            self.statusBar().showMessage("Line scan stopped and discarded (nothing saved)")
            return
        try:
            from scanner import update_experiment
            update_experiment(path, self.conditions(), self.exp_notes.toPlainText())
        except Exception as e:
            self.statusBar().showMessage(f"Could not update notes: {e}")
        try:
            import linescan
            self.line_data = load(path)
            linescan.export_csv(self.line_data)
            linescan.save_all(self.line_data)
            self._redraw_line()
        except Exception as e:
            self.statusBar().showMessage(f"CSV/figure export failed: {e}")
            return
        self.statusBar().showMessage(("Line scan finished" if done else "Line scan stopped")
                                     + f" - saved {os.path.abspath(path)} + PNG and CSV")

    def on_line_failed(self, tb):
        self._cleanup()
        self.use_line_btn.setEnabled(self.line_btn.isChecked())
        if getattr(self, "_discard", False):
            self._delete_scan_files(self.line_data.path)
            self.statusBar().showMessage("Line scan stopped and discarded (nothing saved)")
            return
        self._redraw_line()
        self.error("Line scan aborted (what was measured is saved):\n\n" + tb)

    # ---------------------------------------------------------------- point sweeps
    def build_sweep_tab(self, lay):
        sw = self.config.get("sweep", {}) or {}
        g = QtWidgets.QGroupBox("Where")
        f = QtWidgets.QFormLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.sw_pos = QtWidgets.QLabel("-")
        self.sw_pos.setWordWrap(True)
        rd = QtWidgets.QPushButton("Read")
        rd.setToolTip("Read where the stage is now")
        rd.clicked.connect(self._show_spot)
        row.addWidget(self.sw_pos, 1)
        row.addWidget(rd)
        f.addRow("Laser at", row)
        lay.addWidget(g)

        g = QtWidgets.QGroupBox("Sweep")
        f = QtWidgets.QFormLayout(g)
        self.sw_kind = QtWidgets.QComboBox()
        self.sw_kind.addItem("Power", "power")
        self.sw_kind.addItem("Bias", "bias")
        self.sw_kind.addItem("Bias × power", "bias_power")
        self.sw_kind.setToolTip("Bias × power: for every bias (outer loop) the whole "
                                "power list (inner loop)")
        self.sw_kind.setCurrentIndex(max(self.sw_kind.findData(sw.get("kind", "power")), 0))
        f.addRow("Sweep", self.sw_kind)
        self.sw_powers = QtWidgets.QLineEdit(str(sw.get("powers", "1:50:5")))
        self.sw_powers.setToolTip(
            "Power on the sample, in µW (meter reading ÷ ratio).\n"
            "  1, 2, 5, 10        a list\n"
            "  1:50:5             1, 6, 11 … 46, 50  (start:stop:step; stop always included)\n"
            "  1:100:*2           1, 2, 4, 8 … 64, 100  (×2 each time: even on a log scale)\n"
            "Prefixes are absolute: 500n = 0.5 µW, 1m = 1000 µW.\n"
            "Each power is set with the SINE OUT step (2 mV) that comes closest; "
            "the power actually reached is recorded.")
        f.addRow("Powers (µW)", self.sw_powers)
        self.sw_biases = QtWidgets.QLineEdit(str(sw.get("biases", "-1:1:0.25")))
        self.sw_biases.setToolTip("e.g. 0, 0.5, 1   or   -1:1:250m  (start:stop:step)")
        f.addRow("Biases (V)", self.sw_biases)
        self.sw_navg = QtWidgets.QSpinBox()
        self.sw_navg.setRange(1, 1000)
        self.sw_navg.setValue(int(sw.get("n_avg", 10)))
        self.sw_navg.setToolTip("Readings per point, about one time constant apart. "
                                "The mean is saved, and the spread (standard deviation) "
                                "as the error bar.")
        f.addRow("Readings per point", self.sw_navg)
        self.sw_restore = QtWidgets.QCheckBox("Put the SINE OUT back afterwards")
        self.sw_restore.setChecked(bool(sw.get("restore_power", True)))
        f.addRow(self.sw_restore)
        self.sw_note = QtWidgets.QLabel()
        self.sw_note.setWordWrap(True)
        self.sw_note.setStyleSheet("color:#c62828")
        f.addRow(self.sw_note)
        self.sw_eta = QtWidgets.QLabel("-")
        f.addRow("Est. time", self.sw_eta)
        lay.addWidget(g)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._help_btn(
            "Point sweep",
            "• The sweep runs where the laser is now. Double-click a map (or the calibrated "
            "camera) to move the laser to a spot, or use the joystick.\n"
            "• Each point: set the power and/or bias → wait until the lock-ins have settled "
            "(Timing, Map tab) → take the readings (mean ± spread).\n"
            "• Powers are set with 2 mV SINE OUT steps in one direction; the power reached "
            "is saved.\n"
            "• The plots appear in the Plot tab. Saved like maps: own folder, .h5, CSV, PNG."))
        lay.addLayout(row)
        lay.addStretch(1)
        self.sw_kind.currentIndexChanged.connect(self._sweep_kind_changed)
        for w in (self.sw_powers, self.sw_biases):
            w.editingFinished.connect(self.update_sweep_eta)
        self.sw_navg.valueChanged.connect(self.update_sweep_eta)
        self._sweep_kind_changed()

    def _sweep_kind_changed(self, *_):
        k = self.sw_kind.currentData()
        self.sw_powers.setEnabled(k in ("power", "bias_power"))
        self.sw_biases.setEnabled(k in ("bias", "bias_power"))
        self.sw_restore.setEnabled(k in ("power", "bias_power"))
        notes = []
        if k in ("power", "bias_power") and self.pm is None:
            notes.append("Power sweeps need the power meter (Hardware → Settings).")
        if k in ("bias", "bias_power") and not (
                self.has_bias and self.amp_mode.currentData() == "sr570"):
            notes.append("Bias sweeps need the SR570 current preamp: choose it in "
                         "Instruments → Photocurrent amplifier, and as bias source "
                         "in Hardware → Settings.")
        self.sw_note.setText("\n".join(notes))
        self.sw_note.setVisible(bool(notes))
        self.update_sweep_eta()

    def _side_tab_changed(self, i):
        self.start_btn.setText({1: "Start sweep", 2: "Start I–V"}.get(i, "Start scan"))
        if i == 1:
            self._show_spot()
            self._sweep_kind_changed()

    def _show_spot(self):
        if self.thread is not None or self.origin_xy is None:
            return
        try:
            X, Y = self.hw.stage.position()
            u, v = self.stage_to_user(X, Y)
            self.sw_pos.setText(f"({u:.2f}, {v:.2f}) µm")
            self.sw_pos.setToolTip("Position of the laser, measured from the origin (0, 0).")
        except Exception as e:
            self.sw_pos.setText("?")
            self.statusBar().showMessage(f"Could not read the stage: {e}")

    def _sweep_settings(self):
        from scanner import parse_list
        from sweeps import SweepSettings
        k = self.sw_kind.currentData()
        powers, biases = [], []
        if k in ("power", "bias_power"):
            if self.pm is None:
                raise ValueError("Power sweeps need the power meter. Turn it on in "
                                 "Hardware → Settings.")
            powers = parse_list(self.sw_powers.text(), 1e-6)
            if not powers:
                raise ValueError("The power list is empty.")
            if min(powers) <= 0:
                raise ValueError("Powers must be above 0.")
        if k in ("bias", "bias_power"):
            if not (self.has_bias and self.amp_mode.currentData() == "sr570"):
                raise ValueError("Bias sweeps need the SR570 current preamp (Instruments "
                                 "tab) and the SR570 as bias source (Hardware → Settings).")
            biases = parse_bias_list(self.sw_biases.text())
            if not biases:
                raise ValueError("The bias list is empty.")
            lim = self.hw.bias.max_abs_V
            bad = [b for b in biases if abs(b) > lim]
            if bad:
                raise ValueError(f"Bias {bad[0]} V exceeds the limit of {lim} V.")
        if self.origin_xy is None:
            raise ValueError("Set the origin first (Map tab).")
        X, Y = self.hw.stage.position()
        u, v = self.stage_to_user(X, Y)
        lc = self.config.get("power_lock", {}) or {}
        return SweepSettings(
            kind=k, powers_uW=powers, biases=biases, n_avg=self.sw_navg.value(),
            max_amp_V=float(lc.get("max_amplitude_V", 5.0)),
            laser_lockin=lc.get("lockin", "photocurrent"),
            restore_power=self.sw_restore.isChecked(),
            x_stage=X, y_stage=Y, u=u, v=v,
            settle_auto=self.settle_auto.isChecked(), settle_tc=self.settle.value(),
            extra_settle_ms=self.extra.value(),
            amp_mode=self.amp_mode.currentData(), amp_value=self._amp_value(),
            lockin_unit=self._lockin_unit(), signal_as="peak",
            sample=self.sample.text() or "sample", notes=self.exp_notes.toPlainText(),
            conditions=self.conditions())

    def update_sweep_eta(self, *_):
        from scanner import parse_list, pixel_wait
        try:
            k = self.sw_kind.currentData()
            npw = len(parse_list(self.sw_powers.text(), 1e-6)) if k != "bias" else 1
            nb = len(parse_bias_list(self.sw_biases.text())) if k != "power" else 1
        except Exception:
            self.sw_eta.setText("check the lists")
            return
        taus = list(getattr(self, "_tau", {0: 0.1}).values()) or [0.1]
        poles = getattr(self, "_poles_now", [4] * len(taus))
        s = ScanSettings(settle_auto=self.settle_auto.isChecked(),
                         settle_tc=self.settle.value(), extra_settle_ms=self.extra.value())
        wait = pixel_wait(s, taus, (list(poles) + [4] * len(taus))[:len(taus)])
        per = wait + self.sw_navg.value() * max(max(taus), 0.03) + (1.5 if k != "bias" else 0)
        self.sw_eta.setText(f"{nb * npw} points ≈ {nb * npw * per / 60:.1f} min")

    def _remember_sweep(self):
        vals = {"kind": self.sw_kind.currentData(), "powers": self.sw_powers.text(),
                "biases": self.sw_biases.text(), "n_avg": self.sw_navg.value(),
                "restore_power": self.sw_restore.isChecked()}
        self.config["sweep"] = vals
        for k, v in vals.items():
            self._save_setting("sweep", k, v)

    def start_sweep(self):
        try:
            s = self._sweep_settings()
        except Exception as e:
            self.error(str(e))
            return
        if not self._switch_reminder("map"):     # lock-in measurement
            return
        from sweeps import SweepWorker
        self.refresh_lockins()
        self.remember_experiment()
        self._remember_sweep()
        self._show_spot()
        self.worker = SweepWorker(self.hw, s, self.folder.text() or "data",
                                  name=self.measurement_name(s.kind, s))
        self.sweep_data = self.worker.data
        self.sweep_data.path = self.worker.path
        self.show_plot(self.sweep_view)
        self._redraw_sweep()
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.point.connect(lambda *_: self._redraw_sweep())
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_sweep_finished)
        self.worker.failed.connect(self.on_sweep_failed)
        self.worker.notify.connect(self.toast)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        self._discard = False
        self.thread.start()

    def _redraw_sweep(self, *_):
        if getattr(self, "sweep_data", None) is None:
            return
        try:
            self.sweep_view.set_data(self.sweep_data, self.ch_pc.currentIndex())
        except Exception as e:
            self.statusBar().showMessage(f"Sweep plot: {e}")

    def on_sweep_finished(self, path):
        done = self.sweep_data.completed
        self._cleanup()
        if getattr(self, "_discard", False):
            self._delete_scan_files(path)
            self.statusBar().showMessage("Sweep stopped and discarded (nothing saved)")
            return
        try:
            from scanner import update_experiment
            update_experiment(path, self.conditions(), self.exp_notes.toPlainText())
        except Exception as e:
            self.statusBar().showMessage(f"Could not update notes: {e}")
        self.remember_experiment()
        try:
            from sweeps import load_sweep, export_csv, save_all
            self.sweep_data = load_sweep(path)
            export_csv(self.sweep_data)
            save_all(self.sweep_data)
            self._redraw_sweep()
        except Exception as e:
            self.statusBar().showMessage(f"CSV/figure export failed: {e}")
            return
        self.statusBar().showMessage(("Sweep finished" if done else "Sweep stopped") +
                                     f" - saved {os.path.abspath(path)} + PNG and CSV")
        self.toast("Sweep finished" if done else "Sweep stopped", "info")

    def on_sweep_failed(self, tb):
        self._cleanup()
        if getattr(self, "_discard", False):
            self._delete_scan_files(self.sweep_data.path)
            self.statusBar().showMessage("Sweep stopped and discarded (nothing saved)")
            return
        self._redraw_sweep()
        self.error("Sweep aborted (what was measured is saved):\n\n" + tb)

    # ---------------------------------------------------------------- quick I-V
    def build_iv_tab(self, lay):
        ivc = self.config.get("iv", {}) or {}

        def sp(v, lo, hi, dec, scale, unit, step=1.0):
            w = SISpinBox(scale, unit)
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setSingleStep(step)
            w.setValue(v)
            return w
        g = QtWidgets.QGroupBox("Sweep: cycles of 0 → max → min → 0")
        f = QtWidgets.QFormLayout(g)
        lim = float(getattr(self.hw.bias, "max_abs_V", 5.0))
        self.iv_max = sp(float(ivc.get("v_max", 1.0)), 0, lim, 3, 1.0, "V", 0.1)
        self.iv_min = sp(float(ivc.get("v_min", -1.0)), -lim, 0, 3, 1.0, "V", 0.1)
        self.iv_step = sp(float(ivc.get("step_mV", 10)), 1, 5000, 0, 1e-3, "mV", 1)
        self.iv_step.setToolTip("Plain numbers are mV; the SR570 sets the bias in 1 mV "
                                "steps, so values are rounded to 1 mV.")
        self.iv_cycles = QtWidgets.QSpinBox()
        self.iv_cycles.setRange(1, 1000)
        self.iv_cycles.setValue(int(ivc.get("cycles", 1)))
        self.iv_wait = sp(float(ivc.get("wait_ms", 100)), 0, 1e5, 0, 1e-3, "ms", 10)
        self.iv_wait.setToolTip("Wait after each bias step before reading")
        self.iv_navg = QtWidgets.QSpinBox()
        self.iv_navg.setRange(1, 1000)
        self.iv_navg.setValue(int(ivc.get("n_avg", 5)))
        f.addRow("Max (V)", self.iv_max)
        f.addRow("Min (V)", self.iv_min)
        f.addRow("Step (mV)", self.iv_step)
        f.addRow("Cycles", self.iv_cycles)
        f.addRow("Wait per point (ms)", self.iv_wait)
        f.addRow("Readings per point", self.iv_navg)
        self.iv_eta = QtWidgets.QLabel("-")
        f.addRow("Est. time", self.iv_eta)
        lay.addWidget(g)
        g = QtWidgets.QGroupBox("SR570 (DC)")
        f = QtWidgets.QFormLayout(g)
        self.iv_sens = QtWidgets.QComboBox()
        for i in range(self.amp_sens.count()):
            self.iv_sens.addItem(self.amp_sens.itemText(i), self.amp_sens.itemData(i))
        i = self.iv_sens.findData(float(ivc.get("sensitivity", 1e-6)))
        self.iv_sens.setCurrentIndex(i if i >= 0 else self.iv_sens.findData(1e-6))
        self.iv_sens.setToolTip("Set this to the SR570 front-panel sensitivity. The SR570 "
                                "can't be read, so the current is calculated with this value.")
        f.addRow("Sensitivity", self.iv_sens)
        lay.addWidget(g)
        self.iv_note = QtWidgets.QLabel()
        self.iv_note.setWordWrap(True)
        self.iv_note.setStyleSheet("color:#c62828")
        lay.addWidget(self.iv_note)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._help_btn(
            "Quick I–V",
            "• DC current vs the SR570 bias. The SR570 output goes to AUX IN 1 on the rear "
            "of the photocurrent SR830 (instead of the lock-in input).\n"
            "• You'll be reminded to set the SR570 filter OFF and LOW DRIFT and to move the "
            "cable; before the next map, to change them back.\n"
            "• Current = AUX IN voltage × the sensitivity selected here.\n"
            "• The bias ramps back to 0 V at the end, on Stop and on errors. The sweep stops "
            "if the SR570 output goes above 10 V. The curve appears in the Plot tab."))
        lay.addLayout(row)
        lay.addStretch(1)
        for w in (self.iv_max, self.iv_min, self.iv_step, self.iv_wait):
            w.valueChanged.connect(self.update_iv_eta)
        for w in (self.iv_cycles, self.iv_navg):
            w.valueChanged.connect(self.update_iv_eta)
        ok = not isinstance(self.hw.bias, instruments.NoBias)
        self.iv_note.setText("" if ok else "Quick I–V needs the SR570 as bias source "
                             "(Hardware → Settings → Bias).")
        self.iv_note.setVisible(not ok)
        self.update_iv_eta()

    def _iv_settings(self):
        import iv
        if isinstance(self.hw.bias, instruments.NoBias):
            raise ValueError("Quick I–V needs the SR570 as bias source "
                             "(Hardware → Settings → Bias).")
        return iv.IVSettings(
            v_max=self.iv_max.value(), v_min=self.iv_min.value(),
            step=self.iv_step.value() / 1000, cycles=self.iv_cycles.value(),
            wait_ms=self.iv_wait.value(), n_avg=self.iv_navg.value(),
            sensitivity=float(self.iv_sens.currentData()),
            aux_in=int((self.config.get("iv", {}) or {}).get("aux_in", 1)),
            sample=self.sample.text() or "sample", notes=self.exp_notes.toPlainText(),
            conditions=self.conditions())

    def update_iv_eta(self, *_):
        import iv
        try:
            n = len(iv.bias_points(iv.IVSettings(
                v_max=self.iv_max.value(), v_min=self.iv_min.value(),
                step=self.iv_step.value() / 1000, cycles=self.iv_cycles.value())))
        except Exception:
            self.iv_eta.setText("-")
            return
        per = self.iv_wait.value() / 1000 + self.iv_navg.value() * 0.012 + 0.06
        self.iv_eta.setText(f"{n} points ≈ {n * per / 60:.1f} min")

    def _switch_reminder(self, kind):
        """Remind to re-cable / re-set the SR570 when switching between lock-in
        maps and the DC I-V. Returns False if the user cancels."""
        last = (self.config.get("ui", {}) or {}).get("last_measurement", "map")
        if kind == last:
            return True
        if kind == "iv":
            sens = self.iv_sens.currentText()
            text = ("Switching to a DC I–V. Please check:\n\n"
                    "•  SR570 filter: OFF\n"
                    "•  SR570 gain mode: LOW DRIFT\n"
                    "•  SR570 output cable: moved to AUX IN 1 (rear of the photocurrent SR830)\n"
                    f"•  SR570 sensitivity on the front panel: {sens}\n\n"
                    "Ready?")
        else:
            text = ("Switching back to lock-in maps. Please check:\n\n"
                    "•  SR570 output cable: back to the lock-in signal input\n"
                    "•  SR570 filter and gain mode: back to your lock-in settings\n"
                    "•  SR570 sensitivity matches 'Photocurrent amplifier' (Instruments tab)\n\n"
                    "Ready?")
        ans = QtWidgets.QMessageBox.question(self, "SPCM", text)
        if ans != QtWidgets.QMessageBox.StandardButton.Yes:
            return False
        self.config.setdefault("ui", {})["last_measurement"] = kind
        self._save_setting("ui", "last_measurement", kind)
        return True

    def start_iv(self):
        import iv
        try:
            s = self._iv_settings()
            iv.bias_points(s)
        except Exception as e:
            self.error(str(e))
            return
        if not self._switch_reminder("iv"):
            return
        for k, v in (("v_max", s.v_max), ("v_min", s.v_min), ("step_mV", s.step * 1000),
                     ("cycles", s.cycles), ("wait_ms", s.wait_ms), ("n_avg", s.n_avg),
                     ("sensitivity", s.sensitivity)):
            self._save_setting("iv", k, v)
        self.remember_experiment()
        self.worker = iv.IVWorker(self.hw, s, self.folder.text() or "data",
                                  name=self.measurement_name("iv", s))
        self.iv_data = self.worker.data
        self.show_plot(self.iv_glw)
        self._redraw_iv()
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.point.connect(self._redraw_iv)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_iv_finished)
        self.worker.failed.connect(self.on_iv_failed)
        self.worker.notify.connect(self.toast)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        self._discard = False
        self.thread.start()

    def _redraw_iv(self, *_):
        d = getattr(self, "iv_data", None)
        p = self.iv_plot
        p.clear()
        self.iv_legend.clear()
        if d is None or not d.rows:
            return
        a = d.array()
        cycles = sorted(set(a[:, 2].astype(int)))
        cm = pg.colormap.get("viridis")
        cols = cm.map(np.linspace(0, 0.85, max(len(cycles), 1)), mode="qcolor")
        for c, col in zip(cycles, cols):
            sel = a[:, 2] == c
            p.plot(a[sel, 0], a[sel, 1], pen=pg.mkPen(col, width=1.5), symbol="o",
                   symbolSize=4, symbolPen=None, symbolBrush=col,
                   name=f"cycle {c}" if len(cycles) > 1 else None)
        last = a[-1]
        p.setTitle(f"{last[0]:+.3f} V   {si(last[1], 'A')}")

    def on_iv_finished(self, path):
        done = self.iv_data.completed
        self._cleanup()
        if getattr(self, "_discard", False):
            self._delete_scan_files(path)
            self.statusBar().showMessage("I–V stopped and discarded (nothing saved)")
            return
        try:
            from scanner import update_experiment
            update_experiment(path, self.conditions(), self.exp_notes.toPlainText())
        except Exception:
            pass
        try:
            import iv
            self.iv_data = iv.load_iv(path)
            iv.export_csv(self.iv_data)
            iv.save_all(self.iv_data)
            self._redraw_iv()
        except Exception as e:
            self.statusBar().showMessage(f"CSV/figure export failed: {e}")
            return
        self.statusBar().showMessage(("I–V finished" if done else "I–V stopped")
                                     + f" - saved {os.path.abspath(path)} + PNG and CSV")

    def on_iv_failed(self, tb):
        self._cleanup()
        if getattr(self, "_discard", False):
            self._delete_scan_files(self.iv_data.path)
            return
        self._redraw_iv()
        self.error("I–V aborted (bias ramped to 0 V, what was measured is saved):\n\n" + tb)

    # ---------------------------------------------------------------- resolution
    def toggle_resolution(self, on):
        if on and self.data is None:
            self.res_btn.setChecked(False)
            self.error("Open or measure a map first.")
            return
        if on and not self.res_rois:
            u, v, _, _ = self._view_axes(self.data)
            a = (u[0] + (u[-1] - u[0]) * 0.3, v[0] + (v[-1] - v[0]) * 0.5)
            b = (u[0] + (u[-1] - u[0]) * 0.7, v[0] + (v[-1] - v[0]) * 0.5)
            step = abs(u[1] - u[0]) if len(u) > 1 else 0.5
            roi = pg.LineROI(a, b, width=step * 2, pen=pg.mkPen("#ff9800", width=2),
                             hoverPen=pg.mkPen("#ffeb3b", width=2))
            self.r_panel.plot.addItem(roi)
            roi.sigRegionChangeFinished.connect(lambda *_: self.update_resolution())
            self.res_rois = [roi]
        for r in self.res_rois:
            r.setVisible(on)
        if on:
            if self.res_dialog is None:
                self.res_dialog = ResolutionDialog(self)
            self.res_dialog.show()
            self.res_dialog.raise_()
            self.update_resolution()
        elif self.res_dialog is not None:
            self.res_dialog.hide()

    def _res_line(self):
        r = self.res_rois[0]
        w = r.size()[1]
        p0 = r.mapToParent(QtCore.QPointF(0, w / 2))
        p1 = r.mapToParent(QtCore.QPointF(r.size()[0], w / 2))
        return (p0.x(), p0.y()), (p1.x(), p1.y()), abs(w)

    def update_resolution(self):
        if not self.res_rois or self.res_dialog is None or self.data is None:
            return
        import resolution
        d = self.data
        u, v, oix, oiy = self._view_axes(d)
        ib = max(self.map_sel.currentIndex(), 0)
        ch = min(self.ch_r.currentIndex(), 2)
        img = d.refl[ib, :, :, ch][np.ix_(oiy, oix)]
        p0, p1, width = self._res_line()
        try:
            m = resolution.measure(img, u, v, p0, p1, width)
        except Exception as e:
            self.res_dialog.show_error(str(e))
            return
        m["map_step_um"] = float(min(abs(u[1] - u[0]), abs(v[1] - v[0]))) \
            if len(u) > 1 and len(v) > 1 else float("nan")
        m["width_um"] = width
        m["channel"] = CHANNELS[ch]
        m["map"] = getattr(d, "path", "") or "(not saved)"
        self.res_dialog.show_result(m)

    # ----------------------------------------------------------------
    def start(self):
        if getattr(self, "af_thread", None) is not None:
            self.error("Wait for the autofocus to finish.")
            return
        if self.side_tabs.currentIndex() == 1:
            self.start_sweep()
            return
        if self.side_tabs.currentIndex() == 2:
            self.start_iv()
            return
        try:
            s = self.settings()
        except Exception as e:
            self.error(str(e))
            return
        if not self._switch_reminder("map"):
            return
        # sanity check: is the scan where the stage is now?
        try:
            px, py = self.hw.stage.position()
            sx, sy = s.start_position()
            dist = float(np.hypot(sx - px, sy - py))
            if dist > 500:
                ans = QtWidgets.QMessageBox.question(
                    self, "SPCM",
                    f"The scan starts {dist / 1000:.2f} mm away from where the stage is "
                    f"now.\n\nStage now: x = {px:.1f}, y = {py:.1f} µm\n"
                    f"Scan start: x = {sx:.1f}, y = {sy:.1f} µm\n\n"
                    "Move there and scan?")
                if ans != QtWidgets.QMessageBox.StandardButton.Yes:
                    return
        except Exception as e:
            self.error(f"Could not read the stage position:\n{e}")
            return
        self.refresh_lockins()
        self.remember_experiment()
        self.remember_scan(s)
        self.worker = ScanWorker(self.hw, s, self.folder.text() or "data",
                                 self.config.get("power_lock", {}) or {},
                                 name=self.measurement_name("map", s))
        self.data = self.worker.data
        self._setup_views()
        for p in (self.pc_panel, self.r_panel):
            p.follow.setChecked(True)          # line cut follows the new scan
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.pixel.connect(self.on_pixel)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.status.connect(self._learn_overhead)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.notify.connect(self.toast)
        self._last_draw = 0
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.discard_btn.setEnabled(True)
        self._discard = False
        self.thread.start()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.statusBar().showMessage("Stopping after current pixel…")

    def toast(self, text, level="info"):
        """Small notification in the top-right corner that fades after a few
        seconds (doesn't block the scan). Also a Windows notification when the
        SPCM window isn't in front."""
        if not hasattr(self, "_toast"):
            self._toast = QtWidgets.QLabel(self)
            self._toast.setWordWrap(True)
            self._toast.setMaximumWidth(420)
            self._toast_timer = QtCore.QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._toast.hide)
        bg = "#2e7d32" if level == "info" else "#c62828"
        self._toast.setStyleSheet(f"background:{bg}; color:white; padding:10px 14px; "
                                  "border-radius:8px; font-weight:bold;")
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move(self.width() - self._toast.width() - 24, 60)
        self._toast.raise_()
        self._toast.show()
        self._toast_timer.start(5000 if level == "info" else 10000)
        self.statusBar().showMessage(text, 10000)
        if not self.isActiveWindow():
            try:
                if not hasattr(self, "_tray"):
                    self._tray = QtWidgets.QSystemTrayIcon(self.windowIcon(), self)
                    self._tray.setVisible(True)
                self._tray.showMessage("SPCM", text)
            except Exception:
                pass

    def stop_discard(self):
        if not self.worker:
            return
        if QtWidgets.QMessageBox.question(
                self, "SPCM", "Stop and delete this scan?\nNothing of it will be saved.") \
                != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if not self.worker:      # it finished (and was saved) while the question was open
            self.statusBar().showMessage("It had already finished and is saved.")
            return
        self._discard = True
        self.worker.stop()
        self.statusBar().showMessage("Stopping, the scan will be discarded…")

    # -- small helpers for a calm window --
    def _help_btn(self, title, text):
        """A small '?' button: the explanation as tooltip, and in a window on click."""
        b = QtWidgets.QToolButton()
        b.setText("?")
        b.setToolTip(text)
        b.setAutoRaise(True)
        b.setStyleSheet("font-weight:bold")
        b.clicked.connect(lambda: QtWidgets.QMessageBox.information(self, title, text))
        return b

    def _draw_menu(self, items):
        """'Draw ▾' button whose menu switches the given checkable buttons on/off."""
        tb = QtWidgets.QToolButton()
        tb.setText("Draw ▾")
        tb.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(tb)
        for label, btn in items:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.toggled.connect(btn.setChecked)
            btn.toggled.connect(lambda on, a=act: a.isChecked() != on and a.setChecked(on))
        tb.setMenu(menu)
        return tb

    def show_plot(self, w):
        """Show a line scan / point sweep / I-V plot in the Plot tab."""
        self.plot_stack.setCurrentWidget(w)
        name = {self.iv_glw: "I–V", self.line_view: "line scan",
                self.sweep_view: "point sweep"}.get(w, "")
        i = self.tabs.indexOf(self.plot_stack)
        self.tabs.setTabText(i, f"Plot: {name}" if name else "Plot")
        self.tabs.setCurrentWidget(self.plot_stack)

    # -- measurement names --
    def _fill_name_menu(self):
        import naming
        m = self.name_menu
        m.clear()
        for key, label in naming.FIELDS:
            m.addAction(f"{label}   {{{key}}}", lambda k=key: self._insert_field(k))
        exp = [k for k, _ in self.experiment_fields()]
        if exp:
            m.addSection("From the Experiment notes")
            for k in exp:
                m.addAction(f"{k}   {{{k}}}", lambda k=k: self._insert_field(k))

    def _insert_field(self, key):
        e = self.name_pat.lineEdit()
        t, pos = e.text(), e.cursorPosition()
        ins = "{" + key + "}"
        if pos > 0 and t[pos - 1] not in "_-":
            ins = "_" + ins
        e.setText(t[:pos] + ins + t[pos:])
        e.setCursorPosition(pos + len(ins))

    def _name_values(self, kind, s=None):
        """Values for the {fields} of the file name, for a map ('map'), a line
        scan ('line') or a point sweep ('power', 'bias', 'bias_power')."""
        import naming
        v = {"sample": self.sample.text() or "sample",
             "date": time.strftime("%Y%m%d"), "time": time.strftime("%H%M"),
             "type": {"map": "map", "line": "line"}.get(kind, kind.replace("_", "") + "sweep")}
        v.update({k: val for k, val in self.experiment_fields()})
        if kind == "iv":
            v["type"] = "IV"
            if s is not None:
                v["bias"] = naming.range_text([s.v_min, s.v_max], "V")
            v["power"] = self._power_now_text()
            return v
        try:
            s = s or (self.settings() if kind in ("map", "line") else None)
        except Exception:
            s = None
        if kind == "map" and s is not None:
            v["size"] = f"{naming.num(s.x_size, '')}x{naming.num(s.y_size, 'um')}"
            v["step"] = naming.num(s.step * 1000, "nm")
            v["mode"] = s.mode
        elif kind == "line" and s is not None:
            v["size"] = naming.num(s.x_size, "um")
            v["step"] = naming.num(s.step * 1000, "nm")
            v["mode"] = "step"
        if s is not None:
            v["bias"] = naming.range_text([x for x in s.biases if x is not None], "V")
        if kind in ("power", "bias_power") and s is not None:
            v["power"] = naming.range_text(list(s.powers_uW), "uW")
        elif kind == "bias":
            v["power"] = self._power_now_text()
        elif s is not None and getattr(s, "outer", "bias") == "power":   # power series
            v["power"] = naming.range_text(list(s.outer_values), "uW")
        else:              # maps and lines: the set power, or what the meter reads now
            tgt = getattr(s, "target_power_uW", None) if s is not None else None
            v["power"] = naming.num(tgt, "uW") if tgt else self._power_now_text()
        return v

    def _power_now_text(self):
        import naming
        p = getattr(self, "_last_power_W", None)
        if p is None or self.pm is None:
            return ""
        return naming.num(p / (self.pm.sample_factor or 1.0) * 1e6, "uW")

    def measurement_name(self, kind, s=None):
        import naming
        pat = self.name_pat.currentText().strip() or naming.DEFAULT_PATTERN
        self._save_setting("scan_defaults", "file_pattern", pat)
        return naming.make_name(naming.pattern_for(pat, kind), self._name_values(kind, s),
                                self.folder.text() or "data")

    def _name_changed(self, *_):
        try:
            import naming
            pat = self.name_pat.currentText().strip() or naming.DEFAULT_PATTERN
            name = naming.make_name(pat, self._name_values("map"), self.folder.text() or "data")
            self.name_preview.setText(f"next map: {name}")
        except Exception:
            self.name_preview.setText("")

    def _delete_scan_files(self, path):
        import glob
        import shutil
        base = os.path.splitext(path)[0]
        folder = os.path.dirname(os.path.abspath(path))
        if os.path.basename(folder) == os.path.basename(base):   # its own folder
            shutil.rmtree(folder, ignore_errors=True)
            return
        for f in [path, base + ".png"] + glob.glob(base + "_map*.png"):
            try:
                os.remove(f)
            except OSError:
                pass
        shutil.rmtree(base + "_csv", ignore_errors=True)

    def _setup_views(self):
        d = self.data
        for r in self.rois + self.res_rois + self.line_rois:
            r.scene() and r.scene().removeItem(r)
        self.rois, self.res_rois, self.line_rois = [], [], []
        if self.line_btn.isChecked():
            self.line_btn.setChecked(False)
        if self.res_btn.isChecked():
            self.res_btn.setChecked(False)
        if self.frame_btn.isChecked():
            self.frame_btn.setChecked(False)
        u, v, _, _ = self._view_axes(d)
        for p in (self.pc_panel, self.r_panel):
            p.set_axes(u, v)
        self.map_sel.blockSignals(True)
        self.map_sel.clear()
        from scanner import outer_text
        for b in d.settings.outer_list():
            self.map_sel.addItem("single" if b is None else outer_text(d.settings, b))
        self.map_sel.blockSignals(False)
        self.redraw()

    def on_pixel(self, ib, iy, ix, pc, rf):
        if self.map_sel.currentIndex() != ib:
            self.map_sel.blockSignals(True)
            self.map_sel.setCurrentIndex(ib)
            self.map_sel.blockSignals(False)
        ox, oy, sx, sy = self._frame(self.data)
        pos = (sx * (self.data.xs[ix] - ox), sy * (self.data.ys[iy] - oy))
        for p in (self.pc_panel, self.r_panel):
            p.follow_row(pos[1])
        self._pending_pos = pos
        now = time.time()
        if now - self._last_draw > 0.1:  # redraw at most 10x per second
            self._last_draw = now
            self.redraw(pos=pos)
        elif not getattr(self, "_redraw_queued", False):
            # make sure the latest pixel is drawn shortly, even if no new one comes
            self._redraw_queued = True
            QtCore.QTimer.singleShot(120, self._flush_redraw)

    def _flush_redraw(self):
        self._redraw_queued = False
        self._last_draw = time.time()
        self.redraw(pos=getattr(self, "_pending_pos", None))

    def redraw(self, *_, pos=None):
        d = self.data
        if d is None:
            return
        ib = max(self.map_sel.currentIndex(), 0)
        from scanner import pc_scale
        ch = self.ch_pc.currentIndex()
        fac, unit, _ = pc_scale(d.settings)
        unit = unit or getattr(d.settings, "lockin_unit", "V")
        fac = fac if ch < 3 else 1.0          # theta is not scaled
        unit = unit if ch < 3 else "°"
        from scanner import signal_kind
        self.pc_panel.plot.setTitle(f"{signal_kind(d.settings)} {CHANNELS[ch]}" +
                                    (f"   (lock-in × {fac:.4g})" if fac != 1 else ""))
        self.pc_panel.set_unit(unit, CHANNELS[ch])
        _, _, oix, oiy = self._view_axes(d)
        view = np.ix_(oiy, oix)
        self.pc_panel.show_map((d.pc[ib, :, :, ch] * fac)[view], pos)
        rch = self.ch_r.currentIndex()
        self.r_panel.set_unit("V" if rch < 3 else "°", CHANNELS[rch])
        self.r_panel.show_map(d.refl[ib, :, :, self.ch_r.currentIndex()][view], pos)

    def reset_levels(self):
        for p in (self.pc_panel, self.r_panel):
            p.autoscale = True
        self.redraw()

    def on_progress(self, done, total, eta):
        self.prog.setMaximum(total)
        self.prog.setValue(done)
        self.prog.setFormat(f"%p%   {eta / 60:.1f} min left")

    def _cleanup(self):
        self.thread.quit()
        self.thread.wait()
        self.thread = self.worker = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)
        self.redraw()

    def _nothing_saved(self, path):
        """The run stopped before any data was taken (e.g. power setting aborted)."""
        if path and os.path.exists(path):
            return False
        self._cleanup()
        self.statusBar().showMessage("Stopped before the measurement started - "
                                     "nothing was saved.")
        return True

    def on_finished(self, path):
        if self._nothing_saved(path):
            return
        done = self.data.completed
        if getattr(self, "_discard", False):
            self._cleanup()
            self._delete_scan_files(path)
            self.statusBar().showMessage("Scan stopped and discarded (nothing saved)")
            return
        try:   # include notes typed while the scan was running
            from scanner import update_experiment
            update_experiment(path, self.conditions(), self.exp_notes.toPlainText())
        except Exception as e:
            self.statusBar().showMessage(f"Could not update notes: {e}")
        self.remember_experiment()
        self._cleanup()
        csv_dir = ""
        try:
            from scanner import export_csv, load
            saved = load(path)
            csv_dir = export_csv(saved)
            from figures import save_all
            save_all(saved)
        except Exception as e:
            self.statusBar().showMessage(f"CSV/figure export failed: {e}")
        self.statusBar().showMessage(("Finished" if done else "Stopped") +
                                     f" - saved {os.path.abspath(path)}"
                                     + (" + figure (PNG) and CSV files" if csv_dir else ""))

    def on_failed(self, tb):
        self._cleanup()
        if getattr(self, "_discard", False):
            self._delete_scan_files(self.data.path) if getattr(self.data, "path", None) else None
            self.statusBar().showMessage("Scan stopped and discarded (nothing saved)")
            return
        self.error("Scan aborted (partial data saved):\n\n" + tb)

    def save_figure(self):
        """Figure like the publication layout, using the channel and colour
        scale currently shown (Manual limits are kept; otherwise symmetric).
        On the Line scan / Point sweep / I-V tab: that plot's figure."""
        w = self.tabs.currentWidget()
        if w is self.plot_stack:
            w = self.plot_stack.currentWidget()
        other = {self.line_view: ("line_data", "linescan"),
                 self.sweep_view: ("sweep_data", "sweeps"),
                 self.iv_glw: ("iv_data", "iv")}.get(w)
        if other is not None:
            d = getattr(self, other[0], None)
            if d is None:
                self.error("Nothing to save yet.")
                return
            base = os.path.splitext(getattr(d, "path", "") or "figure")[0]
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save figure", base + ".png", "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg)")
            if path:
                try:
                    import importlib
                    mod = importlib.import_module(other[1])
                    if other[1] == "iv":
                        mod.save_figure(d, path)
                    else:
                        mod.save_figure(d, path, channel=min(self.ch_pc.currentIndex(), 2))
                    self.statusBar().showMessage(f"Figure saved: {path}")
                except Exception as e:
                    self.error(f"Could not save the figure:\n{e}")
            return
        if self.data is None:
            self.error("No scan to save yet.")
            return
        base = os.path.splitext(getattr(self.data, "path", "") or
                                os.path.join(self.folder.text(), "figure"))[0]
        ib = max(self.map_sel.currentIndex(), 0)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save figure", f"{base}_map{ib}.png",
            "PNG image (*.png);;PDF (*.pdf);;SVG (*.svg)")
        if not path:
            return
        ch = min(self.ch_pc.currentIndex(), 2)
        lim = self.pc_panel.manual if self.pc_panel.mode == "manual" else None
        rlim = self.r_panel.manual if self.r_panel.mode == "manual" else None
        try:
            from figures import save_figure
            save_figure(self.data, ib, path, channel=ch,
                        refl_channel=min(self.ch_r.currentIndex(), 2),
                        limits=lim, refl_limits=rlim)
            self.statusBar().showMessage(f"Figure saved: {path}")
        except Exception as e:
            self.error(f"Could not save the figure:\n{e}")

    def export_csv(self):
        if self.data is None or not getattr(self.data, "path", None):
            self.error("Open a saved scan first (File → Open saved scan…).")
            return
        try:
            from scanner import export_csv
            out = export_csv(self.data)
            self.statusBar().showMessage(f"CSV files written to {out}")
            self.open_path(out)
        except Exception as e:
            self.error(f"CSV export failed:\n{e}")

    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open scan", self.folder.text(), "HDF5 (*.h5)")
        if path:
            try:
                import iv
                if iv.is_iv_file(path):
                    self.iv_data = iv.load_iv(path)
                    self._redraw_iv()
                    self.show_plot(self.iv_glw)
                    self.statusBar().showMessage(f"Loaded {path}")
                    return
                from sweeps import is_sweep_file, load_sweep
                if is_sweep_file(path):
                    self.sweep_data = load_sweep(path)
                    self._redraw_sweep()
                    self.show_plot(self.sweep_view)
                    self.statusBar().showMessage(f"Loaded {path}")
                    return
                d = load(path)
                if getattr(d.settings, "scan_type", "map") == "line":
                    self.line_data = d
                    self._redraw_line()
                    self.show_plot(self.line_view)
                    self.statusBar().showMessage(f"Loaded {path}")
                    return
                self.data = d
                self._setup_views()
                self.statusBar().showMessage(f"Loaded {path}")
            except Exception as e:
                self.error(f"Could not open file:\n{e}")

    def error(self, msg):
        QtWidgets.QMessageBox.critical(self, "SPCM", msg)

    def closeEvent(self, ev):
        if getattr(self, "af_thread", None) is not None:
            self.af_worker.stop()
            self.af_thread.quit()
            self.af_thread.wait(10000)
        if self.thread is not None:
            if QtWidgets.QMessageBox.question(
                    self, "SPCM", "A scan is running. Stop it and quit?") != \
                    QtWidgets.QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.worker.stop()
            self.thread.quit()
            self.thread.wait(5000)
        self.remember_experiment()
        for t in self.findChildren(QtCore.QTimer):
            t.stop()
        self._stop_poller()
        if not self._hw_closed:
            self.hw.close()
            self._hw_closed = True
        ev.accept()


class ResolutionDialog(QtWidgets.QDialog):
    """Profile + edge fit and derivative + Gaussian for 'Measure resolution'."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("SPCM – resolution from a reflection edge")
        self.resize(560, 640)
        lay = QtWidgets.QVBoxLayout(self)
        self.result = QtWidgets.QLabel("Move the orange line across a sharp edge.")
        self.result.setWordWrap(True)
        self.result.setStyleSheet("font-size:12pt")
        lay.addWidget(self.result)
        glw = pg.GraphicsLayoutWidget()
        lay.addWidget(glw, 1)
        self.p1 = glw.addPlot(row=0, col=0)
        self.p2 = glw.addPlot(row=1, col=0)
        self.p2.setXLink(self.p1)
        self.p1.setLabel("left", "Reflection", units="V")
        self.p2.setLabel("left", "dR/dx", units="V/µm")
        self.p2.setLabel("bottom", "Distance along the line (µm)")
        for p in (self.p1, self.p2):
            p.getAxis("bottom").enableAutoSIPrefix(False)
            p.showGrid(x=True, y=True, alpha=0.25)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Objective NA"))
        self.na = SISpinBox(1.0)
        self.na.setRange(0.05, 1.6)
        self.na.setDecimals(2)
        self.na.setSingleStep(0.05)
        self.na.setValue(float((win.config.get("ui", {}) or {}).get("objective_na", 0.9)))
        self.na.valueChanged.connect(self._na_changed)
        row.addWidget(self.na)
        row.addStretch(1)
        self.save_btn = QtWidgets.QPushButton("Save (PNG, CSV, TXT) next to the map")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        self.m = None
        from pyqtgraph.Qt import QtGui
        dark = bool((win.config.get("ui", {}) or {}).get("dark", False))
        style_plots(glw, dark)

    def _na_changed(self, v):
        self.win._save_setting("ui", "objective_na", float(v))
        if self.m is not None:
            self.show_result(self.m)

    def _wavelength_nm(self):
        pm = getattr(self.win, "pm", None)
        if pm is not None and getattr(pm, "wavelength_nm", None):
            return float(pm.wavelength_nm)
        return float((self.win.config.get("powermeter", {}) or {}).get("wavelength_nm", 532))

    def show_error(self, text):
        self.m = None
        self.result.setText(f"<span style='color:#c62828'>{text}</span>")
        self.p1.clear()
        self.p2.clear()

    def show_result(self, m):
        import resolution
        self.m = m
        fit, x, y = m["fit"], m["distance"], m["values"]
        lam = self._wavelength_nm()
        dl = 0.51 * lam / self.na.value()
        lines = [f"<b>FWHM = {fit['fwhm'] * 1000:.0f} nm</b>   (10–90 %: "
                 f"{fit['w1090'] * 1000:.0f} nm)",
                 f"Diffraction limit 0.51 λ/NA = {dl:.0f} nm  (λ = {lam:g} nm, NA = "
                 f"{self.na.value():.2f})"]
        if m.get("realigned"):
            lines.append(f"The line crossed the edge {m['angle_deg']:.0f}° off perpendicular; "
                         "measured along the edge's normal instead.")
        step = m.get("map_step_um", float("nan"))
        if np.isfinite(step) and step > fit["fwhm"] / 3:
            lines.append(f"<span style='color:#c62828'>The map step ({step * 1000:.0f} nm) is "
                         "coarse for this spot size: the true FWHM may be smaller. Scan the "
                         f"edge with a step of ≤ {fit['fwhm'] * 250:.0f} nm.</span>")
        L = float(np.nanmax(x) - np.nanmin(x))
        if fit["w1090"] > 0.4 * L:
            lines.append("<span style='color:#c62828'>The line is short compared with the "
                         "edge: make it longer so it reaches the flat parts on both "
                         "sides.</span>")
        if fit.get("contrast_to_noise", 99) < 10:
            lines.append("<span style='color:#c62828'>Weak edge or noisy profile: the fit "
                         "is uncertain.</span>")
        self.result.setText("<br>".join(lines))
        self.p1.clear()
        self.p2.clear()
        xx = np.linspace(np.nanmin(x), np.nanmax(x), 300)
        self.p1.plot(x, y, pen=None, symbol="o", symbolSize=4, symbolBrush="#888",
                     symbolPen=None)
        self.p1.plot(xx, resolution.model(xx, fit), pen=pg.mkPen("#e53935", width=2))
        self.p2.plot(x, resolution.derivative(x, y), pen=None, symbol="o", symbolSize=4,
                     symbolBrush="#888", symbolPen=None)
        self.p2.plot(xx, resolution.gaussian(xx, fit), pen=pg.mkPen("#1e88e5", width=2))

    def save(self):
        import resolution
        m = self.m
        d = self.win.data
        path = getattr(d, "path", None) if d is not None else None
        if m is None or not path:
            self.win.error("Nothing to save, or the map is not saved yet.")
            return
        folder = os.path.dirname(os.path.abspath(path))
        base = resolution.next_base(folder, os.path.splitext(os.path.basename(path))[0])
        lam = self._wavelength_nm()
        info = {"Map file": os.path.basename(path), "Reflection channel": m["channel"],
                "Line from (µm)": f"({m['p0'][0]:.3f}, {m['p0'][1]:.3f})",
                "Line to (µm)": f"({m['p1'][0]:.3f}, {m['p1'][1]:.3f})",
                "Band width averaged (µm)": f"{m['width_um']:.3f}",
                "Re-measured perpendicular to the edge": m.get("realigned", False),
                "Map step (nm)": f"{m.get('map_step_um', float('nan')) * 1000:.0f}",
                "Wavelength (nm)": f"{lam:g}", "Objective NA": f"{self.na.value():.2f}",
                "Diffraction limit 0.51 λ/NA (nm)": f"{0.51 * lam / self.na.value():.0f}"}
        try:
            files = resolution.save(base, m["distance"], m["values"], m["fit"], info)
            self.win.statusBar().showMessage("Resolution saved: " + ", ".join(
                os.path.basename(f) for f in files))
        except Exception as e:
            self.win.error(f"Could not save:\n{e}")
