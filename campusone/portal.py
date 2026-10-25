from __future__ import annotations
import secrets, string, sqlite3
from datetime import datetime, timedelta, timezone
from .security import hash_secret, verify_secret
from .academics import AcademicValidationError
from .auth import _dummy_verify

REGISTRATION_LOCK_ATTEMPTS = 5
REGISTRATION_LOCK_MINUTES = 15
STUDENT_LOGIN_ATTEMPTS = 5
STUDENT_LOGIN_LOCK_MINUTES = 15
DEFAULT_CODE_TTL_DAYS = 14
RESET_LOCK_ATTEMPTS = 5
RESET_LOCK_MINUTES = 15
BLOCKED_STUDENT_STATUSES = ("inactive", "withdrawn")   # Confirm this policy with the college before deployment.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def utcnow(): return datetime.now(timezone.utc)
def stamp(dt): return dt.astimezone(timezone.utc).isoformat()
def parse_stamp(s): return datetime.fromisoformat(s)
def new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))

class PortalError(ValueError): pass

def check_password_policy(password):
    """Single password policy for registration and reset (S4-FR-15; values are a draft pending approval)."""
    if len(password or "") < 12 or not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise PortalError("Password must contain at least 12 characters and include a letter and a number.")

class StudentPortalService:
    def __init__(self, db): self.db=db

    def issue_registration_code(self, student_id, admin_user_id, ttl_days=DEFAULT_CODE_TTL_DAYS):
        try: ttl=int(ttl_days)
        except (TypeError,ValueError): raise PortalError("Expiry must be a whole number of days.")
        if not 1 <= ttl <= 365: raise PortalError("Expiry must be between 1 and 365 days.")
        code=new_code()
        with self.db.transaction() as c:
            student=c.execute("SELECT id FROM students WHERE id=?",(student_id,)).fetchone()
            if not student: raise PortalError("Student record was not found.")
            if c.execute("SELECT 1 FROM users WHERE student_id=?",(student_id,)).fetchone():
                raise PortalError("This student already has a portal account.")
            c.execute("UPDATE registration_codes SET revoked_at=? WHERE student_id=? AND used_at IS NULL AND revoked_at IS NULL",
                      (stamp(utcnow()),student_id))
            c.execute("INSERT INTO registration_codes(student_id,code_hash,expires_at,issued_by) VALUES(?,?,?,?)",
                      (student_id,hash_secret(code),stamp(utcnow()+timedelta(days=ttl)),admin_user_id))
            self.db.write_audit(c,"registration_code_issued","student",student_id,details=f"Expiry days: {ttl}; code omitted",actor_id=admin_user_id)
        return code

    def revoke_registration_code(self, student_id, admin_user_id):
        with self.db.transaction() as c:
            cur=c.execute("UPDATE registration_codes SET revoked_at=? WHERE student_id=? AND used_at IS NULL AND revoked_at IS NULL",
                          (stamp(utcnow()),student_id))
            self.db.write_audit(c,"registration_code_revoked","student",student_id,details=f"{cur.rowcount} active code(s) revoked",actor_id=admin_user_id)
            return cur.rowcount

    def _registration_lock(self, c, enrollment):
        row=c.execute("SELECT failed_attempts,locked_until FROM registration_attempts WHERE enrollment_no=?",(enrollment,)).fetchone()
        if row and row["locked_until"]:
            try:
                if utcnow() < parse_stamp(row["locked_until"]): return True
            except ValueError: return True
        return False

    def verify_registration(self, enrollment_no, code):
        enrollment=str(enrollment_no or "").strip()[:80]
        # Return one generic error for unknown, used, expired, revoked and mismatched code.
        with self.db.transaction() as c:
            if self._registration_lock(c,enrollment):
                return False, "Unable to verify registration details.", None
            student=c.execute("""SELECT s.id,s.enrollment_no,s.full_name,s.course_name,s.status
                FROM students s WHERE s.enrollment_no=?""",(enrollment,)).fetchone()
            valid=False
            if student and student["status"] not in BLOCKED_STUDENT_STATUSES and not c.execute("SELECT 1 FROM users WHERE student_id=?",(student["id"],)).fetchone():
                codes=c.execute("""SELECT * FROM registration_codes WHERE student_id=? AND used_at IS NULL AND revoked_at IS NULL
                    ORDER BY id DESC""",(student["id"],)).fetchall()
                for row in codes:
                    if utcnow() >= parse_stamp(row["expires_at"]): continue
                    if verify_secret(row["code_hash"],str(code or "")):
                        valid=True; break
            if valid:
                c.execute("DELETE FROM registration_attempts WHERE enrollment_no=?",(enrollment,))
                return True, "Verified.", {"student_id":student["id"],"enrollment_no":student["enrollment_no"],
                    "full_name":student["full_name"],"course_name":student["course_name"]}
            row=c.execute("SELECT failed_attempts FROM registration_attempts WHERE enrollment_no=?",(enrollment,)).fetchone()
            attempts=(row["failed_attempts"] if row else 0)+1
            locked=stamp(utcnow()+timedelta(minutes=REGISTRATION_LOCK_MINUTES)) if attempts>=REGISTRATION_LOCK_ATTEMPTS else None
            if locked: attempts=0
            c.execute("""INSERT INTO registration_attempts(enrollment_no,failed_attempts,locked_until) VALUES(?,?,?)
                ON CONFLICT(enrollment_no) DO UPDATE SET failed_attempts=excluded.failed_attempts,locked_until=excluded.locked_until""",
                (enrollment,attempts,locked))
            self.db.write_audit(c,"registration_verification_locked" if locked else "registration_verification_failed","student",
                                enrollment[:80] or None,details="Failed registration verification; secret omitted",actor_id=None)
            return False, "Unable to verify registration details.", None

    def complete_registration(self, verified_student_id, enrollment_no, code, password, confirm_password):
        enrollment=str(enrollment_no or "").strip()
        if password != confirm_password: raise PortalError("Passwords do not match.")
        check_password_policy(password)
        generic=PortalError("Registration could not be completed. Verify the details again.")
        bad_code=False
        with self.db.transaction() as c:
            if self._registration_lock(c,enrollment): raise generic
            student=c.execute("SELECT id,enrollment_no,status FROM students WHERE id=? AND enrollment_no=?",(verified_student_id,enrollment)).fetchone()
            if student and student["status"] in BLOCKED_STUDENT_STATUSES: student=None
            if not student: raise generic
            if c.execute("SELECT 1 FROM users WHERE student_id=?",(verified_student_id,)).fetchone():
                raise PortalError("This student already has a portal account.")
            codes=c.execute("""SELECT * FROM registration_codes WHERE student_id=? AND used_at IS NULL AND revoked_at IS NULL
                ORDER BY id DESC""",(verified_student_id,)).fetchall()
            matched=None
            for row in codes:
                if utcnow()<parse_stamp(row["expires_at"]) and verify_secret(row["code_hash"],str(code or "")):
                    matched=row; break
            if not matched:
                # Same throttle as verify_registration, so this method cannot be used to guess codes around the lockout.
                row=c.execute("SELECT failed_attempts FROM registration_attempts WHERE enrollment_no=?",(enrollment,)).fetchone()
                attempts=(row["failed_attempts"] if row else 0)+1
                locked=stamp(utcnow()+timedelta(minutes=REGISTRATION_LOCK_MINUTES)) if attempts>=REGISTRATION_LOCK_ATTEMPTS else None
                if locked: attempts=0
                c.execute("""INSERT INTO registration_attempts(enrollment_no,failed_attempts,locked_until) VALUES(?,?,?)
                    ON CONFLICT(enrollment_no) DO UPDATE SET failed_attempts=excluded.failed_attempts,locked_until=excluded.locked_until""",
                    (enrollment,attempts,locked))
                self.db.write_audit(c,"registration_completion_failed","student",enrollment,details="Failed registration completion; secret omitted")
                bad_code=True
            else:
                # Account creation and code consumption commit together (S4-FR-11).
                cur=c.execute("""INSERT INTO users(username,role,password_hash,student_id) VALUES(?, 'student', ?, ?)""",
                              (enrollment,hash_secret(password),verified_student_id))
                c.execute("UPDATE registration_codes SET used_at=? WHERE id=?",(stamp(utcnow()),matched["id"]))
                c.execute("UPDATE registration_codes SET revoked_at=? WHERE student_id=? AND id<>? AND used_at IS NULL AND revoked_at IS NULL",
                          (stamp(utcnow()),verified_student_id,matched["id"]))
                c.execute("DELETE FROM registration_attempts WHERE enrollment_no=?",(enrollment,))
                self.db.write_audit(c,"student_registered","user",cur.lastrowid,details="Student portal account created",actor_id=cur.lastrowid)
                new_id=int(cur.lastrowid)
        # Raise only after commit so the failure counter is persisted.
        if bad_code: raise generic
        return new_id

    def student_login(self, enrollment_no, password):
        enrollment=str(enrollment_no or "").strip()
        with self.db.connect() as c:
            u=c.execute("""SELECT u.id,u.username,u.role,u.password_hash,u.student_id,u.is_active,u.failed_attempts,u.locked_until,
                s.status AS student_status FROM users u JOIN students s ON s.id=u.student_id
                WHERE u.username=? AND u.role='student'""",(enrollment,)).fetchone()
        if not u:
            _dummy_verify(password); return False,"Unable to sign in with those credentials.",None
        if not u["is_active"] or u["student_status"] in BLOCKED_STUDENT_STATUSES: return False,"Unable to sign in with those credentials.",None
        if u["locked_until"]:
            try:
                if utcnow()<parse_stamp(u["locked_until"]): return False,"Unable to sign in with those credentials.",None
            except ValueError: return False,"Unable to sign in with those credentials.",None
        if not verify_secret(u["password_hash"],str(password or "")):
            attempts=u["failed_attempts"]+1; lock=None
            if attempts>=STUDENT_LOGIN_ATTEMPTS:
                attempts=0; lock=stamp(utcnow()+timedelta(minutes=STUDENT_LOGIN_LOCK_MINUTES))
            with self.db.transaction() as c:
                c.execute("UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?",(attempts,lock,u["id"]))
                self.db.write_audit(c,"student_login_locked" if lock else "student_login_failed","user",u["id"],details="Failed login; credential omitted",actor_id=u["id"])
            return False,"Unable to sign in with those credentials.",None
        with self.db.transaction() as c:
            c.execute("UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?",(u["id"],))
            self.db.write_audit(c,"student_login_success","user",u["id"],actor_id=u["id"])
        return True,"Signed in.",{"id":u["id"],"username":u["username"],"role":"student","student_id":u["student_id"]}

    def issue_reset_code(self, student_id, admin_user_id, ttl_minutes=30):
        try: ttl_minutes=int(ttl_minutes)
        except (TypeError,ValueError): raise PortalError("Expiry must be a whole number of minutes.")
        if not 5 <= ttl_minutes <= 24*60: raise PortalError("Reset-code expiry must be between 5 minutes and 24 hours.")
        code=new_code()
        with self.db.transaction() as c:
            user=c.execute("SELECT id FROM users WHERE student_id=? AND role='student'",(student_id,)).fetchone()
            if not user: raise PortalError("Student portal account was not found.")
            c.execute("UPDATE password_reset_codes SET revoked_at=? WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL",
                      (stamp(utcnow()),user["id"]))
            c.execute("INSERT INTO password_reset_codes(user_id,code_hash,expires_at,issued_by) VALUES(?,?,?,?)",
                      (user["id"],hash_secret(code),stamp(utcnow()+timedelta(minutes=ttl_minutes)),admin_user_id))
            self.db.write_audit(c,"password_reset_code_issued","user",user["id"],details="Identity verification completed by administrator; code omitted",actor_id=admin_user_id)
        return code

    def reset_password(self, enrollment_no, code, password, confirm_password):
        if password!=confirm_password: raise PortalError("Passwords do not match.")
        check_password_policy(password)
        enrollment=str(enrollment_no or "").strip()[:80]
        gate="reset:"+enrollment          # separate counter from registration attempts
        generic=PortalError("Password reset could not be completed. Check the details or contact the administrator.")
        failed=False
        with self.db.transaction() as c:
            if self._registration_lock(c,gate): raise generic
            user=c.execute("""SELECT u.id,u.student_id,s.status FROM users u JOIN students s ON s.id=u.student_id
                WHERE u.username=? AND u.role='student'""",(enrollment,)).fetchone()
            matched=None
            if user and user["status"] not in BLOCKED_STUDENT_STATUSES:
                codes=c.execute("SELECT * FROM password_reset_codes WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL ORDER BY id DESC",(user["id"],)).fetchall()
                for row in codes:
                    if utcnow()<parse_stamp(row["expires_at"]) and verify_secret(row["code_hash"],str(code or "")):
                        matched=row; break
            if not matched:
                # Throttle guessing: 5 failures lock reset for this enrollment number for 15 minutes.
                row=c.execute("SELECT failed_attempts FROM registration_attempts WHERE enrollment_no=?",(gate,)).fetchone()
                attempts=(row["failed_attempts"] if row else 0)+1
                locked=stamp(utcnow()+timedelta(minutes=RESET_LOCK_MINUTES)) if attempts>=RESET_LOCK_ATTEMPTS else None
                if locked: attempts=0
                c.execute("""INSERT INTO registration_attempts(enrollment_no,failed_attempts,locked_until) VALUES(?,?,?)
                    ON CONFLICT(enrollment_no) DO UPDATE SET failed_attempts=excluded.failed_attempts,locked_until=excluded.locked_until""",
                    (gate,attempts,locked))
                self.db.write_audit(c,"password_reset_locked" if locked else "password_reset_failed","user",
                                    user["id"] if user else None,details="Failed reset attempt; secret omitted",actor_id=user["id"] if user else None)
                failed=True
            else:
                c.execute("UPDATE users SET password_hash=?,failed_attempts=0,locked_until=NULL WHERE id=?",(hash_secret(password),user["id"]))
                c.execute("UPDATE password_reset_codes SET used_at=? WHERE id=?",(stamp(utcnow()),matched["id"]))
                c.execute("UPDATE password_reset_codes SET revoked_at=? WHERE user_id=? AND id<>? AND used_at IS NULL AND revoked_at IS NULL",
                          (stamp(utcnow()),user["id"],matched["id"]))
                c.execute("DELETE FROM registration_attempts WHERE enrollment_no=?",(gate,))
                self.db.write_audit(c,"student_password_reset","user",user["id"],details="Password reset completed; secrets omitted",actor_id=user["id"])
        # Raise only after the transaction commits, otherwise the failure counter itself would roll back.
        if failed: raise generic

    # Authorization is enforced here: callers provide the authenticated session, not a form-supplied student id.
    def own_profile(self, session):
        self._require_student(session)
        with self.db.connect() as c:
            row=c.execute("""SELECT s.id,s.enrollment_no,s.full_name,s.email,s.phone,s.batch,s.semester,s.status,
                s.course_name FROM users u JOIN students s ON s.id=u.student_id
                WHERE u.id=? AND u.role='student' AND u.is_active=1""",
                (session["id"],)).fetchone()
        if not row: raise PermissionError("Student profile is not available.")
        return dict(row)

    def own_attendance(self, session):
        sid=self._require_student(session)
        with self.db.connect() as c:
            rows=[dict(r) for r in c.execute("""SELECT sub.subject_code,sub.name AS subject_name,
                SUM(a.classes_held) AS classes_held,SUM(a.classes_attended) AS classes_attended
                FROM attendance a JOIN subjects sub ON sub.id=a.subject_id
                WHERE a.student_id=? GROUP BY a.subject_id ORDER BY sub.subject_code""",(sid,)).fetchall()]
        for r in rows:r["percentage"]=round(r["classes_attended"]/r["classes_held"]*100,2) if r["classes_held"] else None
        return rows

    def own_results(self, session):
        sid=self._require_student(session)
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("""SELECT sub.subject_code,sub.name AS subject_name,r.exam_type,r.term,
                r.marks_obtained,r.maximum_marks,ROUND(r.marks_obtained/r.maximum_marks*100,2) AS percentage
                FROM results r JOIN subjects sub ON sub.id=r.subject_id
                WHERE r.student_id=? ORDER BY r.term,sub.subject_code,r.exam_type""",(sid,)).fetchall()]

    def own_fee_status(self, session, policy_allows=False):
        if not policy_allows: raise PermissionError("Student fee status is not enabled by college policy.")
        sid=self._require_student(session)
        with self.db.connect() as c:
            student=c.execute("SELECT course_name FROM students WHERE id=?",(sid,)).fetchone()
            if not student: raise PermissionError("Student profile is not available.")
            terms=[r["term"] for r in c.execute("SELECT DISTINCT term FROM fee_structures WHERE course_name=?",(student["course_name"],)).fetchall()]
        from .fees import FeeRepository
        return [FeeRepository(self.db).student_balance(sid,t) for t in terms]

    def _require_student(self, session):
        if not session or session.get("role") != "student" or not session.get("id"):
            raise PermissionError("Student access required.")
        # Resolve the student reference from the persisted authenticated user,
        # never from a caller-controlled student_id field.
        with self.db.connect() as c:
            row = c.execute("SELECT student_id FROM users WHERE id=? AND role='student' AND is_active=1",
                            (int(session["id"]),)).fetchone()
        if not row or row["student_id"] is None:
            raise PermissionError("Student access required.")
        return int(row["student_id"])
