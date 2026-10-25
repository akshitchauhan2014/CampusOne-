from __future__ import annotations
import json, math
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from .security import safe_export_value
from .fees import FeeRepository

APPROVED_FEATURES = [
    "attendance_pct", "subjects_below_threshold", "attendance_trend",
    "internal_marks_pct", "assessment_trend", "previous_result_pct",
    "backlog_count", "course", "semester"
]
FORBIDDEN_FEATURES = {
    "gender","religion","caste","phone","phone_number","parent_name","parents_name",
    "family_income","income","fee","fees","fee_due","fee_paid","outstanding_fee",
    "risk_score","risk_band","student_name","enrollment_no","email"
}
TARGET = "failed_any_subject"
ACADEMIC_THRESHOLD = 40.0

def validate_feature_columns(columns):
    cols=list(columns)
    bad=[c for c in cols if c not in APPROVED_FEATURES]
    if bad: raise ValueError("Unapproved or prohibited feature columns: "+", ".join(bad))
    if not cols: raise ValueError("No approved feature columns were supplied.")
    return cols

def rule_baseline(attendance_pct, academic_pct, attendance_threshold=75.0,
                  academic_threshold=ACADEMIC_THRESHOLD):
    if attendance_pct is None or academic_pct is None:
        return 1
    attendance_pct, academic_pct = float(attendance_pct), float(academic_pct)
    if not math.isfinite(attendance_pct) or not math.isfinite(academic_pct):
        return 1
    return int(attendance_pct < float(attendance_threshold) or
               academic_pct < float(academic_threshold))

def performance_risk(attendance_pct, result_pct, attendance_threshold=75.0,
                     academic_threshold=ACADEMIC_THRESHOLD):
    if attendance_pct is None or result_pct is None:
        missing = []
        if attendance_pct is None: missing.append("attendance")
        if result_pct is None: missing.append("exam result")
        return "Insufficient data", "Missing " + " and ".join(missing)
    low_attendance = float(attendance_pct) < float(attendance_threshold)
    low_result = float(result_pct) < float(academic_threshold)
    if low_attendance and low_result:
        return "High", f"Exam result below {academic_threshold:g}% and attendance below {attendance_threshold:g}%"
    if low_attendance:
        return "Medium", f"Attendance below {attendance_threshold:g}%"
    if low_result:
        return "Medium", f"Exam result below {academic_threshold:g}%"
    return "Low", "Exam result and attendance meet thresholds"

def band_for_score(score):
    # Draft operational cutoffs; must be approved by the college before production use.
    score=float(score)
    if score < 0.33:return "Low"
    if score < 0.66:return "Medium"
    return "High"

def _json_metrics(metrics):
    def clean(v):
        if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        if hasattr(v,"item"):return clean(v.item())
        if isinstance(v,float) and (math.isnan(v) or math.isinf(v)):return None
        return v
    return json.dumps(clean(metrics),sort_keys=True)

class AnalyticsRepository:
    def __init__(self,db):self.db=db

    def filters(self):
        with self.db.connect() as c:
            courses=[dict(r) for r in c.execute("SELECT id,name FROM courses ORDER BY name")]
        return courses

    def student_performance_rows(self, course_name=None, semester=None):
        attendance_sql = """SELECT a.student_id,
                SUM(a.classes_attended)*100.0/NULLIF(SUM(a.classes_held),0) AS attendance_pct
            FROM attendance a
            JOIN students st ON st.id=a.student_id
            JOIN subjects sub ON sub.id=a.subject_id AND sub.course_name=st.course_name
            WHERE 1=1"""
        result_sql = """SELECT r.student_id,
                AVG(r.marks_obtained/r.maximum_marks*100) AS result_pct
            FROM results r
            JOIN students st ON st.id=r.student_id
            JOIN subjects sub ON sub.id=r.subject_id AND sub.course_name=st.course_name
            WHERE 1=1"""
        args=[]
        if semester is not None:
            attendance_sql += " AND sub.semester=?"
            result_sql += " AND sub.semester=?"
            args.extend([semester, semester])
        attendance_sql += " GROUP BY a.student_id"
        result_sql += " GROUP BY r.student_id"
        sql = f"""SELECT s.id,s.enrollment_no,s.full_name,s.course_name AS course,s.semester,
                COALESCE(a.attendance_pct,x.attendance_percentage) AS attendance_pct,
                COALESCE(r.result_pct,x.result_percentage) AS result_pct
            FROM students s
            LEFT JOIN ({attendance_sql}) a ON a.student_id=s.id
            LEFT JOIN ({result_sql}) r ON r.student_id=s.id
            LEFT JOIN student_import_summaries x ON x.student_id=s.id
            WHERE s.status='active'"""
        if course_name is not None:
            sql += " AND s.course_name=?"
            args.append(course_name)
        if semester is not None:
            sql += " AND s.semester=?"
            args.append(semester)
        sql += " ORDER BY s.full_name,s.enrollment_no"
        from .academics import AcademicRepository
        attendance_threshold = AcademicRepository(self.db).get_low_attendance_threshold()
        with self.db.connect() as c:
            rows = [dict(r) for r in c.execute(sql,args).fetchall()]
        for row in rows:
            row["risk_status"], row["risk_reason"] = performance_risk(
                row["attendance_pct"], row["result_pct"], attendance_threshold
            )
            row["overall_pct"] = (
                round((row["attendance_pct"] + row["result_pct"]) / 2, 2)
                if row["attendance_pct"] is not None and row["result_pct"] is not None else None
            )
        return rows

    def admin_summary(self,course_name=None,semester=None):
        with self.db.connect() as c:
            student_sql="SELECT COUNT(*) FROM students WHERE 1=1"; args=[]
            if course_name is not None:student_sql+=" AND course_name=?";args.append(course_name)
            if semester is not None:student_sql+=" AND semester=?";args.append(semester)
            student_count=c.execute(student_sql,args).fetchone()[0]
            account_sql="""SELECT COUNT(*) FROM users u JOIN students s ON s.id=u.student_id WHERE u.role='student'"""
            aa=[]
            if course_name is not None:account_sql+=" AND s.course_name=?";aa.append(course_name)
            if semester is not None:account_sql+=" AND s.semester=?";aa.append(semester)
            account_count=c.execute(account_sql,aa).fetchone()[0]
            imports=[dict(r) for r in c.execute("""SELECT source_name,started_at,status,rows_seen,rows_imported,rows_rejected
                FROM import_logs ORDER BY id DESC LIMIT 8""").fetchall()]
            summary_sql="""SELECT COUNT(x.attendance_percentage) AS attendance_students,
                AVG(x.attendance_percentage) AS average_attendance,
                COUNT(x.result_percentage) AS result_students,AVG(x.result_percentage) AS average_result,
                COALESCE(SUM(x.total_fees_paise),0) AS total_fees_paise,
                COALESCE(SUM(x.submitted_fees_paise),0) AS submitted_fees_paise,
                COALESCE(SUM(x.pending_fees_paise),0) AS pending_fees_paise
                FROM student_import_summaries x JOIN students s ON s.id=x.student_id WHERE 1=1"""
            summary_args=[]
            if course_name is not None:summary_sql+=" AND s.course_name=?";summary_args.append(course_name)
            if semester is not None:summary_sql+=" AND s.semester=?";summary_args.append(semester)
            imported=dict(c.execute(summary_sql,summary_args).fetchone())
        fees=FeeRepository(self.db).fee_totals(course_name,semester)
        imported["total_fees"]=imported.pop("total_fees_paise")/100
        imported["submitted_fees"]=imported.pop("submitted_fees_paise")/100
        imported["pending_fees"]=imported.pop("pending_fees_paise")/100
        return {"students":student_count,"registered_accounts":account_count,"fee_collected":round(fees["collected"],2),
                "outstanding_fees":round(fees["outstanding"],2),"recent_imports":imports,
                "imported_summary":imported}

    def academic_summary(self,course_name=None,semester=None):
        with self.db.connect() as c:
            marks_sql="""SELECT sub.name AS subject,sub.semester,AVG(r.marks_obtained/r.maximum_marks*100) AS average_pct,
                COUNT(*) AS assessments FROM results r JOIN subjects sub ON sub.id=r.subject_id
                JOIN students s ON s.id=r.student_id WHERE 1=1"""
            args=[]
            if course_name is not None:marks_sql+=" AND s.course_name=?";args.append(course_name)
            if semester is not None:marks_sql+=" AND sub.semester=?";args.append(semester)
            marks_sql+=" GROUP BY sub.id ORDER BY sub.semester,sub.name"
            marks=[dict(r) for r in c.execute(marks_sql,args).fetchall()]
            att_sql="""SELECT sub.name AS subject,sub.semester,
                SUM(a.classes_attended)*100.0/NULLIF(SUM(a.classes_held),0) AS attendance_pct,
                COUNT(DISTINCT a.student_id) AS students FROM attendance a JOIN subjects sub ON sub.id=a.subject_id
                JOIN students s ON s.id=a.student_id WHERE 1=1"""
            aa=[]
            if course_name is not None:att_sql+=" AND s.course_name=?";aa.append(course_name)
            if semester is not None:att_sql+=" AND sub.semester=?";aa.append(semester)
            att_sql+=" GROUP BY sub.id ORDER BY sub.semester,sub.name"
            attendance=[dict(r) for r in c.execute(att_sql,aa).fetchall()]
            trend_sql="""SELECT r.term,AVG(r.marks_obtained/r.maximum_marks*100) AS average_pct,
                COUNT(*) AS assessments FROM results r JOIN students s ON s.id=r.student_id
                JOIN subjects sub ON sub.id=r.subject_id WHERE 1=1"""
            ta=[]
            if course_name is not None:trend_sql+=" AND s.course_name=?";ta.append(course_name)
            if semester is not None:trend_sql+=" AND sub.semester=?";ta.append(semester)
            trend_sql+=" GROUP BY r.term ORDER BY MIN(r.recorded_at)"
            trends=[dict(r) for r in c.execute(trend_sql,ta).fetchall()]
            # Pass rate is supportable only where exam_type clearly denotes final/end-semester.
            # NOTE the parentheses: without them AND bound tighter than OR and the course filter
            # silently applied to only one of the two exam-type patterns.
            pass_sql="""SELECT r.term,AVG(CASE WHEN r.marks_obtained/r.maximum_marks>=0.4 THEN 1.0 ELSE 0.0 END)*100 AS pass_pct,
                COUNT(*) AS assessments FROM results r JOIN students s ON s.id=r.student_id
                JOIN subjects sub ON sub.id=r.subject_id
                WHERE (lower(r.exam_type) LIKE '%final%' OR lower(r.exam_type) LIKE '%end-semester%' OR lower(r.exam_type) LIKE '%end semester%')"""
            pa=[]
            if course_name is not None:pass_sql+=" AND s.course_name=?";pa.append(course_name)
            if semester is not None:pass_sql+=" AND sub.semester=?";pa.append(semester)
            pass_sql+=" GROUP BY r.term ORDER BY MIN(r.recorded_at)"
            passes=[dict(r) for r in c.execute(pass_sql,pa).fetchall()]
        return {"marks_by_subject":marks,"attendance_by_subject":attendance,"term_trend":trends,"pass_rates":passes}

    def own_dashboard(self,service,session,fee_policy=False):
        # Stage 4 service applies authenticated-user identity restrictions.
        profile=service.own_profile(session)
        attendance=service.own_attendance(session);results=service.own_results(session)
        trends={}
        for r in results:trends.setdefault(r["term"],[]).append(r["percentage"])
        trends=[{"term":k,"average_pct":round(sum(v)/len(v),2)} for k,v in trends.items()]
        fees=service.own_fee_status(session,policy_allows=fee_policy) if fee_policy else []
        return {"profile":profile,"attendance":attendance,"results":results,"term_trends":trends,"fees":fees}

    def export_report(self,rows,path,report_name="CampusOne report"):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix.lower() not in (".xlsx",".csv"): raise ValueError("Supported report formats are .xlsx and .csv.")
        # Student names/ids originate from imports, so every exported cell is formula-sanitised.
        frame=pd.DataFrame([{str(k):safe_export_value(v) for k,v in dict(r).items()} for r in rows])
        if path.suffix.lower()==".xlsx":
            with pd.ExcelWriter(path,engine="openpyxl") as writer:
                frame.to_excel(writer,index=False,sheet_name="Report")
                pd.DataFrame([{"Report":report_name,"Generated UTC":datetime.now(timezone.utc).isoformat(),
                               "Rows":len(frame)}]).to_excel(writer,index=False,sheet_name="Metadata")
        elif path.suffix.lower()==".csv":
            frame.to_csv(path,index=False)
        else: raise ValueError("Supported report formats are .xlsx and .csv.")
        return path

    def active_model(self):
        with self.db.connect() as c:
            r=c.execute("SELECT * FROM model_runs WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
        if not r:return None
        d=dict(r)
        try:d["model_sha256"]=json.loads(d["metrics_json"]).get("model_sha256")
        except (TypeError,ValueError):d["model_sha256"]=None
        return d

    def save_model_run(self,version,algorithm,n,metrics,dataset_kind,model_path=None,activate=False):
        if dataset_kind not in {"historical","synthetic_demo"}:raise ValueError("Unknown dataset kind.")
        with self.db.transaction() as c:
            if activate:c.execute("UPDATE model_runs SET active=0")
            c.execute("""INSERT INTO model_runs(version,algorithm,training_data_size,metrics_json,dataset_kind,model_path,active)
                VALUES(?,?,?,?,?,?,?)""",(version,algorithm,int(n),_json_metrics(metrics),dataset_kind,model_path,int(activate)))
            rid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.write_audit(c,"model_training_recorded","model_run",rid,
                                details=f"{algorithm}; version={version}; dataset={dataset_kind}; n={n}; active={bool(activate)}")
        return rid

    def model_runs(self):
        with self.db.connect() as c:return [dict(r) for r in c.execute("SELECT * FROM model_runs ORDER BY id DESC").fetchall()]

    def risk_rows(self,course_name=None,semester=None,band=None):
        sql="""SELECT p.student_id,s.enrollment_no,s.full_name,s.course_name,s.semester,p.score,p.band,
            p.factors_json,p.model_version,p.source,p.created_at FROM risk_predictions p
            JOIN students s ON s.id=p.student_id WHERE p.id IN
            (SELECT MAX(id) FROM risk_predictions GROUP BY student_id)"""
        args=[]
        if course_name is not None:sql+=" AND s.course_name=?";args.append(course_name)
        if semester is not None:sql+=" AND s.semester=?";args.append(semester)
        if band:sql+=" AND p.band=?";args.append(band)
        sql+=" ORDER BY p.score DESC,s.full_name"
        with self.db.connect() as c:return [dict(r) for r in c.execute(sql,args).fetchall()]

    def save_predictions(self,predictions,version,source):
        with self.db.transaction() as c:
            c.execute("DELETE FROM risk_predictions")
            for p in predictions:
                c.execute("""INSERT INTO risk_predictions(student_id,score,band,factors_json,model_version,source)
                    VALUES(?,?,?,?,?,?)""",(p["student_id"],p["score"],p["band"],json.dumps(p["factors"]),version,source))
            self.db.write_audit(c,"risk_predictions_generated","risk_prediction",None,
                                details=f"Count={len(predictions)}; source={source}; version={version}")
