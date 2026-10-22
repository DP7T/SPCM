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
    print("      fly pixels without a reading (left empty, not filled in):",
          int((~np.isfinite(d.pc[..., 0])).sum()))


@check("bias series: laser power set again before every bias (maps and lines)")
def _():
    import scanner
    from scanner import ScanSettings, ScanWorker
    from linescan import line_settings
    calls = []
    orig = scanner.set_laser_power

    def spy(*a, **k):
        calls.append(HW.bias.value)
        return orig(*a, **k)
    scanner.set_laser_power = spy
    try:
        s = ScanSettings(x_center=-8, y_center=-6, x_size=3, y_size=2, step=1.0,
                         serpentine=False, mode="step", origin_x=0, origin_y=0,
                         biases=[-0.2, 0.0, 0.3], target_power_uW=60.0, power_tol_pct=2.0,
                         sample="selftest_bias_power")
        w = ScanWorker(HW, s, OUT)
        run_worker(w)
        assert len(calls) == 3, calls            # once per bias
        assert all("laser_power_reached_uW" in m for m in w.data.map_info), w.data.map_info
        calls.clear()
        ls = line_settings((-8, -6), (-5, -6), 1.0, biases=[-0.2, 0.3],
                           target_power_uW=60.0, power_tol_pct=2.0,
                           sample="selftest_line_bias_power")
        run_worker(ScanWorker(HW, ls, OUT))
        assert len(calls) == 2, calls
    finally:
        scanner.set_laser_power = orig


@check("auto range up: no saturation (map, fly map, point sweep)")
def _():
    import sweeps
    from scanner import ScanSettings, ScanWorker
    li = HW.lockin_pc
    i0 = li.sens_index()
    try:
        base = dict(x_center=-8, y_center=-3, x_size=16, y_size=2, step=1.0,
                    serpentine=False, origin_x=0, origin_y=0)
        li.set_sens_index(0)                          # 2 nV: the signal saturates
        w = ScanWorker(HW, ScanSettings(mode="step", sample="selftest_noauto", **base), OUT)
        run_worker(w)
        top = np.nanmax(w.data.pc[..., 2])
        assert top <= 1.09 * 2e-9 + 1e-15, top          # simulator saturates like an SR830
        for mode in ("step", "fly"):
            li.set_sens_index(0)
            w = ScanWorker(HW, ScanSettings(mode=mode, auto_range=True,
                                            sample=f"selftest_auto_{mode}", **base), OUT)
            run_worker(w)
            ch = w.data.map_info[0].get("range_changes", [])
            fs = li.full_scale()
            top = np.nanmax(w.data.pc[..., 2])
            print(f"      {mode}: {len(ch)} range change(s), now {fs * 1e9:g} nV, "
                  f"max signal {top / fs * 100:.0f} % of full scale")
            assert ch and top <= 0.8 * fs, (mode, ch, top, fs)
        li.set_sens_index(0)
        HW.stage.move_to(-5, 0.5)                     # a spot with signal
        ss = sweeps.SweepSettings(kind="power", powers_uW=[30, 80], n_avg=2, x_stage=-5,
                                  y_stage=0.5, u=5, v=0.5, auto_range=True,
                                  sample="selftest_auto_sweep")
        w = sweeps.SweepWorker(HW, ss, OUT)
        run_worker(w)
        assert w.data.info.get("range_changes"), w.data.info
        assert np.nanmax(w.data.pc[..., 2]) <= 0.8 * li.full_scale()
    finally:
        li.set_sens_index(i0)


@check("phase correction: angle, copy (raw kept), no copy for pure noise, substrate")
def _():
    import phase
    from scanner import ScanSettings, ScanWorker, load
    s = ScanSettings(x_center=-8, y_center=-6, x_size=16, y_size=12, step=1.0,
                     serpentine=False, mode="step", origin_x=0, origin_y=0,
                     biases=[-0.5, 0.5], sample="selftest_phase")
    d = load(run_worker(ScanWorker(HW, s, OUT)))
    X, Y = phase.processed_xy(d)
    r = phase.find_phase(X, Y)
    print("      " + r["message"])
    assert r["ok"] and abs(r["phase_deg"] - 12.0) < 1.0, r      # simulator: 12°
    p = phase.write_copy(d, r)
    c = load(p)
    assert c.phase and np.allclose(c.raw_pc, d.pc, equal_nan=True), "raw must stay unrotated"
    Xc, Yc = phase.processed_xy(c)
    assert np.nanstd(Yc) < 0.1 * np.nanstd(Xc), (np.nanstd(Yc), np.nanstd(Xc))
    i = np.nanargmax(np.hypot(X, Y))
    assert Xc.ravel()[i] > 0, "X must be positive at the strongest pixel"
    assert os.path.isdir(os.path.splitext(p)[0] + "_csv")
    rng = np.random.default_rng(1)
    assert not phase.find_phase(rng.normal(size=400), rng.normal(size=400))["ok"]
    # a constant pickup on top: found from a marked substrate area
    bx, by = 0.3 * np.nanmax(np.abs(X)), -0.2 * np.nanmax(np.abs(X))
    quiet = np.hypot(X, Y) < np.nanquantile(np.hypot(X, Y), 0.2)
    r2 = phase.find_phase(X + bx, Y + by, True, quiet)
    assert abs(r2["offset"][0] - bx) < 0.1 * abs(bx) and abs(r2["phase_deg"] - 12) < 2, r2
    phase.remove_copy(d.path)
    assert not os.path.exists(p)
    # a strong spike with a random phase: ignored, angle and sign unchanged
    Xs, Ys = X.copy(), Y.copy()
    big = 3 * np.nanmax(np.hypot(X, Y))
    Xs[0, 0, 0], Ys[0, 0, 0] = -big * np.cos(np.deg2rad(70)), -big * np.sin(np.deg2rad(70))
    r3 = phase.find_phase(Xs, Ys)
    assert r3["n_rejected"] >= 1 and abs(r3["phase_deg"] - r["phase_deg"]) < 1, r3["message"]
    # sign: follows the last angle; far from it -> majority, with a warning
    assert abs(phase.find_phase(X, Y, ref_deg=10)["phase_deg"] - 12) < 1.5
    r4 = phase.find_phase(X, Y, ref_deg=-170)
    assert abs(phase._wrap(r4["phase_deg"] + 168)) < 1.5, r4["phase_deg"]
    assert "FAR" in phase.find_phase(X, Y, ref_deg=100)["message"]
    # series: sign change with bias is kept; one angle; a real phase change is flagged
    X2, Y2 = np.stack([X[0], -X[0]]), np.stack([Y[0], -Y[0]])
    r5 = phase.find_phase(X2, Y2, ref_deg=12)
    Xr, _ = phase.rotate(X2, Y2, r5["phase_deg"])
    i = np.nanargmax(np.abs(Xr[0]))
    assert np.sign(Xr[0].ravel()[i]) == -np.sign(Xr[1].ravel()[i]), "sign change lost"
    assert not r5["inconsistent_maps"], r5["message"]
    X3, Y3 = phase.rotate(X[0], Y[0], -30)           # map 2: phase 30° different
    r6 = phase.find_phase(np.stack([X[0], X3]), np.stack([Y[0], Y3]))
    assert r6["inconsistent_maps"] and "CHECK" in r6["message"], r6["message"]
    print("      spike ignored, sign follows the last angle, series checked")


@check("cursors: nearest pixel at a point and along a line, all maps, raw + corrected, CSV")
def _():
    import cursors
    import phase
    from scanner import ScanSettings, ScanWorker, load, pc_scale
    s = ScanSettings(x_center=-8, y_center=-6, x_size=16, y_size=12, step=1.0,
                     serpentine=False, mode="step", origin_x=0, origin_y=0,
                     biases=[-0.5, 0.5], sample="selftest_cursor")
    d = load(run_worker(ScanWorker(HW, s, OUT)))
    fac = pc_scale(d.settings)[0]
    u, v, ix, iy = cursors.axes(d)
    r = cursors.point(d, u[3] + 0.2, v[5] - 0.3)            # nearest: pixel (3, 5)
    assert r["u"] == u[3] and r["v"] == v[5], (r["u"], r["v"])
    assert np.allclose(r["pc"][:, 0], d.pc[:, iy[5], ix[3], 0] * fac, equal_nan=True)
    assert r["pc"].shape == (2, 4) and r["corr"] is None
    cp = phase.write_copy(d, phase.find_phase(*phase.processed_xy(d)))
    r = cursors.point(d, u[3], v[5])
    if os.path.exists(cp):                       # (not with the test's in-memory h5py)
        assert r["corr"] is not None and r["corr"].shape == (2, 2), r["corr"]
    ln = cursors.line(d, (u[0], v[4]), (u[-1], v[4]))       # along one row
    assert len(ln["distance"]) == len(u), len(ln["distance"])
    assert np.allclose(ln["pc"][0][:, 0], d.pc[0, iy[4], ix, 0] * fac, equal_nan=True)
    for kind, res in (("point", r), ("line", ln)):
        p = cursors.save(d, res, kind)
        cols = open(p[:-4] + "_info.txt", encoding="utf-8").read().count("\n  ")
        first = open(p).readline().strip().split(",")
        assert len(first) == cols, (kind, len(first), cols)
    print(f"      point and line read; line has {len(ln['distance'])} points; CSV columns match info.txt")


@check("lock-ins: one at a time on the GPIB bus; a garbled reply is read again")
def _():
    import threading
    import instruments

    class FakeInst:
        def __init__(self, replies):
            self.replies, self.cleared, self.busy, self.overlap = list(replies), 0, False, 0

        def query(self, cmd):
            if self.busy:                       # two threads at once on the bus
                self.overlap += 1
            self.busy = True
            time.sleep(0.002)
            self.busy = False
            return self.replies.pop(0) if self.replies else "1e-9,2e-9,3e-9,45.0"

        def clear(self):
            self.cleared += 1

    li = object.__new__(instruments.SR830)
    li.name = "test lock-in"
    li.inst = FakeInst(["1e-9,2e-9,3e-9,-38.370.000156404", "1e-9,2e-9,3e-9,12.5"])
    v = li.read()                               # the garbled reply from the lab
    assert v[3] == 12.5 and li.inst.cleared == 1, (v, li.inst.cleared)
    # two lock-in objects sharing one bus, read from two threads: never at the same time
    bus = FakeInst([])
    a, b = object.__new__(instruments.SR830), object.__new__(instruments.SR830)
    a.name, b.name, a.inst, b.inst = "a", "b", bus, bus
    ts = [threading.Thread(target=lambda o=o: [o.read() for _ in range(30)]) for o in (a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert bus.overlap == 0, bus.overlap
    print("      garbled reply read again; no overlapping bus access from two threads")


@check("point sweep, finest: every SINE OUT step up (and down), trimmed, saved")
def _():
    import sweeps
    HW.stage.move_to(-5, 0.5)
    s = sweeps.SweepSettings(kind="power", finest=True, p_from_uW=25, p_to_uW=55,
                             every_steps=10, both_ways=True, n_avg=1, x_stage=-5,
                             y_stage=0.5, u=5, v=0.5, sample="selftest_finest")
    w = sweeps.SweepWorker(HW, s, OUT)
    run_worker(w)
    d = w.data
    up = d.direction[0] > 0
    a = d.amp_V[0]
    assert np.all(np.diff(a[up]) > 0), "SINE OUT must only go up on the way up"
    assert np.allclose(np.diff(a[up]), 0.02, atol=1e-6), np.diff(a[up])     # 10 x 2 mV
    assert d.p_uW[0][up][0] <= 27 and np.nanmax(d.p_uW[0][up]) >= 55, d.p_uW[0][up]
    dn = d.direction[0] < 0
    assert dn.any() and np.all(np.diff(a[dn]) < 0), "way down"
    assert np.isfinite(d.t_s).all(), "trimmed: no empty columns"
    r = d.fits(0)[0]
    print(f"      finest: {int(up.sum())} steps up, {int(dn.sum())} down, alpha "
          f"{r['alpha']:.2f}")
    assert 0.7 < r["alpha"] < 1.0, r
    sweeps.export_csv(d)
    sweeps.save_all(d)


@check("stage: a move that stops off target is retried once, then refused")
def _():
    import instruments

    class Fake(instruments.TangoStage):
        def __init__(self, stuck_at):
            self.poll_interval, self.move_timeout, self.settle_s = 0.001, 10, 0.05
            self.has_z, self.stuck, self.sent = False, stuck_at, []

        def write(self, cmd):
            self.sent.append(cmd)

        def _log(self, line):
            pass

        def is_moving(self):
            return False

        def position(self):
            return self.stuck

    st = Fake((10.4, 0.0))                  # 0.4 um off: deadband, accepted after a retry
    st.move_to(10.0, 0.0)
    assert len(st.sent) == 2, st.sent
    st = Fake((15.0, 0.0))                  # 5 um off: blocked / at a limit -> error
    try:
        st.move_to(10.0, 0.0)
    except RuntimeError as e:
        assert "did not reach" in str(e)
    else:
        raise AssertionError("a stage 5 um off target was accepted")


@check("bias ramp remembers the last value really sent when a step fails")
def _():
    import instruments

    class Flaky(instruments._RampMixin):
        ramp_delay = 0

        def __init__(self):
            self.n = 0

        def _set(self, v):
            self.n += 1
            if self.n == 4:
                raise IOError("simulated GPIB error")

    b = Flaky()
    try:
        b.ramp_to(1.0)
    except IOError:
        pass
    assert abs(b.value - 0.3) < 1e-9, b.value


@check("fly scan: Stop halts the stage at once")
def _():
    from scanner import ScanSettings, ScanWorker
    s = ScanSettings(x_center=-8, y_center=-6, x_size=16, y_size=20, step=1.0,
                     serpentine=False, mode="fly", origin_x=0, origin_y=0,
                     sample="selftest_fly_stop")
    w = ScanWorker(HW, s, OUT)
    halted = []
    orig = w._halt_stage

    def spy(stage):
        orig(stage)
        halted.append(stage.is_moving())
    w._halt_stage = spy
    seen = []

    def press_stop(ib, iy, *_):             # press Stop during the 2nd line
        if iy not in seen:
            seen.append(iy)
        if len(seen) == 2:
            w.stop()
    w.pixel.connect(press_stop)
    run_worker(w)
    assert halted and halted[0] is False, "stage was not halted after Stop"


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
        # the phase correction ran by itself after the scan
        pd = getattr(w, "phase_dialog", None)
        assert pd is not None and pd.saved and os.path.exists(pd.saved), "no phased copy"
        print(f"      phase found after the map: {pd.ang.value():+.1f}° (simulation: +12°)")
        pump(0.5)
        pd.grab().save(os.path.join(OUT, "screen_phase_dialog.png"))
        pd.bg.setChecked(True)                    # substrate box on the map
        pump(0.5)
        pd.grab().save(os.path.join(OUT, "screen_phase_dialog_substrate.png"))
        pd.view.setCurrentIndex(1)                # phase-lag view
        pump(0.5)
        pd.grab().save(os.path.join(OUT, "screen_phase_lag.png"))
        pd.save()
        pd.close()
        # the next map follows the angle just used (in the same session, no restart)
        assert abs(w._phase_ref() - pd.ang.value()) < 0.01, (w._phase_ref(), pd.ang.value())
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 120:
            pump(0.2)
        msg2 = w.phase_dialog.res.get("message", "")
        print("      second map: " + msg2.split("Sign")[0][-60:] + "| " +
              ("follows the first map" if "sign as in your earlier" in msg2.lower() else msg2))
        assert "sign as in your earlier" in msg2.lower() and "FAR" not in msg2, msg2
        w.phase_dialog.close()
        # cursors on the map
        w.cpt_btn.setChecked(True)
        w.cln_btn.setChecked(True)
        pump(0.8)
        assert w.cursor_dialog is not None and w.cursor_dialog.rp is not None
        rl = w.cursor_dialog.rl
        assert rl is not None and rl["corr"] is not None, "no corrected values on the line"
        xr, xc = rl["pc"][0][:, 0], rl["corr"][0][:, 0]    # X' follows X (same pixels)
        assert np.nansum(xr * xc) > 0.5 * np.nansum(xr * xr), (xr, xc)
        shot("map_cursors")
        w.cursor_dialog.grab().save(os.path.join(OUT, "screen_cursor_dialog.png"))
        w.cursor_dialog.close()
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
        w.scan_line()                             # 'Use this line' -> Line tab
        assert w.side_tabs.currentIndex() == 1, "Line tab not shown"
        assert w.start_btn.text() == "Start line", w.start_btn.text()
        w.ln_navg.setValue(3)                     # 3 readings per point, error bars
        w.ln_step.setValue(500)
        shot("line_tab")
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 300:
            pump(0.2)
        assert w.thread is None and w.line_data is not None, "line scan did not finish"
        ld = w.line_data
        assert ld.pc_std is not None and np.isfinite(ld.pc_std[..., 0]).any(), "no spread"
        from scanner import load as _load
        ld2 = _load(ld.path)
        assert ld2.pc_std is not None, "spread not saved"
        assert int(ld2.settings.n_avg) == 3
        print(f"      line from the Line tab: {ld.pc.shape[2]} points, 3 readings each "
              "(mean + std saved)")
        shot("line")
        w.line_btn.setChecked(False)
        # live position from the origin while the stage moves (joystick)
        HW.stage.move_to(w.origin_xy[0] + 2.0 * w.sx, w.origin_xy[1] - 1.5 * w.sy)
        w._poll_position()
        assert "+2.00, -1.50" in w.here_lab.text(), w.here_lab.text()
        print("      " + w.here_lab.text())
        w.side_tabs.setCurrentIndex(0)
        w.tabs.setCurrentIndex(0)
        w.side_tabs.widget(0).ensureWidgetVisible(w.here_lab)
        shot("map_position")
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
        w.side_tabs.setCurrentIndex(2)
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
        # finest through the GUI: powers list greyed, From / To shown, 1 reading
        w.sw_kind.setCurrentIndex(w.sw_kind.findData("power"))
        w.sw_fine.setChecked(True)
        assert not w.sw_powers.isEnabled() and w.sw_from.isVisible(), "finest controls"
        assert w.sw_navg.value() == 1, w.sw_navg.value()
        w.sw_from.setValue(30)
        w.sw_to.setValue(50)
        w.sw_every.setValue(10)
        w.refresh_lockins()
        print("      finest estimate: " + w.sw_eta.text())
        shot("sweep_finest_tab")
        w.start()
        t0 = time.time()
        while w.thread is not None and time.time() - t0 < 300:
            pump(0.2)
        assert w.thread is None and w.sweep_data is not None, "finest sweep did not finish"
        assert w.sweep_data.settings.finest
        shot("sweep_finest")
        w.sw_fine.setChecked(False)
        assert w.sw_navg.value() == 3, "readings per point back to before"
        # quick I-V through the GUI
        w.side_tabs.setCurrentIndex(3)
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
