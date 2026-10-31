from flask import Blueprint, jsonify, request, session
from mysql.connector import Error

from database import get_db_connection
from performance_calculator import calculate_subject_performance
from routes.assessments import ensure_default_assessments


performance_bp = Blueprint(
    "performance",
    __name__,
    url_prefix="/api/performance"
)

LEGACY_DUMMY_SUBJECT_CODES = ("CS301", "CS302", "CS303", "CS304")


def cleanup_legacy_cs_subjects(cursor, connection):
    placeholders = ", ".join(["%s"] * len(LEGACY_DUMMY_SUBJECT_CODES))
    cursor.execute(
        f"""
        SELECT bs.branch_subject_id, s.subject_id, s.subject_code
        FROM branch_subjects bs
        JOIN subjects s ON s.subject_id = bs.subject_id
        WHERE s.subject_code IN ({placeholders})
        """,
        LEGACY_DUMMY_SUBJECT_CODES
    )
    legacy_rows = cursor.fetchall()
    if not legacy_rows:
        return

    legacy_bs_ids = [r["branch_subject_id"] for r in legacy_rows]
    legacy_sub_ids = list({r["subject_id"] for r in legacy_rows})

    bs_placeholders = ", ".join(["%s"] * len(legacy_bs_ids))
    cursor.execute(
        f"""
        DELETE m FROM marks m
        JOIN assessments a ON a.assessment_id = m.assessment_id
        WHERE a.branch_subject_id IN ({bs_placeholders})
        """,
        tuple(legacy_bs_ids)
    )
    cursor.execute(
        f"DELETE FROM assessments WHERE branch_subject_id IN ({bs_placeholders})",
        tuple(legacy_bs_ids)
    )
    cursor.execute(
        f"DELETE FROM branch_subjects WHERE branch_subject_id IN ({bs_placeholders})",
        tuple(legacy_bs_ids)
    )

    sub_placeholders = ", ".join(["%s"] * len(legacy_sub_ids))
    cursor.execute(
        f"""
        DELETE s FROM subjects s
        LEFT JOIN branch_subjects bs ON bs.subject_id = s.subject_id
        WHERE s.subject_id IN ({sub_placeholders})
          AND bs.branch_subject_id IS NULL
        """,
        tuple(legacy_sub_ids)
    )
    connection.commit()


def format_failure_reason(perf, subject_code=None):
    prefix = f"{subject_code}: " if subject_code else ""
    reasons = []
    if perf["missing_mandatory_assessments"]:
        missed = " & ".join(perf["missing_mandatory_assessments"])
        reasons.append(f"{prefix}Skipped {missed}")
    else:
        if not perf["final_exam_passed"]:
            f_score = perf["assessment_marks"].get("Final Exam", 0)
            reasons.append(f"{prefix}Final Exam {f_score:g}/100 (< 40)")
        if not perf["overall_passed"]:
            t_score = perf["total_marks"]
            reasons.append(f"{prefix}Total {t_score:g}/100 (< 40)")
    return ", ".join(reasons) if reasons else f"{prefix}Failed criteria"


@performance_bp.before_request
def require_login():
    if request.method == "OPTIONS":
        return None

    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "Please log in first."
        }), 401


@performance_bp.route("/filters", methods=["GET"])
def get_performance_filters():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cleanup_legacy_cs_subjects(cursor, connection)

        cursor.execute("""
            SELECT branch_id, branch_code, branch_name
            FROM branches
            ORDER BY branch_code
        """)
        branches = cursor.fetchall()

        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                bs.branch_id,
                b.branch_code,
                b.branch_name,
                bs.semester,
                s.subject_id,
                s.subject_code,
                s.subject_name
            FROM branch_subjects bs
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects s ON s.subject_id = bs.subject_id
            ORDER BY b.branch_code, bs.semester, s.subject_code
        """)
        assignments = cursor.fetchall()

        for a in assignments:
            ensure_default_assessments(cursor, connection, a["branch_subject_id"])

        return jsonify({
            "status": "success",
            "branches": branches,
            "assignments": assignments,
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve branch and subject filters."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@performance_bp.route("/overview", methods=["GET"])
def get_overview_analytics():
    branch_id = request.args.get("branch_id", type=int)

    if branch_id is not None and branch_id <= 0:
        return jsonify({
            "status": "error",
            "message": "branch_id must be a positive integer."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cleanup_legacy_cs_subjects(cursor, connection)

        scope_label = "Overall — All branches and subjects"
        branch_info = None

        if branch_id is not None:
            cursor.execute("""
                SELECT branch_id, branch_code, branch_name
                FROM branches
                WHERE branch_id = %s
            """, (branch_id,))
            branch_info = cursor.fetchone()

            if not branch_info:
                return jsonify({
                    "status": "error",
                    "message": "Branch not found."
                }), 404

            scope_label = (
                f"{branch_info['branch_code']} — "
                f"{branch_info['branch_name']} (All subjects)"
            )

        if branch_id is not None:
            cursor.execute("""
                SELECT
                    s.student_id,
                    s.roll_no,
                    s.name AS student_name,
                    s.branch_id,
                    b.branch_code,
                    s.semester,
                    s.section
                FROM student s
                JOIN branches b ON b.branch_id = s.branch_id
                WHERE s.branch_id = %s
                ORDER BY s.student_id
            """, (branch_id,))
        else:
            cursor.execute("""
                SELECT
                    s.student_id,
                    s.roll_no,
                    s.name AS student_name,
                    s.branch_id,
                    b.branch_code,
                    s.semester,
                    s.section
                FROM student s
                JOIN branches b ON b.branch_id = s.branch_id
                ORDER BY s.student_id
            """)

        students = cursor.fetchall()

        if not students:
            return jsonify({
                "status": "success",
                "scope": {
                    "mode": "branch" if branch_id else "overall",
                    "branch_id": branch_id,
                    "label": scope_label,
                },
                "analytics": {
                    "total_students": 0,
                    "passed_students": 0,
                    "failed_students": 0,
                    "incomplete_students": 0,
                    "pass_percentage": None,
                    "fail_percentage": None,
                    "class_average": None,
                    "top_performers": [],
                    "students_needing_support": [],
                }
            })

        if branch_id is not None:
            cursor.execute("""
                SELECT
                    bs.branch_subject_id,
                    bs.branch_id,
                    bs.semester,
                    s.subject_code,
                    s.subject_name
                FROM branch_subjects bs
                JOIN subjects s ON s.subject_id = bs.subject_id
                WHERE bs.branch_id = %s
                ORDER BY bs.branch_subject_id
            """, (branch_id,))
        else:
            cursor.execute("""
                SELECT
                    bs.branch_subject_id,
                    bs.branch_id,
                    bs.semester,
                    s.subject_code,
                    s.subject_name
                FROM branch_subjects bs
                JOIN subjects s ON s.subject_id = bs.subject_id
                ORDER BY bs.branch_subject_id
            """)

        branch_subjects = cursor.fetchall()

        subjects_by_cohort = {}
        for bs in branch_subjects:
            key = (bs["branch_id"], bs["semester"])
            subjects_by_cohort.setdefault(key, []).append(bs)

        if branch_id is not None:
            cursor.execute("""
                SELECT
                    m.student_id,
                    a.branch_subject_id,
                    a.assessment_name,
                    m.marks_obtained
                FROM marks m
                JOIN assessments a ON a.assessment_id = m.assessment_id
                JOIN student s ON s.student_id = m.student_id
                WHERE s.branch_id = %s
            """, (branch_id,))
        else:
            cursor.execute("""
                SELECT
                    m.student_id,
                    a.branch_subject_id,
                    a.assessment_name,
                    m.marks_obtained
                FROM marks m
                JOIN assessments a ON a.assessment_id = m.assessment_id
            """)

        mark_rows = cursor.fetchall()

        marks_lookup = {}
        for row in mark_rows:
            key = (row["student_id"], row["branch_subject_id"])
            marks_lookup.setdefault(key, {})[row["assessment_name"]] = row["marks_obtained"]

        student_summaries = []

        for st in students:
            cohort_key = (st["branch_id"], st["semester"])
            assigned_subjects = subjects_by_cohort.get(cohort_key, [])

            if not assigned_subjects:
                student_summaries.append({
                    "student_id": st["student_id"],
                    "roll_no": st["roll_no"],
                    "student_name": st["student_name"],
                    "branch_code": st["branch_code"],
                    "semester": st["semester"],
                    "total_marks": 0.0,
                    "percentage": 0.0,
                    "result": "INCOMPLETE",
                    "completion_status": "INCOMPLETE",
                    "failed_subjects": [],
                    "missing_assessments": ["No subjects assigned"],
                    "support_reasons": ["no_subjects_assigned"],
                    "failure_explanation": "No subjects assigned to this branch/semester yet",
                })
                continue

            subject_totals = []
            failed_subjects = []
            missing_details = []
            support_reasons = set()
            explanation_parts = []

            for bs in assigned_subjects:
                s_marks = marks_lookup.get(
                    (st["student_id"], bs["branch_subject_id"]),
                    {}
                )
                perf = calculate_subject_performance(s_marks)
                subject_totals.append(perf["total_marks"])

                if perf["missing_assessments"]:
                    for m_name in perf["missing_assessments"]:
                        missing_details.append(f"{bs['subject_code']}: {m_name}")

                if perf["result"] == "FAIL":
                    failed_subjects.append(bs["subject_code"])
                    explanation_parts.append(
                        format_failure_reason(perf, bs["subject_code"])
                    )
                    if perf["missing_mandatory_assessments"]:
                        support_reasons.add("missing_internals_or_final")
                    if not perf["overall_passed"]:
                        support_reasons.add("overall_score_below_40")
                    if not perf["final_exam_passed"]:
                        support_reasons.add("final_exam_below_40")

            avg_marks = round(sum(subject_totals) / len(subject_totals), 2)
            overall_result = "FAIL" if failed_subjects else "PASS"
            completion_status = "INCOMPLETE" if missing_details else "COMPLETE"

            student_summaries.append({
                "student_id": st["student_id"],
                "roll_no": st["roll_no"],
                "student_name": st["student_name"],
                "branch_code": st["branch_code"],
                "semester": st["semester"],
                "total_marks": avg_marks,
                "percentage": avg_marks,
                "result": overall_result,
                "completion_status": completion_status,
                "failed_subjects": failed_subjects,
                "missing_assessments": missing_details,
                "support_reasons": sorted(support_reasons),
                "failure_explanation": " | ".join(explanation_parts) if explanation_parts else "Passed all subjects",
            })

        total_students = len(student_summaries)
        passed_students = sum(s["result"] == "PASS" for s in student_summaries)
        failed_students = sum(s["result"] == "FAIL" for s in student_summaries)
        incomplete_students = sum(
            s["result"] == "INCOMPLETE" for s in student_summaries
        )

        class_average = round(
            sum(s["total_marks"] for s in student_summaries) / total_students,
            2
        ) if total_students else None

        pass_percentage = round(
            (passed_students / total_students) * 100,
            2
        ) if total_students else None

        fail_percentage = round(
            (failed_students / total_students) * 100,
            2
        ) if total_students else None

        ranked_results = sorted(
            student_summaries,
            key=lambda r: (-r["total_marks"], r["roll_no"])
        )

        for rank, r in enumerate(ranked_results, start=1):
            r["rank"] = rank

        top_performers = [
            r for r in ranked_results
            if r["result"] == "PASS"
        ][:5]

        students_needing_support = [
            r for r in ranked_results
            if r["result"] != "PASS"
        ]

        return jsonify({
            "status": "success",
            "scope": {
                "mode": "branch" if branch_id else "overall",
                "branch_id": branch_id,
                "label": scope_label,
            },
            "analytics": {
                "total_students": total_students,
                "passed_students": passed_students,
                "failed_students": failed_students,
                "incomplete_students": incomplete_students,
                "pass_percentage": pass_percentage,
                "fail_percentage": fail_percentage,
                "class_average": class_average,
                "top_performers": top_performers,
                "students_needing_support": students_needing_support,
                "all_students": ranked_results,
            }
        })

    except ValueError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc)
        }), 500

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not calculate overview analytics."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@performance_bp.route("/students/<int:student_id>/all-subjects", methods=["GET"])
def get_student_all_subjects_performance(student_id):
    if student_id <= 0:
        return jsonify({
            "status": "error",
            "message": "Student ID must be positive."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT student_id, roll_no, name, branch_id, semester
            FROM student
            WHERE student_id = %s
        """, (student_id,))
        student = cursor.fetchone()

        if not student:
            return jsonify({
                "status": "error",
                "message": "Student not found."
            }), 404

        # Get all branch subjects for this student's branch & semester
        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                s.subject_code,
                s.subject_name
            FROM branch_subjects bs
            JOIN subjects s ON s.subject_id = bs.subject_id
            WHERE bs.branch_id = %s AND bs.semester = %s
            ORDER BY s.subject_code
        """, (student["branch_id"], student["semester"]))
        assigned_subjects = cursor.fetchall()

        if not assigned_subjects:
            return jsonify({
                "status": "success",
                "student": student,
                "subjects_performance": []
            })

        bs_ids = [bs["branch_subject_id"] for bs in assigned_subjects]
        format_strings = ','.join(['%s'] * len(bs_ids))

        cursor.execute(f"""
            SELECT
                a.branch_subject_id,
                a.assessment_name,
                m.marks_obtained
            FROM assessments a
            LEFT JOIN marks m ON m.assessment_id = a.assessment_id AND m.student_id = %s
            WHERE a.branch_subject_id IN ({format_strings})
        """, [student_id] + bs_ids)
        mark_rows = cursor.fetchall()

        marks_by_bs = {}
        for row in mark_rows:
            bs_id = row["branch_subject_id"]
            marks_by_bs.setdefault(bs_id, {})[row["assessment_name"]] = row["marks_obtained"]

        subjects_performance = []
        for bs in assigned_subjects:
            bs_id = bs["branch_subject_id"]
            s_marks = marks_by_bs.get(bs_id, {})
            perf = calculate_subject_performance(s_marks)
            subjects_performance.append({
                "branch_subject_id": bs_id,
                "subject_code": bs["subject_code"],
                "subject_name": bs["subject_name"],
                "performance": perf
            })

        return jsonify({
            "status": "success",
            "student": {
                "student_id": student["student_id"],
                "roll_no": student["roll_no"],
                "name": student["name"],
                "semester": student["semester"]
            },
            "subjects_performance": subjects_performance
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve student performance across subjects."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@performance_bp.route(
    "/students/<int:student_id>/subjects/<int:branch_subject_id>",
    methods=["GET"]
)
def get_subject_performance(student_id, branch_subject_id):
    if student_id <= 0 or branch_subject_id <= 0:
        return jsonify({
            "status": "error",
            "message": "Student and subject IDs must be positive."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        ensure_default_assessments(cursor, connection, branch_subject_id)

        cursor.execute("""
            SELECT student_id, roll_no, name, branch_id, semester
            FROM student
            WHERE student_id = %s
        """, (student_id,))

        student = cursor.fetchone()

        if not student:
            return jsonify({
                "status": "error",
                "message": "Student not found."
            }), 404

        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                bs.branch_id,
                bs.semester,
                s.subject_id,
                s.subject_code,
                s.subject_name,
                a.assessment_id,
                a.assessment_name,
                a.max_marks,
                m.marks_obtained
            FROM branch_subjects bs
            JOIN subjects s
                ON s.subject_id = bs.subject_id
            LEFT JOIN assessments a
                ON a.branch_subject_id = bs.branch_subject_id
            LEFT JOIN marks m
                ON m.assessment_id = a.assessment_id
                AND m.student_id = %s
            WHERE bs.branch_subject_id = %s
            ORDER BY a.assessment_id
        """, (student_id, branch_subject_id))

        rows = cursor.fetchall()

        if not rows:
            return jsonify({
                "status": "error",
                "message": "Subject assignment not found."
            }), 404

        subject = rows[0]

        if (
            subject["branch_id"] != student["branch_id"]
            or subject["semester"] != student["semester"]
        ):
            return jsonify({
                "status": "error",
                "message": (
                    "This subject assignment does not match "
                    "the student's branch and semester."
                )
            }), 400

        expected_names = {
            "Midterm 1",
            "Midterm 2",
            "Internals",
            "Final Exam",
        }

        marks_by_assessment = {}

        for row in rows:
            assessment_name = row["assessment_name"]

            if assessment_name is None:
                continue

            if assessment_name not in expected_names:
                continue

            if assessment_name in marks_by_assessment:
                return jsonify({
                    "status": "error",
                    "message": (
                        f"Duplicate assessment definition: "
                        f"{assessment_name}."
                    )
                }), 500

            marks_by_assessment[assessment_name] = (
                row["marks_obtained"]
            )

        result = calculate_subject_performance(
            marks_by_assessment
        )

        return jsonify({
            "status": "success",
            "student": {
                "student_id": student["student_id"],
                "roll_no": student["roll_no"],
                "name": student["name"],
                "semester": student["semester"],
            },
            "subject": {
                "branch_subject_id": subject["branch_subject_id"],
                "subject_code": subject["subject_code"],
                "subject_name": subject["subject_name"],
            },
            "performance": result,
        })

    except ValueError as exc:
        return jsonify({
            "status": "error",
            "message": str(exc)
        }), 500

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not calculate subject performance."
        }), 500

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()


@performance_bp.route(
    "/subjects/<int:branch_subject_id>/analytics",
    methods=["GET"]
)
def get_subject_analytics(branch_subject_id):
    connection = None
    cursor = None

    expected_maximums = {
        "Midterm 1": 50,
        "Midterm 2": 50,
        "Internals": 20,
        "Final Exam": 100,
    }

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                bs.branch_id,
                bs.semester,
                b.branch_code,
                s.subject_code,
                s.subject_name
            FROM branch_subjects bs
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects s ON s.subject_id = bs.subject_id
            WHERE bs.branch_subject_id = %s
        """, (branch_subject_id,))

        subject = cursor.fetchone()

        if not subject:
            return jsonify({
                "status": "error",
                "message": "Subject assignment not found."
            }), 404

        ensure_default_assessments(cursor, connection, branch_subject_id)

        cursor.execute("""
            SELECT assessment_id, assessment_name, max_marks
            FROM assessments
            WHERE branch_subject_id = %s
        """, (branch_subject_id,))

        assessments = cursor.fetchall()
        assessment_ids = {}

        for assessment in assessments:
            name = assessment["assessment_name"]

            if name not in expected_maximums:
                continue

            if name in assessment_ids:
                return jsonify({
                    "status": "error",
                    "message": (
                        f"Duplicate assessment definition: {name}."
                    )
                }), 409

            if float(assessment["max_marks"]) != expected_maximums[name]:
                return jsonify({
                    "status": "error",
                    "message": (
                        f"Unexpected maximum marks for {name}."
                    )
                }), 409

            assessment_ids[name] = assessment["assessment_id"]

        missing_definitions = [
            name for name in expected_maximums
            if name not in assessment_ids
        ]

        if missing_definitions:
            return jsonify({
                "status": "error",
                "message": "Required assessment definitions are missing.",
                "missing_assessments": missing_definitions
            }), 409

        cursor.execute("""
            SELECT student_id, roll_no, name AS student_name
            FROM student
            WHERE branch_id = %s AND semester = %s
            ORDER BY roll_no
        """, (subject["branch_id"], subject["semester"]))

        students = cursor.fetchall()

        if not students:
            return jsonify({
                "status": "success",
                "subject": {
                    "branch_subject_id": branch_subject_id,
                    "subject_code": subject["subject_code"],
                    "subject_name": subject["subject_name"],
                    "branch_code": subject["branch_code"],
                    "semester": subject["semester"],
                },
                "analytics": {
                    "total_students": 0,
                    "passed_students": 0,
                    "failed_students": 0,
                    "incomplete_students": 0,
                    "pass_percentage": None,
                    "fail_percentage": None,
                    "class_average": None,
                    "top_performers": [],
                    "students_needing_support": [],
                }
            })

        cursor.execute("""
            SELECT
                m.student_id,
                a.assessment_name,
                m.marks_obtained
            FROM assessments a
            LEFT JOIN marks m
                ON m.assessment_id = a.assessment_id
            WHERE a.branch_subject_id = %s
              AND a.assessment_id IN (%s, %s, %s, %s)
        """, (
            branch_subject_id,
            assessment_ids["Midterm 1"],
            assessment_ids["Midterm 2"],
            assessment_ids["Internals"],
            assessment_ids["Final Exam"],
        ))

        mark_rows = cursor.fetchall()

        student_records = {}

        for student in students:
            student_records[student["student_id"]] = {
                "student_id": student["student_id"],
                "roll_no": student["roll_no"],
                "student_name": student["student_name"],
                "marks": {},
            }

        for row in mark_rows:
            student_id = row["student_id"]
            assessment_name = row["assessment_name"]

            if student_id is None:
                continue

            if student_id in student_records:
                student_records[student_id]["marks"][
                    assessment_name
                ] = row["marks_obtained"]

        results = []

        for student in student_records.values():
            performance = calculate_subject_performance(
                student["marks"]
            )

            completion_status = (
                "INCOMPLETE"
                if performance["missing_assessments"]
                else "COMPLETE"
            )

            failure_explanation = (
                "Passed subject"
                if performance["result"] == "PASS"
                else format_failure_reason(performance, subject["subject_code"])
            )

            results.append({
                "student_id": student["student_id"],
                "roll_no": student["roll_no"],
                "student_name": student["student_name"],
                "total_marks": performance["total_marks"],
                "percentage": performance["percentage"],
                "result": performance["result"],
                "completion_status": completion_status,
                "missing_assessments": (
                    performance["missing_assessments"]
                ),
                "missing_mandatory_assessments": (
                    performance["missing_mandatory_assessments"]
                ),
                "overall_passed": performance["overall_passed"],
                "final_exam_passed": (
                    performance["final_exam_passed"]
                ),
                "failure_explanation": failure_explanation,
            })

        total_students = len(results)

        passed_students = sum(
            result["result"] == "PASS" for result in results
        )

        failed_students = sum(
            result["result"] == "FAIL" for result in results
        )

        incomplete_students = sum(
            result["result"] == "INCOMPLETE" for result in results
        )

        class_average = round(
            sum(result["total_marks"] for result in results)
            / total_students,
            2,
        )

        pass_percentage = round(
            (passed_students / total_students) * 100,
            2,
        )

        fail_percentage = round(
            (failed_students / total_students) * 100,
            2,
        )

        ranked_results = sorted(
            results,
            key=lambda result: (
                -result["total_marks"],
                result["roll_no"],
            ),
        )

        for rank, result in enumerate(ranked_results, start=1):
            result["rank"] = rank

        students_needing_support = []

        for result in ranked_results:
            if result["result"] == "PASS":
                continue

            reasons = []

            if result["missing_mandatory_assessments"]:
                reasons.append("missing_internals_or_final")

            if not result["overall_passed"]:
                reasons.append("overall_score_below_40")

            if not result["final_exam_passed"]:
                reasons.append("final_exam_below_40")

            support_record = dict(result)
            support_record["support_reasons"] = reasons
            students_needing_support.append(support_record)

        return jsonify({
            "status": "success",
            "subject": {
                "branch_subject_id": branch_subject_id,
                "subject_code": subject["subject_code"],
                "subject_name": subject["subject_name"],
                "branch_code": subject["branch_code"],
                "semester": subject["semester"],
            },
            "analytics": {
                "total_students": total_students,
                "passed_students": passed_students,
                "failed_students": failed_students,
                "incomplete_students": incomplete_students,
                "pass_percentage": pass_percentage,
                "fail_percentage": fail_percentage,
                "class_average": class_average,
                "top_performers": [
                    result
                    for result in ranked_results
                    if result["result"] == "PASS"
                ][:5],
                "students_needing_support": (
                    students_needing_support
                ),
                "all_students": ranked_results,
            },
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not calculate class analytics."
        }), 500

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()