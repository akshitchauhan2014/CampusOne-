import sqlite3
from pathlib import Path

from campusone.db import Database


def test_v7_database_migrates_course_relations_to_names(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    migrations = [
        "001_initial.sql", "002_stage2_import_metadata.sql", "003_stage3_academics_fees.sql",
        "004_stage4_student_portal.sql", "005_stage5_analytics.sql", "006_stage6_operations.sql",
        "007_student_import_summaries.sql",
    ]
    migration_dir = Path(__file__).parents[1] / "campusone" / "migrations"
    for version, filename in enumerate(migrations, start=1):
        sql = (migration_dir / filename).read_text(encoding="utf-8")
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={version};\nCOMMIT;")
    conn.execute("INSERT INTO courses(id,name) VALUES(1,'Science')")
    conn.execute("INSERT INTO students(id,enrollment_no,full_name,course_id,semester) VALUES(1,'S1','Student One',1,1)")
    conn.execute("""INSERT INTO student_import_summaries
        (student_id,total_fees_paise,submitted_fees_paise,pending_fees_paise,source_name)
        VALUES(1,100000,25000,75000,'legacy-fees.xlsx')""")
    conn.execute("INSERT INTO subjects(id,course_id,semester,subject_code,name) VALUES(1,1,1,'SCI101','Science')")
    conn.execute("""INSERT INTO attendance(student_id,subject_id,period_date,classes_held,classes_attended)
                    VALUES(1,1,'2026-10-01',2,1)""")
    conn.execute("""INSERT INTO results(student_id,subject_id,exam_type,term,marks_obtained,maximum_marks)
                    VALUES(1,1,'Midterm','Term 1',80,100)""")
    conn.execute("""INSERT INTO fee_structures(course_id,term,fee_head,amount_paise)
                    VALUES(1,'Term 1','Tuition',100000)""")
    conn.commit()
    conn.close()

    db = Database(path)
    db.initialize()

    with db.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 10
        assert c.execute("SELECT course_name FROM students WHERE id=1").fetchone()[0] == "Science"
        assert c.execute("SELECT course_name FROM subjects WHERE id=1").fetchone()[0] == "Science"
        assert c.execute("SELECT course_name FROM fee_structures WHERE id=1").fetchone()[0] == "Science"
        assert tuple(c.execute("""SELECT term,total_fees_paise,submitted_fees_paise,source_name
            FROM student_fee_import_summaries WHERE student_id=1""").fetchone()) == (
                "Semester 1", 100000, 25000, "legacy-fees.xlsx"
            )
        assert c.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1
        for table in ("students", "subjects", "fee_structures"):
            columns = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
            assert "course_name" in columns
            assert "course_id" not in columns
        student_columns = {row["name"] for row in c.execute("PRAGMA table_info(students)")}
        assert "father_name" in student_columns
        assert c.execute("SELECT father_name FROM students WHERE id=1").fetchone()[0] is None
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
