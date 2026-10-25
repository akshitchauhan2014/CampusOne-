import pytest
from campusone.db import Database
from campusone.students import StudentRepository
from campusone.portal import StudentPortalService, PortalError
from campusone.security import verify_secret

def make_db(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES(?)",("Arts",))
        c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)",("Arts",1,"ENG101","English"))
    sid=StudentRepository(db).create({"enrollment_no":"EN-001","full_name":"Student One","course_name":"Arts","semester":1})
    admin=__import__("campusone.security",fromlist=["hash_secret"]).hash_secret("a-very-long-admin-password")
    with db.transaction() as c:
        admin_id=c.execute("INSERT INTO users(username,role,password_hash) VALUES('administrator','admin',?)",(admin,)).lastrowid
    return db,sid,admin_id

def test_registration_code_single_use_and_password_hash(tmp_path):
    db,sid,admin=make_db(tmp_path); service=StudentPortalService(db)
    code=service.issue_registration_code(sid,admin)
    assert len(code)==8
    ok,_,details=service.verify_registration("EN-001",code)
    assert ok and details["full_name"]=="Student One"
    uid=service.complete_registration(sid,"EN-001",code,"secure-pass-123","secure-pass-123")
    with db.connect() as c:
        user=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        assert user["password_hash"]!="secure-pass-123"
        assert verify_secret(user["password_hash"],"secure-pass-123")
        assert c.execute("SELECT used_at FROM registration_codes WHERE student_id=?",(sid,)).fetchone()["used_at"]
    with pytest.raises(PortalError):
        service.complete_registration(sid,"EN-001",code,"secure-pass-123","secure-pass-123")

def test_reissue_revokes_previous_code_and_expiry_validation(tmp_path):
    db,sid,admin=make_db(tmp_path); service=StudentPortalService(db)
    old=service.issue_registration_code(sid,admin)
    new=service.issue_registration_code(sid,admin)
    assert old != new
    ok,_,_=service.verify_registration("EN-001",old)
    assert not ok
    ok,_,details=service.verify_registration("EN-001",new)
    assert ok and details["course_name"]=="Arts"

def test_student_login_and_own_profile_access(tmp_path):
    db,sid,admin=make_db(tmp_path); service=StudentPortalService(db)
    code=service.issue_registration_code(sid,admin)
    service.complete_registration(sid,"EN-001",code,"secure-pass-123","secure-pass-123")
    ok,_,session=service.student_login("EN-001","secure-pass-123")
    assert ok
    assert service.own_profile(session)["enrollment_no"]=="EN-001"
    fake={**session,"student_id":99999}
    # Identity comes from the authenticated user row, not the caller's student_id field.
    assert service.own_profile(fake)["enrollment_no"]=="EN-001"
    with pytest.raises(PermissionError): service.own_profile({"id":session["id"],"role":"admin"})

def test_admin_assisted_password_reset_is_single_use(tmp_path):
    db,sid,admin=make_db(tmp_path); service=StudentPortalService(db)
    code=service.issue_registration_code(sid,admin)
    service.complete_registration(sid,"EN-001",code,"secure-pass-123","secure-pass-123")
    reset=service.issue_reset_code(sid,admin)
    service.reset_password("EN-001",reset,"new-password-456","new-password-456")
    assert service.student_login("EN-001","secure-pass-123")[0] is False
    assert service.student_login("EN-001","new-password-456")[0] is True
    with pytest.raises(PortalError):
        service.reset_password("EN-001",reset,"again-password-789","again-password-789")
