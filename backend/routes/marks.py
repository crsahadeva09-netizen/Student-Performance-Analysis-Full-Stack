
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session
from mysql.connector import IntegrityError, Error

from database import get_db_connection

marks_bp = Blueprint(
    "marks",
    __name__,
    url_prefix="/api/marks"
)

@marks_bp.before_request
def require_login():
    # Let Flask-CORS handle preflight requests before authentication.
    if request.method == "OPTIONS":
        return None

    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "Please log in first."
        }), 401


def parse_marks(data):
    if not isinstance(data, dict):
        return None, "Request body must be a JSON object."

    student_id = data.get("student_id")
    assessment_id = data.get("assessment_id")
    raw_marks = data.get("marks_obtained")

    if (
        isinstance(student_id, bool)
        or not isinstance(student_id, int)
        or student_id <= 0
    ):
        return None, "student_id must be a positive integer."

    if (
        isinstance(assessment_id, bool)
        or not isinstance(assessment_id, int)
        or assessment_id <= 0
    ):
        return None, "assessment_id must be a positive integer."

    if raw_marks is None or isinstance(raw_marks, bool):
        return None, "marks_obtained is required and must be numeric."

    try:
        obtained = Decimal(str(raw_marks))
    except (InvalidOperation, ValueError):
        return None, "marks_obtained must be numeric."

    if not obtained.is_finite():
        return None, "marks_obtained must be a finite number."

    if obtained < 0:
        return None, "marks_obtained cannot be negative."

    if obtained.as_tuple().exponent < -2:
        return None, "marks_obtained cannot have more than 2 decimal places."

    return {
        "student_id": student_id,
        "assessment_id": assessment_id,
        "marks_obtained": obtained
    }, None


def validate_max_marks(cursor, assessment_id, obtained):
    cursor.execute("""
        SELECT max_marks
        FROM assessments
        WHERE assessment_id = %s
    """, (assessment_id,))

    assessment = cursor.fetchone()

    if not assessment:
        return "Assessment not found."

    maximum = Decimal(str(assessment["max_marks"]))

    if obtained > maximum:
        return f"Marks cannot exceed the assessment maximum of {maximum}."

    return None


@marks_bp.route("", methods=["GET"])
def get_marks():
    student_id = request.args.get("student_id", type=int)
    assessment_id = request.args.get("assessment_id", type=int)

    if student_id is not None and student_id <= 0:
        return jsonify({
            "status": "error",
            "message": "student_id must be positive."
        }), 400

    if assessment_id is not None and assessment_id <= 0:
        return jsonify({
            "status": "error",
            "message": "assessment_id must be positive."
        }), 400

    if student_id is None and assessment_id is None:
        return jsonify({
            "status": "error",
            "message": "Provide student_id or assessment_id."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                m.mark_id,
                st.student_id,
                st.roll_no,
                st.name AS student_name,
                a.assessment_id,
                a.assessment_name,
                a.max_marks,
                m.marks_obtained,
                bs.branch_subject_id,
                b.branch_code,
                bs.semester,
                s.subject_code,
                s.subject_name
            FROM marks m
            JOIN student st ON st.student_id = m.student_id
            JOIN assessments a ON a.assessment_id = m.assessment_id
            JOIN branch_subjects bs
                ON bs.branch_subject_id = a.branch_subject_id
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects s ON s.subject_id = bs.subject_id
            WHERE 1 = 1
        """
        params = []

        if student_id is not None:
            query += " AND m.student_id = %s"
            params.append(student_id)

        if assessment_id is not None:
            query += " AND m.assessment_id = %s"
            params.append(assessment_id)

        query += " ORDER BY st.student_id, a.assessment_id"

        cursor.execute(query, tuple(params))

        return jsonify({
            "status": "success",
            "marks": cursor.fetchall()
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve marks."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@marks_bp.route("/<int:mark_id>", methods=["GET"])
def get_mark(mark_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                mark_id,
                student_id,
                assessment_id,
                marks_obtained
            FROM marks
            WHERE mark_id = %s
        """, (mark_id,))

        mark = cursor.fetchone()

        if not mark:
            return jsonify({
                "status": "error",
                "message": "Marks record not found."
            }), 404

        return jsonify({
            "status": "success",
            "mark": mark
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve the marks record."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@marks_bp.route("", methods=["POST"])
def add_mark():
    data, error = parse_marks(request.get_json(silent=True))

    if error:
        return jsonify({"status": "error", "message": error}), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        error = validate_max_marks(
            cursor,
            data["assessment_id"],
            data["marks_obtained"]
        )

        if error:
            return jsonify({"status": "error", "message": error}), 400

        cursor.execute("""
            SELECT mark_id
            FROM marks
            WHERE student_id = %s AND assessment_id = %s
        """, (data["student_id"], data["assessment_id"]))

        if cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": "Marks already exist for this student and assessment."
            }), 409

        cursor.execute("""
            INSERT INTO marks
                (student_id, assessment_id, marks_obtained)
            VALUES (%s, %s, %s)
        """, (
            data["student_id"],
            data["assessment_id"],
            data["marks_obtained"]
        ))

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Marks recorded successfully.",
            "mark_id": cursor.lastrowid
        }), 201

    except IntegrityError:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": (
                "The student or assessment does not exist, "
                "or the marks record violates a database constraint."
            )
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not record marks."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@marks_bp.route("/<int:mark_id>", methods=["PUT"])
def update_mark(mark_id):
    data, error = parse_marks(request.get_json(silent=True))

    if error:
        return jsonify({"status": "error", "message": error}), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT mark_id FROM marks WHERE mark_id = %s",
            (mark_id,)
        )

        if not cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": "Marks record not found."
            }), 404

        error = validate_max_marks(
            cursor,
            data["assessment_id"],
            data["marks_obtained"]
        )

        if error:
            return jsonify({"status": "error", "message": error}), 400

        cursor.execute("""
            SELECT mark_id
            FROM marks
            WHERE student_id = %s
              AND assessment_id = %s
              AND mark_id <> %s
        """, (
            data["student_id"],
            data["assessment_id"],
            mark_id
        ))

        if cursor.fetchone():
            return jsonify({
                "status": "error",
                "message": "Another marks record already exists for this student and assessment."
            }), 409

        cursor.execute("""
            UPDATE marks
            SET student_id = %s,
                assessment_id = %s,
                marks_obtained = %s
            WHERE mark_id = %s
        """, (
            data["student_id"],
            data["assessment_id"],
            data["marks_obtained"],
            mark_id
        ))

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Marks updated successfully."
        })

    except IntegrityError:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "The student or assessment does not exist."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not update marks."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@marks_bp.route("/<int:mark_id>", methods=["DELETE"])
def delete_mark(mark_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM marks WHERE mark_id = %s",
            (mark_id,)
        )

        if cursor.rowcount == 0:
            connection.rollback()
            return jsonify({
                "status": "error",
                "message": "Marks record not found."
            }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Marks record deleted successfully."
        })

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not delete marks record."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()
