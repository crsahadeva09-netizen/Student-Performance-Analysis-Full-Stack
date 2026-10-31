
import pytest

from app import app


@pytest.fixture
def client():
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key",
    )

    with app.test_client() as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/api/health")

    assert response.status_code == 200

    data = response.get_json()
    assert data["status"] == "success"
    assert "API is running" in data["message"]


def test_performance_endpoint_requires_login(client):
    response = client.get(
        "/api/performance/students/1/subjects/1"
    )

    assert response.status_code == 401

    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == "Please log in first."


def test_analytics_endpoint_requires_login(client):
    response = client.get(
        "/api/performance/subjects/1/analytics"
    )

    assert response.status_code == 401

    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == "Please log in first."


def test_auth_me_requires_login(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401

    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == "Please log in first."


def test_logout_requires_login(client):
    response = client.post("/api/auth/logout")

    assert response.status_code == 401


def test_login_rejects_missing_credentials(client):
    response = client.post(
        "/api/auth/login",
        json={}
    )

    assert response.status_code == 400

    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == (
        "Username and password are required."
    )


def test_login_rejects_empty_username(client):
    response = client.post(
        "/api/auth/login",
        json={
            "username": "   ",
            "password": "some-password",
        }
    )

    assert response.status_code == 400

    data = response.get_json()
    assert data["status"] == "error"
    assert data["message"] == (
        "Username and password are required."
    )
