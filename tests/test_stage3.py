import pytest
from concurrent.futures import ThreadPoolExecutor
from campusone.db import Database
from campusone.students import StudentRepository
from campusone.academics import AcademicRepository, AcademicValidationError, attendance_percentage
from campusone.fees import FeeRepository

def setup_db(tmp_path):
    db=Database(tmp_path/"campusone.db"); db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES(?)",("Science",))
        subject=c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES(?,?,?,?)",
                          ("Science",1,"PHY101","Physics")).lastrowid
    sid=StudentRepository(db).create({"enrollment_no":"ST-100","full_name":"Test Student","course_name":"Science","semester":1})
    course="Science"
    return db,course,subject,sid

def test_attendance_calculation_validation_and_threshold(tmp_path):
    assert attendance_percentage(8,6)==75.0
    assert attendance_percentage(0,0) is None
    with pytest.raises(AcademicValidationError): attendance_percentage(3,4)
    with pytest.raises(AcademicValidationError): attendance_percentage(-1,0)
    db,course,subject,sid=setup_db(tmp_path)
    repo=AcademicRepository(db,80)
    repo.record_attendance(sid,subject,"2026-10-01",10,7)
    rows=repo.attendance_summary()
    assert rows[0]["percentage"]==70.0 and rows[0]["below_threshold"]

def test_results_validation_and_upsert(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=AcademicRepository(db)
    rid=repo.save_result(sid,subject,"Midterm","Semester 1",80,100)
    assert repo.save_result(sid,subject,"Midterm","Semester 1",90,100)==rid
    assert repo.results(student_id=sid)[0]["marks_obtained"]==90
    with pytest.raises(AcademicValidationError): repo.save_result(sid,subject,"Final","Semester 1",101,100)
    with pytest.raises(AcademicValidationError): repo.save_result(sid,subject,"Final","Semester 1",-1,100)

def test_fee_payment_unique_receipt_atomic_and_balance(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=FeeRepository(db)
    repo.set_fee_structure(course.lower(),"Semester 1",1000,"Tuition")
    receipt=repo.record_payment(sid,250,"2026-10-22","upi","Semester 1")
    assert receipt["amount"]==250
    assert receipt["receipt_number"].startswith("CO-")
    assert repo.record_payment(sid,50,"2026-10-22","upi","Semester 1")["receipt_number"] != receipt["receipt_number"]
    assert repo.student_balance(sid,"Semester 1")["balance"]==700
    with pytest.raises(AcademicValidationError):
        repo.record_payment(sid,-10,"2026-10-22","cash","Semester 1")
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM fee_payments").fetchone()[0]==2
        assert c.execute("SELECT COUNT(DISTINCT receipt_number) FROM fee_payments").fetchone()[0]==2

def test_payment_history_can_be_filtered_for_receipt_generation(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=FeeRepository(db)
    other_id=StudentRepository(db).create({
        "enrollment_no":"ST-200","full_name":"Other Student","course_name":course,"semester":1,
    })
    repo.record_payment(sid,125,"2026-10-22","cash","Semester 1")
    repo.record_payment(other_id,250,"2026-10-23","upi","Semester 1")

    payments=repo.list_payments(student_id=sid)
    assert len(payments)==1
    assert payments[0]["enrollment_no"]=="ST-100"
    assert payments[0]["amount"]==125
    assert payments[0]["receipt_number"].startswith("CO-")

def test_fee_summary_filters(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=FeeRepository(db)
    repo.record_payment(sid,125,"2026-10-22","cash","Semester 1")
    assert repo.collection_summary(payment_mode="cash")[0]["total"]==125

def test_student_fee_balances_can_be_limited_to_selected_student(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=FeeRepository(db)
    repo.set_fee_structure(course, "Semester 1", 1000)
    other_id=StudentRepository(db).create({
        "enrollment_no":"ST-200","full_name":"Other Student","course_name":course,"semester":1,
    })
    StudentRepository(db).set_status(sid, "inactive")

    balances=repo.student_balances(student_id=sid)
    assert len(balances)==1
    assert balances[0]["student_id"]==sid
    assert balances[0]["fee_due"]==1000
    assert repo.student_balances(student_id=other_id)[0]["student_id"]==other_id


def test_fully_paid_student_payment_action_is_detected(tmp_path):
    db,course,subject,sid=setup_db(tmp_path); repo=FeeRepository(db)
    repo.set_fee_structure(course, "Semester 1", 1000)
    assert not repo.student_fees_fully_paid(sid)

    repo.record_payment(sid, 1000, "2026-10-22", "cash", "Semester 1")
    assert repo.student_fees_fully_paid(sid)

    repo.set_fee_structure(course, "Semester 2", 500)
    assert repo.student_fees_fully_paid(sid, "Semester 1")
    assert not repo.student_fees_fully_paid(sid, "Semester 2")

def test_partial_payment_can_follow_fully_paid_prior_snapshot(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("""INSERT INTO student_fee_import_summaries(
            student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name
        ) VALUES(?,?,?,?,?,?)""",
        (sid,"Semester 1",100000,25000,75000,"fees.xlsx"))
    repo=FeeRepository(db)
    repo.record_payment(sid,100,"2026-10-25","upi","Semester 1")

    balance=repo.student_balances(student_id=sid)[0]
    assert balance["paid"]==350
    assert balance["pending"]==650


def test_generated_receipts_are_unique_for_concurrent_payments(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    repo=FeeRepository(db)

    def record_payment(_):
        return repo.record_payment(sid,10,"2026-10-22","cash","Semester 1")["receipt_number"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts=list(pool.map(record_payment,range(18)))
    assert len(receipts)==len(set(receipts))==18
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM fee_payments").fetchone()[0]==18

def test_imported_summaries_are_available_to_their_sections(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("""INSERT INTO student_import_summaries
            (student_id,attendance_percentage,result_percentage,total_fees_paise,submitted_fees_paise,
             pending_fees_paise,source_name) VALUES(?,?,?,?,?,?,?)""",
            (sid,72.5,81.25,100000,25000,75000,"demo.xlsx"))
        conn.execute("""INSERT INTO student_fee_import_summaries
            (student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?,?)""",(sid,"Semester 1",100000,25000,75000,"demo.xlsx"))

    attendance=AcademicRepository(db).imported_attendance_summaries()
    results=AcademicRepository(db).imported_result_summaries()
    fees=FeeRepository(db).imported_fee_summaries()
    assert attendance[0]["attendance_percentage"]==72.5
    assert results[0]["result_percentage"]==81.25
    assert fees[0]["total_fees_paise"]==100000
    assert fees[0]["submitted_fees_paise"]==25000
    assert fees[0]["pending_fees_paise"]==75000


def test_imported_fee_snapshot_is_consolidated_without_recounting_old_payments(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("""INSERT INTO student_import_summaries
            (student_id,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?)""",(sid,100000,25000,75000,"fees.xlsx"))
        conn.execute("""INSERT INTO student_fee_import_summaries
            (student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?,?)""",(sid,"Semester 1",100000,25000,75000,"fees.xlsx"))
    repo=FeeRepository(db)
    repo.record_payment(sid,100,"2026-10-22","upi","Semester 1")

    balances=repo.student_balances()
    assert len(balances)==1
    assert balances[0]["term"]=="Semester 1"
    assert balances[0]["fee_due"]==1000
    assert balances[0]["paid"]==350
    assert balances[0]["pending"]==650
    assert balances[0]["submission_percentage"]==35
    assert balances[0]["payment_status"]=="Fee Pending – Attention Required"
    assert "fees.xlsx" in balances[0]["source"]
    dashboard=repo.fee_dashboard_summary()
    assert dashboard["total_due"]==balances[0]["fee_due"]
    assert dashboard["total_submitted"]==balances[0]["paid"]
    assert dashboard["total_pending"]==balances[0]["pending"]


def test_fee_structure_and_recorded_payments_override_imported_snapshot(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("""INSERT INTO student_import_summaries
            (student_id,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?)""",(sid,100000,25000,75000,"fees.xlsx"))
    repo=FeeRepository(db)
    repo.set_fee_structure(course,"Semester 1",1500)
    repo.record_payment(sid,100,"2026-10-22","upi","Semester 1")

    balance=repo.student_balances("Semester 1")[0]
    assert balance["fee_due"]==1500
    assert balance["paid"]==100
    assert balance["pending"]==1400


def test_fee_balance_handles_zero_due_and_multiple_terms(tmp_path):
    db,course,subject,sid=setup_db(tmp_path)
    with db.transaction() as conn:
        conn.execute("""INSERT INTO student_import_summaries
            (student_id,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?)""",(sid,0,0,0,"zero.xlsx"))
        conn.execute("""INSERT INTO student_fee_import_summaries
            (student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?,?)""",(sid,"Semester 1",0,0,0,"zero.xlsx"))
    rows=FeeRepository(db).student_balances()
    assert len(rows)==1
    assert rows[0]["submission_percentage"] is None
    assert rows[0]["pending_percentage"] is None
    assert rows[0]["payment_status"]=="No fee due"
