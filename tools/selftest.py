"""Self-test in simulation mode (no hardware needed).

    python tools/selftest.py [output folder]

Runs small map scans (step and fly) on the simulated microscope, saves and reloads every file, writes CSVs and
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
        w._hw_closed = True          # keep the simulated hardware for later tests
        w.close()


print()
if FAILED:
    print("FAILED:", ", ".join(FAILED))
    sys.exit(1)
print("All tests passed. Output in", OUT)
