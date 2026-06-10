"""Thin wrapper over Azure's image endpoints.

Uses the Azure-native path: `{endpoint}/openai/deployments/{deployment}/images/{op}`
with the `api-key` header (not Bearer). Model name lives in the URL path —
NOT in the request body.

Important: the OpenAI-compat path `{endpoint}/openai/v1/images/...` does NOT
expose the edits endpoint on this Azure resource (returns "model doesn't exist"
or "DeploymentNotFound"). The Azure-native path is the only one that works for
gpt-image-1.5 edits.

LangChain handles chat / embeddings / vision — only image gen + edit live here.
"""

from __future__ import annotations

import base64

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

IMAGE_API_VERSION = "2025-04-01-preview"

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9+3PqpkAAAAASUVORK5CYII="
)


def _deployment_url(endpoint: str, deployment: str, op: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/openai/v1"):
        base = base[: -len("/openai/v1")]
    return f"{base}/openai/deployments/{deployment}/images/{op}?api-version={IMAGE_API_VERSION}"


class AzureImageClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return self.settings.ai_configured

    async def image_edit(
        self,
        room_bytes: bytes,
        product_bytes: bytes | None,  # noqa: ARG002 — reserved for future multi-image support
        prompt: str,
        *,
        size: str = "1024x1024",
        fallback_prompt: str | None = None,
    ) -> bytes:
        """Primary compositing path — Azure /images/edits on gpt-image-1.5 deployment.

        Falls back to text-to-image generation on any 4xx (lets the API stay usable
        even if the deployment doesn't expose edits).

        We enforce n=1 throughout to prevent the API from returning multiple data
        items and to avoid unexpected duplicate renders on the client.
        """
        deployment = self.settings.AZURE_IMAGE_EDIT_DEPLOYMENT
        gen_prompt = fallback_prompt or prompt
        if not deployment or not self.enabled:
            log.warning(
                "image_edit.unavailable",
                has_deployment=bool(deployment),
                ai_enabled=self.enabled,
            )
            return await self.image_gen(gen_prompt, size=size)

        import io
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(room_bytes))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            
            # Crop to square
            w, h = img.size
            if w != h:
                size_min = min(w, h)
                left = (w - size_min) // 2
                top = (h - size_min) // 2
                img = img.crop((left, top, left + size_min, top + size_min))
            
            # Ensure it's not too large (max 1024x1024 usually preferred)
            img.thumbnail((1024, 1024))
            
            out = io.BytesIO()
            img.save(out, format="PNG")
            png_bytes = out.getvalue()
        except Exception as e:
            log.warning("image_edit.png_conversion_failed", error=str(e))
            png_bytes = room_bytes

        url = _deployment_url(self.settings.AZURE_AI_FOUNDRY_ENDPOINT, deployment, "edits")
        files: list[tuple[str, tuple[str, bytes, str]]] = [
            ("image", ("room.png", png_bytes, "image/png")),
        ]
        data = {
            "prompt": prompt[:4000],
            "size": size,
            "n": "1",  # always exactly one output image
        }
        try:
            return await self._post_multipart(url, files=files, data=data)
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                log.warning(
                    "image_edit.fallback_to_gen",
                    status=e.response.status_code,
                    body=e.response.text[:300],
                )
                return await self.image_gen(gen_prompt, size=size)
            raise

    async def image_gen(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        seed: int | None = 42,
    ) -> bytes:
        """Text-to-image via Azure /images/generations on the configured deployment.

        ``seed`` is passed to the API so repeated calls with the same prompt produce
        consistent (though not identical) outputs and avoid wildly different styles.
        Set seed=None to use a random seed.
        """
        deployment = self.settings.AZURE_IMAGE_GEN_DEPLOYMENT or self.settings.AZURE_IMAGE_EDIT_DEPLOYMENT
        if not deployment or not self.enabled:
            log.warning("image_gen.mock_mode")
            return _TINY_PNG

        url = _deployment_url(self.settings.AZURE_AI_FOUNDRY_ENDPOINT, deployment, "generations")
        headers = {
            "api-key": self.settings.AZURE_AI_FOUNDRY_API_KEY,
            "Content-Type": "application/json",
        }
        payload: dict = {
            "prompt": prompt[:4000],
            "size": size,
            "n": 1,  # always exactly one output image
        }
        if seed is not None:
            payload["seed"] = seed
        return await self._post_json(url, headers=headers, json=payload)

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def _post_json(self, url: str, *, headers: dict, json: dict) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(400.0, connect=30.0)) as client:
            resp = await client.post(url, json=json, headers=headers)
            resp.raise_for_status()
            return _decode_image(resp.json())

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def _post_multipart(
        self,
        url: str,
        *,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, str],
    ) -> bytes:
        headers = {"api-key": self.settings.AZURE_AI_FOUNDRY_API_KEY}
        async with httpx.AsyncClient(timeout=httpx.Timeout(400.0, connect=30.0)) as client:
            resp = await client.post(url, headers=headers, data=data, files=files)
            resp.raise_for_status()
            return _decode_image(resp.json())


def _apply_watermark(image_bytes: bytes) -> bytes:
    import io
    import os
    from PIL import Image, ImageDraw, ImageFont

    try:
        # Load base image
        base_img = Image.open(io.BytesIO(image_bytes))
        orig_format = base_img.format or "PNG"
        
        # Convert to RGBA for alpha compositing
        base_rgba = base_img.convert("RGBA")
        img_w, img_h = base_rgba.size

        # Safety check: if image is too small (e.g. mock 1x1 image), skip watermarking
        if img_w < 100 or img_h < 100:
            return image_bytes

        # Load logo image
        current_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(current_dir, "watermark_logo.png")
        logo_img = None
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
            except Exception as le:
                log.warning("watermark.logo_load_failed", error=str(le))

        # Create overlay layer
        overlay = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Measure & configure text
        text = "simulafly.com"
        font = None
        font_paths = [
            "C:\\Windows\\Fonts\\arial.ttf",
            "C:\\Windows\\Fonts\\segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc"
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    font = ImageFont.truetype(fp, 16)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        if hasattr(draw, "textbbox"):
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        else:
            text_w, text_h = draw.textsize(text, font=font)

        # Layout parameters (minimalist bottom-right alignment)
        logo_size = 24
        spacing = 8
        margin_right = 24
        margin_bottom = 24

        if logo_img is not None:
            total_w = logo_size + spacing + text_w
            logo_x = img_w - total_w - margin_right
            logo_y = img_h - logo_size - margin_bottom

            # Resize logo
            logo_resized = logo_img.resize(
                (logo_size, logo_size),
                Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.ANTIALIAS
            )
            # Paste onto overlay using its alpha channel as mask
            overlay.paste(logo_resized, (logo_x, logo_y), logo_resized)

            text_x = logo_x + logo_size + spacing
            text_y = logo_y + (logo_size - text_h) // 2 - 2
        else:
            text_x = img_w - text_w - margin_right
            text_y = img_h - text_h - margin_bottom

        # Draw minimalistic text: semi-transparent white with a thin dark shadow for readability
        shadow_color = (0, 0, 0, 100)
        draw.text((text_x + 1, text_y + 1), text, font=font, fill=shadow_color)

        text_color = (255, 255, 255, 180)
        draw.text((text_x, text_y), text, font=font, fill=text_color)

        # Alpha composite overlay onto base image
        final_rgba = Image.alpha_composite(base_rgba, overlay)

        # Convert back and save to bytes
        out_buf = io.BytesIO()
        if orig_format.upper() in ["JPEG", "JPG"]:
            final_rgba.convert("RGB").save(out_buf, format="JPEG", quality=95)
        else:
            final_rgba.save(out_buf, format="PNG")
        return out_buf.getvalue()
    except Exception as ex:
        log.error("watermark.failed_applying", error=str(ex))
        return image_bytes


def _decode_image(body: dict) -> bytes:
    if not body.get("data"):
        raise ValueError(f"image response missing data: {body}")
    item = body["data"][0]
    raw_bytes = None
    if "b64_json" in item and item["b64_json"]:
        raw_bytes = base64.b64decode(item["b64_json"])
    elif "url" in item and item["url"]:
        with httpx.Client(timeout=60) as c:
            r = c.get(item["url"])
            r.raise_for_status()
            raw_bytes = r.content
    else:
        raise ValueError(f"image response has neither b64_json nor url: {item}")
    
    return _apply_watermark(raw_bytes)


_client: AzureImageClient | None = None


def get_image_client() -> AzureImageClient:
    global _client
    if _client is None:
        _client = AzureImageClient()
    return _client
