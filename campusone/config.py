import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
APP_ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'campusone_icon.png'
if os.getenv('CAMPUSONE_DB_PATH'):
    DB_PATH = Path(os.environ['CAMPUSONE_DB_PATH']).expanduser()
elif getattr(sys, 'frozen', False):
    if os.name == 'nt':
        data_root = Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        data_root = Path.home() / 'Library' / 'Application Support'
    else:
        data_root = Path(os.getenv('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    DB_PATH = data_root / 'CampusOne' / 'data' / 'campusone.db'
else:
    DB_PATH = ROOT / 'data' / 'campusone.db'
SCHEMA_VERSION = 10
