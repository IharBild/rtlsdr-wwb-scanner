from pathlib import Path
import logging
import logging.handlers

USER_HOME = Path.home()
LOG_DIR = USER_HOME / '.config' / 'rtlsdr-wwb-scanner' / 'logs'
LOG_BASE_FILENAME = LOG_DIR / 'wwb_scanner.log'

def setup(use_console=True, use_file=False):
    handlers = []
    if use_file and not LOG_DIR.exists():     # V18.1 (audit 3.6): not at import time
        LOG_DIR.mkdir(parents=True)

    if use_console:
        term_handler = logging.StreamHandler()
        term_handler.setLevel(logging.DEBUG)
        term_handler.setFormatter(logging.Formatter(
            '{name:40} [{levelname:^10}] : {message}',
            style='{',
        ))
        handlers.append(term_handler)

    if use_file:
        file_handler = logging.handlers.TimedRotatingFileHandler(
            LOG_BASE_FILENAME, when='d', interval=1, backupCount=7,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            '{asctime} {name:40} [{levelname:^10}] : {message}',
            style='{',
        ))
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers,
    )
    logging.captureWarnings(True)
