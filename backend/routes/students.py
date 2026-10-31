from datetime import date, timedelta
from flask import Blueprint, jsonify, request, session
from mysql.connector import IntegrityError, Error

from database import get_db_connection

students_bp = Blueprint(
    "students",
    __name__,
    url_prefix="/api/students"
)


@students_bp.before_request
def require_login():
    if request.method == "OPTIONS":
        return None

    if "user_id" not in session:
        return jsonify({
            "status": "error",
            "message": "Please log in first."
        }), 401


def validate_student(data, require_roll_and_section=False):
    if not isinstance(data, dict):
        return "Request body must be a JSON object."

    if not isinstance(data.get("name"), str) or not data["name"].strip():
        return "name is required."

    if len(data["name"].strip()) > 120:
        return "Name must not exceed 120 characters."

    if require_roll_and_section:
        for field in ("roll_no", "section"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                return f"{field} is required."

    if isinstance(data.get("roll_no"), str) and len(data["roll_no"].strip()) > 30:
        return "Roll number must not exceed 30 characters."

    if isinstance(data.get("section"), str) and len(data["section"].strip()) > 10:
        return "Section must not exceed 10 characters."

    branch_id = data.get("branch_id")
    semester = data.get("semester", 3)

    if isinstance(branch_id, bool) or not isinstance(branch_id, int) or branch_id <= 0:
        return "branch_id must be a positive integer."

    if isinstance(semester, bool) or not isinstance(semester, int) or not 1 <= semester <= 12:
        return "semester must be an integer between 1 and 12."

    return None


@students_bp.route("", methods=["GET"])
def get_students():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.student_id,
                s.roll_no,
                s.name,
                s.branch_id,
                b.branch_code,
                b.branch_name,
                s.semester,
                s.section
            FROM student s
            JOIN branches b ON b.branch_id = s.branch_id
            ORDER BY s.student_id
        """)

        return jsonify({
            "status": "success",
            "students": cursor.fetchall()
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve students."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@students_bp.route("/<int:student_id>", methods=["GET"])
def get_student(student_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.student_id,
                s.roll_no,
                s.name,
                s.branch_id,
                b.branch_code,
                b.branch_name,
                s.semester,
                s.section
            FROM student s
            JOIN branches b ON b.branch_id = s.branch_id
            WHERE s.student_id = %s
        """, (student_id,))

        student = cursor.fetchone()

        if not student:
            return jsonify({
                "status": "error",
                "message": "Student not found."
            }), 404

        return jsonify({
            "status": "success",
            "student": student
        })

    except Error:
        return jsonify({
            "status": "error",
            "message": "Could not retrieve the student."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@students_bp.route("", methods=["POST"])
def add_student():
    data = request.get_json(silent=True)
    error = validate_student(data, require_roll_and_section=False)

    if error:
        return jsonify({
            "status": "error",
            "message": error
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        branch_id = data["branch_id"]
        name = data["name"].strip()
        semester = data.get("semester") if isinstance(data.get("semester"), int) else 3
        section = (data.get("section") or "A").strip() or "A"
        roll_no = (data.get("roll_no") or "").strip()

        cursor.execute(
            "SELECT branch_code FROM branches WHERE branch_id = %s",
            (branch_id,)
        )
        branch_row = cursor.fetchone()
        if not branch_row:
            return jsonify({
                "status": "error",
                "message": "The specified branch does not exist."
            }), 400

        if not roll_no:
            prefix = branch_row["branch_code"].replace("-", "").upper()
            cursor.execute(
                "SELECT COUNT(*) + 1 AS next_num FROM student WHERE branch_id = %s",
                (branch_id,)
            )
            next_num = cursor.fetchone()["next_num"]
            roll_no = f"{prefix}{next_num:03d}"

            while True:
                cursor.execute("SELECT student_id FROM student WHERE roll_no = %s", (roll_no,))
                if not cursor.fetchone():
                    break
                next_num += 1
                roll_no = f"{prefix}{next_num:03d}"

        cursor.execute("""
            INSERT INTO student
                (roll_no, name, branch_id, semester, section)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            roll_no,
            name,
            branch_id,
            semester,
            section
        ))

        new_student_id = cursor.lastrowid
        connection.commit()

        return jsonify({
            "status": "success",
            "message": f"Student '{name}' ({roll_no}) created successfully.",
            "student_id": new_student_id,
            "roll_no": roll_no
        }), 201

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1062:
            return jsonify({
                "status": "error",
                "message": "That roll number already exists."
            }), 409

        if exc.errno == 1452:
            return jsonify({
                "status": "error",
                "message": "The specified branch does not exist."
            }), 400

        return jsonify({
            "status": "error",
            "message": "Student conflicts with database constraints."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not create student."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@students_bp.route("/<int:student_id>", methods=["PUT"])
def update_student(student_id):
    data = request.get_json(silent=True)
    error = validate_student(data, require_roll_and_section=True)

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
            UPDATE student
            SET roll_no = %s,
                name = %s,
                branch_id = %s,
                semester = %s,
                section = %s
            WHERE student_id = %s
        """, (
            data["roll_no"].strip(),
            data["name"].strip(),
            data["branch_id"],
            data["semester"],
            data["section"].strip(),
            student_id
        ))

        if cursor.rowcount == 0:
            cursor.execute(
                "SELECT student_id FROM student WHERE student_id = %s",
                (student_id,)
            )

            if not cursor.fetchone():
                connection.rollback()
                return jsonify({
                    "status": "error",
                    "message": "Student not found."
                }), 404

        connection.commit()

        return jsonify({
            "status": "success",
            "message": "Student updated successfully."
        })

    except IntegrityError as exc:
        if connection:
            connection.rollback()

        if exc.errno == 1062:
            return jsonify({
                "status": "error",
                "message": "That roll number already exists."
            }), 409

        if exc.errno == 1452:
            return jsonify({
                "status": "error",
                "message": "The specified branch does not exist."
            }), 400

        return jsonify({
            "status": "error",
            "message": "Student conflicts with database constraints."
        }), 409

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not update student."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@students_bp.route("/<int:student_id>", methods=["DELETE"])
def delete_student(student_id):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT student_id, roll_no, name FROM student WHERE student_id = %s",
            (student_id,)
        )
        student = cursor.fetchone()
        if not student:
            return jsonify({
                "status": "error",
                "message": "Student not found."
            }), 404

        # Safely delete dependent marks and attendance records first within the transaction
        cursor.execute("DELETE FROM marks WHERE student_id = %s", (student_id,))
        cursor.execute("DELETE FROM attendance_records WHERE student_id = %s", (student_id,))
        cursor.execute("DELETE FROM student WHERE student_id = %s", (student_id,))

        connection.commit()

        return jsonify({
            "status": "success",
            "message": f"Student '{student['name']}' ({student['roll_no']}) deleted successfully."
        })

    except Error:
        if connection:
            connection.rollback()

        return jsonify({
            "status": "error",
            "message": "Could not delete student."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()