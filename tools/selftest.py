"""Self-test in simulation mode (no hardware needed).

    python tools/selftest.py [output folder]

Runs small map scans (step and fly), a quick I-V and the resolution fit on the
simulated microscope, saves and reloads every file, writes CSVs and
figures, and - if PyQt5 is installed - opens the main window off-screen and
saves screenshots of its tabs. Exits with an error if anything fails.
Used by the GitHub test workflow; also handy after changing the code.
"""
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)

import numpy as np   # noqa: E402
import yaml          # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "selftest_out")
os.makedirs(OUT, exist_ok=True)
FAILED = []


def check(name):
    def deco(fn):
        t = time.time()
        try:
            fn()
            print(f"ok    {name}  ({time.time() - t:.1f} s)")
        except Exception:
            FAILED.append(name)
            print(f"FAIL  {name}\n{traceback.format_exc()}")
        return fn
    return deco


def hardware():
    import instruments
    cfg = yaml.safe_load(open("config.default.yaml", encoding="utf-8"))
    cfg.setdefault("powermeter", {})["enabled"] = True
    cfg.setdefault("camera", {})["enabled"] = True
    hw = instruments.connect(cfg, simulate=True)
    for li in (hw.lockin_pc, hw.lockin_refl):
        li.tau = 1e-3
    return cfg, hw


CFG, HW = hardware()
for _a in HW.accessories:                 # faster simulated power meter readings
    if _a.name == "powermeter":
        _a.averages = 1


def run_worker(w):
    errors = []
    w.failed.connect(errors.append)
    w.run()
    if errors:
        raise RuntimeError(errors[0])
    return w.path


@check("file names")
def _():
    import naming
    import tempfile
    v = dict(sample="flake A", date="20261015", size="20x20um", step="500nm",
             power="10uW", type="line", Temperature="4.2 K")
    t = tempfile.mkdtemp()
    assert naming.make_name(naming.DEFAULT_PATTERN, v, t) == \
        "flake-A_20261015_20x20um_500nm_10uW_001"
    os.makedirs(os.path.join(t, "flake-A_20261015_20x20um_500nm_10uW_001"))
    assert naming.make_name(naming.DEFAULT_PATTERN, v, t).endswith("_002")
    assert naming.make_name(naming.pattern_for(naming.DEFAULT_PATTERN, "line"), v, t) \
        == "flake-A_20261015_20x20um_500nm_10uW_line_001"
    assert naming.make_name("{sample}_{temperature}", v, t) == "flake-A_4.2-K"


@check("laser power: monotonic 2 mV steps, abort")
def _():
    from scanner import set_laser_power
    pm = next(a for a in HW.accessories if a.name == "powermeter")
    li = HW.lockin_pc
    li.set_sine_amplitude(1.0)
    seen = []
    orig = li.set_sine_amplitude

    def spy(v):
        seen.append(v)
        return orig(v)
    li.set_sine_amplitude = spy
    try:
        for target, tol in ((pm.power() * 0.9, 1.0), (pm.power() * 1.05, 0.0)):
            seen.clear()
            r = set_laser_power(pm, li, target, tol)
            steps = np.diff(seen)
            assert r["ok"], r["message"]
            assert np.all(np.abs(np.abs(steps) - 0.002) < 1e-6), "not 2 mV steps"
            assert np.all(np.sign(steps[:-1]) == np.sign(steps[0])), "went up and down"
        k = [0]
        r = set_laser_power(pm, li, target * 0.5, 1.0,
                            stop=lambda: k.__setitem__(0, k[0] + 1) or k[0] > 5)
        assert r["aborted"]
    finally:
        li.set_sine_amplitude = orig
        li.set_sine_amplitude(1.0)


@check("map scan, step mode (+ bias), save/load/CSV/figure")
def _():
    from scanner import ScanSettings, ScanWorker, load, export_csv
    from figures import save_all
    s = ScanSettings(x_center=-8, y_center=-6, x_size=16, y_size=12, step=1.0,
                     serpentine=False, biases=[0.0, 0.5], amp_mode="sr570",
                     amp_value=1e-6, origin_x=0, origin_y=0, sample="selftest_map")
    p = run_worker(ScanWorker(HW, s, OUT, name="selftest_map_001"))
    assert p == os.path.join(OUT, "selftest_map_001", "selftest_map_001.h5"), p
    d = load(p)
    assert np.isfinite(d.pc).all(), "missing pixels"
    assert np.allclose(d.xs, np.sort(d.xs)) and len(d.xs) == 17, "x axis not restored"
    out = export_csv(d)
    first = open(os.path.join(out, "map0_+0V", "photocurrent_X.csv")).readline()
    assert len(first.split(",")) == 17 and float(first.split(",")[0]) == float(
        first.split(",")[0]), "CSV should be numbers only"
    save_all(d)
    import h5py
    with h5py.File(p, "r") as f:
        assert "x_stage_um" not in f["map0_+0V"], "stage axes should not be saved"
        pl = f["power_log"][()]
        assert pl.ndim == 2 and pl.shape[1] == 2, "power_log: time, power"


@check("map scan, fly mode")
def _():
    from scanner import ScanSettings, ScanWorker, load
    s = ScanSettings(x_center=-8, y_center=-6, x_size=16, y_size=6, step=1.0,
                     serpentine=False, mode="fly", origin_x=0, origin_y=0,
                     sample="selftest_fly")
    d = load(run_worker(ScanWorker(HW, s, OUT)))
    assert np.isfinite(d.pc[..., 0]).mean() > 0.9


@check("series of maps over laser power (2 mV steps), SINE OUT back")
def _():
    from scanner import ScanSettings, ScanWorker, load, export_csv
    from figures import save_all
    HW.lockin_pc.set_sine_amplitude(1.0)
    s = ScanSettings(x_center=-8, y_center=-6, x_size=4, y_size=2, step=1.0,
                     serpentine=False, outer="power", outer_values=[60, 80],
                     origin_x=0, origin_y=0, sample="selftest_powerseries")
    d = load(run_worker(ScanWorker(HW, s, OUT, name="selftest_powerseries_001")))
    got = [m.get("laser_power_reached_uW") for m in d.map_info]
    assert d.pc.shape[0] == 2 and all(abs(g / t - 1) < 0.03 for g, t in zip(got, (60, 80))), got
    assert abs(HW.lockin_pc.sine_amplitude() - 1.0) < 0.003, "SINE OUT not put back"
    export_csv(d)
    save_all(d)


@check("point sweeps: power, bias, bias x power")
def _():
    import sweeps
    HW.stage.move_to(-5, 0.5)
    HW.lockin_pc.set_sine_amplitude(1.0)
    for kind in ("power", "bias", "bias_power"):
        s = sweeps.SweepSettings(kind=kind, powers_uW=[30, 50, 80], biases=[-0.5, 0.5],
                                 n_avg=3, x_stage=-5, y_stage=0.5, u=5, v=0.5,
                                 amp_mode="sr570", amp_value=1e-6, sample="selftest")
        d = sweeps.load_sweep(run_worker(sweeps.SweepWorker(HW, s, OUT)))
        assert np.isfinite(d.pc).all(), kind
        if kind != "bias":
            a = d.fits(0)[0]["alpha"]
            assert 0.7 < a < 1.0, f"alpha {a}"
        sweeps.export_csv(d)
        sweeps.save_all(d)
    assert abs(HW.lockin_pc.sine_amplitude() - 1.0) < 0.003, "SINE OUT not restored"


@check("line scans: bias series and power series")
def _():
    import linescan
    from scanner import ScanWorker, load
    for outer, vals in (("bias", [-0.5, 0, 0.5]), ("power", [50, 80])):
        s = linescan.line_settings((-8, -3), (8, 4), 0.5,
                                   biases=vals if outer == "bias" else None,
                                   outer=outer, values=vals if outer == "power" else None,
                                   origin=(0, 0), flips=(False, False), amp_mode="sr570",
                                   amp_value=1e-6, sample=f"selftest_line_{outer}")
        d = load(run_worker(ScanWorker(HW, s, OUT)))
        assert d.pc.shape[:2] == (len(vals), 1) and np.isfinite(d.pc).all(), outer
        dist, _, _ = linescan.geometry(d)
        assert dist[0] == 0 and dist[-1] > 17, "distance must run 0 -> length"
        linescan.export_csv(d)
        linescan.save_all(d)
    HW.lockin_pc.set_sine_amplitude(1.0)


@check("camera calibration (simulated)")
def _():
    import calibration
    cam = next(a for a in HW.accessories if a.name == "camera")
    HW.stage.move_to(0, 0)
    HW.stage.move_z(101.7)                     # in focus (the simulated sample)
    cal = calibration.calibrate(HW.stage, cam, step_um=4.0, laser_px=(240, 180))
    assert abs(cal.scale_um_per_px() - cam.um_per_px) / cam.um_per_px < 0.03, "wrong scale"
    x, y = cal.px_to_stage(240 + 50, 180, cur=(0, 0), laser=(240, 180))
    assert abs(x - 50 * cam.um_per_px) < 0.3 and abs(y) < 0.3, (x, y)


@check("quick I-V (cycles, AUX IN), save/load/CSV/figure")
def _():
    import iv
    s = iv.IVSettings(v_max=0.5, v_min=-0.5, step=0.05, cycles=2, wait_ms=0, n_avg=2,
                      sensitivity=1e-6, sample="selftest_iv")
    pts = iv.bias_points(s)
    assert pts[0][0] == 0 and max(p for p, _ in pts) == 0.5 and min(p for p, _ in pts) == -0.5
    assert len(pts) == 1 + 2 * 40, len(pts)
    p = run_worker(iv.IVWorker(HW, s, OUT, name="selftest_iv_001"))
    d = iv.load_iv(p)
    a = d.array()
    assert d.completed and a.shape == (len(pts), 5)
    k = np.argmin(abs(a[:, 0] - 0.5))
    assert 1e-7 < a[k, 1] < 1e-6, a[k]              # simulated device: ~0.27 µA at 0.5 V
    assert HW.bias.value == 0.0, "bias not back at 0"
    iv.export_csv(d)
    iv.save_all(d)


@check("resolution from an edge (fit, slanted line)")
def _():
    import math
    import resolution
    xs, ys = np.arange(0, 10, 0.05), np.arange(0, 8, 0.05)
    X, Y = np.meshgrid(xs, ys)
    img = 0.05 + 0.02 * resolution._erf((X - 5) / (math.sqrt(2) * 0.2))
    img = img + 2e-4 * np.random.default_rng(0).normal(size=img.shape)
    for p1 in ((7, 4), (7, 5.5)):
        m = resolution.measure(img, xs, ys, (3, 4), p1, width=0.5)
        fw = m["fit"]["fwhm"]
        assert abs(fw - 0.2 * 2.3548) < 0.04, (p1, fw)
    resolution.save(os.path.join(OUT, "selftest_resolution"), m["distance"], m["values"],
                    m["fit"], {"test": "synthetic edge, sigma 200 nm"})


try:
    from pyqtgraph.Qt import QtWidgets, QtCore  # noqa: F401
    HAVE_QT = True
except Exception:
    HAVE_QT = False

if HAVE_QT:
    @check("main window off-screen (screenshots)")
    def _():
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if os.name == "nt" and not os.environ.get("QT_QPA_FONTDIR"):
            os.environ["QT_QPA_FONTDIR"] = r"C:\Windows\Fonts"   # text off-screen
        from pyqtgraph.Qt import QtWidgets, QtCore
        import gui
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        gui.apply_theme(app, False)
        cfg = dict(CFG)
        cfg["save_dir"] = OUT
        QtWidgets.QMessageBox.question = staticmethod(
            lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)   # reminders: OK
        w = gui.MainWindow(HW, cfg, None)
        w.resize(1600, 950)
        w.show()

        def pump(sec):
            end = time.time() + sec
            while time.time() < end:
                app.processEvents()
                time.sleep(0.02)

        def shot(name):
            pump(0.3)
            w.grab().save(os.path.join(OUT, f"screen_{name}.png"))

        pump(1.0)
        ms = w.minimumSizeHint()
        print(f"      window {w.width()} x {w.height()}, smallest possible "
              f"{ms.width()} x {ms.height()}")
        wide = sorted(((c.minimumSizeHint().width(), type(c).__name__,
                        (getattr(c, "text", lambda: "")() or "")[:40])
                       for c in w.findChildren(QtWidgets.QWidget)
                       if c.minimumSizeHint().width() > 700), reverse=True)
        for wd, typ, txt in wide[:25]:
            print(f"        {wd:5d}  {typ}  {txt}")
        # small map scan through the GUI
        w.xs_.setValue(12)
        w.ys_.setValue(8)
        w.step.setValue(1000)
        w.xc.setValue(-6)
        w.yc.setValue(-4)
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 120:
            pump(0.2)
        assert w.thread is None, "map scan did not finish"
        shot("map")
        if getattr(w, "cam_tab", None) is not None:
            w.tabs.setCurrentWidget(w.cam_tab)
            pump(0.5)
            shot("camera")
        # the Save box: file-name pattern and preview
        w.side_tabs.setCurrentIndex(0)
        w.side_tabs.widget(0).ensureWidgetVisible(w.name_pat)
        shot("save_box")
        assert w.name_preview.text().startswith("next map:"), w.name_preview.text()
        print("      " + w.name_preview.text())
        # fine map across an electrode edge, then "Measure resolution"
        w.set_origin(0.0, 0.0)
        w.xc.setValue(1)
        w.yc.setValue(-1)
        w.xs_.setValue(8)
        w.ys_.setValue(2)
        w.step.setValue(200)
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 300:
            pump(0.2)
        assert w.thread is None, "edge map did not finish"
        w.res_btn.setChecked(True)
        pump(0.5)
        m = w.res_dialog.m
        assert m is not None, "no resolution result"
        print(f"      resolution on the simulated edge: FWHM {m['fit']['fwhm'] * 1000:.0f} nm")
        shot("resolution_line")
        w.res_dialog.grab().save(os.path.join(OUT, "screen_resolution_dialog.png"))
        w.res_dialog.save()
        w.res_btn.setChecked(False)
        # line drawn on the map -> line scan (series: nothing)
        w.line_btn.setChecked(True)
        shot("map_with_line")
        w.scan_line()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 300:
            pump(0.2)
        assert w.thread is None and w.line_data is not None, "line scan did not finish"
        shot("line")
        w.line_btn.setChecked(False)
        # camera: calibrate, then draw a frame and a line on it
        if getattr(w, "cam_tab", None) is not None:
            w.tabs.setCurrentWidget(w.cam_tab)
            HW.stage.move_z(101.7)
            HW.stage.move_to(0, 0)
            pump(0.5)
            w.cam_cross.setPos(240, 180)
            w._cross_placed = True
            w.cal_step.setValue(4.0)
            w.calibrate_camera()
            t0 = time.time()
            while getattr(w, "cal_thread", None) is not None and time.time() - t0 < 60:
                pump(0.2)
            assert w.cal is not None, "calibration failed"
            w.cam_frame_btn.setChecked(True)
            w.cam_line_btn.setChecked(True)
            pump(0.3)
            before = w.cam_rois[0].pos()
            HW.stage.move_to(3, 0)
            w._track_overlays(force=True)
            moved = w.cam_rois[0].pos().x() - before.x()
            assert abs(moved + 3 / w.cal.scale_um_per_px()) < 2, f"overlay moved {moved}"
            shot("camera_calibrated")
            w.use_cam_frame()
            assert w.side_tabs.currentIndex() == 0
            w.cam_frame_btn.setChecked(False)
            w.cam_line_btn.setChecked(False)
        # point sweep (bias x power) through the GUI
        HW.stage.move_to(-5, 0.5)
        w.amp_mode.setCurrentIndex(w.amp_mode.findData("sr570"))
        w.side_tabs.setCurrentIndex(1)
        w.sw_kind.setCurrentIndex(w.sw_kind.findData("bias_power"))
        w.sw_powers.setText("40, 60, 80")
        w.sw_biases.setText("-0.5, 0.5")
        w.sw_navg.setValue(3)
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 600:
            pump(0.2)
        assert w.thread is None and w.sweep_data is not None, "sweep did not finish"
        shot("sweep_bias_power")
        # quick I-V through the GUI
        w.side_tabs.setCurrentIndex(2)
        w.iv_max.setValue(0.5)
        w.iv_min.setValue(-0.5)
        w.iv_step.setValue(50)
        w.iv_cycles.setValue(2)
        w.iv_wait.setValue(0)
        shot("iv_tab")
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 300:
            pump(0.2)
        assert w.thread is None, "I-V did not finish"
        assert w.iv_data is not None and len(w.iv_data.rows) == 81
        shot("iv")
        w._toggle_dark(True)                      # the I-V plot follows the theme
        shot("iv_dark")
        w._toggle_dark(False)
        shot("iv_light")
        # Settings window: saving keeps what the main window remembered on disk
        import tempfile
        import yaml as _yaml
        from settings_dialog import SettingsDialog
        cp = os.path.join(tempfile.mkdtemp(), "config.yaml")
        with open(cp, "w", encoding="utf-8") as fh:
            _yaml.safe_dump({"save_dir": "data", "scan_defaults": {"file_pattern": "{n}_x"},
                             "camera": {"flip_x": False}}, fh)
        dlg = SettingsDialog({"save_dir": "data"}, cp, w)
        dlg.save()
        disk = _yaml.safe_load(open(cp, encoding="utf-8"))
        assert disk["scan_defaults"]["file_pattern"] == "{n}_x", disk
        assert disk["camera"]["flip_x"] is False and disk["save_dir"] == "data", disk
        w._hw_closed = True          # keep the simulated hardware for later tests
        w.close()


print()
if FAILED:
    print("FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("All tests passed. Output in", OUT)
