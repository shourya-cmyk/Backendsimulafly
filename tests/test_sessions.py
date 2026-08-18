import base64

import pytest


@pytest.mark.asyncio
async def test_session_crud_and_patch(auth_client):
    r = await auth_client.post("/api/v1/sessions/", json={"title": "Living Room"})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await auth_client.get("/api/v1/sessions/")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())

    image = base64.b64encode(
        b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9"
    ).decode()
    upload = await auth_client.post(
        "/api/v1/upload/room-image",
        json={"image_base64": image, "media_type": "image/jpeg"},
    )
    image_id = upload.json()["id"]

    r = await auth_client.patch(
        f"/api/v1/sessions/{sid}",
        json={
            "title": "Renamed",
            "status": "archived",
            "room_image_id": image_id,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Renamed"
    assert body["status"] == "archived"
    assert body["room_image_id"] == image_id

    r = await auth_client.delete(f"/api/v1/sessions/{sid}")
    assert r.status_code == 204
    r = await auth_client.get(f"/api/v1/sessions/{sid}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_session_ownership(auth_client, client, db_session):
    # Create session as auth_client
    r = await auth_client.post("/api/v1/sessions/", json={"title": "Mine"})
    sid = r.json()["id"]

    # Register a second user and try to access
    await client.post(
        "/api/v1/auth/register",
        json={"email": "intruder@example.com", "password": "password123"},
    )
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": "intruder@example.com", "password": "password123"},
    )
    other_token = r.json()["access_token"]
    r = await client.get(
        f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert r.status_code == 404
