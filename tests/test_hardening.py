"""Regression tests for the production-readiness hardening pass.

Each test pins a defect that existed before and would fail on the old code.
"""
import hashlib
import pandas as pd
import pytest
from campusone.db import Database
from campusone.auth import AuthService
from campusone.students import StudentRepository, StudentValidationError
from campusone.academics import AcademicRepository, AcademicValidationError
from campusone.fees import FeeRepository, to_paise
from campusone.analytics import AnalyticsRepository
from campusone.excel_import import ExcelImportService, suggest_mapping
from campusone.operations import BackupManager, AuditRepository
from campusone.portal import StudentPortalService, PortalError
from campusone.security import hash_secret


def make_db(tmp_path):
    db = Database(tmp_path / "campusone.db"); db.initialize()
    return db


def seed(db, students=3):
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        subject = c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)", ("Science", 1, "S1", "Sci")).lastrowid
        admin = c.execute("INSERT INTO users(username,role,password_hash) VALUES('root','admin',?)", (hash_secret("a-very-long-password"),)).lastrowid
    repo = StudentRepository(db)
    ids = [repo.create({"enrollment_no": f"E{i}", "full_name": f"Student {i}", "course_name": "Science", "semester": 1}) for i in range(students)]
    return "Science", subject, admin, ids


# ---- money / dashboard correctness ---------------------------------------------------------------
def test_outstanding_fees_scale_with_number_of_students(tmp_path):
    """Old code subtracted ALL payments from ONE copy of the fee structure (3 students owing 1000 each showed 600)."""
    db = make_db(tmp_path); course, _, _, ids = seed(db, 3)
    fees = FeeRepository(db)
    fees.set_fee_structure(course, "T1", "1000")
    fees.record_payment(ids[0], "400", "2026-10-01", "cash", "T1")
    s = AnalyticsRepository(db).admin_summary()
    assert s["fee_collected"] == 400.0
    assert s["outstanding_fees"] == 2600.0          # 600 + 1000 + 1000
    t = fees.fee_totals()
    assert t["billed"] == 3000.0 and t["outstanding"] == 2600.0
    assert t["by_course"][0]["collected"] == 400.0


def test_overpayment_by_one_student_does_not_hide_anothers_debt(tmp_path):
    db = make_db(tmp_path); course, _, _, ids = seed(db, 2)
    fees = FeeRepository(db); fees.set_fee_structure(course, "T1", "1000")
    fees.record_payment(ids[0], "1500", "2026-10-01", "cash", "T1")
    assert fees.fee_totals()["outstanding"] == 1000.0


def test_money_parsing_is_exact_and_rejects_bad_values():
    assert to_paise("0.1") + to_paise("0.2") == to_paise("0.3")
    assert to_paise("1,250.50") == 125050
    for bad in ("inf", "nan", "-5", "1.005", "abc", "", "1e30"):
        with pytest.raises(AcademicValidationError): to_paise(bad)


def test_payment_date_is_normalised_so_range_filters_work(tmp_path):
    db = make_db(tmp_path); _, _, _, ids = seed(db, 1)
    fees = FeeRepository(db)
    fees.record_payment(ids[0], "10", "20261005", "cash", "T1")      # compact form accepted by fromisoformat
    with db.connect() as c: assert c.execute("SELECT payment_date FROM fee_payments").fetchone()[0] == "2026-10-05"
    assert fees.collection_summary(start_date="2026-10-01", end_date="2026-10-31")[0]["total"] == 10.0


def test_set_based_balances_match_per_student_balance(tmp_path):
    db = make_db(tmp_path); course, _, _, ids = seed(db, 3)
    fees = FeeRepository(db); fees.set_fee_structure(course, "T1", "500")
    fees.record_payment(ids[1], "200", "2026-10-01", "upi", "T1")
    rows = {r["id"]: r for r in fees.student_balances("T1")}
    for sid in ids: assert rows[sid]["balance"] == fees.student_balance(sid, "T1")["balance"]
    assert {r["id"] for r in fees.outstanding_summary("T1")} == set(ids)


# ---- analytics query bug -----------------------------------------------------------------------
def test_pass_rate_course_filter_applies_to_every_exam_pattern(tmp_path):
    """Old SQL: WHERE A OR B AND course=? -> the course filter only guarded the second pattern."""
    db = make_db(tmp_path); course, subject, _, ids = seed(db, 1)
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Arts')")
        c.execute("INSERT INTO students(enrollment_no,full_name,course_name) VALUES('X1','Other','Arts')")
    ac = AcademicRepository(db)
    ac.save_result(ids[0], subject, "Final Exam", "T1", 80, 100)
    with db.transaction() as c:
        osid = c.execute("SELECT id FROM students WHERE enrollment_no='X1'").fetchone()[0]
        osub = c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)", ("Arts", 1, "A1", "Art")).lastrowid
    ac.save_result(osid, osub, "Final Exam", "T1", 10, 100)
    rows = AnalyticsRepository(db).academic_summary(course_name=course)["pass_rates"]
    assert rows[0]["assessments"] == 1 and rows[0]["pass_pct"] == 100.0


# ---- exports -----------------------------------------------------------------------------------
def test_report_export_neutralises_formulas(tmp_path):
    out = AnalyticsRepository(make_db(tmp_path)).export_report(
        [{"name": "=HYPERLINK(\"http://evil\")", "n": 5, "tab": "\t=1+1"}], tmp_path / "r.csv")
    text = out.read_text()
    assert "'=HYPERLINK" in text and "\"\t=1" not in text and "'\t=1+1" in text
    xl = AnalyticsRepository(make_db(tmp_path)).export_report([{"name": "+cmd"}], tmp_path / "r.xlsx")
    assert pd.read_excel(xl, sheet_name="Report")["name"][0] == "'+cmd"


# ---- Excel import ------------------------------------------------------------------------------
def _xlsx(tmp_path, rows, name="in.xlsx"):
    p = tmp_path / name; pd.DataFrame(rows).to_excel(p, index=False); return p


def test_reimport_with_blank_cells_does_not_wipe_or_reactivate(tmp_path):
    db = make_db(tmp_path); repo = StudentRepository(db)
    sid = repo.create({"enrollment_no": "E1", "full_name": "Asha", "email": "a@x.com", "phone": "98765 43210", "batch": "2024", "status": "withdrawn"})
    src = _xlsx(tmp_path, [{"Enrollment": "E1", "Name": "Asha K", "Email": "", "Phone": ""}])
    svc = ExcelImportService(db)
    mapping = suggest_mapping(["Enrollment", "Name", "Email", "Phone"])
    pv = svc.preview(src, "Sheet1", mapping, update_existing=True)
    assert pv.counts["update"] == 1
    svc.commit(pv, True)
    row = repo.get(sid)
    assert row["full_name"] == "Asha K"                       # supplied value applied
    assert row["email"] == "a@x.com" and row["phone"] == "98765 43210" and row["batch"] == "2024"   # blanks kept
    assert row["status"] == "withdrawn"                        # unmapped Status must not reset to 'active'


def test_import_automatically_adds_course_names(tmp_path):
    db = make_db(tmp_path); seed(db, 0)
    src = _xlsx(tmp_path, [{"Enrollment": "N1", "Name": "A", "Course": "science"},
                           {"Enrollment": "N2", "Name": "B", "Course": "Nope"}])
    svc = ExcelImportService(db)
    pv = svc.preview(src, "Sheet1", suggest_mapping(["Enrollment", "Name", "Course"]))
    assert pv.counts["add"] == 2 and pv.counts["reject"] == 0
    svc.commit(pv, True)
    assert StudentRepository(db).search("N1")[0]["course_name"] == "Science"
    assert StudentRepository(db).search("N2")[0]["course_name"] == "Nope"
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM courses WHERE name='Nope'").fetchone()[0] == 1


def test_failed_import_rolls_back_and_is_logged(tmp_path):
    db = make_db(tmp_path); seed(db, 0)
    src = _xlsx(tmp_path, [{"Enrollment": "N1", "Name": "A"}, {"Enrollment": "N2", "Name": "B"}])
    svc = ExcelImportService(db)
    pv = svc.preview(src, "Sheet1", suggest_mapping(["Enrollment", "Name"]))
    pv.rows[1].data["status"] = "bogus"            # forces validate_student to fail on the 2nd row, mid-transaction
    with pytest.raises(StudentValidationError): svc.commit(pv, True)
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0          # row 1 rolled back too
        assert c.execute("SELECT status FROM import_logs").fetchone()[0] == "failed"  # outcome still recorded
        assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='excel_import_failed'").fetchone()[0] == 1


def test_concurrent_duplicate_is_skipped_not_fatal(tmp_path):
    db = make_db(tmp_path); seed(db, 0)
    src = _xlsx(tmp_path, [{"Enrollment": "N1", "Name": "A"}])
    svc = ExcelImportService(db); pv = svc.preview(src, "Sheet1", suggest_mapping(["Enrollment", "Name"]))
    StudentRepository(db).create({"enrollment_no": "N1", "full_name": "Created meanwhile"})
    r = svc.commit(pv, True)
    assert r["added"] == 0 and r["skipped"] == 1


def test_oversized_workbook_refused(tmp_path, monkeypatch=None):
    import campusone.excel_import as ei
    db = make_db(tmp_path); src = _xlsx(tmp_path, [{"Enrollment": "N1", "Name": "A"}])
    old = ei.MAX_WORKBOOK_BYTES; ei.MAX_WORKBOOK_BYTES = 10
    try:
        with pytest.raises(ValueError): ExcelImportService(db).preview(src, "Sheet1", {"enrollment_no": "Enrollment", "full_name": "Name"})
    finally: ei.MAX_WORKBOOK_BYTES = old


# ---- validation --------------------------------------------------------------------------------
def test_academic_inputs_reject_nan_inf_fractions_and_bad_dates(tmp_path):
    db = make_db(tmp_path); _, subject, _, ids = seed(db, 1); ac = AcademicRepository(db)
    for marks, mx in (("nan", 100), (50, "inf"), ("inf", "inf")):
        with pytest.raises(AcademicValidationError): ac.save_result(ids[0], subject, "Mid", "T1", marks, mx)
    with pytest.raises(AcademicValidationError): ac.record_attendance(ids[0], subject, "2026-10-01", 10.5, 9)
    with pytest.raises(AcademicValidationError): ac.record_attendance(ids[0], subject, "31/12/2026", 10, 9)
    with pytest.raises(AcademicValidationError): ac.set_low_attendance_threshold("nan")
    assert ac.record_attendance(ids[0], subject, "2026-10-01", "10", 9.0)


def test_student_validation_email_phone_and_search_wildcards(tmp_path):
    db = make_db(tmp_path); repo = StudentRepository(db)
    for bad in ({"email": "a@b"}, {"email": "a b@c.com"}, {"phone": "<script>"}):
        with pytest.raises(StudentValidationError): repo.create({"enrollment_no": "Z", "full_name": "Z", **bad})
    repo.create({"enrollment_no": "A_1", "full_name": "Underscore"}); repo.create({"enrollment_no": "AB1", "full_name": "Other"})
    assert [s["enrollment_no"] for s in repo.search("A_1")] == ["A_1"]       # '_' is literal, not a wildcard
    assert repo.search("%") == []
    assert repo.count() == 2 and len(repo.search(limit=1)) == 1


# ---- auth --------------------------------------------------------------------------------------
def test_student_credentials_rejected_by_admin_login_server_side(tmp_path):
    db = make_db(tmp_path); _, _, admin, ids = seed(db, 1); svc = StudentPortalService(db)
    code = svc.issue_registration_code(ids[0], admin); svc.complete_registration(ids[0], "E0", code, "student-pass-123", "student-pass-123")
    ok, msg, user = AuthService(db).login("E0", "student-pass-123")
    assert ok is False and user is None


def test_recovery_key_is_single_use_and_throttled(tmp_path):
    db = make_db(tmp_path); auth = AuthService(db)
    key = auth.setup("administrator", "a-very-long-password")
    new_key = auth.recover("administrator", key, "another-long-password")
    assert new_key != key
    with pytest.raises(ValueError): auth.recover("administrator", key, "third-long-password-x")      # old key is void
    for _ in range(5):
        with pytest.raises(ValueError): auth.recover("administrator", "WRONG-WRONG-WRONG-WRONG", "third-long-password-x")
    with pytest.raises(ValueError): auth.recover("administrator", new_key, "third-long-password-x")   # locked even with the right key
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='admin_recovery_failed'").fetchone()[0] >= 5


def test_failed_admin_logins_are_audited_and_lock(tmp_path):
    db = make_db(tmp_path); auth = AuthService(db); auth.setup("administrator", "a-very-long-password")
    for _ in range(5): assert auth.login("administrator", "nope")[0] is False
    assert auth.login("administrator", "a-very-long-password")[0] is False       # locked
    actions = [r["action"] for r in AuditRepository(db).search()]
    assert actions.count("login_failed") == 4 and "login_locked" in actions


# ---- student portal ----------------------------------------------------------------------------
def _registered(tmp_path):
    db = make_db(tmp_path); _, _, admin, ids = seed(db, 1); svc = StudentPortalService(db)
    code = svc.issue_registration_code(ids[0], admin)
    svc.complete_registration(ids[0], "E0", code, "student-pass-123", "student-pass-123")
    return db, svc, admin, ids[0]


def test_reset_code_cannot_be_brute_forced(tmp_path):
    db, svc, admin, sid = _registered(tmp_path)
    real = svc.issue_reset_code(sid, admin)
    for _ in range(5):
        with pytest.raises(PortalError): svc.reset_password("E0", "AAAAAAAA", "new-student-pass-1", "new-student-pass-1")
    with pytest.raises(PortalError): svc.reset_password("E0", real, "new-student-pass-1", "new-student-pass-1")   # locked
    assert svc.student_login("E0", "student-pass-123")[0] is True                                                 # password untouched


def test_registration_completion_cannot_guess_codes_around_the_lockout(tmp_path):
    db = make_db(tmp_path); _, _, admin, ids = seed(db, 1); svc = StudentPortalService(db)
    real = svc.issue_registration_code(ids[0], admin)
    for _ in range(5):
        with pytest.raises(PortalError): svc.complete_registration(ids[0], "E0", "AAAAAAAA", "student-pass-123", "student-pass-123")
    with pytest.raises(PortalError): svc.complete_registration(ids[0], "E0", real, "student-pass-123", "student-pass-123")


def test_inactive_or_withdrawn_students_cannot_sign_in(tmp_path):
    db, svc, _, sid = _registered(tmp_path)
    assert svc.student_login("E0", "student-pass-123")[0] is True
    StudentRepository(db).set_status(sid, "inactive")
    assert svc.student_login("E0", "student-pass-123")[0] is False
    StudentRepository(db).set_status(sid, "active")
    assert svc.student_login("E0", "student-pass-123")[0] is True


def test_reset_code_ttl_is_validated(tmp_path):
    db, svc, admin, sid = _registered(tmp_path)
    for bad in (-5, 0, 10**6, "x"):
        with pytest.raises(PortalError): svc.issue_reset_code(sid, admin, bad)


# ---- audit / operations ------------------------------------------------------------------------
def test_audit_rows_name_the_acting_administrator_and_keep_before_after(tmp_path):
    db = make_db(tmp_path); _, subject, admin, ids = seed(db, 1)
    db.actor_id = admin
    StudentRepository(db).update(ids[0], {"enrollment_no": "E0", "full_name": "Renamed", "status": "active", "course_name": "Science", "semester": 1})
    FeeRepository(db).record_payment(ids[0], "10", "2026-10-01", "cash", "T1")
    AcademicRepository(db).save_result(ids[0], subject, "Mid", "T1", 5, 10)
    rows = AuditRepository(db).search()
    for action in ("student_updated", "fee_payment_recorded", "result_saved"):
        row = next(r for r in rows if r["action"] == action)
        assert row["actor_user_id"] == admin and row["actor"] == "root", action
    upd = next(r for r in rows if r["action"] == "student_updated")
    assert "Student 0" in upd["old_values"] and "Renamed" in upd["new_values"]


def test_connections_are_closed_by_with_blocks(tmp_path):
    import sqlite3
    db = make_db(tmp_path)
    with db.connect() as c: c.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError): c.execute("SELECT 1")     # leaked handles lock files on Windows


def test_restore_takes_its_own_safety_copy(tmp_path):
    db = make_db(tmp_path); _, _, _, ids = seed(db, 1); mgr = BackupManager(db)
    mgr.create_backup(tmp_path / "b.sqlite")
    StudentRepository(db).create({"enrollment_no": "LATE", "full_name": "Added after backup"})
    info = mgr.restore_backup(tmp_path / "b.sqlite")
    assert info["students"] == 1
    safety = tmp_path / "backups"
    copies = list(safety.glob("campusone_pre_restore_*.db"))
    assert len(copies) == 1
    import sqlite3
    assert sqlite3.connect(str(copies[0])).execute("SELECT COUNT(*) FROM students").fetchone()[0] == 2   # pre-restore state is recoverable


def test_pre_migration_backup_and_legacy_upgrade(tmp_path):
    import sqlite3
    from pathlib import Path
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    migration_dir = Path(__file__).parents[1] / "campusone" / "migrations"
    for version, filename in enumerate((
        "001_initial.sql", "002_stage2_import_metadata.sql", "003_stage3_academics_fees.sql",
        "004_stage4_student_portal.sql", "005_stage5_analytics.sql",
    ), start=1):
        sql = (migration_dir / filename).read_text(encoding="utf-8")
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={version};\nCOMMIT;")
    conn.close()
    Database(path).initialize()
    assert list((tmp_path / "backups").glob("campusone_pre_migration_v5_*.db"))
    assert sqlite3.connect(str(path)).execute("PRAGMA user_version").fetchone()[0] == 10


# ---- early warning -----------------------------------------------------------------------------
def _frame(n=80):
    rows = []
    for i in range(n):
        att = 40 + (i * 7) % 60; internal = (i * 13) % 101
        rows.append({"attendance_pct": att, "internal_marks_pct": internal, "semester": 2,
                     "failed_any_subject": int(att < 65 or internal < 35)})
    return pd.DataFrame(rows)


def test_model_file_integrity_is_enforced_and_explanations_are_meaningful(tmp_path):
    from campusone.early_warning import train_and_evaluate, predict_frame
    # attendance is deliberately the only strong driver, so a sensible explanation must name it
    rows = [{"attendance_pct": 30 + (i * 7) % 70, "internal_marks_pct": 50, "failed_any_subject": int(30 + (i * 7) % 70 < 60)} for i in range(120)]
    res = train_and_evaluate(pd.DataFrame(rows), tmp_path, dataset_kind="synthetic_demo")
    path = res["active_model_path"]
    if path is None:                      # model did not beat baseline on this data: nothing to load, which is also correct
        assert res["active_source"] == "attendance_baseline"; return
    sha = res["report"]["model_sha256"]
    assert sha == hashlib.sha256(open(path, "rb").read()).hexdigest()
    preds, _ = predict_frame(path, pd.DataFrame([{"attendance_pct": 35, "internal_marks_pct": 50}]), sha)
    assert preds[0]["factors"][0]["factor"] == "Attendance" and preds[0]["factors"][0]["direction"] == "increases estimated risk"
    with pytest.raises(ValueError): predict_frame(path, pd.DataFrame([{"attendance_pct": 35, "internal_marks_pct": 50}]), None)
    with pytest.raises(ValueError): predict_frame(path, pd.DataFrame([{"attendance_pct": 35, "internal_marks_pct": 50}]), "0" * 64)
    with open(path, "ab") as f: f.write(b"tamper")
    with pytest.raises(ValueError): predict_frame(path, pd.DataFrame([{"attendance_pct": 35, "internal_marks_pct": 50}]), sha)


def test_two_training_runs_in_one_second_get_distinct_versions(tmp_path):
    from campusone.early_warning import train_and_evaluate
    a = train_and_evaluate(_frame(), tmp_path / "a", dataset_kind="synthetic_demo")["report"]["version"]
    b = train_and_evaluate(_frame(), tmp_path / "b", dataset_kind="synthetic_demo")["report"]["version"]
    assert a != b
