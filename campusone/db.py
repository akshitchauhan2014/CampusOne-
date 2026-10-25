import json, sqlite3
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from .config import SCHEMA_VERSION

class DatabaseError(RuntimeError): pass

class _Connection(sqlite3.Connection):
    """sqlite3's own context manager only commits/rolls back and never closes the
    connection, which leaks handles (and locks the file on Windows). This subclass
    closes the connection when a ``with`` block ends."""
    def __exit__(self, exc_type, exc, tb):
        try: return super().__exit__(exc_type, exc, tb)
        finally: self.close()

class Database:
    def __init__(self, path):
        self.path = Path(path)
        # Id of the signed-in administrator; stamped on audit rows written through write_audit().
        self.actor_id = None
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(self.path), timeout=10, factory=_Connection)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA busy_timeout=10000')
        return c
    def initialize(self):
        try:
            c = self.connect()
            try:
                version = int(c.execute('PRAGMA user_version').fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise DatabaseError('Database is newer than this application. Update CampusOne.')
                migrations = [(1, '001_initial.sql'), (2, '002_stage2_import_metadata.sql'), (3, '003_stage3_academics_fees.sql'), (4, '004_stage4_student_portal.sql'), (5, '005_stage5_analytics.sql'), (6, '006_stage6_operations.sql'), (7, '007_student_import_summaries.sql'), (8, '008_course_names.sql'), (9, '009_imported_fee_history.sql'), (10, '010_student_father_name.sql')]
                if 0 < version < SCHEMA_VERSION:
                    self._pre_migration_backup(c, version)
                for target_version, filename in migrations:
                    if version < target_version:
                        sql = (Path(__file__).parent / 'migrations' / filename).read_text(encoding='utf-8')
                        try:
                            if target_version == 8:
                                c.execute('PRAGMA foreign_keys=OFF')
                            c.executescript('BEGIN;\n' + sql + f'\nPRAGMA user_version={target_version};\nCOMMIT;')
                            if target_version == 8:
                                c.execute('PRAGMA foreign_keys=ON')
                                violations = c.execute('PRAGMA foreign_key_check').fetchall()
                                if violations:
                                    raise DatabaseError('Course-name migration found invalid database references.')
                        except sqlite3.Error:
                            # Never leave a half-applied migration behind or mark it as done.
                            if c.in_transaction: c.rollback()
                            raise
                        finally:
                            if target_version == 8:
                                c.execute('PRAGMA foreign_keys=ON')
                        version = target_version
                # Ensure ordered migration metadata exists even when upgrading an older
                # database or reopening a database already at the current schema.
                if version >= 6:
                    for v, fn in migrations:
                        c.execute("INSERT OR IGNORE INTO migration_history(version,filename) VALUES(?,?)", (v, fn))
                    c.commit()
                if int(c.execute('PRAGMA user_version').fetchone()[0]) != SCHEMA_VERSION:
                    raise DatabaseError('Database schema initialization did not complete.')
            finally:
                c.close()
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as e:
            raise DatabaseError('CampusOne could not initialize or access its database.') from e
    def _pre_migration_backup(self, conn, version):
        """Keep a restorable copy before upgrading an existing database."""
        folder = self.path.parent / 'backups'; folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"campusone_pre_migration_v{version}_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        out = sqlite3.connect(str(dest))
        try: conn.backup(out)
        finally: out.close()
        return dest

    def write_audit(self, conn, action, entity_type, entity_id=None, old=None, new=None, details=None, actor_id=None):
        """Single place that writes audit rows, always stamping the acting administrator."""
        conn.execute('INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,old_values,new_values,details) VALUES(?,?,?,?,?,?,?)',
                     (actor_id if actor_id is not None else self.actor_id, action, entity_type,
                      None if entity_id is None else str(entity_id),
                      None if old is None else json.dumps(old, default=str, sort_keys=True),
                      None if new is None else json.dumps(new, default=str, sort_keys=True),
                      None if details is None else str(details)[:1000]))

    @contextmanager
    def transaction(self):
        c = self.connect()
        try:
            c.execute('BEGIN')
            yield c
            c.commit()
        except Exception:
            c.rollback(); raise
        finally: c.close()
    def has_admin(self):
        with self.connect() as c: return c.execute("SELECT 1 FROM users WHERE role=? LIMIT 1", ('admin',)).fetchone() is not None
    def create_admin(self, username, password_hash, recovery_hash):
        with self.transaction() as c:
            if c.execute("SELECT 1 FROM users WHERE role=?", ('admin',)).fetchone(): raise ValueError('Administrator already exists.')
            cur = c.execute("INSERT INTO users(username,role,password_hash,recovery_key_hash) VALUES(?, 'admin', ?, ?)", (username.strip(), password_hash, recovery_hash))
            c.execute('INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)', (cur.lastrowid,'admin_created','user',str(cur.lastrowid),'Initial administrator setup'))
            return cur.lastrowid
    def user_by_name(self, username):
        with self.connect() as c: return c.execute('SELECT id,username,role,password_hash,is_active,failed_attempts,locked_until FROM users WHERE username=?', (username.strip(),)).fetchone()
    def admin_for_recovery(self, username):
        with self.connect() as c:
            return c.execute("SELECT id,recovery_key_hash FROM users WHERE username=? AND role='admin' AND is_active=1",
                             (username.strip(),)).fetchone()
    def reset_admin_password(self, uid, password_hash, new_recovery_hash=None):
        with self.transaction() as c:
            c.execute('UPDATE users SET password_hash=?,failed_attempts=0,locked_until=NULL WHERE id=?', (password_hash, uid))
            if new_recovery_hash:  # recovery keys are single-use: rotate on every successful recovery
                c.execute('UPDATE users SET recovery_key_hash=? WHERE id=?', (new_recovery_hash, uid))
            c.execute('INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,details) VALUES(?,?,?,?,?)',
                      (uid,'admin_password_recovered','user',str(uid),'Administrator password recovered; secrets omitted'))
    def login_success(self, uid):
        with self.transaction() as c:
            c.execute('UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?',(uid,))
            c.execute('INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id) VALUES(?,?,?,?)',(uid,'login_success','user',str(uid)))
    def login_failure(self, uid, attempts, lock):
        with self.transaction() as c:
            c.execute('UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?',(attempts,lock,uid))
            self.write_audit(c, 'login_failed' if not lock else 'login_locked', 'user', uid, details='Failed administrator sign-in; credential omitted', actor_id=uid)
    def recovery_state(self, uid):
        with self.connect() as c:
            r = {x['setting_key']: x['setting_value'] for x in c.execute("SELECT setting_key,setting_value FROM app_settings WHERE setting_key IN (?,?)",
                 (f'recovery_failures:{uid}', f'recovery_locked_until:{uid}'))}
        return int(r.get(f'recovery_failures:{uid}', 0)), r.get(f'recovery_locked_until:{uid}') or None
    def set_recovery_state(self, uid, failures, locked_until):
        with self.transaction() as c:
            for k, v in ((f'recovery_failures:{uid}', str(failures)), (f'recovery_locked_until:{uid}', locked_until or '')):
                c.execute("INSERT INTO app_settings(setting_key,setting_value) VALUES(?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value", (k, v))
            if failures or locked_until:
                self.write_audit(c, 'admin_recovery_failed', 'user', uid, details='Failed recovery-key attempt; secret omitted', actor_id=uid)
