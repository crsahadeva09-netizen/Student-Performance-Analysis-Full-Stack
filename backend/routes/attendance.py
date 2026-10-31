from datetime import date, timedelta
from flask import Blueprint, jsonify, request, session
from mysql.connector import Error
from database import get_db_connection

attendance_bp = Blueprint("attendance", __name__, url_prefix="/api/attendance")


@attendance_bp.before_request
def require_login():
    if request.method == "OPTIONS":
        return None
    if "user_id" not in session:
        return jsonify({"status": "error", "message": "Please log in first."}), 401


def attendance_status_from_pct(percentage):
    if percentage is None:
        return None
    pct = float(percentage)
    if pct > 75:
        return "Active"
    if pct >= 65:
        return "Warning"
    return "Inactive"


def ensure_attendance_schema(cursor, connection=None):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_records (
            attendance_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            attendance_date DATE NOT NULL,
            attendance_status ENUM('PRESENT', 'ABSENT') NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_attendance_records_student
                FOREIGN KEY (student_id) REFERENCES student(student_id)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CONSTRAINT uq_attendance_student_date
                UNIQUE (student_id, attendance_date),
            INDEX idx_attendance_date (attendance_date),
            INDEX idx_attendance_student (student_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subject_attendance (
            subject_attendance_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            branch_subject_id INT NOT NULL,
            attended_periods INT UNSIGNED NOT NULL DEFAULT 0,
            total_periods INT UNSIGNED NOT NULL DEFAULT 50,
            attendance_percentage DECIMAL(5,2) NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_sa_student
                FOREIGN KEY (student_id) REFERENCES student(student_id)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CONSTRAINT fk_sa_branch_subject
                FOREIGN KEY (branch_subject_id) REFERENCES branch_subjects(branch_subject_id)
                ON UPDATE CASCADE ON DELETE CASCADE,
            CONSTRAINT uq_sa_student_subject
                UNIQUE (student_id, branch_subject_id),
            CONSTRAINT chk_sa_percentage
                CHECK (attendance_percentage >= 0 AND attendance_percentage <= 100)
        )
    """)

    # Safely add attended_periods and total_periods columns if subject_attendance was created by the previous version
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'subject_attendance'
    """)
    existing_cols = {
        (row["COLUMN_NAME"] if isinstance(row, dict) else row[0])
        for row in cursor.fetchall()
    }

    altered = False
    if "attended_periods" not in existing_cols:
        cursor.execute(
            "ALTER TABLE subject_attendance ADD COLUMN attended_periods INT UNSIGNED NOT NULL DEFAULT 0 AFTER branch_subject_id"
        )
        altered = True
    if "total_periods" not in existing_cols:
        cursor.execute(
            "ALTER TABLE subject_attendance ADD COLUMN total_periods INT UNSIGNED NOT NULL DEFAULT 50 AFTER attended_periods"
        )
        altered = True

    if altered:
        # Backfill realistic period counts matching the stored attendance_percentage
        cursor.execute("""
            UPDATE subject_attendance
            SET total_periods = 45 + (branch_subject_id % 16),
                attended_periods = ROUND((attendance_percentage / 100.0) * (45 + (branch_subject_id % 16)))
            WHERE subject_attendance_id > 0
        """)
        cursor.execute("""
            UPDATE subject_attendance
            SET attendance_percentage = ROUND((attended_periods * 100.0) / NULLIF(total_periods, 0), 2)
            WHERE subject_attendance_id > 0 AND total_periods > 0
        """)
        if connection:
            connection.commit()

    # Populate initial distinct subject-wise period counts and percentages if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM subject_attendance")
    row = cursor.fetchone()
    cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)

    if cnt == 0:
        cursor.execute("""
            INSERT IGNORE INTO subject_attendance (
                student_id,
                branch_subject_id,
                attended_periods,
                total_periods,
                attendance_percentage
            )
            SELECT
                t.student_id,
                t.branch_subject_id,
                t.attended_periods,
                t.total_periods,
                ROUND((t.attended_periods * 100.0) / t.total_periods, 2) AS attendance_percentage
            FROM (
                SELECT
                    s.student_id,
                    bs.branch_subject_id,
                    (45 + ((bs.branch_subject_id * 3) % 16)) AS total_periods,
                    CASE
                        -- Inactive cohort (<65% average, varies by subject)
                        WHEN (s.student_id % 10) = 0 THEN
                            ROUND(((46.0 + ((s.student_id * 3 + bs.branch_subject_id * 7) % 18)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                        -- Warning cohort (65%-75% average, varies by subject)
                        WHEN (s.student_id % 6) = 0 THEN
                            ROUND(((65.0 + ((s.student_id * 5 + bs.branch_subject_id * 4) % 10)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                        -- Active cohort (>75% average, varies by subject)
                        ELSE
                            ROUND(((76.0 + ((s.student_id * 7 + bs.branch_subject_id * 9) % 23)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                    END AS attended_periods
                FROM student s
                JOIN branch_subjects bs
                    ON bs.branch_id = s.branch_id
                    AND bs.semester = s.semester
            ) t
        """)
        if connection:
            connection.commit()


@attendance_bp.route("", methods=["GET"])
def get_attendance():
    branch_id = request.args.get("branch_id", type=int)
    branch_subject_id = request.args.get("branch_subject_id", type=int)
    branch = request.args.get("branch", "").strip()
    semester = request.args.get("semester", "").strip()
    requested_status = request.args.get("status", "").strip().title()

    if requested_status and requested_status not in {"Active", "Warning", "Inactive"}:
        return jsonify({"status": "error", "message": "Invalid attendance status filter."}), 400
    if semester and (not semester.isdigit() or not 1 <= int(semester) <= 12):
        return jsonify({"status": "error", "message": "Semester must be between 1 and 12."}), 400

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        ensure_attendance_schema(cursor, connection)

        sem_val = int(semester) if semester else None

        if branch_subject_id and branch_subject_id > 0:
            query = """
                SELECT
                    s.student_id,
                    s.roll_no,
                    s.name,
                    s.branch_id,
                    b.branch_code,
                    s.section,
                    s.semester,
                    sub.subject_code,
                    sub.subject_name,
                    sa.attended_periods AS attended_periods,
                    sa.total_periods AS total_periods,
                    sa.attendance_percentage AS subj_pct,
                    COUNT(bs.branch_subject_id) AS total_subjects,
                    COUNT(sa.subject_attendance_id) AS recorded_subjects
                FROM student s
                JOIN branches b ON b.branch_id = s.branch_id
                JOIN branch_subjects bs
                    ON bs.branch_id = s.branch_id
                    AND bs.semester = s.semester
                    AND bs.branch_subject_id = %s
                JOIN subjects sub ON sub.subject_id = bs.subject_id
                LEFT JOIN subject_attendance sa
                    ON sa.student_id = s.student_id
                    AND sa.branch_subject_id = bs.branch_subject_id
                WHERE (%s IS NULL OR s.branch_id = %s)
                  AND (%s = '' OR b.branch_code = %s)
                  AND (%s = '' OR s.semester = %s)
                GROUP BY s.student_id, s.roll_no, s.name, s.branch_id, b.branch_code,
                         s.section, s.semester, sub.subject_code, sub.subject_name,
                         sa.attended_periods, sa.total_periods, sa.attendance_percentage
                ORDER BY s.student_id
            """
            cursor.execute(query, (branch_subject_id, branch_id, branch_id, branch, branch, semester, sem_val))
        else:
            query = """
                SELECT
                    s.student_id,
                    s.roll_no,
                    s.name,
                    s.branch_id,
                    b.branch_code,
                    s.section,
                    s.semester,
                    COUNT(DISTINCT bs.branch_subject_id) AS total_subjects,
                    COUNT(DISTINCT sa.subject_attendance_id) AS recorded_subjects,
                    SUM(COALESCE(sa.attended_periods, 0)) AS attended_periods,
                    SUM(COALESCE(sa.total_periods, 0)) AS total_periods,
                    AVG(sa.attendance_percentage) AS subj_pct
                FROM student s
                JOIN branches b ON b.branch_id = s.branch_id
                LEFT JOIN branch_subjects bs
                    ON bs.branch_id = s.branch_id
                    AND bs.semester = s.semester
                LEFT JOIN subject_attendance sa
                    ON sa.student_id = s.student_id
                    AND sa.branch_subject_id = bs.branch_subject_id
                WHERE (%s IS NULL OR s.branch_id = %s)
                  AND (%s = '' OR b.branch_code = %s)
                  AND (%s = '' OR s.semester = %s)
                GROUP BY s.student_id, s.roll_no, s.name, s.branch_id, b.branch_code, s.section, s.semester
                ORDER BY s.student_id
            """
            cursor.execute(query, (branch_id, branch_id, branch, branch, semester, sem_val))

        students = cursor.fetchall()

        for student in students:
            raw_pct = student.pop("subj_pct", None)
            total_subs = int(student.pop("total_subjects", None) or 0)
            rec_subs = int(student.pop("recorded_subjects", None) or 0)
            att_periods = int(student.pop("attended_periods", None) or 0)
            tot_periods = int(student.pop("total_periods", None) or 0)
            percentage = round(float(raw_pct), 2) if raw_pct is not None else None
            status = attendance_status_from_pct(percentage)

            student["total_subjects"] = total_subs
            student["recorded_subjects"] = rec_subs
            student["attended_periods"] = att_periods
            student["total_periods"] = tot_periods
            student["attendance_percentage"] = percentage
            student["status"] = status

        if requested_status:
            students = [s for s in students if s["status"] == requested_status]

        return jsonify({
            "status": "success",
            "students": students,
            "message": f"Showing attendance for {len(students)} student(s)."
        })
    except Error:
        return jsonify({"status": "error", "message": "Could not retrieve attendance records."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/student/<int:student_id>", methods=["GET"])
def get_student_attendance_summary(student_id):
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        cursor.execute("""
            SELECT student_id, roll_no, name, branch_id, semester
            FROM student
            WHERE student_id = %s
        """, (student_id,))
        student = cursor.fetchone()
        if not student:
            return jsonify({"status": "error", "message": "Student not found."}), 404

        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                s.subject_code,
                s.subject_name,
                sa.attended_periods,
                sa.total_periods,
                sa.attendance_percentage
            FROM branch_subjects bs
            JOIN subjects s ON s.subject_id = bs.subject_id
            LEFT JOIN subject_attendance sa
                ON sa.branch_subject_id = bs.branch_subject_id
                AND sa.student_id = %s
            WHERE bs.branch_id = %s AND bs.semester = %s
            ORDER BY s.subject_code
        """, (student_id, student["branch_id"], student["semester"]))
        subject_rows = cursor.fetchall()

        subjects_attendance = []
        recorded_pcts = []
        total_att_periods = 0
        total_max_periods = 0

        for row in subject_rows:
            pct = round(float(row["attendance_percentage"]), 2) if row["attendance_percentage"] is not None else None
            att_p = int(row["attended_periods"]) if row["attended_periods"] is not None else None
            tot_p = int(row["total_periods"]) if row["total_periods"] is not None else None

            if pct is not None:
                recorded_pcts.append(pct)
            if att_p is not None and tot_p is not None:
                total_att_periods += att_p
                total_max_periods += tot_p

            subjects_attendance.append({
                "branch_subject_id": row["branch_subject_id"],
                "subject_code": row["subject_code"],
                "subject_name": row["subject_name"],
                "attended_periods": att_p,
                "total_periods": tot_p,
                "attendance_percentage": pct,
                "status": attendance_status_from_pct(pct)
            })

        overall_pct = round(sum(recorded_pcts) / len(recorded_pcts), 2) if recorded_pcts else None

        return jsonify({
            "status": "success",
            "student": student,
            "summary": {
                "total_subjects": len(subject_rows),
                "recorded_subjects": len(recorded_pcts),
                "attended_periods": total_att_periods,
                "total_periods": total_max_periods,
                "attendance_percentage": overall_pct,
                "status": attendance_status_from_pct(overall_pct)
            },
            "subjects_attendance": subjects_attendance
        })
    except Error:
        return jsonify({"status": "error", "message": "Could not retrieve student subject attendance."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("", methods=["POST"])
def record_attendance():
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id")

    if not isinstance(student_id, int) or student_id <= 0:
        return jsonify({"status": "error", "message": "Select a valid student."}), 400

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        cursor.execute(
            "SELECT student_id, roll_no, name, branch_id, semester FROM student WHERE student_id = %s",
            (student_id,)
        )
        student = cursor.fetchone()
        if not student:
            return jsonify({"status": "error", "message": "Student not found."}), 404

        cursor.execute(
            """
            SELECT bs.branch_subject_id, s.subject_code
            FROM branch_subjects bs
            JOIN subjects s ON s.subject_id = bs.subject_id
            WHERE bs.branch_id = %s AND bs.semester = %s
            """,
            (student["branch_id"], student["semester"])
        )
        valid_subjects = {r["branch_subject_id"]: r["subject_code"] for r in cursor.fetchall()}

        def upsert_one_subject(bs_id, att_periods, tot_periods):
            if bs_id not in valid_subjects:
                return f"Subject ID {bs_id} does not belong to this student's branch."
            if tot_periods <= 0 or tot_periods > 500:
                return f"Total periods for {valid_subjects[bs_id]} must be between 1 and 500."
            if att_periods < 0 or att_periods > tot_periods:
                return f"Periods attended for {valid_subjects[bs_id]} must be between 0 and {tot_periods}."

            pct = round((att_periods * 100.0) / tot_periods, 2)
            cursor.execute(
                """
                INSERT INTO subject_attendance (
                    student_id,
                    branch_subject_id,
                    attended_periods,
                    total_periods,
                    attendance_percentage
                )
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    attended_periods = VALUES(attended_periods),
                    total_periods = VALUES(total_periods),
                    attendance_percentage = VALUES(attendance_percentage)
                """,
                (student_id, bs_id, att_periods, tot_periods, pct)
            )
            return None

        # Mode A: Batch of subject rows: [{"branch_subject_id": 10, "attended_periods": 42, "total_periods": 50}, ...]
        subject_records = data.get("subject_records") or data.get("subject_percentages")
        if isinstance(subject_records, list) and subject_records:
            updated_count = 0
            for item in subject_records:
                if not isinstance(item, dict):
                    continue
                raw_bs = item.get("branch_subject_id")
                raw_att = item.get("attended_periods")
                raw_tot = item.get("total_periods")

                if raw_bs is None or raw_att is None or raw_tot is None or raw_att == "" or raw_tot == "":
                    continue
                try:
                    bs_id = int(raw_bs)
                    att_p = int(raw_att)
                    tot_p = int(raw_tot)
                except (TypeError, ValueError):
                    return jsonify({"status": "error", "message": "Periods must be whole numbers."}), 400

                err = upsert_one_subject(bs_id, att_p, tot_p)
                if err:
                    connection.rollback()
                    return jsonify({"status": "error", "message": err}), 400
                updated_count += 1

            connection.commit()
            return jsonify({
                "status": "success",
                "message": f"Auto-saved attendance for {updated_count} subject(s) of {student['name']} ({student['roll_no']})."
            }), 201

        # Mode B: Single subject auto-save: {"student_id": 1, "branch_subject_id": 10, "attended_periods": 42, "total_periods": 50}
        raw_bs = data.get("branch_subject_id")
        raw_att = data.get("attended_periods")
        raw_tot = data.get("total_periods")
        if raw_bs is not None and raw_att is not None and raw_tot is not None:
            try:
                bs_id = int(raw_bs)
                att_p = int(raw_att)
                tot_p = int(raw_tot)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Periods must be whole numbers."}), 400

            err = upsert_one_subject(bs_id, att_p, tot_p)
            if err:
                return jsonify({"status": "error", "message": err}), 400

            connection.commit()
            pct = round((att_p * 100.0) / tot_p, 2)
            return jsonify({
                "status": "success",
                "attendance_percentage": pct,
                "status_label": attendance_status_from_pct(pct),
                "message": f"Auto-saved {valid_subjects[bs_id]}: {att_p}/{tot_p} periods ({pct}%) for {student['name']}."
            }), 201

        return jsonify({
            "status": "error",
            "message": "Provide attended_periods and total_periods for the subject."
        }), 400

    except Error:
        if connection:
            connection.rollback()
        return jsonify({"status": "error", "message": "Could not save attendance record."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()