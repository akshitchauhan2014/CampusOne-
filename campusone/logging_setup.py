"""Local, rotating application log (data/logs/campusone.log).

Unhandled errors used to vanish into a console nobody sees, or into ``except Exception: pass``.
Nothing here ever receives passwords, codes or recovery keys: only exception types, messages and
tracebacks are written."""
import logging, sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "campusone"

def setup_logging(data_dir):
    log = logging.getLogger(LOGGER_NAME)
    if log.handlers: return log
    log.setLevel(logging.INFO)
    try:
        folder = Path(data_dir) / "logs"; folder.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(folder / "campusone.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    except OSError:
        h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(h)
    return log

def get_logger(): return logging.getLogger(LOGGER_NAME)

def install_excepthook(show_dialog=None):
    """Log every unhandled exception and (optionally) tell the user instead of failing silently."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb); return
        get_logger().error("Unhandled exception", exc_info=(exc_type, exc, tb))
        if show_dialog:
            try: show_dialog("CampusOne hit an unexpected problem. Your data was not changed by the failed action. Details were saved to the application log (data/logs/campusone.log).")
            except Exception: pass
    sys.excepthook = hook
