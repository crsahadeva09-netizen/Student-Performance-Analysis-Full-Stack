
from unittest.mock import MagicMock, patch

import pytest
from werkzeug.security import generate_password_hash

from app import app


@pytest.fixture
def client():
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key",
    )
    with app.test_client() as client:
        yield client


def make_user(password="correct-password", active=True):
    return {
        "user_id": 1,
        "username": "test_user",
        "password_hash": generate_password_hash(password),
        "role": "admin",
        "is_active": active,
    }


def mock_db(user):
    connection = MagicMock()
    cursor = MagicMock()
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = user
    connection.is_connected.return_value = True
    return connection, cursor


def test_successful_login_creates_session(client):
    connection, cursor = mock_db(make_user())

    with patch("app.get_db_connection", return_value=connection):
        response = client.post(
            "/api/auth/login",
            json={
                "username": "test_user",
                "password": "correct-password",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"

    with client.session_transaction() as session:
        assert session["user_id"] == 1
        assert session["username"] == "test_user"
        assert session["role"] == "admin"


def test_invalid_password_is_rejected(client):
    connection, cursor = mock_db(make_user())

    with patch("app.get_db_connection", return_value=connection):
        response = client.post(
            "/api/auth/login",
            json={
                "username": "test_user",
                "password": "wrong-password",
            },
        )

    assert response.status_code == 401
    assert response.get_json()["message"] == (
        "Invalid username or password."
    )


def test_inactive_user_cannot_login(client):
    connection, cursor = mock_db(make_user(active=False))

    with patch("app.get_db_connection", return_value=connection):
        response = client.post(
            "/api/auth/login",
            json={
                "username": "test_user",
                "password": "correct-password",
            },
        )

    assert response.status_code == 401


def test_logout_clears_session(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["username"] = "test_user"
        session["role"] = "admin"

    response = client.post("/api/auth/logout")

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"

    protected_response = client.get("/api/auth/me")
    assert protected_response.status_code == 401
