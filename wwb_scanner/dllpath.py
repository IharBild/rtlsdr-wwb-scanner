"""V21.3: where pyrtlsdr finds the dongle DLL on Windows.

Since Python 3.8, Windows ctypes no longer searches PATH for a DLL given by
name; it searches the folder of python.exe (for a virtual environment that
is the base installation, not the venv), System32 and folders added with
os.add_dll_directory(). pyrtlsdr 0.4+ asks for ``rtlsdr.dll`` by name, so
a DLL put into the ``Scripts`` folder of a venv is not found. This module
adds that folder (and the folder of the environment itself) when it holds
the DLL. It is called by ``wwb_scanner/__init__.py``, before anything
imports ``rtlsdr``.

Nothing happens on other systems, on Python 3.7 (the user's installation
loads ``librtlsdr.dll`` through PATH as before) or when no DLL is there.
"""
import os
import sys

DLL_NAMES = ('rtlsdr.dll', 'librtlsdr.dll')

_added = []          # keep the handles: closing one removes the folder again


def candidate_dirs(prefix=None):
    prefix = sys.prefix if prefix is None else prefix
    return [os.path.join(prefix, 'Scripts'), prefix]


def find_dll_dir(prefix=None):
    """The first candidate folder that holds rtlsdr.dll or librtlsdr.dll."""
    for d in candidate_dirs(prefix):
        for name in DLL_NAMES:
            if os.path.isfile(os.path.join(d, name)):
                return d
    return None


def prepare():
    """Add the folder with the dongle DLL to the DLL search path (Windows,
    Python 3.8+). Returns the folder, or None."""
    if sys.platform != 'win32' or not hasattr(os, 'add_dll_directory'):
        return None
    d = find_dll_dir()
    if d is None:
        return None
    if any(p == d for p, _ in _added):
        return d
    try:
        _added.append((d, os.add_dll_directory(d)))   # novm (guarded above)
    except OSError:
        return None
    return d
