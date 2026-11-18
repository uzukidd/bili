"""Persistent, entity-scoped audio blocks. No sparse files or source-wide deduplication."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

BLOCK_SIZE = 256 * 1024


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(eq=False)
class CacheEntry:
    key: str
    source: str
    directory: Path
    size: int | None = None
    content_type: str = "application/octet-stream"
    etag: str = ""
    url_hash: str = ""
    chunks: dict[int, dict] = field(default_factory=dict)
    accessed: float = field(default_factory=time.time)
    active: bool = False
    readers: int = 0
    pending: bool = False
    writable: bool = True
    revision: int = 0
    download_lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def byte_count(self) -> int:
        return sum(chunk["size"] for chunk in self.chunks.values())

    @property
    def complete(self) -> bool:
        if self.size is None or self.size <= 0:
            return False
        count = (self.size + BLOCK_SIZE - 1) // BLOCK_SIZE
        return len(self.chunks) == count and all(
            self.chunks.get(i, {}).get("size") == min(BLOCK_SIZE, self.size - i * BLOCK_SIZE)
            for i in range(count)
        )


class AudioCache:
    """Short manager lock for accounting; entry locks serialize network misses separately."""

    def __init__(self, directory: Path, max_bytes: int = 1024**3,
                 target_bytes: int = 800 * 1024**2):
        self.directory = Path(directory)
        self.max_bytes = max(0, max_bytes)
        self.target_bytes = max(0, min(target_bytes, self.max_bytes))
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._retired: list[CacheEntry] = []
        self._enabled = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._load()
        except OSError:
            self._enabled = False
        self._cleanup_orphans()
        self.trim()

    def _cleanup_orphans(self):
        """Only remove directories whose exact name belongs to this cache format."""
        known = {entry.directory for entry in list(self._entries.values()) + self._retired}
        try:
            paths = list(self.directory.iterdir())
        except OSError:
            return
        for path in paths:
            if (path in known or path.is_symlink() or not path.is_dir()
                    or not re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{32}", path.name)):
                continue
            orphan = CacheEntry("", "", path, pending=True, writable=False)
            try:
                for index, block in enumerate(path.iterdir()):
                    if block.is_file() and block.suffix in (".block", ".tmp"):
                        orphan.chunks[index] = {"size": block.stat().st_size}
            except OSError:
                pass
            self._retired.append(orphan)
            self._delete(orphan)

    def _load(self):
        for path in self.directory.glob("*/metadata.json"):
            if (path.parent.is_symlink() or path.is_symlink()
                    or not re.fullmatch(r"[0-9a-f]{64}\.[0-9a-f]{32}", path.parent.name)):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if data.get("version") != 1 or data.get("block_size") != BLOCK_SIZE:
                    continue
                key, source = data["key"], data["source"]
                if not isinstance(key, str) or not key or not isinstance(source, str):
                    continue
                if path.parent.name.split(".")[0] != fingerprint(key):
                    continue
                size = data["size"]
                if size is not None and (type(size) is not int or size <= 0):
                    continue
                entry = CacheEntry(key, source, path.parent, size=size,
                                   content_type=str(data.get("content_type", "application/octet-stream")),
                                   etag=str(data.get("etag", "")), url_hash=str(data.get("url_hash", "")),
                                   accessed=float(data.get("accessed", 0)),
                                   pending=bool(data.get("pending", False)) or (path.parent / "pending").exists())
                for number, chunk in data.get("chunks", {}).items():
                    index = int(number)
                    length, digest = chunk["size"], chunk["sha256"]
                    if (size is None or index < 0 or type(length) is not int
                            or length != min(BLOCK_SIZE, size - index * BLOCK_SIZE)
                            or length <= 0 or not isinstance(digest, str) or len(digest) != 64):
                        continue
                    block_path = entry.directory / f"{index}.block"
                    try:
                        if block_path.stat().st_size == length:
                            entry.chunks[index] = {"size": length, "sha256": digest}
                    except OSError:
                        continue
                previous = self._entries.get(key)
                if entry.pending or (previous and previous.accessed >= entry.accessed):
                    self._retired.append(entry)
                    entry.pending = True
                else:
                    if previous:
                        previous.pending = True
                        self._retired.append(previous)
                    self._entries[key] = entry
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
                continue
        for entry in list(self._retired):
            self._delete(entry)
        # A crash before metadata commit can leave unreferenced complete blocks/temp files.
        for entry in self._entries.values():
            for path in entry.directory.iterdir():
                if path.name == "metadata.json":
                    continue
                if path.suffix == ".block" and path.stem.isdigit() and int(path.stem) in entry.chunks:
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass

    def _save(self, entry: CacheEntry):
        if not self._enabled or not entry.writable:
            return
        temporary = entry.directory / "metadata.tmp"
        try:
            entry.directory.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "block_size": BLOCK_SIZE, "key": entry.key,
                    "source": entry.source, "size": entry.size, "etag": entry.etag,
                    "content_type": entry.content_type, "url_hash": entry.url_hash,
                    "accessed": entry.accessed, "pending": entry.pending,
                    "chunks": entry.chunks}
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, entry.directory / "metadata.json")
        except OSError:
            entry.writable = False
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def activate(self, key: str, source: str, url: str = "", *, complete_only=False) -> CacheEntry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry and entry.source != source:
                self._retire(entry)
                entry = None
            if entry and entry.pending:
                entry = None
            if complete_only:
                if not entry or not self._complete_on_disk(entry):
                    return None
            elif entry is None:
                entry = CacheEntry(key, source, self.directory / (fingerprint(key) + "." + uuid.uuid4().hex))
                entry.writable = self._enabled
                self._entries[key] = entry
            if entry is None:
                return None
            if url and not entry.complete and entry.url_hash and entry.url_hash != fingerprint(url):
                # A refreshed signed URL may refer to a different representation. A full
                # source-key hit is reusable, but partial data needs a confirmed validator.
                # Conservatively start a new generation rather than mix representations.
                self._retire(entry)
                entry = CacheEntry(key, source, self.directory / (fingerprint(key) + "." + uuid.uuid4().hex))
                entry.writable = self._enabled
                self._entries[key] = entry
            if url:
                entry.url_hash = fingerprint(url)
            entry.active = True
            entry.accessed = time.time()
            self._save(entry)
            return entry

    def _complete_on_disk(self, entry):
        if not entry.complete:
            return False
        for index, chunk in list(entry.chunks.items()):
            try:
                if (entry.directory / f"{index}.block").stat().st_size == chunk["size"]:
                    continue
            except OSError:
                pass
            entry.chunks.pop(index, None)
            self._save(entry)
            return False
        return True

    def acquire(self, entry):
        with self._lock:
            entry.readers += 1

    def release(self, entry):
        with self._lock:
            entry.readers = max(0, entry.readers - 1)
            if entry.pending and not entry.active and not entry.readers:
                self._delete(entry)
            self.trim()

    def deactivate(self, entry):
        with self._lock:
            entry.active = False
            if entry.pending and not entry.readers:
                self._delete(entry)
            self.trim()

    def _retire(self, entry):
        entry.pending = True
        try:
            if entry.directory.exists():
                (entry.directory / "pending").touch(exist_ok=True)
        except OSError:
            pass
        if self._entries.get(entry.key) is entry:
            self._entries.pop(entry.key, None)
        if entry not in self._retired:
            self._retired.append(entry)
        self._save(entry)
        if not entry.active and not entry.readers:
            self._delete(entry)

    def _delete(self, entry):
        try:
            if entry.directory.exists():
                shutil.rmtree(entry.directory)
        except OSError:
            entry.pending = True
            if entry not in self._retired:
                self._retired.append(entry)
            return False
        if self._entries.get(entry.key) is entry:
            self._entries.pop(entry.key, None)
        if entry in self._retired:
            self._retired.remove(entry)
        return True

    def representation(self, entry, size: int, content_type: str, etag: str):
        with self._lock:
            changed = entry.size is not None and (
                entry.size != size or entry.etag != etag
            )
            if changed:
                entry.revision += 1
                entry.chunks.clear()
                try:
                    for path in entry.directory.glob("*.block"):
                        path.unlink(missing_ok=True)
                except OSError:
                    entry.writable = False
            entry.size, entry.content_type, entry.etag = size, content_type, etag
            self._save(entry)

    def read_block(self, entry, index: int) -> bytes | None:
        with self._lock:
            chunk = entry.chunks.get(index)
            if not chunk:
                return None
            try:
                with (entry.directory / f"{index}.block").open("rb") as handle:
                    data = handle.read(BLOCK_SIZE + 1)
                if len(data) == chunk["size"] and hashlib.sha256(data).hexdigest() == chunk["sha256"]:
                    entry.accessed = time.time()
                    return data
            except OSError:
                pass
            entry.chunks.pop(index, None)
            try:
                (entry.directory / f"{index}.block").unlink(missing_ok=True)
            except OSError:
                pass
            self._save(entry)
            return None

    def write_block(self, entry, index: int, data: bytes) -> bool:
        with self._lock:
            if (entry.pending or not entry.writable or entry.size is None or index < 0
                    or len(data) != min(BLOCK_SIZE, entry.size - index * BLOCK_SIZE) or not data):
                return False
            required = max(0, len(data) - entry.chunks.get(index, {}).get("size", 0))
            self.trim(required)
            if self.info()["bytes"] + required > self.max_bytes:
                return False
            temporary = entry.directory / f"{index}.tmp"
            try:
                entry.directory.mkdir(parents=True, exist_ok=True)
                with temporary.open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, entry.directory / f"{index}.block")
                entry.chunks[index] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                entry.accessed = time.time()
                self._save(entry)
                if not entry.writable:
                    # An uncommitted block is not persistent coverage.
                    entry.chunks.pop(index, None)
                    try:
                        (entry.directory / f"{index}.block").unlink(missing_ok=True)
                    except OSError:
                        pass
                self.trim()
                return entry.writable
            except OSError:
                entry.writable = False
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                return False

    def trim(self, required=0):
        with self._lock:
            for entry in list(self._retired):
                if not entry.active and not entry.readers:
                    self._delete(entry)
            entries = list(self._entries.values()) + self._retired
            total = sum(entry.byte_count for entry in entries)
            if total + required <= self.max_bytes:
                return
            for entry in sorted(entries, key=lambda item: item.accessed):
                if entry.active or entry.readers:
                    continue
                length = entry.byte_count
                self._retire(entry)
                if entry not in self._retired and self._entries.get(entry.key) is not entry:
                    total -= length
                if total + required <= self.target_bytes:
                    break

    def info(self) -> dict:
        with self._lock:
            entries = list(self._entries.values()) + self._retired
            return {"bytes": sum(e.byte_count for e in entries),
                    "entries": sum(bool(e.chunks) for e in entries),
                    "pending_bytes": sum(e.byte_count for e in entries if e.pending),
                    "max_bytes": self.max_bytes, "target_bytes": self.target_bytes}

    def clear(self) -> dict:
        with self._lock:
            before = self.info()["bytes"]
            for entry in list(self._entries.values()) + list(self._retired):
                self._retire(entry)
            after = self.info()
            return {"removed_bytes": before - after["bytes"], "pending_bytes": after["pending_bytes"]}
