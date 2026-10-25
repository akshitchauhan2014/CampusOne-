from __future__ import annotations
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from .config import SCHEMA_VERSION

class OperationsError(RuntimeError): pass

def _validate_database(conn, require_schema=False):
    try:
        check=conn.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok": raise OperationsError("Database integrity check failed.")
        fk=conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk: raise OperationsError("Database contains foreign-key violations.")
        version=int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1 or (require_schema and version > SCHEMA_VERSION):
            raise OperationsError("Database schema version is not supported.")
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"students","users","courses","audit_logs"}.issubset(tables):
            raise OperationsError("Database is missing required CampusOne tables.")
        return version
    except sqlite3.DatabaseError as e:
        raise OperationsError("The selected file is not a valid SQLite database.") from e

class BackupManager:
    def __init__(self, db): self.db=db

    def _record_event(self, operation, source, destination, outcome, details):
        # Best-effort metadata: a broken DB must not mask the original failure.
        try:
            with self.db.transaction() as c:
                c.execute("""INSERT INTO backup_events(operation,source_path,destination_path,outcome,details)
                    VALUES(?,?,?,?,?)""",(operation,str(source) if source else None,str(destination) if destination else None,outcome,details[:1000]))
                self.db.write_audit(c,f"{operation}_{outcome}","database",str(destination or source or ""),details=details[:1000])
        except Exception: pass

    def create_backup(self, destination):
        target=Path(destination).expanduser().resolve()
        if target == self.db.path.expanduser().resolve():
            raise OperationsError("Backup destination must differ from the live database.")
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+".tmp")
        try:
            if temp.exists(): temp.unlink()
            # closing(): sqlite3's own "with" never closes, and an open handle blocks the rename on Windows.
            with closing(self.db.connect()) as source, closing(sqlite3.connect(str(temp))) as dest:
                source.backup(dest)
                _validate_database(dest,require_schema=True)
            temp.replace(target)
            self._record_event("backup",self.db.path,target,"success","SQLite online backup completed and integrity verified.")
            return {"path":str(target),"created_at":datetime.now(timezone.utc).isoformat(),"verified":True}
        except Exception as e:
            try:
                if temp.exists():temp.unlink()
            except OSError:pass
            self._record_event("backup",self.db.path,target,"failure",str(e))
            if isinstance(e,OperationsError):raise
            raise OperationsError("Backup failed. No successful backup was reported.") from e

    def validate_backup(self, source):
        path=Path(source).expanduser().resolve()
        if not path.is_file(): raise OperationsError("Backup file does not exist.")
        try:
            uri=path.as_uri()+"?mode=ro"
            conn=sqlite3.connect(uri,uri=True)  # read-only: validating must never modify the candidate file
            try:
                version=_validate_database(conn,require_schema=True)
                return {"path":str(path),"schema_version":version,"valid":True}
            finally:conn.close()
        except OperationsError:raise
        except (sqlite3.DatabaseError,OSError) as e:
            raise OperationsError("The selected file is not a valid, readable CampusOne backup.") from e

    def restore_backup(self, source):
        path=Path(source).expanduser().resolve()
        if path == self.db.path.expanduser().resolve():
            raise OperationsError("Choose a separate backup file, not the live database.")
        validation=self.validate_backup(path)
        # Validate before touching the live database. An automatic safety copy is always taken here,
        # so recovery never depends on the UI having asked for one; the in-memory image covers the
        # restore step itself, and SQLite's backup API keeps WAL connections consistent.
        try:
            safety=self.db.path.parent/"backups"/f"campusone_pre_restore_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
            self.create_backup(safety)
            with closing(self.db.connect()) as live, closing(sqlite3.connect(":memory:")) as rollback:
                live.backup(rollback)
                try:
                    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as src:
                        src.backup(live)
                    _validate_database(live,require_schema=True)
                    live.commit()
                except Exception:
                    rollback.backup(live)
                    live.commit()
                    raise
            # An older (but valid) backup is migrated forward immediately rather than at the next launch.
            self.db.initialize()
            with closing(self.db.connect()) as verify:
                restored_version=_validate_database(verify,require_schema=True)
                count=verify.execute("SELECT COUNT(*) FROM students").fetchone()[0]
            self._record_event("restore",path,self.db.path,"success",
                               f"Restored schema {restored_version}; students accessible={count}; safety copy {safety.name}.")
            return {"source":str(path),"schema_version":restored_version,"students":count,"verified":True,"safety_copy":str(safety)}
        except Exception as e:
            self._record_event("restore",path,self.db.path,"failure",str(e))
            if isinstance(e,OperationsError):raise
            raise OperationsError("Restore failed; the previous database was retained where possible.") from e

    def backup_history(self):
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM backup_events ORDER BY id DESC LIMIT 100")]

class AuditRepository:
    def __init__(self,db):self.db=db
    def search(self, limit=500, action=None, entity_type=None):
        limit=max(1,min(int(limit),2000))
        sql="""SELECT a.id,a.occurred_at,a.actor_user_id,u.username AS actor,a.action,a.entity_type,
            a.entity_id,a.old_values,a.new_values,a.details FROM audit_logs a
            LEFT JOIN users u ON u.id=a.actor_user_id WHERE 1=1"""
        args=[]
        if action:
            esc=str(action).replace("\\","\\\\").replace("%","\\%").replace("_","\\_")
            sql+=" AND a.action LIKE ? ESCAPE '\\'";args.append("%"+esc+"%")
        if entity_type:
            sql+=" AND a.entity_type=?";args.append(str(entity_type))
        sql+=" ORDER BY a.id DESC LIMIT ?";args.append(limit)
        with self.db.connect() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]
