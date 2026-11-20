"""Download and retain cover art independently of expiring playback addresses."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtGui import QImage

from . import network as requests
from .api import HEADERS

MAX_IMAGE_BYTES = 8 * 1024 * 1024


class CoverCache:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def fetch(self, source: str) -> Path:
        remote = urlparse(source).scheme in ("https", "http")
        if remote:
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            destination = self.directory / (digest + ".img")
            if destination.is_file() and not QImage(str(destination)).isNull():
                return destination
            parts = []
            size = 0
            with requests.get(source, headers=HEADERS, timeout=(5, 12), stream=True) as response:
                response.raise_for_status()
                for part in response.iter_content(64 * 1024):
                    size += len(part)
                    if size > MAX_IMAGE_BYTES:
                        raise ValueError("封面图片超过 8 MB")
                    parts.append(part)
            data = b"".join(parts)
        else:
            path = Path(source).expanduser().resolve()
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("封面图片超过 8 MB")
            if path.parent == self.directory.resolve() and not QImage(str(path)).isNull():
                return path
            data = path.read_bytes()
            # Re-selecting an edited image at the same path must import its new bytes.
            digest = hashlib.sha256(data).hexdigest()
            destination = self.directory / (digest + ".img")
            if destination.is_file() and not QImage(str(destination)).isNull():
                return destination
        image = QImage.fromData(data)
        if image.isNull() or image.width() * image.height() > 16_000_000:
            raise ValueError("封面不是有效图片或尺寸过大")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination
