
from flask import Blueprint, jsonify, request, session
from mysql.connector import IntegrityError, Error

from database import get_db_connection

subjects_bp = Blueprint(
    "subjects",
    __name__,
    url_prefix="/api/subjects"
)


@subjects_bp.before_request
def require_login():
    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "Please log in first."
        }), 401


def validate_subject(data):
    if not isinstance(data, dict):
        return "Request body must be a JSON object."

    for field in ("subject_code", "subject_name"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            return f"{field} is required."

    if len(data["subject_code"].strip()) > 30:
        return "Subject code must not exceed 30 characters."

    if len(data["subject_name"].strip()) > 120:
        return "Subject name must not exceed 120 characters."

    return None


@subjects_bp.route("", methods=["GET"])
def get_subjects():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT subject_id, subject_code, subject_name
            FROM subjects
            ORDER BY subject_code
        """)

        return jsonify({
            "status": "success",
            "subjects": cursor.fetchall()
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve subjects."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@subjects_bp.route("/<int:subject_id>", methods=["GET"])
def get_subject(subject_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT subject_id, subject_code, subject_name
            FROM subjects
            WHERE subject_id = %s
        """, (subject_id,))

        subject = cursor.fetchone()

        if not subject:
            return jsonify({
                "status": "error",
                "message": "Subject not found."
            }), 404

        return jsonify({"status": "success", "subject": subject})

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve the subject."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@subjects_bp.route("", methods=["POST"])
def add_subject():
    data = request.get_json(silent=True)
    error = validate_subject(data)

    if error:
        return jsonify({"status": "error", "message": error}), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            INSERT INTO subjects (subject_code, subject_name)
            VALUES (%s, %s)
        """, (
            data["subject_code"].strip(),
            data["subject_name"].strip()
        ))

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Subject created successfully.",
            "subject_id": cursor.lastrowid
        }), 201

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1062:
            return jsonify({
                "status": "error",
                "message": "That subject code already exists."
            }), 409

        return jsonify({
            "status": "error",
            "message": "Subject conflicts with existing database records."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not create subject."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@subjects_bp.route("/<int:subject_id>", methods=["PUT"])
def update_subject(subject_id):
    data = request.get_json(silent=True)
    error = validate_subject(data)

    if error:
        return jsonify({"status": "error", "message": error}), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE subjects
            SET subject_code = %s, subject_name = %s
            WHERE subject_id = %s
        """, (
            data["subject_code"].strip(),
            data["subject_name"].strip(),
            subject_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT subject_id FROM subjects WHERE subject_id = %s",
                (subject_id,)
            )

            if not cursor.fetchone():
                connection.rollback()
                return jsonify({
                    "status": "error",
                    "message": "Subject not found."
                }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Subject updated successfully."
        })

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1062:
            return jsonify({
                "status": "error",
                "message": "That subject code already exists."
            }), 409

        return jsonify({
            "status": "error",
            "message": "Subject conflicts with existing database records."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not update subject."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@subjects_bp.route("/<int:subject_id>", methods=["DELETE"])
def delete_subject(subject_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM subjects WHERE subject_id = %s",
            (subject_id,)
        )

        if cursor.rowcount == 0:
            connection.rollback()
            return jsonify({
                "status": "error",
                "message": "Subject not found."
            }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Subject deleted successfully."
        })

    except IntegrityError:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": (
                "This subject is assigned to a branch or has related records. "
                "Remove its dependent records first."
            )
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not delete subject."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@subjects_bp.route("/assignments", methods=["GET"])
def get_branch_subjects():
    branch_id = request.args.get("branch_id", type=int)
    semester = request.args.get("semester", type=int)

    if branch_id is None or branch_id <= 0:
        return jsonify({
            "status": "error",
            "message": "Provide a valid branch_id."
        }), 400

    if semester is None or not 1 <= semester <= 12:
        return jsonify({
            "status": "error",
            "message": "Provide a semester between 1 and 12."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                bs.branch_subject_id,
                b.branch_code,
                bs.semester,
                s.subject_id,
                s.subject_code,
                s.subject_name,
                bs.max_marks,
                bs.pass_marks
            FROM branch_subjects bs
            JOIN branches b ON b.branch_id = bs.branch_id
            JOIN subjects s ON s.subject_id = bs.subject_id
            WHERE bs.branch_id = %s AND bs.semester = %s
            ORDER BY s.subject_code
        """, (branch_id, semester))

        return jsonify({
            "status": "success",
            "assignments": cursor.fetchall()
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve subject assignments."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()
