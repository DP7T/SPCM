"""Number input that accepts 0.1, 1e-1, 100m, 2.5µ, 3k, 0,5 ... everywhere.

Rule:
  * a plain number is in the field's own unit        ("0.5" in a µm field = 0.5 µm)
  * k / M / G just multiply                           ("1k" in a nm field = 1000 nm)
  * m, µ/u, n, p (or a typed unit) are ABSOLUTE       ("100n" in a µm field = 100 nm
                                                       = 0.1 µm; "1u" in a nm field
                                                       = 1000 nm; "100m" in a V
                                                       field = 0.1 V)
A unit also makes it absolute: 1s in a ms field = 1000 ms; 100nm, 100mV, 2µA/V.
Prefixes: f p n u/µ m k M G   ("m" = milli, "M" = mega)
"""
import re

SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
      "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
_UNITS = r"(?:A/V|V/A|Hz|V|A|W|s|m|%)"
_NUM = re.compile(
    r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*"
    r"([fpnuµμmkKMG])?\s*(" + _UNITS + r")?\s*$")


def parse_number(text, scale=1.0):
    """scale = the field's unit in base units (1e-6 for a µm field,
    1e-3 for a ms field, 1 for V/A/W fields).  Raises ValueError."""
    t = str(text).strip().replace("−", "-")
    if t.count(",") == 1 and "." not in t:   # decimal comma (e.g. Turkish locale)
        t = t.replace(",", ".")
    m = _NUM.match(t)
    if not m:
        raise ValueError(f"Not a number: '{text}'")
    v = float(m.group(1))
    prefix, unit = m.group(2), m.group(3)
    if prefix in ("k", "K", "M", "G") and not unit:
        v = v * SI[prefix]                    # k/M/G: x1000 ... of the field's unit
    elif prefix or (unit and unit != "%"):    # m, µ, n, p or a unit -> absolute
        v = v * SI.get(prefix or "", 1.0) / scale
    return float(f"{v:.15g}")


def make_spinbox_class(QtWidgets, QtGui, QtCore):
    class SISpinBox(QtWidgets.QDoubleSpinBox):
        """QDoubleSpinBox that understands 100m, 1e-3, 2.5µ, 100nm ...
        scale: the field's unit in base units (e.g. 1e-6 for µm)."""
        def __init__(self, scale=1.0, unit_name="", *a, **k):
            super().__init__(*a, **k)
            self.scale = scale
            self.setLocale(QtCore.QLocale.c())     # always '.' as decimal point
            self.setKeyboardTracking(False)
            if unit_name:
                self.setToolTip(f"Plain numbers are in {unit_name}. With a prefix the "
                                f"value is absolute, e.g. 100n = 100 n(unit).")

        def validate(self, text, pos):
            V = QtGui.QValidator.State
            try:
                parse_number(text, self.scale)
                return (V.Acceptable, text, pos)
            except ValueError:
                return (V.Intermediate, text, pos)

        def valueFromText(self, text):
            try:
                return parse_number(text, self.scale)
            except ValueError:
                return self.value()

        def fixup(self, text):
            return self.textFromValue(self.value())

    return SISpinBox
