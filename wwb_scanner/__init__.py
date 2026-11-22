# V21.3: on Windows with Python 3.8+ let pyrtlsdr find rtlsdr.dll in the
# Scripts folder of this Python (virtual environment); see dllpath.py
try:
    from wwb_scanner import dllpath as _dllpath
    _dllpath.prepare()
except Exception:      # never stop the program here
    pass


def __getattr__(name):
    """V22.9 (external audit P3-20): ``wwb_scanner.__version__``, the version
    of the installed package (setup.cfg), read when asked for."""
    if name == '__version__':
        try:
            from importlib import metadata
            return metadata.version('rtlsdr-wwb-scanner')
        except Exception:
            return 'unknown'
    raise AttributeError('module %r has no attribute %r' % (__name__, name))
