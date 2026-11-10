from datetime import date, timedelta
import io
import csv
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

from flask import Blueprint, jsonify, request, session, send_file
from mysql.connector import Error
from database import get_db_connection

attendance_bp = Blueprint("attendance", __name__, url_prefix="/api/attendance")

VALID_LEAVE_CATEGORIES = {"NSS", "Sports", "Medical Leave"}


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
    # 1. Daily attendance_records table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_records (
            attendance_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            attendance_date DATE NOT NULL,
            attendance_status ENUM('PRESENT', 'ABSENT') NOT NULL,
            leave_category VARCHAR(30) NULL DEFAULT NULL,
            leave_status ENUM('NONE', 'PENDING', 'APPROVED', 'REJECTED') NOT NULL DEFAULT 'NONE',
            leave_reason VARCHAR(255) NULL DEFAULT NULL,
            attendance_credit DECIMAL(4,2) NOT NULL DEFAULT 1.00,
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

    # Safely add leave columns to attendance_records if it already existed without them
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'attendance_records'
    """)
    ar_cols = {
        (row["COLUMN_NAME"] if isinstance(row, dict) else row[0])
        for row in cursor.fetchall()
    }
    ar_altered = False
    if "leave_category" not in ar_cols:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN leave_category VARCHAR(30) NULL DEFAULT NULL AFTER attendance_status")
        ar_altered = True
    if "leave_status" not in ar_cols:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN leave_status ENUM('NONE', 'PENDING', 'APPROVED', 'REJECTED') NOT NULL DEFAULT 'NONE' AFTER leave_category")
        ar_altered = True
    if "leave_reason" not in ar_cols:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN leave_reason VARCHAR(255) NULL DEFAULT NULL AFTER leave_status")
        ar_altered = True
    if "attendance_credit" not in ar_cols:
        cursor.execute("ALTER TABLE attendance_records ADD COLUMN attendance_credit DECIMAL(4,2) NOT NULL DEFAULT 1.00 AFTER leave_reason")
        cursor.execute("""
            UPDATE attendance_records
            SET attendance_credit = CASE WHEN attendance_status = 'PRESENT' THEN 1.00 ELSE 0.00 END
            WHERE attendance_id > 0
        """)
        ar_altered = True
    if ar_altered and connection:
        connection.commit()

    # 2. Subject-wise period attendance table (supports 0.6 decimal periods for approved NSS/Sports/Medical Leave)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subject_attendance (
            subject_attendance_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            branch_subject_id INT NOT NULL,
            attended_periods DECIMAL(6,2) NOT NULL DEFAULT 0.00,
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

    cursor.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'subject_attendance'
    """)
    sa_col_rows = cursor.fetchall()
    sa_cols = {
        (row["COLUMN_NAME"] if isinstance(row, dict) else row[0]): (row["DATA_TYPE"] if isinstance(row, dict) else row[1])
        for row in sa_col_rows
    }
    if "attended_periods" not in sa_cols:
        cursor.execute(
            "ALTER TABLE subject_attendance ADD COLUMN attended_periods DECIMAL(6,2) NOT NULL DEFAULT 0.00 AFTER branch_subject_id"
        )
    elif sa_cols.get("attended_periods", "").lower() != "decimal":
        cursor.execute(
            "ALTER TABLE subject_attendance MODIFY COLUMN attended_periods DECIMAL(6,2) NOT NULL DEFAULT 0.00"
        )
    if "total_periods" not in sa_cols:
        cursor.execute(
            "ALTER TABLE subject_attendance ADD COLUMN total_periods INT UNSIGNED NOT NULL DEFAULT 50 AFTER attended_periods"
        )

    # 3. Ensure exam_date column exists on assessments
    cursor.execute("""
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'assessments'
    """)
    assess_cols = {
        (row["COLUMN_NAME"] if isinstance(row, dict) else row[0])
        for row in cursor.fetchall()
    }
    if "exam_date" not in assess_cols:
        cursor.execute("ALTER TABLE assessments ADD COLUMN exam_date DATE NULL AFTER max_marks")
        if connection:
            connection.commit()

    # 4. Seed specific branch-wise exam dates for Midterm 1, Midterm 2, Internals, and Final Exam if any are NULL
    cursor.execute("SELECT COUNT(*) AS missing_dates FROM assessments WHERE exam_date IS NULL")
    r_missing = cursor.fetchone()
    missing_dates = r_missing["missing_dates"] if isinstance(r_missing, dict) else (r_missing[0] if r_missing else 0)
    if missing_dates > 0:
        cursor.execute("""
            UPDATE assessments a
            JOIN branch_subjects bs ON bs.branch_subject_id = a.branch_subject_id
            SET a.exam_date = CASE a.assessment_name
                WHEN 'Midterm 1'  THEN DATE_ADD('2026-08-10', INTERVAL ((bs.branch_subject_id - 1) % 5) DAY)
                WHEN 'Midterm 2'  THEN DATE_ADD('2026-09-14', INTERVAL ((bs.branch_subject_id - 1) % 5) DAY)
                WHEN 'Internals'  THEN DATE_ADD('2026-10-05', INTERVAL ((bs.branch_subject_id - 1) % 5) DAY)
                WHEN 'Final Exam' THEN DATE_ADD('2026-10-19', INTERVAL ((bs.branch_subject_id - 1) % 5) DAY)
                ELSE '2026-10-15'
            END
            WHERE a.exam_date IS NULL AND a.assessment_id > 0
        """)
        if connection:
            connection.commit()

    # 5. Seed initial subject_attendance if empty
    cursor.execute("SELECT COUNT(*) AS cnt FROM subject_attendance")
    row = cursor.fetchone()
    cnt = row["cnt"] if isinstance(row, dict) else (row[0] if row else 0)
    if cnt == 0:
        cursor.execute("""
            INSERT IGNORE INTO subject_attendance (
                student_id, branch_subject_id, attended_periods, total_periods, attendance_percentage
            )
            SELECT
                t.student_id,
                t.branch_subject_id,
                t.attended_periods,
                t.total_periods,
                ROUND((t.attended_periods * 100.0) / t.total_periods, 2)
            FROM (
                SELECT
                    s.student_id,
                    bs.branch_subject_id,
                    (45 + ((bs.branch_subject_id * 3) % 16)) AS total_periods,
                    CASE
                        WHEN (s.student_id % 10) = 0 THEN
                            ROUND(((46.0 + ((s.student_id * 3 + bs.branch_subject_id * 7) % 18)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                        WHEN (s.student_id % 6) = 0 THEN
                            ROUND(((65.0 + ((s.student_id * 5 + bs.branch_subject_id * 4) % 10)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                        ELSE
                            ROUND(((76.0 + ((s.student_id * 7 + bs.branch_subject_id * 9) % 23)) / 100.0) * (45 + ((bs.branch_subject_id * 3) % 16)))
                    END AS attended_periods
                FROM student s
                JOIN branch_subjects bs ON bs.branch_id = s.branch_id AND bs.semester = s.semester
            ) t
        """)
        if connection:
            connection.commit()

    # 6. Seed exam-day attendance records if empty
    cursor.execute("SELECT COUNT(*) AS ar_cnt FROM attendance_records")
    r_ar = cursor.fetchone()
    ar_cnt = r_ar["ar_cnt"] if isinstance(r_ar, dict) else (r_ar[0] if r_ar else 0)
    if ar_cnt == 0:
        cursor.execute("""
            INSERT IGNORE INTO attendance_records (
                student_id, attendance_date, attendance_status, attendance_credit
            )
            SELECT
                st.student_id,
                a.exam_date,
                CASE
                    WHEN MAX(m.mark_id) IS NOT NULL THEN 'PRESENT'
                    WHEN (st.student_id % 17) = 0 THEN 'PRESENT'
                    ELSE 'ABSENT'
                END AS attendance_status,
                CASE
                    WHEN MAX(m.mark_id) IS NOT NULL THEN 1.00
                    WHEN (st.student_id % 17) = 0 THEN 1.00
                    ELSE 0.00
                END AS attendance_credit
            FROM student st
            JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
            JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
            LEFT JOIN marks m ON m.student_id = st.student_id AND m.assessment_id = a.assessment_id
            WHERE a.exam_date IS NOT NULL
            GROUP BY st.student_id, a.exam_date
        """)
        if connection:
            connection.commit()

    # 7. Randomly generate realistic NSS, Sports, and Medical Leave requests if none exist yet
    cursor.execute("SELECT COUNT(*) AS lv_cnt FROM attendance_records WHERE leave_category IS NOT NULL")
    r_lv = cursor.fetchone()
    lv_cnt = r_lv["lv_cnt"] if isinstance(r_lv, dict) else (r_lv[0] if r_lv else 0)
    if lv_cnt == 0:
        # Ensure we have records on key dates (including 2026-08-10, 2026-08-11, 2026-09-14, 2026-10-05, 2026-10-19)
        # and assign ~18% of students across branches into NSS, Sports, or Medical Leave PENDING requests
        cursor.execute("""
            INSERT INTO attendance_records (
                student_id, attendance_date, attendance_status,
                leave_category, leave_status, leave_reason, attendance_credit
            )
            SELECT
                s.student_id,
                d.dt AS attendance_date,
                'ABSENT' AS attendance_status,
                CASE (s.student_id % 3)
                    WHEN 0 THEN 'Medical Leave'
                    WHEN 1 THEN 'NSS'
                    ELSE 'Sports'
                END AS leave_category,
                'PENDING' AS leave_status,
                CASE (s.student_id % 3)
                    WHEN 0 THEN 'Medical certificate submitted for fever / hospital visit'
                    WHEN 1 THEN 'NSS Blood Donation & Community Service Camp duty'
                    ELSE 'Inter-University Zonal Sports Tournament representation'
                END AS leave_reason,
                0.00 AS attendance_credit
            FROM student s
            CROSS JOIN (
                SELECT DATE('2026-08-10') AS dt
                UNION ALL SELECT DATE('2026-08-11')
                UNION ALL SELECT DATE('2026-09-14')
                UNION ALL SELECT DATE('2026-10-05')
                UNION ALL SELECT DATE('2026-10-19')
            ) d
            WHERE (s.student_id % 6) IN (1, 2)
            ON DUPLICATE KEY UPDATE
                leave_category = VALUES(leave_category),
                leave_status = CASE
                    WHEN attendance_records.leave_status = 'APPROVED' THEN 'APPROVED'
                    ELSE 'PENDING'
                END,
                leave_reason = VALUES(leave_reason),
                attendance_status = CASE
                    WHEN attendance_records.leave_status = 'APPROVED' THEN 'PRESENT'
                    ELSE 'ABSENT'
                END,
                attendance_credit = CASE
                    WHEN attendance_records.leave_status = 'APPROVED' THEN 0.60
                    ELSE 0.00
                END
        """)
        if connection:
            connection.commit()


@attendance_bp.route("/daily", methods=["GET"])
def get_daily_attendance_roster():
    """Return every student in the selected branch/semester with their daily status, credit (1.0 / 0.6 / 0.0), and NSS/Sports/Medical Leave info."""
    attendance_date = (request.args.get("date") or "2026-08-10").strip()
    branch_id = request.args.get("branch_id", type=int)
    semester = request.args.get("semester", type=int)
    leave_filter = (request.args.get("leave_category") or "").strip()

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        # Check if any exams are scheduled on this date for the selected branch
        cursor.execute("""
            SELECT
                a.assessment_id,
                a.assessment_name,
                CAST(a.exam_date AS CHAR) AS exam_date,
                a.max_marks,
                b.branch_id,
                b.branch_code,
                bs.semester,
                sub.subject_code,
                sub.subject_name
            FROM assessments a
            JOIN branch_subjects bs ON bs.branch_subject_id = a.branch_subject_id
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects sub ON sub.subject_id = bs.subject_id
            WHERE a.exam_date = %s
              AND (%s IS NULL OR bs.branch_id = %s)
            ORDER BY b.branch_code, sub.subject_code, a.assessment_name
        """, (attendance_date, branch_id, branch_id))
        exams_on_date = cursor.fetchall()

        # Fetch all students in scope and their attendance + leave status for that date
        cursor.execute("""
            SELECT
                s.student_id,
                s.roll_no,
                s.name,
                s.branch_id,
                b.branch_code,
                s.semester,
                s.section,
                ar.attendance_status,
                ar.leave_category,
                ar.leave_status,
                ar.leave_reason,
                ar.attendance_credit
            FROM student s
            JOIN branches b ON b.branch_id = s.branch_id
            LEFT JOIN attendance_records ar
                ON ar.student_id = s.student_id
                AND ar.attendance_date = %s
            WHERE (%s IS NULL OR s.branch_id = %s)
              AND (%s IS NULL OR s.semester = %s)
            ORDER BY s.student_id
        """, (attendance_date, branch_id, branch_id, semester, semester))
        students = cursor.fetchall()

        # If a date has no leave requests yet, deterministically synthesize pending NSS/Sports/Medical Leave requests for ~15% of students
        has_any_leave = any(st.get("leave_category") for st in students)
        if not has_any_leave and students:
            day_seed = sum(ord(c) for c in attendance_date)
            seeded_rows = []
            for st in students:
                sid = st["student_id"]
                if ((sid + day_seed) % 7) == 0:
                    cat = "Medical Leave" if (sid % 3 == 0) else ("NSS" if (sid % 3 == 1) else "Sports")
                    reason = (
                        "Medical certificate submitted for fever / clinic visit"
                        if cat == "Medical Leave"
                        else (
                            "NSS Community Service & Campus Drive duty"
                            if cat == "NSS"
                            else "University Sports Club tournament practice / match"
                        )
                    )
                    st["leave_category"] = cat
                    st["leave_status"] = "PENDING"
                    st["leave_reason"] = reason
                    if not st["attendance_status"]:
                        st["attendance_status"] = "ABSENT"
                        st["attendance_credit"] = 0.0
                    seeded_rows.append((sid, attendance_date, st["attendance_status"], cat, "PENDING", reason, float(st["attendance_credit"] or 0.0)))
            if seeded_rows:
                cursor.executemany("""
                    INSERT INTO attendance_records (
                        student_id, attendance_date, attendance_status,
                        leave_category, leave_status, leave_reason, attendance_credit
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        leave_category = COALESCE(attendance_records.leave_category, VALUES(leave_category)),
                        leave_status = CASE
                            WHEN attendance_records.leave_status = 'NONE' THEN VALUES(leave_status)
                            ELSE attendance_records.leave_status
                        END,
                        leave_reason = COALESCE(attendance_records.leave_reason, VALUES(leave_reason))
                """, seeded_rows)
                connection.commit()

        exam_attempts_map = {}
        if exams_on_date:
            assessment_ids = [e["assessment_id"] for e in exams_on_date]
            placeholders = ",".join(["%s"] * len(assessment_ids))
            cursor.execute(f"""
                SELECT student_id, assessment_id, marks_obtained
                FROM marks
                WHERE assessment_id IN ({placeholders})
            """, tuple(assessment_ids))
            for m_row in cursor.fetchall():
                exam_attempts_map[(m_row["student_id"], m_row["assessment_id"])] = float(m_row["marks_obtained"])

        exams_by_branch = {}
        for ex in exams_on_date:
            exams_by_branch.setdefault((ex["branch_id"], ex["semester"]), []).append(ex)

        present_count = 0
        absent_count = 0
        unmarked_count = 0
        approved_leave_count = 0
        pending_leave_count = 0
        category_counts = {"Medical Leave": 0, "NSS": 0, "Sports": 0}

        for st in students:
            st_status = st["attendance_status"]
            lv_cat = st.get("leave_category")
            lv_status = st.get("leave_status") or "NONE"
            credit = float(st["attendance_credit"]) if st.get("attendance_credit") is not None else (
                0.60 if lv_status == "APPROVED" else (1.00 if st_status == "PRESENT" else 0.00)
            )
            st["attendance_credit"] = round(credit, 2)
            st["leave_status"] = lv_status

            if lv_cat in category_counts:
                category_counts[lv_cat] += 1
            if lv_status == "APPROVED":
                approved_leave_count += 1
            elif lv_status == "PENDING" and lv_cat:
                pending_leave_count += 1

            if st_status == "PRESENT":
                present_count += 1
            elif st_status == "ABSENT":
                absent_count += 1
            else:
                unmarked_count += 1

            cohort_exams = exams_by_branch.get((st["branch_id"], st["semester"]), [])
            if cohort_exams:
                ex = cohort_exams[0]
                score = exam_attempts_map.get((st["student_id"], ex["assessment_id"]))
                st["scheduled_exam"] = f"{ex['subject_code']} · {ex['assessment_name']}"
                st["exam_attempted"] = score is not None
                st["exam_marks"] = score
            else:
                st["scheduled_exam"] = None
                st["exam_attempted"] = None
                st["exam_marks"] = None

        if leave_filter:
            if leave_filter == "ALL_LEAVES":
                students = [s for s in students if s.get("leave_category") in VALID_LEAVE_CATEGORIES]
            elif leave_filter == "PENDING_LEAVES":
                students = [s for s in students if s.get("leave_category") in VALID_LEAVE_CATEGORIES and s.get("leave_status") == "PENDING"]
            elif leave_filter == "APPROVED_LEAVES":
                students = [s for s in students if s.get("leave_status") == "APPROVED"]
            elif leave_filter in VALID_LEAVE_CATEGORIES:
                students = [s for s in students if s.get("leave_category") == leave_filter]

        return jsonify({
            "status": "success",
            "date": attendance_date,
            "summary": {
                "total": len(students),
                "present": present_count,
                "absent": absent_count,
                "unmarked": unmarked_count,
                "approved_leaves": approved_leave_count,
                "pending_leaves": pending_leave_count,
                "category_counts": category_counts
            },
            "exams_on_date": exams_on_date,
            "students": students
        })
    except Error:
        return jsonify({"status": "error", "message": "Could not load daily attendance roster."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/daily", methods=["POST"])
def save_daily_attendance_roster():
    """Save daily PRESENT/ABSENT status (or 0.6 approved special leave) for one or more students on a specific date."""
    data = request.get_json(silent=True) or {}
    attendance_date = (data.get("attendance_date") or "").strip()
    records = data.get("records")

    if not attendance_date:
        return jsonify({"status": "error", "message": "Attendance date is required."}), 400
    if not isinstance(records, list) or not records:
        return jsonify({"status": "error", "message": "Provide at least one student attendance record."}), 400

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        rows_to_upsert = []
        for item in records:
            if not isinstance(item, dict):
                continue
            sid = item.get("student_id")
            st_val = (item.get("attendance_status") or "").strip().upper()
            if not isinstance(sid, int) or sid <= 0 or st_val not in {"PRESENT", "ABSENT"}:
                continue

            lv_cat = item.get("leave_category")
            lv_status = (item.get("leave_status") or "NONE").strip().upper()
            lv_reason = item.get("leave_reason")
            credit = item.get("attendance_credit")

            if credit is None:
                if lv_status == "APPROVED":
                    credit = 0.60
                elif st_val == "PRESENT":
                    credit = 1.00
                else:
                    credit = 0.00

            rows_to_upsert.append((sid, attendance_date, st_val, lv_cat, lv_status, lv_reason, float(credit)))

        if not rows_to_upsert:
            return jsonify({"status": "error", "message": "No valid attendance rows found to save."}), 400

        cursor.executemany("""
            INSERT INTO attendance_records (
                student_id, attendance_date, attendance_status,
                leave_category, leave_status, leave_reason, attendance_credit
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                attendance_status = VALUES(attendance_status),
                leave_category = COALESCE(VALUES(leave_category), attendance_records.leave_category),
                leave_status = CASE
                    WHEN VALUES(leave_status) <> 'NONE' THEN VALUES(leave_status)
                    WHEN VALUES(attendance_status) = 'ABSENT' AND attendance_records.leave_status = 'APPROVED' THEN 'PENDING'
                    ELSE attendance_records.leave_status
                END,
                leave_reason = COALESCE(VALUES(leave_reason), attendance_records.leave_reason),
                attendance_credit = VALUES(attendance_credit)
        """, rows_to_upsert)

        connection.commit()

        return jsonify({
            "status": "success",
            "saved_count": len(rows_to_upsert),
            "message": f"Saved daily attendance for {len(rows_to_upsert)} student(s) on {attendance_date}."
        }), 201
    except Error:
        if connection:
            connection.rollback()
        return jsonify({"status": "error", "message": "Could not save daily attendance records."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/leave-approve", methods=["POST"])
def approve_special_leave():
    """
    Approve (or update) NSS, Sports, or Medical Leave for one or multiple students on a date.
    Approved students are marked PRESENT with 0.60 / 1.00 (60%) attendance credit,
    and their subject_attendance periods are also credited by +0.60 so cumulative attendance reflects it immediately.
    """
    data = request.get_json(silent=True) or {}
    attendance_date = (data.get("attendance_date") or "2026-08-10").strip()
    action = (data.get("action") or "APPROVE").strip().upper()
    items = data.get("students")

    # Support single-student payload as well as bulk list
    if not isinstance(items, list):
        sid = data.get("student_id")
        if isinstance(sid, int) and sid > 0:
            items = [{
                "student_id": sid,
                "leave_category": data.get("leave_category") or "Medical Leave",
                "leave_reason": data.get("leave_reason") or ""
            }]
        else:
            return jsonify({"status": "error", "message": "Select at least one student to approve."}), 400

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        updated_count = 0
        for item in items:
            sid = item.get("student_id")
            if not isinstance(sid, int) or sid <= 0:
                continue
            cat = (item.get("leave_category") or "Medical Leave").strip()
            if cat not in VALID_LEAVE_CATEGORIES:
                cat = "Medical Leave"
            reason = (item.get("leave_reason") or "").strip() or f"Approved {cat} representation / leave (0.6/1 credit)"

            # Check previous status on this date so we don't double-add 0.6 to subject_attendance
            cursor.execute("""
                SELECT leave_status, attendance_credit
                FROM attendance_records
                WHERE student_id = %s AND attendance_date = %s
            """, (sid, attendance_date))
            prev_row = cursor.fetchone()
            was_already_approved = prev_row and prev_row.get("leave_status") == "APPROVED"

            if action == "REJECT":
                cursor.execute("""
                    INSERT INTO attendance_records (
                        student_id, attendance_date, attendance_status,
                        leave_category, leave_status, leave_reason, attendance_credit
                    )
                    VALUES (%s, %s, 'ABSENT', %s, 'REJECTED', %s, 0.00)
                    ON DUPLICATE KEY UPDATE
                        attendance_status = 'ABSENT',
                        leave_category = VALUES(leave_category),
                        leave_status = 'REJECTED',
                        leave_reason = VALUES(leave_reason),
                        attendance_credit = 0.00
                """, (sid, attendance_date, cat, reason))

                if was_already_approved:
                    cursor.execute("""
                        UPDATE subject_attendance
                        SET attended_periods = GREATEST(0, ROUND(attended_periods - 0.60, 2)),
                            attendance_percentage = ROUND((GREATEST(0, ROUND(attended_periods - 0.60, 2)) * 100.0) / NULLIF(total_periods, 0), 2)
                        WHERE student_id = %s
                    """, (sid,))
            else:
                # APPROVE: mark PRESENT with 0.60 / 1.00 (60% attendance considered)
                cursor.execute("""
                    INSERT INTO attendance_records (
                        student_id, attendance_date, attendance_status,
                        leave_category, leave_status, leave_reason, attendance_credit
                    )
                    VALUES (%s, %s, 'PRESENT', %s, 'APPROVED', %s, 0.60)
                    ON DUPLICATE KEY UPDATE
                        attendance_status = 'PRESENT',
                        leave_category = VALUES(leave_category),
                        leave_status = 'APPROVED',
                        leave_reason = VALUES(leave_reason),
                        attendance_credit = 0.60
                """, (sid, attendance_date, cat, reason))

                if not was_already_approved:
                    cursor.execute("""
                        UPDATE subject_attendance
                        SET attended_periods = LEAST(total_periods, ROUND(attended_periods + 0.60, 2)),
                            attendance_percentage = ROUND((LEAST(total_periods, ROUND(attended_periods + 0.60, 2)) * 100.0) / NULLIF(total_periods, 0), 2)
                        WHERE student_id = %s
                    """, (sid,))

            updated_count += 1

        connection.commit()
        verb = "Approved (0.6/1 · 60% credit)" if action != "REJECT" else "Rejected (Marked Absent · 0 credit)"
        return jsonify({
            "status": "success",
            "updated_count": updated_count,
            "message": f"{verb} for {updated_count} student(s) on {attendance_date}."
        }), 200
    except Error:
        if connection:
            connection.rollback()
        return jsonify({"status": "error", "message": "Could not process special leave approval."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/exam-schedule", methods=["GET", "POST"])
def handle_exam_schedule():
    """GET branch-wise exam timetable or POST updated exam_date for any assessment."""
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            assessment_id = data.get("assessment_id")
            exam_date = (data.get("exam_date") or "").strip()

            if not isinstance(assessment_id, int) or assessment_id <= 0 or not exam_date:
                return jsonify({"status": "error", "message": "Valid assessment_id and exam_date are required."}), 400

            cursor.execute(
                "UPDATE assessments SET exam_date = %s WHERE assessment_id = %s",
                (exam_date, assessment_id)
            )
            connection.commit()
            return jsonify({
                "status": "success",
                "message": f"Exam date updated to {exam_date}."
            })

        branch_id = request.args.get("branch_id", type=int)
        assessment_type = (request.args.get("assessment_name") or "").strip()

        cursor.execute("""
            SELECT
                a.assessment_id,
                a.branch_subject_id,
                b.branch_id,
                b.branch_code,
                bs.semester,
                sub.subject_code,
                sub.subject_name,
                a.assessment_name,
                a.max_marks,
                CAST(a.exam_date AS CHAR) AS exam_date
            FROM assessments a
            JOIN branch_subjects bs ON bs.branch_subject_id = a.branch_subject_id
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects sub ON sub.subject_id = bs.subject_id
            WHERE (%s IS NULL OR bs.branch_id = %s)
              AND (%s = '' OR a.assessment_name = %s)
            ORDER BY b.branch_code, a.exam_date, sub.subject_code
        """, (branch_id, branch_id, assessment_type, assessment_type))

        return jsonify({
            "status": "success",
            "schedule": cursor.fetchall()
        })
    except Error:
        if connection and request.method == "POST":
            connection.rollback()
        return jsonify({"status": "error", "message": "Could not process exam schedule."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/exam-verification", methods=["GET"])
def get_exam_absence_verification():
    """Verify students who did not attempt an exam against their daily attendance & special leave status on that exam date."""
    branch_id = request.args.get("branch_id", type=int)
    assessment_name = (request.args.get("assessment_name") or "").strip()

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        cursor.execute("""
            SELECT
                st.student_id,
                st.roll_no,
                st.name AS student_name,
                b.branch_id,
                b.branch_code,
                st.semester,
                sub.subject_code,
                sub.subject_name,
                a.assessment_id,
                a.assessment_name,
                CAST(a.exam_date AS CHAR) AS exam_date,
                COALESCE(ar.attendance_status, 'UNMARKED') AS attendance_on_exam_day,
                ar.leave_category,
                COALESCE(ar.leave_status, 'NONE') AS leave_status,
                ar.leave_reason,
                COALESCE(ar.attendance_credit, 0.00) AS attendance_credit,
                CASE
                    WHEN ar.leave_status = 'APPROVED' THEN CONCAT('Approved ', ar.leave_category, ' (0.6/1 · 60% Credit)')
                    WHEN ar.attendance_status = 'ABSENT' AND ar.leave_category IS NOT NULL THEN CONCAT('Pending ', ar.leave_category, ' Request')
                    WHEN ar.attendance_status = 'ABSENT' THEN 'Verified Absent on Exam Day'
                    WHEN ar.attendance_status = 'PRESENT' THEN 'Anomaly: Present on Campus but Skipped Exam'
                    ELSE 'Pending: Daily Attendance Not Marked for Exam Date'
                END AS verification_verdict,
                CASE
                    WHEN a.assessment_name IN ('Internals', 'Final Exam') THEN 'Mandatory Exam Missed -> FAIL'
                    ELSE 'Midterm Missed -> Counted as 0'
                END AS academic_impact
            FROM student st
            JOIN branches b ON b.branch_id = st.branch_id
            JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
            JOIN subjects sub ON sub.subject_id = bs.subject_id
            JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
            LEFT JOIN marks m ON m.student_id = st.student_id AND m.assessment_id = a.assessment_id
            LEFT JOIN attendance_records ar
                ON ar.student_id = st.student_id
                AND ar.attendance_date = a.exam_date
            WHERE m.mark_id IS NULL
              AND (%s IS NULL OR st.branch_id = %s)
              AND (%s = '' OR a.assessment_name = %s)
            ORDER BY a.exam_date DESC, b.branch_code, st.roll_no
            LIMIT 500
        """, (branch_id, branch_id, assessment_name, assessment_name))
        records = cursor.fetchall()

        verified_absent = sum(1 for r in records if r["attendance_on_exam_day"] == "ABSENT" or r["leave_status"] == "APPROVED")
        anomalies = sum(1 for r in records if r["attendance_on_exam_day"] == "PRESENT" and r["leave_status"] != "APPROVED")
        unmarked = sum(1 for r in records if r["attendance_on_exam_day"] == "UNMARKED")

        return jsonify({
            "status": "success",
            "summary": {
                "total_unattempted": len(records),
                "verified_absent": verified_absent,
                "present_skipped_anomaly": anomalies,
                "unmarked_attendance": unmarked
            },
            "records": records
        })
    except Error:
        return jsonify({"status": "error", "message": "Could not load exam absence verification."}), 500
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@attendance_bp.route("/export-excel", methods=["GET"])
def export_attendance_and_exam_verification_excel():
    """Generate and download a live multi-sheet Excel (.xlsx) report (or CSV fallback if openpyxl is unavailable)."""
    branch_id = request.args.get("branch_id", type=int)
    selected_date = (request.args.get("date") or "2026-08-10").strip()

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        ensure_attendance_schema(cursor, connection)

        if not HAS_OPENPYXL:
            cursor.execute("""
                SELECT s.roll_no, s.name, b.branch_code, s.semester, s.section,
                       COALESCE(ar.attendance_status, 'UNMARKED') AS attendance_status,
                       COALESCE(ar.leave_category, 'None') AS leave_category,
                       COALESCE(ar.leave_status, 'NONE') AS leave_status,
                       COALESCE(ar.attendance_credit, 0.00) AS attendance_credit
                FROM student s
                JOIN branches b ON b.branch_id = s.branch_id
                LEFT JOIN attendance_records ar
                    ON ar.student_id = s.student_id AND ar.attendance_date = %s
                WHERE (%s IS NULL OR s.branch_id = %s)
                ORDER BY s.student_id
            """, (selected_date, branch_id, branch_id))
            out_str = io.StringIO()
            writer = csv.writer(out_str)
            writer.writerow(["Roll Number", "Student Name", "Branch", "Semester", "Section", "Date", "Attendance Status", "Leave Category", "Leave Status", "Credit"])
            for r in cursor.fetchall():
                writer.writerow([r["roll_no"], r["name"], r["branch_code"], r["semester"], r["section"], selected_date, r["attendance_status"], r["leave_category"], r["leave_status"], r["attendance_credit"]])
            mem = io.BytesIO(out_str.getvalue().encode("utf-8"))
            mem.seek(0)
            return send_file(mem, as_attachment=True, download_name=f"Attendance_{selected_date}.csv", mimetype="text/csv")

        wb = openpyxl.Workbook()
        navy_fill = PatternFill(start_color="1B2A4A", end_color="1B2A4A", fill_type="solid")
        header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        bold_font = Font(name="Segoe UI", size=10, bold=True, color="1E293B")
        body_font = Font(name="Segoe UI", size=10, color="1E293B")
        green_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
        red_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
        amber_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
        thin_border = Border(
            left=Side(style="thin", color="D8DFEB"),
            right=Side(style="thin", color="D8DFEB"),
            top=Side(style="thin", color="D8DFEB"),
            bottom=Side(style="thin", color="D8DFEB"),
        )

        # Sheet 1: Unattempted Exam Absence Verification
        ws_verify = wb.active
        ws_verify.title = "Exam Absence Verification"
        v_headers = [
            "Roll Number", "Student Name", "Branch", "Semester",
            "Subject Code", "Assessment", "Exam Date",
            "Exam Attempt Status", "Daily Attendance on Exam Date",
            "Special Leave (NSS/Sports/Medical)", "Leave Status", "Attendance Credit",
            "Verification Verdict"
        ]
        ws_verify.append(v_headers)
        for col_i in range(1, len(v_headers) + 1):
            c = ws_verify.cell(row=1, column=col_i)
            c.fill = navy_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center")

        cursor.execute("""
            SELECT
                st.roll_no,
                st.name AS student_name,
                b.branch_code,
                st.semester,
                sub.subject_code,
                a.assessment_name,
                CAST(a.exam_date AS CHAR) AS exam_date,
                COALESCE(ar.attendance_status, 'UNMARKED') AS attendance_on_exam_day,
                COALESCE(ar.leave_category, '—') AS leave_category,
                COALESCE(ar.leave_status, 'NONE') AS leave_status,
                COALESCE(ar.attendance_credit, 0.00) AS attendance_credit,
                CASE
                    WHEN ar.leave_status = 'APPROVED' THEN CONCAT('Approved ', ar.leave_category, ' (0.6/1 · 60% Credit)')
                    WHEN ar.attendance_status = 'ABSENT' THEN 'Verified Absent on Exam Day'
                    WHEN ar.attendance_status = 'PRESENT' THEN 'Anomaly: Present on Campus but Skipped Exam'
                    ELSE 'Pending: Daily Attendance Not Marked'
                END AS verification_verdict
            FROM student st
            JOIN branches b ON b.branch_id = st.branch_id
            JOIN branch_subjects bs ON bs.branch_id = st.branch_id AND bs.semester = st.semester
            JOIN subjects sub ON sub.subject_id = bs.subject_id
            JOIN assessments a ON a.branch_subject_id = bs.branch_subject_id
            LEFT JOIN marks m ON m.student_id = st.student_id AND m.assessment_id = a.assessment_id
            LEFT JOIN attendance_records ar
                ON ar.student_id = st.student_id AND ar.attendance_date = a.exam_date
            WHERE m.mark_id IS NULL
              AND (%s IS NULL OR st.branch_id = %s)
            ORDER BY b.branch_code, st.roll_no, a.exam_date
        """, (branch_id, branch_id))

        for r_idx, row in enumerate(cursor.fetchall(), start=2):
            vals = [
                row["roll_no"], row["student_name"], row["branch_code"], row["semester"],
                row["subject_code"], row["assessment_name"], row["exam_date"],
                "NOT ATTEMPTED", row["attendance_on_exam_day"],
                row["leave_category"], row["leave_status"], float(row["attendance_credit"]),
                row["verification_verdict"]
            ]
            ws_verify.append(vals)
            for c_i in range(1, len(vals) + 1):
                cell = ws_verify.cell(row=r_idx, column=c_i)
                cell.font = bold_font if c_i == 1 else body_font
                cell.border = thin_border
                if c_i == 13:
                    cell.fill = green_fill if ("Verified Absent" in row["verification_verdict"] or "Approved" in row["verification_verdict"]) else red_fill

        # Sheet 2: Daily Roll Call & Special Leave Credits
        ws_daily = wb.create_sheet(title=f"Daily Roll Call ({selected_date})")
        d_headers = ["Roll Number", "Student Name", "Branch", "Semester", "Section", "Date", "Attendance Status", "Leave Category", "Leave Status", "Attendance Credit (Out of 1)"]
        ws_daily.append(d_headers)
        for col_i in range(1, len(d_headers) + 1):
            c = ws_daily.cell(row=1, column=col_i)
            c.fill = navy_fill
            c.font = header_font

        cursor.execute("""
            SELECT s.roll_no, s.name, b.branch_code, s.semester, s.section,
                   COALESCE(ar.attendance_status, 'UNMARKED') AS attendance_status,
                   COALESCE(ar.leave_category, '—') AS leave_category,
                   COALESCE(ar.leave_status, 'NONE') AS leave_status,
                   COALESCE(ar.attendance_credit, 0.00) AS attendance_credit
            FROM student s
            JOIN branches b ON b.branch_id = s.branch_id
            LEFT JOIN attendance_records ar
                ON ar.student_id = s.student_id AND ar.attendance_date = %s
            WHERE (%s IS NULL OR s.branch_id = %s)
            ORDER BY s.student_id
        """, (selected_date, branch_id, branch_id))
        for r_idx, row in enumerate(cursor.fetchall(), start=2):
            ws_daily.append([
                row["roll_no"], row["name"], row["branch_code"],
                row["semester"], row["section"], selected_date,
                row["attendance_status"], row["leave_category"],
                row["leave_status"], float(row["attendance_credit"])
            ])

        for sheet in wb.worksheets:
            sheet.freeze_panes = "A2"
            for col in sheet.columns:
                col_letter = get_column_letter(col[0].column)
                max_len = max((len(str(cell.value or "")) for cell in col), default=10)
                sheet.column_dimensions[col_letter].width = max(max_len + 4, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name=f"AstraMindNexus_Attendance_Exam_Audit_{selected_date}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


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
            att_periods = round(float(student.pop("attended_periods", None) or 0), 2)
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
        total_att_periods = 0.0
        total_max_periods = 0

        for row in subject_rows:
            pct = round(float(row["attendance_percentage"]), 2) if row["attendance_percentage"] is not None else None
            att_p = round(float(row["attended_periods"]), 2) if row["attended_periods"] is not None else None
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
                "attended_periods": round(total_att_periods, 2),
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
                    att_p = round(float(raw_att), 2)
                    tot_p = int(raw_tot)
                except (TypeError, ValueError):
                    return jsonify({"status": "error", "message": "Enter valid numbers for periods."}), 400

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

        raw_bs = data.get("branch_subject_id")
        raw_att = data.get("attended_periods")
        raw_tot = data.get("total_periods")
        if raw_bs is not None and raw_att is not None and raw_tot is not None:
            try:
                bs_id = int(raw_bs)
                att_p = round(float(raw_att), 2)
                tot_p = int(raw_tot)
            except (TypeError, ValueError):
                return jsonify({"status": "error", "message": "Enter valid numbers for periods."}), 400

            err = upsert_one_subject(bs_id, att_p, tot_p)
            if err:
                return jsonify({"status": "error", "message": err}), 400

            connection.commit()
            pct = round((att_p * 100.0) / tot_p, 2)
            return jsonify({
                "status": "success",
                "attendance_percentage": pct,
                "status_label": attendance_status_from_pct(pct),
                "message": f"Auto-saved {valid_subjects[bs_id]}: {att_p:g}/{tot_p} periods ({pct}%) for {student['name']}."
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