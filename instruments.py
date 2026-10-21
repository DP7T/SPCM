"""
Instrument drivers for the scanning photocurrent microscope (SPCM).

Real hardware:
    TangoStage     - Märzhäuser Tango / Tango Desktop XY controller (native ASCII
                     command set over the USB virtual COM port or RS232)
    SR830          - Stanford Research SR830 lock-in (GPIB or RS232 via PyVISA)
    SR570Bias      - bias from the SR570 preamp (RS-232, +/-5 V)
    LockinAuxBias  - bias from an SR830 AUX OUT (+/-10.5 V)
    Keithley2400   - bias from a Keithley 24xx source-meter (SCPI)

Simulation (no hardware needed):
    SimWorld, SimStage, SimSR830, SimBias

All stage positions are in micrometres. All voltages in volts.
"""
import os
import time
import numpy as np


# --------------------------------------------------------------------------
# Märzhäuser Tango stage
# --------------------------------------------------------------------------
class TangoStage:
    """Märzhäuser Tango controller using the native Tango command set.

    Commands used (Tango native ASCII, terminated by CR):
        !autostatus 0   disable unsolicited status replies
        !dim 1 1        x/y units = micrometres
        ?ver            firmware version
        ?pos            current position "x y [z ...]"
        !moa x y        absolute move (x, y only; z untouched)
        !moa x y z      absolute move including z (focus), when has_z
        ?statusaxis     per-axis status chars, 'M' = moving
        !a              abort all moves
        !joy 0 / !joy 2 joystick off / on
    Check these against the Tango manual for your firmware if anything
    misbehaves (see README).
    """

    def __init__(self, port, baudrate=57600, timeout=2.0, move_timeout=60.0,
                 poll_interval=0.002, has_z=False):
        import serial  # pyserial
        self.ser = serial.Serial(port, baudrate=baudrate, bytesize=8,
                                 parity="N", stopbits=1, timeout=timeout)
        self.move_timeout = move_timeout
        self.poll_interval = poll_interval
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        self.has_z = has_z
        self.write("!autostatus 0")
        self.write("!dim 1 1 1" if has_z else "!dim 1 1")
        self.idn = "Tango " + self.query("?ver")

    # -- low level (every command and reply is logged to spcm_stage.log) --
    from appinfo import app_dir as _app_dir
    LOG = os.path.join(_app_dir(), "spcm_stage.log")

    def _log(self, line):
        try:
            if not hasattr(self, "_logf"):
                if os.path.exists(self.LOG) and os.path.getsize(self.LOG) > 5e6:
                    os.replace(self.LOG, self.LOG + ".old")
                self._logf = open(self.LOG, "a", encoding="utf-8", buffering=1)
                self._logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            self._logf.write(f"{time.time():.3f} {line}\n")
        except Exception:
            pass

    def write(self, cmd):
        self._log(f"-> {cmd}")
        self.ser.write((cmd + "\r").encode("ascii"))

    def query(self, cmd):
        stale = self.ser.read(self.ser.in_waiting or 0)    # late replies of earlier commands
        if stale:
            self._log(f"   (discarded {stale!r})")
        self.write(cmd)
        reply = self.ser.read_until(b"\r").decode("ascii", "ignore").strip()
        self._log(f"<- {reply}")
        if not reply:
            raise TimeoutError(f"Tango: no reply to '{cmd}'")
        return reply

    def _numbers(self, reply, n, cmd):
        """First n numbers of a reply, or a clear error (never a wrong guess)."""
        try:
            vals = [float(v) for v in reply.split()[:n]]
        except ValueError:
            vals = []
        if len(vals) < n:
            raise IOError(f"Tango: unexpected reply to {cmd}: {reply!r}")
        return vals

    # -- high level --
    def position(self):
        x, y = self._numbers(self.query("?pos"), 2, "?pos")
        return x, y

    def is_moving(self):
        return "M" in self.query("?statusaxis")[:3 if self.has_z else 2]

    def position_z(self):
        return self._numbers(self.query("?pos"), 3, "?pos")[2]

    def move_z(self, z, tol_um=0.25):
        """Absolute focus move (µm) of the z axis only. Like move_to, it returns
        only when z has stopped AND reports the target (the status alone can say
        'not moving' before the move has started)."""
        self.write(f"!moa z {z:.3f}")
        self._wait(target_z=z, tol=tol_um)

    def move_to(self, x, y, tol_um=0.25):
        """Absolute move; returns only when the stage reports it is no longer
        moving AND its position is at the target (the 'not moving' status alone
        can come too early, right after the command, before motion starts)."""
        self.write(f"!moa {x:.3f} {y:.3f}")
        self._wait(target=(x, y), tol=tol_um)

    # -- for fly scans --
    def start_move(self, x, y):
        """Start an absolute move and return immediately."""
        self.write(f"!moa {x:.3f} {y:.3f}")

    def velocity(self):
        """x-axis positioning velocity in the controller's own units."""
        return self._numbers(self.query("?vel x"), 1, "?vel x")[0]

    def set_velocity(self, v):
        self.write(f"!vel x {v:.6g}")

    # a stopped stage that is off target by less than this is accepted (encoder
    # deadband); anything more is retried once and then stops the measurement
    accept_um = 1.0
    settle_s = 3.0         # how long a stopped stage may sit off target before acting

    def _wait(self, target=None, tol=0.25, target_z=None):
        t0 = time.time()
        retried = False
        time.sleep(self.poll_interval)
        while True:
            if time.time() - t0 > self.move_timeout:
                self.stop()
                raise TimeoutError("Tango: move timed out")
            if not self.is_moving():
                if target is None and target_z is None:
                    return
                if target_z is not None:
                    if abs(self.position_z() - target_z) <= tol:
                        return
                else:
                    px, py = self.position()
                    if abs(px - target[0]) <= tol and abs(py - target[1]) <= tol:
                        return
                if time.time() - t0 > self.settle_s and not self.is_moving():
                    # stopped for 3 s but not on target
                    if target_z is not None:
                        now, want = (self.position_z(),), (target_z,)
                    else:
                        now, want = self.position(), target
                    err = max(abs(a - b) for a, b in zip(now, want))
                    if not retried:                    # send the move once more
                        self._log(f"# off target by {err:.3f} um after 3 s: sending again")
                        retried, t0 = True, time.time()
                        if target_z is not None:
                            self.write(f"!moa z {target_z:.3f}")
                        else:
                            self.write(f"!moa {target[0]:.3f} {target[1]:.3f}")
                    elif err <= self.accept_um:        # encoder deadband: accept, logged
                        self._log(f"# accepted {err:.3f} um off target")
                        return
                    else:
                        raise RuntimeError(
                            "The stage stopped but did not reach its target: sent to "
                            + ", ".join(f"{w:.2f}" for w in want) + " µm, it reports "
                            + ", ".join(f"{n:.2f}" for n in now) + " µm. Is something "
                            "blocking it, or is it at a limit? (details in spcm_stage.log)")
            time.sleep(self.poll_interval)

    def stop(self):
        self.write("!a")

    def set_joystick(self, enabled):
        self.write("!joy 2" if enabled else "!joy 0")

    def close(self):
        try:
            self.set_joystick(True)
        finally:
            self.ser.close()


# --------------------------------------------------------------------------
# SR830 lock-in amplifier
# --------------------------------------------------------------------------
SR830_TAU = [10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 3e-3, 10e-3, 30e-3, 100e-3,
             300e-3, 1, 3, 10, 30, 100, 300, 1e3, 3e3, 10e3, 30e3]
SR830_SENS = [2e-9, 5e-9, 10e-9, 20e-9, 50e-9, 100e-9, 200e-9, 500e-9,
              1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
              1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3, 1]


class SR830:
    """SR830 via PyVISA. resource e.g. 'GPIB0::8::INSTR' or 'ASRL4::INSTR'."""

    def __init__(self, resource, name="SR830"):
        import pyvisa
        self.name = name
        rm = pyvisa.ResourceManager()
        self.inst = rm.open_resource(resource)
        self.inst.timeout = 3000
        if resource.upper().startswith("ASRL"):
            self.inst.baud_rate = 9600
            self.inst.read_termination = "\r"
            self.inst.write_termination = "\r"
            self.inst.write("OUTX 0")   # answer on RS232
        else:
            self.inst.read_termination = "\n"
            self.inst.write_termination = "\n"
            self.inst.write("OUTX 1")   # answer on GPIB
            self.inst.write("OVRM 1")   # keep front panel usable in GPIB remote
        self.idn = self.inst.query("*IDN?").strip()
        # NOTE: this driver never changes time constant, phase, reference or
        # filters -- those stay on the front panel. The sensitivity is changed only
        # by the optional "Auto range up" (photocurrent lock-in, off by default).

    def time_constant(self):
        return SR830_TAU[int(self.inst.query("OFLT?"))]

    def sensitivity(self):
        return SR830_SENS[int(self.inst.query("SENS?"))]

    def frequency(self):
        """Reference (chopping) frequency in Hz, read only."""
        return float(self.inst.query("FREQ?"))

    # -- only for the optional auto range (off by default) --
    def sens_index(self):
        return int(self.inst.query("SENS?"))

    def set_sens_index(self, i):
        """Change the sensitivity (used only by 'Auto range up', never otherwise)."""
        self.inst.write(f"SENS {int(i)}")

    def full_scale(self):
        """Full scale in the unit SNAP? returns: V, or A for the current inputs
        (I 1 MΩ: × 1e-6, I 100 MΩ: × 1e-8)."""
        src = int(self.inst.query("ISRC?"))
        return SR830_SENS[self.sens_index()] * {2: 1e-6, 3: 1e-8}.get(src, 1.0)

    def read(self):
        """Simultaneous snapshot -> np.array([X, Y, R, theta_deg])."""
        vals = self.inst.query("SNAP?1,2,3,4").split(",")
        return np.array([float(v) for v in vals])

    def slope_poles(self):
        """Low-pass filter order: 6/12/18/24 dB/oct -> 1/2/3/4."""
        return int(self.inst.query("OFSL?")) + 1

    def input_unit(self):
        """'A' for the current inputs I(1MΩ)/I(100MΩ), else 'V' (ISRC?)."""
        return "A" if int(self.inst.query("ISRC?")) >= 2 else "V"

    def overloaded(self):
        """True if input/amplifier or output overload (LIAS bits 0-2)."""
        status = int(self.inst.query("LIAS?"))
        return bool(status & 0b111)

    def set_aux_out(self, channel, volts):
        self.inst.write(f"AUXV {int(channel)},{volts:.4f}")

    def aux_in(self, channel=1):
        """Voltage on the rear-panel AUX IN 1-4 (±10.5 V), e.g. the SR570
        output for a DC I-V."""
        return float(self.inst.query(f"OAUX? {int(channel)}"))

    # reference SINE OUT amplitude (Vrms, 0.004-5.000 V in 2 mV steps).
    # Only used by the power lock during a scan.
    def sine_amplitude(self):
        return float(self.inst.query("SLVL?"))

    def set_sine_amplitude(self, v):
        v = round(min(max(v, 0.004), 5.0) / 0.002) * 0.002
        self.inst.write(f"SLVL {v:.3f}")
        return v

    def close(self):
        self.inst.close()


# --------------------------------------------------------------------------
# Bias sources
# --------------------------------------------------------------------------
class _RampMixin:
    """Ramp the voltage gently instead of jumping (protects devices/gates)."""
    ramp_step = 0.1    # V
    ramp_delay = 0.05  # s
    max_abs_V = 10.0
    value = 0.0

    def ramp_to(self, target):
        if abs(target) > self.max_abs_V:
            raise ValueError(f"Bias {target} V exceeds limit {self.max_abs_V} V")
        n = int(np.ceil(abs(target - self.value) / self.ramp_step))
        for v in np.linspace(self.value, target, n + 1)[1:]:
            self._set(v)
            self.value = float(v)      # last value really sent (right even if a step fails)
            time.sleep(self.ramp_delay)
        self._set(target)
        self.value = target


class NoBias(_RampMixin):
    idn = "none"

    def _set(self, v):
        pass

    def close(self):
        pass


class LockinAuxBias(_RampMixin):
    def __init__(self, lockin, channel=1, max_abs_V=10.0, **ramp):
        self.lockin, self.channel = lockin, channel
        self.max_abs_V = min(max_abs_V, 10.5)
        self.ramp_step = ramp.get("ramp_step", self.ramp_step)
        self.ramp_delay = ramp.get("ramp_delay", self.ramp_delay)
        self.idn = f"{lockin.name} AUX OUT {channel}"
        self._set(0.0)

    def _set(self, v):
        self.lockin.set_aux_out(self.channel, v)

    def close(self):
        self.ramp_to(0.0)


class SR570Bias(_RampMixin):
    """Bias from the SR570 current preamp's built-in bias source (±5 V).

    The SR570 RS-232 port is listen-only: 9600 baud, 8 data bits, no parity,
    2 stop bits, commands end with CR LF. The bias cannot be read back, so the
    program does not know what was set on the front panel. On its first use
    it sets 0 V and switches the bias on, then ramps from there. Set the
    front-panel bias to 0 / off before a sweep.
    """
    def __init__(self, port, max_abs_V=5.0, **ramp):
        import serial
        self.ser = serial.Serial(port, baudrate=9600, bytesize=8, parity="N",
                                 stopbits=2, timeout=0.5, rtscts=False,
                                 dsrdtr=False, xonxoff=False)
        self.max_abs_V = min(max_abs_V, 5.0)
        self.ramp_step = ramp.get("ramp_step", self.ramp_step)
        self.ramp_delay = ramp.get("ramp_delay", self.ramp_delay)
        self.idn = f"SR570 bias on {port}"
        self._armed = False

    def _send(self, cmd):
        self.ser.write((cmd + "\r\n").encode("ascii"))
        self.ser.flush()
        time.sleep(0.01)
        self.ser.reset_input_buffer()   # SR570 echoes; discard it

    def _set(self, v):
        if not self._armed:
            self._send("BSLV0")
            self._send("BSON1")
            self._armed = True
        self._send(f"BSLV{int(round(v * 1000))}")

    def close(self):
        try:
            if self._armed:
                self.ramp_to(0.0)
                self._send("BSON0")
        finally:
            self.ser.close()


class Keithley2400(_RampMixin):
    def __init__(self, resource, compliance_A=1e-6, max_abs_V=10.0, **ramp):
        import pyvisa
        self.inst = pyvisa.ResourceManager().open_resource(resource)
        self.inst.timeout = 3000
        self.idn = self.inst.query("*IDN?").strip()
        self.max_abs_V = max_abs_V
        self.ramp_step = ramp.get("ramp_step", self.ramp_step)
        self.ramp_delay = ramp.get("ramp_delay", self.ramp_delay)
        self.inst.write(":SOUR:FUNC VOLT")
        self.inst.write(":SOUR:VOLT:MODE FIXED")
        self.inst.write(f":SENS:CURR:PROT {compliance_A}")
        self.inst.write(":SOUR:VOLT 0")
        self.inst.write(":OUTP ON")

    def _set(self, v):
        self.inst.write(f":SOUR:VOLT {v:.4f}")

    def close(self):
        self.ramp_to(0.0)
        self.inst.write(":OUTP OFF")
        self.inst.close()


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
class SimWorld:
    """Fake sample: a 2D flake between two gold electrodes on Si/SiO2.
    Photocurrent appears at the two metal/flake contacts (opposite sign,
    Schottky-like) plus a bias-driven term across the channel."""

    def __init__(self):
        self.x, self.y = 0.0, 0.0
        self.bias = 0.0
        self.rng = np.random.default_rng()
        self.laser_amp = 1.0          # lock-in sine output driving the laser (V)
        self.z = 100.0                # focus position (µm)
        self.z_focus = 101.7          # where the sample is in focus
        self._t0 = time.time()

    def laser_power(self):
        """Laser power at the sample: proportional to the drive amplitude,
        with a slow downward drift (-15 % over a few minutes) plus wobble."""
        t = time.time() - self._t0
        drift = 1 - 0.15 * (1 - np.exp(-t / 90)) + 0.02 * np.sin(t / 7)
        return 100e-6 * self.laser_amp * drift

    def dc_current(self):
        """DC current through the simulated device at the present bias (A):
        a slightly non-linear resistor plus the photocurrent of the laser."""
        v = self.bias
        return 2e-7 * v + 5e-8 * np.sinh(2.5 * v) + 2e-9 * self.power_factor()

    def power_factor(self):
        return self.laser_power() / 100e-6

    @staticmethod
    def _edge(u, w=0.6):
        return 0.5 * (1 + np.tanh(u / w))

    def flake(self, x, y):
        # rotated slab, ~14 x 8 um
        c, s = np.cos(0.35), np.sin(0.35)
        u, v = c * x + s * y, -s * x + c * y
        return (self._edge(7 - abs(u)) * self._edge(4 - abs(v - 1)))

    def electrode(self, x, y):
        left = self._edge(-5 - x) * self._edge(6 - abs(y))
        right = self._edge(x - 5) * self._edge(6 - abs(y))
        return left, right

    def reflection(self, x, y):
        f = self.flake(x, y)
        l, r = self.electrode(x, y)
        return 1.0 - 0.18 * f * (1 - l - r) + 0.7 * (l + r)

    def photocurrent(self, x, y):
        beam = 0.8  # um, Gaussian spot
        def spot(x0, y0):
            return np.exp(-((x - x0) ** 2 + (y - y0) ** 2) / (2 * beam ** 2))
        f = self.flake(x, y)
        l, r = self.electrode(x, y)
        # contacts (spread along the electrode edge)
        contact_l = sum(spot(-5, yy) for yy in np.linspace(-2, 3, 6)) * \
            self.flake(-4.5, y)
        contact_r = sum(spot(5, yy) for yy in np.linspace(-1, 4, 6)) * \
            self.flake(4.5, y)
        pc = 2e-9 * (contact_l - 0.8 * contact_r)
        pc += 1.5e-9 * self.bias * f * (1 - l - r)
        return pc


class SimStage:
    idn = "Simulated Tango"
    has_z = True

    def __init__(self, world):
        self.w = world

    def position_z(self):
        return self.w.z

    def move_z(self, z):
        time.sleep(min(0.002 + abs(z - self.w.z) * 2e-3, 0.2))
        self.w.z = float(z)

    vel_native = 2.0                 # like the Tango: mm/s, unknown to the scanner
    _mv = None

    def _update(self):
        if self._mv is None:
            return
        x0, y0, x1, y1, t0, v = self._mv
        d = np.hypot(x1 - x0, y1 - y0)
        frac = 1.0 if d == 0 else min((time.time() - t0) * v / d, 1.0)
        self.w.x, self.w.y = x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac
        if frac >= 1.0:
            self._mv = None

    def position(self):
        self._update()
        return self.w.x, self.w.y

    def start_move(self, x, y):
        self._update()
        self._mv = (self.w.x, self.w.y, float(x), float(y), time.time(),
                    self.vel_native * 1000.0)          # µm/s

    def move_to(self, x, y):
        dist = np.hypot(x - self.w.x, y - self.w.y)
        time.sleep(min(0.001 + dist * 2e-4, 0.2))
        self._mv = None
        self.w.x, self.w.y = float(x), float(y)

    def is_moving(self):
        self._update()
        return self._mv is not None

    def velocity(self):
        return self.vel_native

    def set_velocity(self, v):
        self.vel_native = float(v)

    def stop(self):
        self._update()
        self._mv = None

    def set_joystick(self, enabled):
        pass

    def close(self):
        pass


class SimSR830:
    def __init__(self, world, role, name):
        self.w, self.role, self.name = world, role, name
        self.idn = f"Simulated SR830 ({role})"
        self.tau = 3e-3

    def time_constant(self):
        return self.tau

    sens_i = None

    def _si(self):
        if self.sens_i is None:
            self.sens_i = SR830_SENS.index(10e-9 if self.role == "photocurrent" else 100e-3)
        return self.sens_i

    def sensitivity(self):
        return SR830_SENS[self._si()]

    def sens_index(self):
        return self._si()

    def frequency(self):
        return 1013.0

    def set_sens_index(self, i):
        self.sens_i = int(min(max(i, 0), len(SR830_SENS) - 1))

    def full_scale(self):
        return SR830_SENS[self._si()]

    def read(self):
        time.sleep(0.004)                       # GPIB transfer time
        x, y = self.w.x, self.w.y
        if self.role == "photocurrent":
            sig, noise, phase = self.w.photocurrent(x, y), 3e-11, 12.0
        else:
            sig, noise, phase = 0.05 * self.w.reflection(x, y), 2e-4, -3.0
        pf = max(self.w.power_factor(), 0.0)
        # photocurrent grows a bit slower than linearly with power (α ≈ 0.85)
        sig *= pf ** 0.85 if self.role == "photocurrent" else pf
        ph = np.deg2rad(phase)
        X = sig * np.cos(ph) + noise * self.w.rng.normal()
        Y = sig * np.sin(ph) + noise * self.w.rng.normal()
        fs = 1.09 * self.full_scale()                 # the outputs saturate like a real SR830
        R = float(np.hypot(X, Y))
        if R > fs:
            X, Y = X * fs / R, Y * fs / R
        return np.array([X, Y, np.hypot(X, Y), np.rad2deg(np.arctan2(Y, X))])

    def overloaded(self):
        return False

    def set_aux_out(self, channel, volts):
        if self.role == "photocurrent":
            self.w.bias = volts

    def aux_in(self, channel=1):
        """Simulated SR570 output (DC, 1 µA/V) for the quick I-V."""
        time.sleep(0.003)
        return self.w.dc_current() / 1e-6 + 2e-4 * self.w.rng.normal()

    def input_unit(self):
        return "V"

    def slope_poles(self):
        return 2

    def sine_amplitude(self):
        return self.w.laser_amp

    def set_sine_amplitude(self, v):
        v = round(min(max(v, 0.004), 5.0) / 0.002) * 0.002
        self.w.laser_amp = v
        return v

    def close(self):
        pass


class SimBias(_RampMixin):
    idn = "Simulated bias"
    ramp_delay = 0.0

    def __init__(self, world, max_abs_V=10.0):
        self.w, self.max_abs_V = world, max_abs_V

    def _set(self, v):
        self.w.bias = v

    def close(self):
        self.ramp_to(0.0)


# --------------------------------------------------------------------------
# Accessories (camera, power meter, ...) -- hooks for later
# --------------------------------------------------------------------------
class Accessory:
    """Something read once at the start and once at the end of every map.

    Subclass it and implement snapshot(), which returns a dict of numbers,
    strings or numpy arrays (e.g. {"power_W": 1.2e-4} or {"image": array}).
    Everything returned is saved in the .h5 file under accessories/.
    Examples to add later: Thorlabs camera image of the scan area,
    laser power from the power meter for normalising photocurrent.
    """
    name = "accessory"
    idn = ""

    def snapshot(self):
        return {}

    def close(self):
        pass


# --------------------------------------------------------------------------
# Hardware bundle
# --------------------------------------------------------------------------
class Hardware:
    def __init__(self, stage, lockin_pc, lockin_refl, bias, simulated,
                 accessories=None, warnings=None):
        self.stage, self.lockin_pc, self.lockin_refl = stage, lockin_pc, lockin_refl
        self.bias, self.simulated = bias, simulated
        self.accessories = accessories or []
        self.warnings = warnings or []

    def describe(self):
        return {"stage": self.stage.idn, "lockin_photocurrent": self.lockin_pc.idn,
                "lockin_reflection": self.lockin_refl.idn, "bias": self.bias.idn,
                "accessories": {a.name: a.idn for a in self.accessories},
                "simulated": self.simulated}

    def close(self):
        for dev in (self.bias, self.stage, self.lockin_pc, self.lockin_refl,
                    *self.accessories):
            try:
                dev.close()
            except Exception:
                pass


def connect(config, simulate=False):
    bcfg = config.get("bias", {}) or {}
    ramp = {k: bcfg[k] for k in ("ramp_step", "ramp_delay") if k in bcfg}
    max_v = float(bcfg.get("max_abs_V", 10.0))

    if simulate:
        w = SimWorld()
        pc = SimSR830(w, "photocurrent", "lock-in PC")
        from accessories import connect_accessories
        acc, _ = connect_accessories(config, world=w)
        return Hardware(SimStage(w), pc, SimSR830(w, "reflection", "lock-in R"),
                        SimBias(w, max_v), True, acc)

    s = config["stage"]
    stage = TangoStage(s["port"], s.get("baudrate", 57600),
                       move_timeout=s.get("move_timeout", 60.0),
                       has_z=bool(s.get("has_z", False)))
    pc = SR830(config["lockin_photocurrent"]["resource"], "lock-in PC")
    refl = SR830(config["lockin_reflection"]["resource"], "lock-in R")

    # The bias source is optional: if it can't be opened, carry on without it.
    warnings = []
    btype = bcfg.get("type", "none")
    try:
        if btype == "sr570":
            bias = SR570Bias(bcfg["port"], max_v, **ramp)
        elif btype == "lockin_aux":
            which = pc if bcfg.get("lockin", "photocurrent") == "photocurrent" else refl
            bias = LockinAuxBias(which, bcfg.get("aux_channel", 1), max_v, **ramp)
        elif btype == "keithley2400":
            bias = Keithley2400(bcfg["resource"], bcfg.get("compliance_A", 1e-6),
                                max_v, **ramp)
        else:
            bias = NoBias()
    except Exception as e:
        warnings.append(f"Bias source '{btype}' not available ({e}). "
                        "Continuing without bias control.")
        bias = NoBias()
    from accessories import connect_accessories
    acc, acc_warn = connect_accessories(config)
    return Hardware(stage, pc, refl, bias, False, acc, warnings + acc_warn)
