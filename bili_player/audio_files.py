"""Persistent audio files shared by the library and ZIP packages."""
from __future__ import annotations

import hashlib
import os
import tempfile
from itertools import chain
from pathlib import Path
from urllib.parse import urlsplit

from requests import RequestException
from requests.structures import CaseInsensitiveDict

from . import network
from .api import HEADERS
from .models import AUDIO_EXTENSIONS, valid_audio_file
from .stream import _audio_bytes, _content_type

MAX_AUDIO_BYTES = 1024 ** 3
CHUNK_SIZE = 256 * 1024
_AUDIO_TYPES = {
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "application/mp4": "m4a",
    "audio/webm": "webm", "audio/mpeg": "mp3", "audio/aac": "aac",
    "audio/ogg": "ogg", "application/ogg": "ogg", "audio/flac": "flac",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/opus": "opus",
}


class AudioFiles:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()

    def path(self, name: str) -> Path | None:
        if not valid_audio_file(name):
            return None
        candidate = self.directory / name
        try:
            if candidate.is_file() and candidate.resolve().parent == self.directory and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            pass
        return None

    def import_file(self, source: Path) -> str:
        extension = source.suffix.lower().lstrip(".")
        if extension not in AUDIO_EXTENSIONS:
            raise ValueError("不支持的音频文件格式")
        with source.open("rb") as handle:
            return self.store(iter(lambda: handle.read(CHUNK_SIZE), b""), extension,
                              size=os.fstat(handle.fileno()).st_size)

    def download(self, url: str) -> str:
        headers = CaseInsensitiveDict(getattr(url, "http_headers", HEADERS))
        headers.pop("Range", None)
        headers["Accept-Encoding"] = "identity"
        try:
            with network.get(url, headers=headers, timeout=(8, 25), stream=True) as response:
                if response.status_code == 403:
                    raise ValueError("音频服务器拒绝下载请求（HTTP 403）")
                response.raise_for_status()
                if response.status_code != 200 or response.headers.get("Content-Range"):
                    raise ValueError("服务器未返回完整音频")
                length = response.headers.get("Content-Length")
                return self.store_stream(_content_type(response), response.iter_content(CHUNK_SIZE),
                                         size=int(length) if length else None, url=url)
        except RequestException:
            raise ValueError("下载音频时网络请求失败") from None

    def store_stream(self, content_type: str, chunks, *, size: int | None, url: str = "") -> str:
        mime = content_type.split(";", 1)[0].strip().lower()
        extension = _AUDIO_TYPES.get(mime) or Path(urlsplit(url).path).suffix.lower().lstrip(".")
        chunks = (chunk for chunk in chunks if chunk)
        if extension not in AUDIO_EXTENSIONS:
            first = next(chunks, b"")
            if first[4:8] in (b"ftyp", b"styp"):
                extension = "m4a"
            elif first.startswith(b"\x1a\x45\xdf\xa3"):
                extension = "webm"
            else:
                raise ValueError("不支持的音频文件格式")
            chunks = chain((first,), chunks)
        return self.store(chunks, extension, size=size)

    def store(self, chunks, extension: str, *, size: int | None = None, expected: str | None = None) -> str:
        if extension not in AUDIO_EXTENSIONS:
            raise ValueError("不支持的音频文件格式")
        if size is not None and not 0 < size <= MAX_AUDIO_BYTES:
            raise ValueError("音频文件必须非空且不超过 1 GiB")
        self.directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        written = 0
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                for chunk in chunks:
                    if not chunk:
                        continue
                    if not written:
                        _audio_bytes(chunk)
                    written += len(chunk)
                    if written > MAX_AUDIO_BYTES:
                        raise ValueError("音频文件必须非空且不超过 1 GiB")
                    digest.update(chunk)
                    output.write(chunk)
            if not written or (size is not None and written != size):
                raise ValueError("音频文件为空或不完整")
            name = f"{digest.hexdigest()}.{extension}"
            if expected is not None and name != expected:
                raise ValueError("音乐库包的音频校验失败")
            destination = self.directory / name
            if self.path(name) is None or not self._matches(destination, digest.digest(), written):
                os.replace(temporary, destination)
                temporary = None
            return name
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _matches(path: Path, digest: bytes, size: int) -> bool:
        try:
            with path.open("rb") as file:
                return os.fstat(file.fileno()).st_size == size and hashlib.file_digest(file, "sha256").digest() == digest
        except OSError:
            return False
