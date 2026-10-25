import pandas as pd
import pytest
from campusone.db import Database
from campusone.students import StudentRepository, StudentValidationError
from campusone.excel_import import ExcelImportService, suggest_mapping, export_rejected

def make_db(tmp_path):
    db = Database(tmp_path / "campusone.db")
    db.initialize()
    return db

def test_student_crud_search_and_duplicate(tmp_path):
    db = make_db(tmp_path); repo = StudentRepository(db)
    sid = repo.create({"enrollment_no":"EN-01","full_name":"Asha Sharma","status":"active"})
    assert repo.search("Asha")[0]["id"] == sid
    with pytest.raises(StudentValidationError):
        repo.create({"enrollment_no":"EN-01","full_name":"Duplicate"})
    repo.set_status(sid,"inactive")
    assert repo.search(status="inactive")[0]["enrollment_no"] == "EN-01"

def test_student_search_matches_enrollment_student_father_name_and_phone(tmp_path):
    db = make_db(tmp_path); repo = StudentRepository(db)
    sid = repo.create({
        "enrollment_no": "EN-9281", "full_name": "Asha Sharma",
        "father_name": "Rajesh Kumar", "phone": "+91 98765 43210",
    })

    for term in ("EN-9281", "Asha", "Rajesh", "Asha Rajesh", "98765", "9876543210"):
        assert [row["id"] for row in repo.search(term)] == [sid]

def test_excel_preview_does_not_change_database_and_commit_is_backed_up(tmp_path):
    db = make_db(tmp_path)
    source = tmp_path / "students.xlsx"
    pd.DataFrame([
        {"Enrollment Number":"EN-10","Student Name":"Ravi Kumar","Email":"ravi@example.com"},
        {"Enrollment Number":"","Student Name":"Missing ID","Email":"bad"},
        {"Enrollment Number":"EN-10","Student Name":"Duplicate row","Email":""},
    ]).to_excel(source,index=False)
    svc = ExcelImportService(db)
    mapping = suggest_mapping(["Enrollment Number","Student Name","Email"])
    preview = svc.preview(source,"Sheet1",mapping)
    assert preview.counts["add"] == 1
    assert preview.counts["reject"] == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    result = svc.commit(preview,False)
    assert not result["committed"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    result = svc.commit(preview,True)
    assert result["added"] == 1 and result["rejected"] == 2
    assert __import__("pathlib").Path(result["backup_path"]).exists()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 1

def test_rejected_export_escapes_formula_values(tmp_path):
    from campusone.excel_import import safe_export_value
    assert safe_export_value("=1+1") == "'=1+1"
    assert safe_export_value("+cmd") == "'+cmd"
    assert safe_export_value("@name") == "'@name"

def test_btech_workbook_columns_map_and_summary_fees_are_calculated(tmp_path):
    db = make_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("INSERT INTO courses(name) VALUES(?)", ("B.tech Computer Science Engineering",))
    source = tmp_path / "btech_students.xlsx"
    pd.DataFrame([{
        "Enrollment No.": "BTECH202600001",
        "Student Name": "Ananya Sharma",
        "Email": "student00001@example.com",
        "Phone No.": "+91-9896233790",
        "Father's Name": "Rakesh Sharma",
        "Semester": 5,
        "Course": "B.tech Computer Science Engineering",
        "Attendance (in %)": 53.39,
        "Result (in %)": 80.24,
        "Total Fees": 50000,
        "Pending Fees": 123,
        "Submitted Fees": 48000,
    }]).to_excel(source, index=False)

    columns = pd.read_excel(source, nrows=0).columns
    mapping = suggest_mapping(columns)
    assert set(mapping) == {
        "enrollment_no", "full_name", "father_name", "email", "phone", "semester", "course_name",
        "attendance_percentage", "result_percentage", "total_fees", "pending_fees", "submitted_fees",
    }
    service = ExcelImportService(db)
    preview = service.preview(source, "Sheet1", mapping)
    assert preview.counts["add"] == 1
    row = preview.rows[0]
    assert row.provided["attendance_percentage"] == 53.39
    assert row.provided["result_percentage"] == 80.24
    assert row.provided["total_fees_paise"] == 5_000_000
    assert row.provided["submitted_fees_paise"] == 4_800_000
    assert row.provided["pending_fees_paise"] == 200_000
    assert "pending_fees" in row.warnings

    result = service.commit(preview, True)
    assert result["added"] == 1
    with db.connect() as conn:
        student = conn.execute("SELECT * FROM students").fetchone()
        summary = conn.execute("SELECT * FROM student_import_summaries").fetchone()
    assert student["phone"] == "+91-9896233790"
    assert student["father_name"] == "Rakesh Sharma"
    assert summary["attendance_percentage"] == 53.39
    assert summary["result_percentage"] == 80.24
    assert summary["pending_fees_paise"] == summary["total_fees_paise"] - summary["submitted_fees_paise"]

def test_reimport_updates_existing_student_summary_when_enabled(tmp_path):
    db = make_db(tmp_path)
    source = tmp_path / "students.xlsx"
    columns = {
        "Enrollment No.": ["EN-11"],
        "Student Name": ["Mira Patel"],
        "Attendance (in %)": [72],
        "Result (in %)": [81],
        "Total Fees": [1000],
        "Submitted Fees": [250],
        "Pending Fees": [750],
    }
    pd.DataFrame(columns).to_excel(source, index=False)
    service = ExcelImportService(db)
    mapping = suggest_mapping(columns)
    first = service.preview(source, "Sheet1", mapping)
    service.commit(first, True)

    columns["Attendance (in %)"] = [88]
    columns["Submitted Fees"] = [600]
    columns["Pending Fees"] = [400]
    pd.DataFrame(columns).to_excel(source, index=False)
    updated = service.preview(source, "Sheet1", mapping, update_existing=True)
    assert updated.counts["update"] == 1
    service.commit(updated, True)
    with db.connect() as conn:
        summary = conn.execute("SELECT * FROM student_import_summaries").fetchone()
    assert summary["attendance_percentage"] == 88
    assert summary["pending_fees_paise"] == 40_000


def test_fee_imports_for_multiple_semesters_are_kept_separate(tmp_path):
    db = make_db(tmp_path)
    service = ExcelImportService(db)
    mapping = suggest_mapping([
        "Enrollment No.", "Student Name", "Semester", "Course", "Total Fees", "Submitted Fees",
    ])

    first_source = tmp_path / "semester_1.xlsx"
    pd.DataFrame([{
        "Enrollment No.": "EN-20", "Student Name": "Asha Rao", "Semester": 1,
        "Course": "Science", "Total Fees": 1000, "Submitted Fees": 250,
    }]).to_excel(first_source, index=False)
    service.commit(service.preview(first_source, "Sheet1", mapping), True)

    second_source = tmp_path / "semester_2.xlsx"
    pd.DataFrame([{
        "Enrollment No.": "EN-20", "Student Name": "Asha Rao", "Semester": 2,
        "Course": "Science", "Total Fees": 2000, "Submitted Fees": 1000,
    }]).to_excel(second_source, index=False)
    update = service.preview(second_source, "Sheet1", mapping, update_existing=True)
    assert update.counts["update"] == 1
    service.commit(update, True)

    with db.connect() as conn:
        snapshots = conn.execute("""SELECT term,total_fees_paise,submitted_fees_paise,source_name
            FROM student_fee_import_summaries ORDER BY term""").fetchall()
    assert [(row["term"], row["total_fees_paise"], row["submitted_fees_paise"]) for row in snapshots] == [
        ("Semester 1", 100_000, 25_000),
        ("Semester 2", 200_000, 100_000),
    ]

    from campusone.fees import FeeRepository
    balances = FeeRepository(db).student_balances()
    assert [(row["term"], row["fee_due"], row["paid"]) for row in balances] == [
        ("Semester 1", 1000, 250),
        ("Semester 2", 2000, 1000),
    ]
