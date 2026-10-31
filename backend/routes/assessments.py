from flask import Blueprint, jsonify, request, session
from mysql.connector import IntegrityError, Error

from database import get_db_connection

assessments_bp = Blueprint(
    "assessments",
    __name__,
    url_prefix="/api/assessments"
)

DEFAULT_ASSESSMENTS = (
    ("Midterm 1", 50),
    ("Midterm 2", 50),
    ("Internals", 20),
    ("Final Exam", 100),
)


def ensure_default_assessments(cursor, connection, branch_subject_id):
    """Ensure the 4 standard assessments exist for a valid branch_subject_id."""
    cursor.execute(
        "SELECT branch_subject_id FROM branch_subjects WHERE branch_subject_id = %s",
        (branch_subject_id,)
    )
    if not cursor.fetchone():
        return False

    cursor.execute(
        "SELECT assessment_name FROM assessments WHERE branch_subject_id = %s",
        (branch_subject_id,)
    )
    existing = {row["assessment_name"] for row in cursor.fetchall()}

    inserted = False
    for name, max_marks in DEFAULT_ASSESSMENTS:
        if name not in existing:
            cursor.execute(
                """
                INSERT INTO assessments (branch_subject_id, assessment_name, max_marks)
                VALUES (%s, %s, %s)
                """,
                (branch_subject_id, name, max_marks)
            )
            inserted = True

    if inserted:
        connection.commit()

    return True


@assessments_bp.before_request
def require_login():
    if request.method == "OPTIONS":
        return None

    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "Please log in first."
        }), 401


def validate_assessment(data):
    if not isinstance(data, dict):
        return "Request body must be a JSON object."

    branch_subject_id = data.get("branch_subject_id")
    assessment_name = data.get("assessment_name")
    max_marks = data.get("max_marks")

    if (
        isinstance(branch_subject_id, bool)
        or not isinstance(branch_subject_id, int)
        or branch_subject_id <= 0
    ):
        return "branch_subject_id must be a positive integer."

    if not isinstance(assessment_name, str) or not assessment_name.strip():
        return "assessment_name is required."

    if len(assessment_name.strip()) > 50:
        return "assessment_name must not exceed 50 characters."

    if isinstance(max_marks, bool) or not isinstance(max_marks, (int, float)):
        return "max_marks must be a number."

    if max_marks <= 0 or max_marks > 9999.99:
        return "max_marks must be greater than 0 and at most 9999.99."

    allowed_marks = {
        "Midterm 1": 50,
        "Midterm 2": 50,
        "Internals": 20,
        "Final Exam": 100
    }

    expected_marks = allowed_marks.get(assessment_name.strip())

    if expected_marks is not None and max_marks != expected_marks:
        return (
            f"{assessment_name.strip()} must have "
            f"max_marks = {expected_marks}."
        )

    return None


@assessments_bp.route("", methods=["GET"])
def get_assessments():
    branch_subject_id = request.args.get(
        "branch_subject_id", type=int
    )

    if branch_subject_id is None or branch_subject_id <= 0:
        return jsonify({
            "status": "error",
            "message": "Provide a valid branch_subject_id."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        ensure_default_assessments(cursor, connection, branch_subject_id)

        cursor.execute("""
            SELECT
                a.assessment_id,
                a.branch_subject_id,
                a.assessment_name,
                a.max_marks,
                bs.branch_id,
                b.branch_code,
                bs.semester,
                s.subject_code,
                s.subject_name
            FROM assessments a
            JOIN branch_subjects bs
                ON bs.branch_subject_id = a.branch_subject_id
            JOIN branches b
                ON b.branch_id = bs.branch_id
            JOIN subjects s
                ON s.subject_id = bs.subject_id
            WHERE a.branch_subject_id = %s
            ORDER BY a.assessment_id
        """, (branch_subject_id,))

        return jsonify({
            "status": "success",
            "assessments": cursor.fetchall()
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve assessments."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@assessments_bp.route("/<int:assessment_id>", methods=["GET"])
def get_assessment(assessment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                assessment_id,
                branch_subject_id,
                assessment_name,
                max_marks
            FROM assessments
            WHERE assessment_id = %s
        """, (assessment_id,))

        assessment = cursor.fetchone()

        if not assessment:
            return jsonify({
                "status": "error",
                "message": "Assessment not found."
            }), 404

        return jsonify({
            "status": "success",
            "assessment": assessment
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve the assessment."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@assessments_bp.route("", methods=["POST"])
def add_assessment():
    data = request.get_json(silent=True)
    error = validate_assessment(data)

    if error:
        return jsonify({
            "status": "error",
            "message": error
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO assessments
                (branch_subject_id, assessment_name, max_marks)
            VALUES (%s, %s, %s)
        """, (
            data["branch_subject_id"],
            data["assessment_name"].strip(),
            data["max_marks"]
        ))

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Assessment created successfully.",
            "assessment_id": cursor.lastrowid
        }), 201

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1452:
            return jsonify({
                "status": "error",
                "message": "The branch-subject assignment does not exist."
            }), 400

        return jsonify({
            "status": "error",
            "message": "Assessment conflicts with database constraints."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not create assessment."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@assessments_bp.route("/<int:assessment_id>", methods=["PUT"])
def update_assessment(assessment_id):
    data = request.get_json(silent=True)
    error = validate_assessment(data)

    if error:
        return jsonify({
            "status": "error",
            "message": error
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE assessments
            SET branch_subject_id = %s,
                assessment_name = %s,
                max_marks = %s
            WHERE assessment_id = %s
        """, (
            data["branch_subject_id"],
            data["assessment_name"].strip(),
            data["max_marks"],
            assessment_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT assessment_id FROM assessments "
                "WHERE assessment_id = %s",
                (assessment_id,)
            )

            if not cursor.fetchone():
                connection.rollback()
                return jsonify({
                    "status": "error",
                    "message": "Assessment not found."
                }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Assessment updated successfully."
        })

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1452:
            return jsonify({
                "status": "error",
                "message": "The branch-subject assignment does not exist."
            }), 400

        return jsonify({
            "status": "error",
            "message": "Assessment conflicts with database constraints."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not update assessment."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@assessments_bp.route("/<int:assessment_id>", methods=["DELETE"])
def delete_assessment(assessment_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM assessments WHERE assessment_id = %s",
            (assessment_id,)
        )

        if cursor.rowcount == 0:
            connection.rollback()
            return jsonify({
                "status": "error",
                "message": "Assessment not found."
            }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Assessment deleted successfully."
        })

    except IntegrityError:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": (
                "This assessment has related marks. "
                "Remove dependent marks before deleting it."
            )
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not delete assessment."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()