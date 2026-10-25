import pandas as pd
import pytest
from campusone.db import Database
from campusone.fees import FeeRepository
from campusone.analytics import AnalyticsRepository, validate_feature_columns, rule_baseline, band_for_score
from campusone.early_warning import train_and_evaluate
from campusone.app import DashboardData

def test_schema_stage5_and_dashboard_totals(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize()
    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0]==10
        assert c.execute("SELECT name FROM sqlite_master WHERE name='model_runs'").fetchone()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        c.execute("INSERT INTO students(enrollment_no,full_name,course_name,semester) VALUES('S1','Student One','Science',1)")
        c.execute("INSERT INTO fee_structures(course_name,term,fee_head,amount_paise) VALUES(?,?,?,?)",("Science","Term 1","Tuition",100000))
    summary=AnalyticsRepository(db).admin_summary()
    assert summary["students"]==1 and summary["registered_accounts"]==0
    assert summary["outstanding_fees"]==1000.0
    with db.transaction() as c:
        student_id=c.execute("SELECT id FROM students WHERE enrollment_no='S1'").fetchone()[0]
        c.execute("""INSERT INTO student_import_summaries
            (student_id,attendance_percentage,result_percentage,total_fees_paise,submitted_fees_paise,
             pending_fees_paise,source_name) VALUES(?,?,?,?,?,?,?)""",
            (student_id,72.5,81.25,150000,50000,100000,"demo.xlsx"))
    summary=AnalyticsRepository(db).admin_summary("Science",1)
    imported=summary["imported_summary"]
    assert imported["attendance_students"]==1 and imported["average_attendance"]==72.5
    assert imported["result_students"]==1 and imported["average_result"]==81.25
    assert imported["total_fees"]==1500 and imported["submitted_fees"]==500 and imported["pending_fees"]==1000
    assert summary["outstanding_fees"]==1000.0

def test_live_student_risk_uses_results_and_attendance_filters(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        c.execute("INSERT INTO courses(name) VALUES('Arts')")
        c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES('Science',1,'S1','Physics')")
        c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES('Science',2,'S2','Chemistry')")
        c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES('Arts',1,'A1','History')")
        science_subject=c.execute("SELECT id FROM subjects WHERE subject_code='S1'").fetchone()[0]
        arts_subject=c.execute("SELECT id FROM subjects WHERE subject_code='A1'").fetchone()[0]
        for enrollment,name,course,semester in [
            ("S1","Both Low","Science",1),("S2","Attendance Low","Science",1),
            ("S3","Both Good","Science",1),("S4","Missing Attendance","Science",1),
            ("A1","Other Course","Arts",1),
        ]:
            c.execute("INSERT INTO students(enrollment_no,full_name,course_name,semester) VALUES(?,?,?,?)",
                      (enrollment,name,course,semester))
        ids={r["enrollment_no"]:r["id"] for r in c.execute("SELECT id,enrollment_no FROM students")}
        for enrollment,attended,subject in [
            ("S1",6,science_subject),("S2",70,science_subject),
            ("S3",80,science_subject),("A1",10,arts_subject),
        ]:
            c.execute("""INSERT INTO attendance(student_id,subject_id,period_date,classes_held,classes_attended)
                         VALUES(?,?,?,100,?)""",(ids[enrollment],subject,"2026-01-01",attended))
        for enrollment,marks,subject in [
            ("S1",30,science_subject),("S2",80,science_subject),
            ("S3",85,science_subject),("S4",90,science_subject),
            ("A1",90,arts_subject),
        ]:
            c.execute("""INSERT INTO results(student_id,subject_id,exam_type,term,marks_obtained,maximum_marks)
                         VALUES(?,?, 'Final', 'Term 1', ?,100)""",(ids[enrollment],subject,marks))

    rows=AnalyticsRepository(db).student_performance_rows("Science",1)
    risks={row["enrollment_no"]:row["risk_status"] for row in rows}
    assert risks=={
        "S1":"High","S2":"Medium","S3":"Low","S4":"Insufficient data"
    }
    filtered=DashboardData(db).load("Science",1)
    assert {row["enrollment_no"] for row in filtered["student_rows"]}=={"S1","S2","S3","S4"}
    assert filtered["bands"]=={"Low":1,"Medium":1,"High":1,"Insufficient data":1}
    assert filtered["performance"]["average_result"]==71.25

def test_admin_home_dashboard_uses_imported_attendance_and_fee_summaries(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        c.execute("INSERT INTO subjects(course_name,semester,subject_code,name) VALUES('Science',1,'PHY1','Physics')")
        c.execute("INSERT INTO students(enrollment_no,full_name,course_name,semester) VALUES('S1','Student One','Science',1)")
        student_id=c.execute("SELECT id FROM students WHERE enrollment_no='S1'").fetchone()[0]
        c.execute("""INSERT INTO student_import_summaries
            (student_id,attendance_percentage,result_percentage,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
            VALUES(?,?,?,?,?,?,?)""",(student_id,62.5,30.0,200000,75000,125000,"demo.xlsx"))

    data=DashboardData(db).load()
    assert data["attendance_count"]==1
    assert data["attendance_buckets"]["<75%"]==1
    assert data["high_risk"]==1
    assert data["imported_fees"]=={"total":2000,"submitted":750,"pending":1250}
    assert data["imported_fee_chart"][0]["course"]=="Science"

def test_dashboard_fee_totals_reconcile_imported_snapshot_with_new_payments(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize()
    with db.transaction() as c:
        c.execute("INSERT INTO courses(name) VALUES('Science')")
        c.execute("INSERT INTO students(enrollment_no,full_name,course_name,semester) VALUES('S1','Student One','Science',1)")
        student_id=c.execute("SELECT id FROM students WHERE enrollment_no='S1'").fetchone()[0]
        c.execute("""INSERT INTO student_import_summaries
            (student_id,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name,imported_at)
            VALUES(?,?,?,?,?,?)""",(student_id,200000,75000,125000,"demo.xlsx","2026-01-01T00:00:00.000Z"))
        c.execute("""INSERT INTO student_fee_import_summaries
            (student_id,term,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name,imported_at)
            VALUES(?,?,?,?,?,?,?)""",(student_id,"Semester 1",200000,75000,125000,"demo.xlsx","2026-01-01T00:00:00.000Z"))
    FeeRepository(db).record_payment(student_id,"100","2026-10-25","cash","Semester 1")

    data=DashboardData(db).load()

    assert data["collected"]==850
    assert data["outstanding"]==1150
    assert data["fee_chart"]==[{"course":"Science","collected":850,"outstanding":1150}]

def test_feature_whitelist_rejects_fee_and_sensitive_fields():
    assert validate_feature_columns(["attendance_pct","semester"])
    with pytest.raises(ValueError):validate_feature_columns(["attendance_pct","fee_due"])
    with pytest.raises(ValueError):validate_feature_columns(["gender"])
    assert rule_baseline(60,80,75)==1
    assert rule_baseline(80,35,75)==1
    assert rule_baseline(80,80,75)==0
    assert band_for_score(.2)=="Low" and band_for_score(.5)=="Medium" and band_for_score(.9)=="High"

def test_model_training_reproducible_and_baseline_reported(tmp_path):
    # Demonstration-only synthetic data; tests pipeline behavior, not real-world performance.
    rows=[]
    for i in range(60):
        attendance=45+(i*7)%55
        internal=(i*13)%101
        fail=int(attendance<65 or internal<35)
        rows.append({"attendance_pct":attendance,"internal_marks_pct":internal,
                     "subjects_below_threshold":int(attendance<75),"semester":2,"failed_any_subject":fail})
    frame=pd.DataFrame(rows)
    result=train_and_evaluate(frame,tmp_path,dataset_kind="synthetic_demo")
    report=result["report"]
    assert "logistic_regression" in report["models"]
    assert "attendance_baseline" in report["models"]
    assert set(["precision","recall","f1","roc_auc","confusion_matrix"]).issubset(report["models"]["logistic_regression"])
    assert result["active_source"] in {"logistic_regression","attendance_baseline"}
    assert (tmp_path/(report["version"]+"_evaluation.json")).exists()

def test_report_export_csv_and_xlsx(tmp_path):
    db=Database(tmp_path/"campusone.db");db.initialize();repo=AnalyticsRepository(db)
    csv_path=repo.export_report([{"student":"A","marks":80}],tmp_path/"r.csv")
    xlsx_path=repo.export_report([{"student":"A","marks":80}],tmp_path/"r.xlsx")
    assert csv_path.exists() and xlsx_path.exists()
