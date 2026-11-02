import base64
import json
import pytest
from fastapi.testclient import TestClient
from promptvault_team.app import create_team_app
from promptvault_team.db import TeamLibrary


def test_admin_mode_first_user_is_admin(tmp_path):
    lib = TeamLibrary(data_dir=tmp_path / "admin_db", mode="admin")
    
    # First user registers in admin mode
    res = lib.register_user("admin@sirket.com", "Admin User", "pass1234")
    assert res["user"]["role"] == "admin"


def test_client_mode_unauthorized_user_blocked(tmp_path):
    lib = TeamLibrary(data_dir=tmp_path / "client_db", mode="client")

    # Set company policy
    lib.update_company_policy(
        company_name="Acme Corp",
        allowed_domains=["acme.com"],
        default_role="editor",
    )

    # 1. An outsider tries to register -> BLOCKED
    with pytest.raises(PermissionError) as exc_info:
        lib.register_user("hacker@gmail.com", "Hacker", "pass1234")
    assert "Erişim Reddedildi" in str(exc_info.value)

    # 2. Company domain user registers -> ACCEPTED as EDITOR (not admin!)
    res = lib.register_user("calisan@acme.com", "Calisan", "pass1234")
    assert res["user"]["role"] == "editor"
    assert res["user"]["role"] != "admin"

    # 3. Explicitly authorized outsider email -> ACCEPTED with assigned role
    lib.set_member_permission("danisman@disarisi.com", role="viewer")
    res2 = lib.register_user("danisman@disarisi.com", "Danisman", "pass1234")
    assert res2["user"]["role"] == "viewer"


def test_client_mode_google_login_whitelist(tmp_path):
    lib = TeamLibrary(data_dir=tmp_path / "client_g_db", mode="client")
    lib.update_company_policy(
        company_name="Acme Corp",
        allowed_domains=["acme.com"],
        default_role="editor",
    )

    # Unauthorized google login -> BLOCKED
    with pytest.raises(PermissionError):
        lib.login_or_register_google("stranger@yahoo.com", "Stranger")

    # Authorized google login -> SUCCESS
    res = lib.login_or_register_google("ayse@acme.com", "Ayse Yilmaz")
    assert res["user"]["role"] == "editor"


def test_google_login_api_endpoints(tmp_path):
    app = create_team_app(data_dir=tmp_path / "api_test_db", mode="admin")
    client = TestClient(app)

    # 1. Direct Google email sign in
    r = client.post("/api/team/auth/google", json={"email": "kurucu@acme.com", "name": "Kurucu"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["user"]["email"] == "kurucu@acme.com"
    assert data["user"]["role"] == "admin"
    token = data["token"]

    # Verify session with /me
    r_me = client.get("/api/team/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r_me.status_code == 200
    assert r_me.json()["user"]["email"] == "kurucu@acme.com"

    # 2. Google JWT credential token sign in
    payload = {
        "email": "muhendis@acme.com",
        "name": "Muhendis Can",
        "sub": "google-12345678",
        "email_verified": True
    }
    raw_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    fake_jwt = f"header.{raw_b64}.signature"

    r2 = client.post("/api/team/auth/google", json={"credential_token": fake_jwt})
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["ok"] is True
    assert data2["user"]["email"] == "muhendis@acme.com"
    assert data2["user"]["role"] == "editor"

    # 3. Google Sign in with existing user and richer name
    r3 = client.post("/api/team/auth/google", json={"email": "muhendis@acme.com", "name": "Muhendis Can Yilmaz"})
    assert r3.status_code == 200
    assert r3.json()["user"]["name"] == "Muhendis Can Yilmaz"


def test_google_login_validation_and_errors(tmp_path):
    app = create_team_app(data_dir=tmp_path / "g_val_db", mode="admin")
    client = TestClient(app)

    # Missing email and no token -> 400
    r = client.post("/api/team/auth/google", json={"email": "", "name": ""})
    assert r.status_code == 400

    # Invalid email without @ -> 400
    r2 = client.post("/api/team/auth/google", json={"email": "notanemail", "name": "Bad"})
    assert r2.status_code == 400

    # Malformed credential token without email in body -> 400
    r3 = client.post("/api/team/auth/google", json={"credential_token": "invalid.jwt.token"})
    assert r3.status_code == 400

    # Malformed token but valid fallback email in body -> SUCCESS
    r4 = client.post("/api/team/auth/google", json={"credential_token": "broken", "email": "valid@gmail.com", "name": "Valid"})
    assert r4.status_code == 200
    assert r4.json()["user"]["email"] == "valid@gmail.com"


def test_client_mode_api_restrictions(tmp_path):
    app = create_team_app(data_dir=tmp_path / "client_api_db", mode="client")
    client = TestClient(app)

    # Set company policy first directly on app lib
    lib: TeamLibrary = app.routes[0].endpoint.__globals__.get("lib") or app.state._state.get("lib")  # or create admin
    # Directly configure policy via admin DB
    admin_app = create_team_app(data_dir=tmp_path / "client_api_db", mode="admin")
    admin_client = TestClient(admin_app)
    admin_res = admin_client.post("/api/team/auth/google", json={"email": "admin@mycompany.com", "name": "Root Admin"})
    admin_token = admin_res.json()["token"]

    admin_client.post(
        "/api/team/auth/company/policy",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "company_name": "MyCompany",
            "allowed_domains": ["mycompany.com"],
            "default_role": "editor",
            "allow_all_domain_users": True,
        }
    )

    # Now use client_client
    # Outsider blocked with 403
    r_bad = client.post("/api/team/auth/google", json={"email": "outsider@evil.com", "name": "Attacker"})
    assert r_bad.status_code == 403

    # Company member allowed with 200
    r_good = client.post("/api/team/auth/google", json={"email": "worker@mycompany.com", "name": "Worker"})
    assert r_good.status_code == 200
    assert r_good.json()["user"]["role"] == "editor"


def test_system_heartbeat_and_shutdown(tmp_path):
    app = create_team_app(data_dir=tmp_path / "sys_db", mode="admin")
    client = TestClient(app)

    # Heartbeat
    r1 = client.post("/api/system/heartbeat")
    assert r1.status_code == 200
    assert r1.json()["status"] == "alive"

    # Shutdown beacon
    r2 = client.post("/api/system/shutdown")
    assert r2.status_code == 200
    assert r2.json()["status"] == "shutting_down"
