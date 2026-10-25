"""File names and folders for measurements.

Every measurement gets its own folder inside the save folder:

    <save folder>/<name>/<name>.h5
                        /<name>_map0.png   (maps; one PNG per map)  or  <name>.png
                        /<name>_csv/...

The name comes from a pattern with {fields}, by default

    {sample}_{date}_{size}_{step}_{power}_{n}  ->  flakeA_20261015_20x20um_500nm_10uW_001

Fields that don't apply (e.g. the step of a point sweep) are left out.
The experiment notes can be used too: {Temperature}, {Polarization}, ...
"""
import os
import re
import time

DEFAULT_PATTERN = "{sample}_{date}_{size}_{step}_{power}_{n}"

# (field, what the menu shows)
FIELDS = [
    ("sample", "Sample name"),
    ("date", "Date (20261015)"),
    ("time", "Time (1430)"),
    ("size", "Scan size (20x20um) / line length"),
    ("step", "Step (500nm)"),
    ("power", "Power on sample (10uW)"),
    ("bias", "Bias (-1to1V)"),
    ("mode", "Scan mode (step / fly)"),
    ("type", "Type (map, line, powersweep …)"),
    ("R", "Resistance from the I–V fit (R12.3kOhm)"),
    ("n", "Number (001, 002 …)"),
]


def clean(text):
    """Safe for file names on Windows; µ -> u, ° -> deg, spaces -> -."""
    t = str(text).strip().replace("µ", "u").replace("μ", "u").replace("°", "deg")
    t = re.sub(r"\s+", "-", t)
    return re.sub(r"[^A-Za-z0-9._+\-]", "", t)


def num(v, unit):
    """10.0, 'uW' -> '10uW';  0.5 -> '0.5uW';  -1.25 -> '-1.25V'."""
    v = float(v)
    if abs(v) >= 100:
        return f"{v:.0f}{unit}"              # 1250nm, 120uW (never 1.25e+03)
    return f"{v:.3g}{unit}"


def range_text(vals, unit):
    vals = [v for v in vals if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    return num(lo, unit) if lo == hi else f"{num(lo, '')}to{num(hi, unit)}"


def fill(pattern, values):
    """Pattern + values -> name (without the number), {n} kept as a marker."""
    def sub(m):
        key = m.group(1)
        if key == "n":
            return "\x00"
        for k, v in values.items():          # experiment fields: any upper/lower case
            if k.lower() == key.lower():
                return clean(v)
        return ""
    name = re.sub(r"\{([^{}]+)\}", sub, pattern)
    name = re.sub(r"[_\-]*\x00", "_\x00", name)              # separator before the number
    name = re.sub(r"_{2,}", "_", name).strip("_-.")
    return name or "measurement"


def make_name(pattern, values, folder):
    """Fill the pattern; {n} becomes the first free number (001, 002 …) for which
    no folder of that name exists yet. Without {n} a number is added only if the
    name is taken."""
    pattern = pattern or DEFAULT_PATTERN
    base = fill(pattern, values)

    def taken(name):
        if "RFIT" in name:          # {R} of an I-V: filled in after the fit (R12kOhm ...)
            import glob
            return bool(glob.glob(os.path.join(glob.escape(folder),
                                               glob.escape(name).replace("RFIT", "R*"))))
        return os.path.exists(os.path.join(folder, name))
    if "\x00" in base:
        for k in range(1, 10000):
            name = base.replace("\x00", f"{k:03d}")
            if not taken(name):
                return name
    if not taken(base):
        return base
    k = 2
    while taken(f"{base}_{k}"):
        k += 1
    return f"{base}_{k}"


def measurement_path(folder, name):
    """Create <folder>/<name>/ and return <folder>/<name>/<name>.h5"""
    d = os.path.join(folder, name)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name + ".h5")


def default_name(sample, kind=""):
    """Name used when none is given (tests, old code paths)."""
    return clean(f"{time.strftime('%Y%m%d_%H%M%S')}_{sample}" + (f"_{kind}" if kind else ""))


def pattern_for(pattern, kind):
    """Line scans and point sweeps get their type in the name even if the
    pattern doesn't ask for it, so they can't be mistaken for maps."""
    pattern = pattern or DEFAULT_PATTERN
    if kind == "map" or "{type}" in pattern:
        return pattern
    if "{n}" in pattern:
        return pattern.replace("{n}", "{type}_{n}", 1)
    return pattern + "_{type}"


# ---------------------------------------------------------------- names since 1.2.15
# date_time_sample_wavelength_type_details[_extra]   (sorting by name = time order)
TYPES = {"map": "map", "line": "line", "point": "point", "iv": "IV"}
MAX_GOOD_LENGTH = 80          # warn above this (Windows: whole path <= ~260 characters)


def signed(v, unit="V"):
    """0.5 -> '+0.5V', -1 -> '-1V' (a single bias always with its sign)."""
    v = float(v)
    return f"{v:+.3g}{unit}"


def wavelength_text(nm):
    return "" if not nm else f"{float(nm):.0f}nm"


def compose(*parts):
    """Join the non-empty parts with '_' (each cleaned for file names)."""
    out = []
    for p in parts:
        if p is None:
            continue
        for q in (p if isinstance(p, (list, tuple)) else [p]):
            q = clean(q)
            if q:
                out.append(q)
    return "_".join(out)


def head(sample, wavelength_nm, kind, when=None):
    """Fixed start: date_time_sample_wavelength_type (no wavelength for the I-V)."""
    t = time.localtime(when) if when else time.localtime()
    return [time.strftime("%Y%m%d", t), time.strftime("%H%M", t), sample or "sample",
            "" if kind == "iv" else wavelength_text(wavelength_nm), TYPES.get(kind, kind)]


def fill_extra(text, values):
    """The optional part a tab adds: '{Temperature}_{Gate voltage}' -> '77K_10V'."""
    if not text or not text.strip():
        return ""
    t = re.sub(r"\{([^{}]+)\}",
               lambda m: next((clean(v) for k, v in values.items()
                               if k.lower() == m.group(1).lower()), ""), text)
    return re.sub(r"_{2,}", "_", clean(t)).strip("_-.")


def unique(folder, name):
    """name, or name_2, name_3 ... if a folder of that name exists already. A name
    with the I-V marker RFIT counts as taken by any R... folder of the same start."""
    def taken(n):
        if "RFIT" in n:
            import glob
            return bool(glob.glob(os.path.join(glob.escape(folder),
                                               glob.escape(n).replace("RFIT", "R*"))))
        return os.path.exists(os.path.join(folder, n))
    if not taken(name):
        return name
    k = 2
    while taken(f"{name}_{k}"):
        k += 1
    return f"{name}_{k}"
