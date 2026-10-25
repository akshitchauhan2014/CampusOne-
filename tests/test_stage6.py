import sqlite3
import pytest
from campusone.db import Database
from campusone.operations import BackupManager, AuditRepository, OperationsError
from campusone.students import StudentRepository
from campusone.academics import AcademicRepository

def setup_db(tmp_path):
    db=Database(tmp_path/"campusone.sqlite");db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        subject=c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)",("Science",1,"SCI101","Science")).lastrowid
    sid=StudentRepository(db).create({"enrollment_no":"S-1","full_name":"Student One","course_name":"Science","semester":1})
    return db,"Science",subject,sid

def test_schema_version_and_migration_history(tmp_path):
    db,_,_,_=setup_db(tmp_path)
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0]==10
        assert c.execute("SELECT COUNT(*) FROM migration_history").fetchone()[0]==10
        assert c.execute("PRAGMA foreign_keys").fetchone()[0]==1

def test_online_backup_restore_and_integrity(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    backup=tmp_path/"backup.sqlite"
    manager=BackupManager(db)
    info=manager.create_backup(backup)
    assert info["verified"] and manager.validate_backup(backup)["valid"]
    with db.transaction() as c:c.execute("DELETE FROM students WHERE id=?",(sid,))
    assert db.connect().execute("SELECT COUNT(*) FROM students").fetchone()[0]==0
    restored=manager.restore_backup(backup)
    assert restored["verified"] and restored["students"]==1
    with db.connect() as c:assert c.execute("SELECT enrollment_no FROM students").fetchone()[0]=="S-1"
    assert any(r["outcome"]=="success" for r in manager.backup_history())

def test_invalid_backup_rejected_without_touching_live_db(tmp_path):
    db,_,_,_=setup_db(tmp_path); bad=tmp_path/"bad.sqlite";bad.write_text("not a sqlite database")
    with pytest.raises(OperationsError):BackupManager(db).restore_backup(bad)
    with db.connect() as c:assert c.execute("SELECT COUNT(*) FROM students").fetchone()[0]==1

def test_audit_history_and_old_new_marks_values(tmp_path):
    db,_,subject,sid=setup_db(tmp_path)
    repo=AcademicRepository(db)
    repo.save_result(sid,subject,"Midterm","Term 1",50,100)
    repo.save_result(sid,subject,"Midterm","Term 1",70,100)
    rows=AuditRepository(db).search(action="result_saved")
    assert len(rows)==2
    assert rows[0]["old_values"] is not None
    assert '"marks_obtained": 70.0' in rows[0]["new_values"]
    assert "password" not in str(rows).lower()
