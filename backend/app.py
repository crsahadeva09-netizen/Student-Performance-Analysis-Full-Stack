import os
from routes.assessments import assessments_bp
from routes.students import students_bp
from routes.subjects import subjects_bp
from routes.marks import marks_bp
from routes.performance import performance_bp
from routes.attendance import attendance_bp
from functools import wraps

from flask import Flask, jsonify, request, session
from flask_cors import CORS
from werkzeug.security import check_password_hash
from dotenv import load_dotenv

from database import get_db_connection

load_dotenv()

app = Flask(__name__)

secret_key = os.getenv("FLASK_SECRET_KEY")
if not secret_key:
    raise RuntimeError("Set FLASK_SECRET_KEY in your .env file.")

app.config.update(
    SECRET_KEY=secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False  # Local development only; use True with HTTPS
)

ALLOWED_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:5501",
    "http://localhost:5501",
    "http://127.0.0.1:5502",
    "http://localhost:5502",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

CORS(
    app,
    origins=ALLOWED_ORIGINS,
    supports_credentials=True
)


@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = app.make_default_options_response()
        return response


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response


app.register_blueprint(subjects_bp)
app.register_blueprint(students_bp)
app.register_blueprint(marks_bp)
app.register_blueprint(assessments_bp)
app.register_blueprint(performance_bp)
app.register_blueprint(attendance_bp)


def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        # Allow CORS preflight requests before checking authentication.
        if request.method == "OPTIONS":
            return "", 204

        if "user_id" not in session:
            return jsonify({
                "status": "error",
                "message": "Please log in first."
            }), 401

        return route_function(*args, **kwargs)

    return wrapper


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "success",
        "message": "Student Performance Analysis System API is running"
    })


@app.route("/api/db-health", methods=["GET"])
def db_health():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        database_name = cursor.fetchone()[0]

        return jsonify({
            "status": "success",
            "database": database_name,
            "message": "MySQL connection successful"
        })

    except Exception:
        app.logger.exception("Database connection failed")
        return jsonify({
            "status": "error",
            "message": "Database connection failed"
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not isinstance(password, str) or not password:
        return jsonify({
            "status": "error",
            "message": "Username and password are required."
        }), 400

    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT user_id, username, password_hash, role, is_active
            FROM users
            WHERE username = %s
            """,
            (username,)
        )
        user = cursor.fetchone()

        if (
            not user
            or not user["is_active"]
            or not check_password_hash(user["password_hash"], password)
        ):
            return jsonify({
                "status": "error",
                "message": "Invalid username or password."
            }), 401

        session.clear()
        session["user_id"] = user["user_id"]
        session["username"] = user["username"]
        session["role"] = user["role"]

        return jsonify({
            "status": "success",
            "message": "Login successful.",
            "user": {
                "username": user["username"],
                "role": user["role"]
            }
        })

    except Exception:
        app.logger.exception("Login failed")
        return jsonify({
            "status": "error",
            "message": "Unable to process login."
        }), 500

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()


@app.route("/api/auth/me", methods=["GET"])
@login_required
def current_user():
    return jsonify({
        "status": "success",
        "user": {
            "user_id": session["user_id"],
            "username": session["username"],
            "role": session["role"]
        }
    })


@app.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return jsonify({
        "status": "success",
        "message": "Logged out successfully."
    })


@app.route("/api/protected-test", methods=["GET"])
@login_required
def protected_test():
    return jsonify({
        "status": "success",
        "message": "You are authenticated.",
        "username": session["username"]
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=True)