"""Version and where the program's own files live (works from source and as .exe)."""
import os
import sys

__version__ = "1.2.8"


def app_dir():
    """Folder that holds config.yaml, logs and (by default) the data folder:
    next to SPCM.exe when packaged, next to main.py when run from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))
