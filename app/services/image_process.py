"""Send images to the processor before they are forwarded."""

import json
import logging
import mimetypes
import os
import tempfile

import aiohttp

from app.config import get_settings

logger = logging.getLogger("signalpeak.images")

_NO_PROVIDER = "No approved provider name detected"


async def manipulate_image(image_path: str) -> str | None:
    """Return a processed image path, the original path, or None when the image must not be sent."""

    settings = get_settings()
    url = settings.image_process_url.strip()
    if not url:
        return image_path

    output_path: str | None = None
    try:
        output_fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(output_fd)

        image_mime_type = mimetypes.guess_type(image_path)[0]
        if not image_mime_type or not image_mime_type.startswith("image/"):
            raise ValueError(f"Could not determine a supported image MIME type for {image_path!r}")

        timeout = aiohttp.ClientTimeout(total=180, connect=10, sock_read=180)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            with open(image_path, "rb") as image_file:
                form = aiohttp.FormData()
                form.add_field(
                    "image",
                    image_file,
                    filename=os.path.basename(image_path),
                    content_type=image_mime_type,
                )
                async with client.post(url, data=form) as response:
                    body = await response.read()
                    if response.status >= 400:
                        text = body.decode("utf-8", errors="replace")
                        try:
                            data = json.loads(text) if text else {}
                        except json.JSONDecodeError:
                            data = {}
                        if not isinstance(data, dict):
                            data = {}
                        if data.get("reason") == _NO_PROVIDER:
                            logger.info(
                                "Image manipulation skipped: no approved provider name detected; original image will be forwarded"
                            )
                            _remove(output_path)
                            return image_path
                        logger.error(
                            "Image manipulation failed: HTTP %s from %s; response=%r",
                            response.status,
                            url,
                            text[:500],
                        )
                        _remove(output_path)
                        return None

        if not body:
            raise ValueError("Image processing service returned an empty response")

        with open(output_path, "wb") as processed_image:
            processed_image.write(body)
        return output_path
    except Exception:
        logger.exception("Image manipulation failed for %r using %s", image_path, url or "unset")
        _remove(output_path)
        return None


def _remove(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)
