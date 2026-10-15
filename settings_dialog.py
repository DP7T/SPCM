"""Hardware settings window: choose ports from drop-down lists, saved to config.yaml."""
import time

import yaml
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets

from numparse import make_spinbox_class

SISpinBox = make_spinbox_class(QtWidgets, QtGui, QtCore)


def detect_devices():
    """-> (visa_resources, [(port, description)])"""
    visa, serial_ports = [], []
    try:
        import pyvisa
        visa = list(pyvisa.ResourceManager().list_resources())
    except Exception:
        pass
    try:
        from serial.tools import list_ports
        serial_ports = [(p.device, p.description) for p in list_ports.comports()]
    except Exception:
        pass
    return visa, serial_ports


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, config, path, parent=None):
        super().__init__(parent)
        self.config, self.path = config, path
        self.setWindowTitle("SPCM – hardware settings")
        self.setMinimumWidth(560)
        lay = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.found = QtWidgets.QLabel()
        self.found.setStyleSheet("color:gray")
        top.addWidget(self.found, 1)
        rescan = QtWidgets.QPushButton("Search again")
        rescan.clicked.connect(self.refresh)
        top.addWidget(rescan)
        lay.addLayout(top)

        c = config
        st, pc = c.get("stage", {}) or {}, c.get("lockin_photocurrent", {}) or {}
        rf, b = c.get("lockin_reflection", {}) or {}, c.get("bias", {}) or {}
        pm, cam = c.get("powermeter", {}) or {}, c.get("camera", {}) or {}

        def combo(current):
            w = QtWidgets.QComboBox()
            w.setEditable(True)
            w.setMinimumWidth(320)
            w.setProperty("initial", str(current or ""))
            return w

        def dspin(v, lo, hi, dec=1, scale=1.0, unit_name=""):
            w = SISpinBox(scale, unit_name)
            w.setRange(lo, hi)
            w.setDecimals(dec)
            w.setValue(float(v))
            return w

        # stage + lock-ins
        g = QtWidgets.QGroupBox("Stage and lock-ins")
        f = QtWidgets.QFormLayout(g)
        self.stage_port = combo(st.get("port"))
        self.pc_res = combo(pc.get("resource"))
        self.rf_res = combo(rf.get("resource"))
        f.addRow("Tango stage (COM port)", self.stage_port)
        self.inv_x = QtWidgets.QCheckBox("Invert x")
        self.inv_y = QtWidgets.QCheckBox("Invert y")
        self.inv_x.setChecked(bool(st.get("invert_x", True)))
        self.inv_y.setChecked(bool(st.get("invert_y", True)))
        inv = QtWidgets.QHBoxLayout()
        inv.addWidget(self.inv_x)
        inv.addWidget(self.inv_y)
        inv.addStretch(1)
        f.addRow("Program axes vs stage", inv)
        f.addRow("SR830 photocurrent", self.pc_res)
        f.addRow("SR830 reflection", self.rf_res)
        lay.addWidget(g)

        # bias
        g = QtWidgets.QGroupBox("Bias")
        f = QtWidgets.QFormLayout(g)
        self.bias_type = QtWidgets.QComboBox()
        self.bias_type.addItem("None (set by hand)", "none")
        self.bias_type.addItem("SR570 preamp (RS-232)", "sr570")
        self.bias_type.addItem("SR830 AUX OUT 1 (photocurrent lock-in)", "lockin_aux")
        i = self.bias_type.findData(b.get("type", "none"))
        self.bias_type.setCurrentIndex(max(i, 0))
        self.sr570_port = combo(b.get("port"))
        self.max_v = dspin(b.get("max_abs_V", 2.0), 0.1, 10.5, 2, 1.0, "V")
        f.addRow("Source", self.bias_type)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.sr570_port, 1)
        test = QtWidgets.QPushButton("Test SR570")
        test.setToolTip("Sets +0.5 V, then −0.5 V, then off. Watch the SR570 "
                        "front panel. Disconnect your device first!")
        test.clicked.connect(self.test_sr570)
        row.addWidget(test)
        f.addRow("SR570 COM port", row)
        f.addRow("Safety limit (± V)", self.max_v)
        lay.addWidget(g)

        # accessories
        g = QtWidgets.QGroupBox("Power meter and camera")
        f = QtWidgets.QFormLayout(g)
        self.pm_on = QtWidgets.QCheckBox("Use PM100USB power meter")
        self.pm_on.setChecked(bool(pm.get("enabled", False)))
        self.pm_wl = dspin(pm.get("wavelength_nm", 532), 200, 2000, 1, 1e-9, "nm")
        self.cam_on = QtWidgets.QCheckBox("Use CS165CU camera (close ThorCam first)")
        self.cam_on.setChecked(bool(cam.get("enabled", False)))
        self.cam_exp = dspin(cam.get("exposure_ms", 20), 0.05, 5000, 2, 1e-3, "ms")
        f.addRow(self.pm_on)
        f.addRow("Laser wavelength (nm)", self.pm_wl)
        lc = c.get("power_lock", {}) or {}
        self.lock_li = QtWidgets.QComboBox()
        self.lock_li.addItem("Photocurrent SR830", "photocurrent")
        self.lock_li.addItem("Reflection SR830", "reflection")
        self.lock_li.setCurrentIndex(max(self.lock_li.findData(lc.get("lockin", "photocurrent")), 0))
        self.lock_max = dspin(lc.get("max_amplitude_V", 5.0), 0.004, 5.0, 3, 1.0, "V")
        f.addRow("Laser driven by SINE OUT of", self.lock_li)
        f.addRow("Never set SINE OUT above (V)", self.lock_max)
        f.addRow(self.cam_on)
        f.addRow("Camera exposure (ms)", self.cam_exp)
        lay.addWidget(g)

        # data
        g = QtWidgets.QGroupBox("Data")
        f = QtWidgets.QFormLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.save_dir = QtWidgets.QLineEdit(str(c.get("save_dir", "data")))
        self._save_dir0 = self.save_dir.text()
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.save_dir, 1)
        row.addWidget(browse)
        f.addRow("Save folder", row)
        lay.addWidget(g)

        btns = QtWidgets.QDialogButtonBox()
        self.save_btn = btns.addButton("Save and restart",
                                       QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self.bias_type.currentIndexChanged.connect(self._enable)
        self.pm_on.toggled.connect(self._enable)
        self.cam_on.toggled.connect(self._enable)
        self.refresh()
        self._enable()

    def _enable(self, *_):
        self.sr570_port.setEnabled(self.bias_type.currentData() == "sr570")
        self.max_v.setEnabled(self.bias_type.currentData() != "none")
        self.pm_wl.setEnabled(self.pm_on.isChecked())
        self.lock_li.setEnabled(self.pm_on.isChecked())
        self.lock_max.setEnabled(self.pm_on.isChecked())
        self.cam_exp.setEnabled(self.cam_on.isChecked())

    def refresh(self):
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
        try:
            visa, ports = detect_devices()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        gpib = [r for r in visa if r.upper().startswith(("GPIB", "ASRL"))] or visa
        for w in (self.pc_res, self.rf_res):
            self._fill(w, [(r, "") for r in gpib])
        for w in (self.stage_port, self.sr570_port):
            self._fill(w, ports)
        self.found.setText(f"Found {len(visa)} VISA instrument(s) and "
                           f"{len(ports)} COM port(s).")

    @staticmethod
    def _fill(w, items):
        current = w.currentData() or w.currentText() or w.property("initial")
        w.clear()
        for value, desc in items:
            w.addItem(f"{value}   {desc}".strip(), value)
        i = w.findData(current)
        if i < 0 and current:
            w.addItem(str(current), current)
            i = w.count() - 1
        w.setCurrentIndex(i)

    @staticmethod
    def _value(w):
        # use the stored value if the text still matches an item, else what was typed
        i = w.findText(w.currentText())
        if i >= 0 and w.itemData(i):
            return w.itemData(i)
        return w.currentText().split()[0] if w.currentText().strip() else ""

    def browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Save folder",
                                                       self.save_dir.text())
        if d:
            self.save_dir.setText(d)

    def test_sr570(self):
        port = self._value(self.sr570_port)
        if not port:
            return
        if QtWidgets.QMessageBox.question(
                self, "Test SR570",
                f"Send a test bias to the SR570 on {port}?\n\n"
                "Disconnect your device from the SR570 input first.\n"
                "The front panel should show +0.5 V, then −0.5 V, then bias off.") != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            import serial
            s = serial.Serial(port, 9600, bytesize=8, parity="N", stopbits=2, timeout=0.5)
            for cmd, wait in (("BSLV500", 0), ("BSON1", 2.5), ("BSLV-500", 2.5),
                              ("BSLV0", 0), ("BSON0", 0)):
                s.write((cmd + "\r\n").encode())
                s.flush()
                QtWidgets.QApplication.processEvents()
                time.sleep(wait)
            s.close()
            QtWidgets.QMessageBox.information(
                self, "Test SR570", "Sent. Did the SR570 bias change on its front panel?\n"
                "If not: check the cable (straight-through, not null-modem) and "
                "the USB adapter driver.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Test SR570", f"Could not open {port}:\n{e}")

    def save(self):
        """Write only what this window edits into config.yaml. Everything else in
        the file (scan defaults, camera flip, file-name pattern ... remembered by
        the main window during the session) is kept as it is on disk."""
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                disk = yaml.safe_load(f) or {}
        except FileNotFoundError:
            disk = {}
        # an unchanged folder stays as written in the file (e.g. "data", relative)
        if self.save_dir.text() == self._save_dir0 and "save_dir" in disk:
            save_dir = disk["save_dir"]
        else:
            save_dir = self.save_dir.text() or "data"
        for c in (self.config, disk):
            self._apply(c, save_dir)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# SPCM settings - edited from the Settings window\n")
            yaml.safe_dump(disk, f, sort_keys=False, allow_unicode=True)
        self.accept()

    def _apply(self, c, save_dir):
        c.setdefault("stage", {})["port"] = self._value(self.stage_port)
        c["stage"]["invert_x"] = self.inv_x.isChecked()
        c["stage"]["invert_y"] = self.inv_y.isChecked()
        c.setdefault("lockin_photocurrent", {})["resource"] = self._value(self.pc_res)
        c.setdefault("lockin_reflection", {})["resource"] = self._value(self.rf_res)
        b = c.setdefault("bias", {})
        b["type"] = self.bias_type.currentData()
        b["port"] = self._value(self.sr570_port)
        b["max_abs_V"] = round(self.max_v.value(), 2)
        b.setdefault("lockin", "photocurrent")
        b.setdefault("aux_channel", 1)
        pm = c.setdefault("powermeter", {})
        pm["enabled"] = self.pm_on.isChecked()
        pm["wavelength_nm"] = round(self.pm_wl.value(), 1)
        lc = c.setdefault("power_lock", {})
        lc["lockin"] = self.lock_li.currentData()
        lc["max_amplitude_V"] = round(self.lock_max.value(), 3)
        cam = c.setdefault("camera", {})
        cam["enabled"] = self.cam_on.isChecked()
        cam["exposure_ms"] = round(self.cam_exp.value(), 2)
        c["save_dir"] = save_dir
        c["simulate"] = False
