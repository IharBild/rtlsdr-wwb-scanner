# V21.3: on Windows with Python 3.8+ let pyrtlsdr find rtlsdr.dll in the
# Scripts folder of this Python (virtual environment); see dllpath.py
try:
    from wwb_scanner import dllpath as _dllpath
    _dllpath.prepare()
except Exception:      # never stop the program here
    pass
