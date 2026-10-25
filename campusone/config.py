import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
APP_ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'campusone_icon.png'
DB_PATH = Path(os.environ['CAMPUSONE_DB_PATH']).expanduser() if os.getenv('CAMPUSONE_DB_PATH') else ROOT / 'data' / 'campusone.db'
SCHEMA_VERSION = 10
