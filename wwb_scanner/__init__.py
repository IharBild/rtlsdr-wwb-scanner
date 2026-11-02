# Load the scan edition of rtlsdr.dll before pyrtlsdr is imported anywhere.
try:
    from wwb_scanner import scan_dll as _scan_dll
    _scan_dll.install()
except Exception:   # never stop the program because of this
    pass
