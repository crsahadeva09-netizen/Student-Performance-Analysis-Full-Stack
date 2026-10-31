from unittest.mock import MagicMock, patch

import pytest

from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key")

    with app.test_client() as test_client:
        with test_client.session_transaction() as session:
            session["user_id"] = 1
            session["username"] = "test_admin"
            session["role"] = "admin"

        yield test_client


def make_assessments():
    return [
        {"assessment_id": 1, "assessment_name": "Midterm 1", "max_marks": 50},
        {"assessment_id": 2, "assessment_name": "Midterm 2", "max_marks": 50},
        {"assessment_id": 3, "assessment_name": "Internals", "max_marks": 20},
        {"assessment_id": 4, "assessment_name": "Final Exam", "max_marks": 100},
    ]


def make_students():
    return [
        {"student_id": 1, "roll_no": "CSE001", "student_name": "Asha"},
        {"student_id": 2, "roll_no": "CSE002", "student_name": "Bala"},
        {"student_id": 3, "roll_no": "CSE003", "student_name": "Charan"},
    ]


def make_marks():
    # Asha: 40, 35, 18, 80 -> total 81.4 (PASS)
    # Bala: missing Midterm 1, Midterm 2=20, Internals=15, Final Exam=50 -> total 49.6 (PASS)
    # Charan: no marks -> missing Internals & Final Exam -> FAIL
    return [
        {"student_id": 1, "assessment_name": "Midterm 1", "marks_obtained": 40},
        {"student_id": 1, "assessment_name": "Midterm 2", "marks_obtained": 35},
        {"student_id": 1, "assessment_name": "Internals", "marks_obtained": 18},
        {"student_id": 1, "assessment_name": "Final Exam", "marks_obtained": 80},
        {"student_id": 2, "assessment_name": "Midterm 2", "marks_obtained": 20},
        {"student_id": 2, "assessment_name": "Internals", "marks_obtained": 15},
        {"student_id": 2, "assessment_name": "Final Exam", "marks_obtained": 50},
    ]


def setup_analytics_database(mock_connection):
    cursor = MagicMock()
    mock_connection.cursor.return_value = cursor
    mock_connection.is_connected.return_value = True

    subject = {
        "branch_subject_id": 1,
        "branch_id": 1,
        "semester": 3,
        "branch_code": "CSE",
        "subject_code": "CS301",
        "subject_name": "Database Management Systems",
    }

    cursor.fetchone.return_value = subject
    cursor.fetchall.side_effect = [
        make_assessments(),
        make_students(),
        make_marks(),
    ]

    return cursor


@patch("routes.performance.get_db_connection")
def test_analytics_calculates_class_statistics(mock_get_db, client):
    connection = MagicMock()
    mock_get_db.return_value = connection
    setup_analytics_database(connection)

    response = client.get("/api/performance/subjects/1/analytics")

    assert response.status_code == 200
    data = response.get_json()
    analytics = data["analytics"]

    assert data["status"] == "success"
    assert analytics["total_students"] == 3
    assert analytics["passed_students"] == 2
    assert analytics["failed_students"] == 1
    assert analytics["incomplete_students"] == 0
    assert analytics["pass_percentage"] == pytest.approx(66.67)
    assert analytics["class_average"] == pytest.approx(43.67)


@patch("routes.performance.get_db_connection")
def test_analytics_ranks_passing_students_including_missed_midterm(mock_get_db, client):
    connection = MagicMock()
    mock_get_db.return_value = connection
    setup_analytics_database(connection)

    response = client.get("/api/performance/subjects/1/analytics")
    analytics = response.get_json()["analytics"]

    top_performers = analytics["top_performers"]

    assert [student["student_name"] for student in top_performers] == [
        "Asha", "Bala"
    ]
    assert top_performers[0]["rank"] == 1
    assert top_performers[1]["rank"] == 2


@patch("routes.performance.get_db_connection")
def test_analytics_flags_missing_mandatory_assessments_as_failed(mock_get_db, client):
    connection = MagicMock()
    mock_get_db.return_value = connection
    setup_analytics_database(connection)

    response = client.get("/api/performance/subjects/1/analytics")
    analytics = response.get_json()["analytics"]

    support = analytics["students_needing_support"]

    assert len(support) == 1
    assert support[0]["student_name"] == "Charan"
    assert support[0]["result"] == "FAIL"
    assert "missing_internals_or_final" in support[0]["support_reasons"]


@patch("routes.performance.get_db_connection")
def test_analytics_returns_404_for_missing_subject(mock_get_db, client):
    connection = MagicMock()
    mock_get_db.return_value = connection
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    connection.is_connected.return_value = True
    cursor.fetchone.return_value = None

    response = client.get("/api/performance/subjects/99999/analytics")

    assert response.status_code == 404
    assert response.get_json()["message"] == "Subject assignment not found."


@patch("routes.performance.get_db_connection")
def test_analytics_rejects_missing_assessment_definitions(
    mock_get_db, client
):
    connection = MagicMock()
    mock_get_db.return_value = connection
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    connection.is_connected.return_value = True

    cursor.fetchone.return_value = {
        "branch_subject_id": 1,
        "branch_id": 1,
        "semester": 3,
        "branch_code": "CSE",
        "subject_code": "CS301",
        "subject_name": "Database Management Systems",
    }
    cursor.fetchall.return_value = [
        {"assessment_id": 1, "assessment_name": "Midterm 1", "max_marks": 50}
    ]

    response = client.get("/api/performance/subjects/1/analytics")

    assert response.status_code == 409
    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == "Required assessment definitions are missing."
    assert len(data["missing_assessments"]) == 3