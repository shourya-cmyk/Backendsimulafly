import base64

import pytest

TINY_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 200 + b"\xff\xd9").decode()


@pytest.mark.asyncio
async def test_upload_and_fetch(auth_client):
    r = await auth_client.post(
        "/api/v1/upload/room-image",
        json={"image_base64": TINY_JPEG, "media_type": "image/jpeg"},
    )
    assert r.status_code == 201, r.text
    image_id = r.json()["id"]
    assert r.json()["byte_size"] > 0

    r = await auth_client.get(f"/api/v1/upload/room-image/{image_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(auth_client):
    r = await auth_client.post(
        "/api/v1/upload/room-image",
        json={"image_base64": TINY_JPEG, "media_type": "application/pdf"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_rejects_invalid_base64(auth_client):
    r = await auth_client.post(
        "/api/v1/upload/room-image",
        json={"image_base64": "!!!not-base64!!!", "media_type": "image/jpeg"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_merchant_product_image(auth_client):
    # Smoke test: a small JPEG-ish base64 blob uploads and returns a URL.
    # The existing upload router uses JSON body with image_base64 (not multipart),
    # so we follow that pattern here.
    r = await auth_client.post(
        "/api/v1/upload/merchant-product-image",
        json={"image_base64": TINY_JPEG, "media_type": "image/jpeg"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert "url" in body
    assert body["url"].startswith("http") or body["url"].startswith("/")
