"""Start the SPCM program.

Normally started from the "SPCM" Desktop icon made by "Install SPCM.bat".

    python main.py            # real hardware, settings from config.yaml
    python main.py --sim      # simulation, no hardware needed
    python main.py --list     # list VISA instruments and serial ports
"""
import argparse
import os
import sys
import traceback

from appinfo import app_dir, __version__

HERE = app_dir()
CONFIG = os.path.join(HERE, "config.yaml")
LOG = os.path.join(HERE, "spcm_error.log")


def list_devices():
    from settings_dialog import detect_devices
    visa, ports = detect_devices()
    print("VISA instruments:", *visa, sep="\n  ")
    print("COM ports:", *[f"{p}  {d}" for p, d in ports], sep="\n  ")


def restart(simulate=False):
    """Start a fresh copy of the program and close this one."""
    from pyqtgraph.Qt import QtCore, QtWidgets
    extra = ["--sim"] if simulate else []
    if getattr(sys, "frozen", False):            # packaged SPCM.exe
        QtCore.QProcess.startDetached(sys.executable, extra)
    else:
        QtCore.QProcess.startDetached(sys.executable, [os.path.join(HERE, "main.py")] + extra)
    QtWidgets.QApplication.quit()


def load_config():
    import yaml
    if not os.path.exists(CONFIG):
        # first start: your own settings file is made from the default one.
        # config.yaml is never part of an update, so updates keep your settings.
        import shutil
        for src in (os.path.join(HERE, "config.default.yaml"),
                    os.path.join(getattr(sys, "_MEIPASS", HERE), "config.default.yaml")):
            if os.path.exists(src):
                shutil.copy(src, CONFIG)
                break
    with open(CONFIG, encoding="utf-8-sig") as f:   # tolerate a BOM
        config = yaml.safe_load(f) or {}
    sd = config.get("save_dir", "data")
    config["save_dir"] = sd if os.path.isabs(sd) else os.path.join(HERE, sd)
    return config


def main():
    os.chdir(HERE)
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="simulation mode")
    ap.add_argument("--list", action="store_true", help="list instruments and exit")
    a = ap.parse_args()
    if a.list:
        list_devices()
        return

    from pyqtgraph.Qt import QtCore, QtWidgets
    # sharp, correctly sized text on Windows display scaling (125 %, 150 %, ...)
    for attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        if hasattr(QtCore.Qt.ApplicationAttribute, attr):
            QtWidgets.QApplication.setAttribute(getattr(QtCore.Qt.ApplicationAttribute, attr), True)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("SPCM")
    app.setApplicationVersion(__version__)
    QMB = QtWidgets.QMessageBox

    # show unexpected errors in a window (there is no console) and log them
    def excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text + "\n")
        QMB.critical(None, "SPCM – unexpected error",
                     f"{value}\n\nDetails were written to\n{LOG}")
    sys.excepthook = excepthook

    import instruments
    from gui import MainWindow
    from settings_dialog import SettingsDialog

    config = load_config()
    from gui import apply_theme
    apply_theme(app, bool((config.get("ui", {}) or {}).get("dark", False)))
    simulate = a.sim or config.get("simulate", False)

    # first start: ask whether to set up the hardware or just try it out
    if config.get("first_run", False) and not simulate:
        box = QMB(QMB.Icon.Question, "Welcome to SPCM",
                  "This is the first start.\n\nSet up the hardware now (choose the "
                  "ports for the stage, lock-ins, SR570, power meter and camera), or "
                  "try the program in simulation mode without any hardware?")
        setup = box.addButton("Set up hardware", QMB.ButtonRole.AcceptRole)
        box.addButton("Try simulation", QMB.ButtonRole.RejectRole)
        box.exec()
        config["first_run"] = False
        if box.clickedButton() is setup:
            if SettingsDialog(config, CONFIG).exec():
                restart()
                return
            simulate = True
        else:
            simulate = True
        _save_first_run_done()

    try:
        hw = instruments.connect(config, simulate=simulate)
    except Exception as e:
        box = QMB(QMB.Icon.Warning, "SPCM",
                  f"Could not connect to the hardware:\n\n{e}\n\n"
                  "Check that everything is switched on and plugged in, "
                  "or open Settings to choose the right ports.")
        settings = box.addButton("Settings…", QMB.ButtonRole.AcceptRole)
        retry = box.addButton("Try again", QMB.ButtonRole.ActionRole)
        sim = box.addButton("Simulation mode", QMB.ButtonRole.ActionRole)
        box.addButton("Quit", QMB.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is settings:
            if SettingsDialog(config, CONFIG).exec():
                restart()
        elif clicked is retry:
            restart()
        elif clicked is sim:
            restart(simulate=True)
        return  # the new copy (if any) is already starting

    w = MainWindow(hw, config, config_path=CONFIG, restart=restart)
    w.show()
    sys.exit(app.exec())


def _save_first_run_done():
    try:
        with open(CONFIG, encoding="utf-8-sig") as f:
            text = f.read()
        with open(CONFIG, "w", encoding="utf-8") as f:
            f.write(text.replace("first_run: true", "first_run: false"))
    except Exception:
        pass


if __name__ == "__main__":
    main()
