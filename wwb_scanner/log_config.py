"""Logging of the program.

V22.0: one log file, ``logs/wwb_scanner.log`` in the data folder
(wwb_scanner/paths.py). Before V22.0 the window wrote two logs: one in
``~/.config/rtlsdr-wwb-scanner/logs`` (without an encoding: cp1251 on the
user's Windows) and one in the home folder.
"""
import logging
import logging.handlers

from wwb_scanner import paths

_state = {'file_handler': None, 'path': None}


def add_file_handler(level=logging.INFO):
    """The log file handler on the root logger (once per process).
    Returns the path of the file, or None when it can not be opened."""
    if _state['file_handler'] is not None:
        return _state['path']
    try:
        path = paths.log_file(create_dir=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=2000000, backupCount=5,
                                                  encoding='utf-8')
    except (IOError, OSError):
        return None
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(fh)
    _state['file_handler'] = fh
    _state['path'] = path
    return path


def log_path():
    return _state['path']


def setup(use_console=True, use_file=False):
    handlers = []
    if use_console:
        term_handler = logging.StreamHandler()
        term_handler.setLevel(logging.DEBUG)
        term_handler.setFormatter(logging.Formatter(
            '{name:40} [{levelname:^10}] : {message}',
            style='{',
        ))
        handlers.append(term_handler)
    logging.basicConfig(level=logging.DEBUG, handlers=handlers)
    if use_file:
        add_file_handler()
    logging.captureWarnings(True)
