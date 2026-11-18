from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bili_player.audio_cache import AudioCache, BLOCK_SIZE


class AudioCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = AudioCache(self.root)

    def entry(self, key="entity", source="source", length=BLOCK_SIZE * 2):
        entry = self.cache.activate(key, source, "http://source/audio")
        self.cache.representation(entry, length, "audio/mp4", '"v1"')
        return entry

    def test_persistence_exact_coverage_and_missing_blocks(self):
        entry = self.entry()
        self.assertTrue(self.cache.write_block(entry, 1, b"b" * BLOCK_SIZE))
        self.assertFalse(entry.complete)
        self.assertIsNone(self.cache.read_block(entry, 0))
        self.cache.deactivate(entry)
        restarted = AudioCache(self.root)
        self.assertIsNone(restarted.activate("entity", "source", complete_only=True))
        loaded = restarted.activate("entity", "source", "http://source/audio")
        self.assertEqual(restarted.read_block(loaded, 1), b"b" * BLOCK_SIZE)
        self.assertIsNone(restarted.read_block(loaded, 0))
        self.assertTrue(restarted.write_block(loaded, 0, b"a" * BLOCK_SIZE))
        self.assertTrue(loaded.complete)
        (loaded.directory / "0.block").unlink()
        missing = AudioCache(self.root)
        self.assertIsNone(missing.activate("entity", "source", complete_only=True))
        survivor = missing.activate("entity", "source", "http://source/audio")
        self.assertEqual(missing.read_block(survivor, 1), b"b" * BLOCK_SIZE)

    def test_corrupt_same_size_block_is_never_read(self):
        entry = self.entry(length=BLOCK_SIZE)
        self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE)
        (entry.directory / "0.block").write_bytes(b"x" * BLOCK_SIZE)
        restarted = AudioCache(self.root)
        loaded = restarted.activate("entity", "source", "http://source/audio")
        self.assertIsNone(restarted.read_block(loaded, 0))
        self.assertFalse(loaded.complete)

    def test_old_response_cannot_commit_blocks_after_representation_changes(self):
        entry = self.entry(length=BLOCK_SIZE)
        revision = entry.revision
        self.cache.representation(entry, BLOCK_SIZE, "audio/mp4", '"v2"')
        self.assertFalse(self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE, revision=revision))
        self.assertIsNone(self.cache.read_block(entry, 0))
        self.assertTrue(self.cache.write_block(entry, 0, b"b" * BLOCK_SIZE, revision=entry.revision))
        self.assertEqual(self.cache.read_block(entry, 0), b"b" * BLOCK_SIZE)

    def test_distinct_entities_same_source_are_independent(self):
        first = self.entry("first", length=BLOCK_SIZE)
        second = self.entry("second", length=BLOCK_SIZE)
        self.cache.write_block(first, 0, b"a" * BLOCK_SIZE)
        self.assertNotEqual(first.directory, second.directory)
        self.assertIsNone(self.cache.read_block(second, 0))
        self.cache.write_block(second, 0, b"a" * BLOCK_SIZE)
        self.assertEqual(self.cache.info()["bytes"], BLOCK_SIZE * 2)
        self.assertEqual(self.cache.info()["entries"], 2)

    def test_source_change_and_refreshed_partial_url_invalidate(self):
        entry = self.entry()
        self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE)
        self.cache.deactivate(entry)
        refreshed = self.cache.activate("entity", "source", "http://source/refreshed")
        self.assertIsNot(entry, refreshed)
        self.assertEqual(refreshed.byte_count, 0)
        self.cache.representation(refreshed, BLOCK_SIZE, "audio/mp4", '"v1"')
        self.cache.write_block(refreshed, 0, b"a" * BLOCK_SIZE)
        self.cache.deactivate(refreshed)
        self.assertIsNotNone(self.cache.activate("entity", "source", complete_only=True))
        self.assertIsNone(self.cache.activate("entity", "new-source", complete_only=True))
        self.assertEqual(self.cache.info()["pending_bytes"], BLOCK_SIZE)
        self.cache.deactivate(refreshed)
        self.assertEqual(self.cache.info()["bytes"], 0)

    def test_lru_trims_to_target_and_protects_active_and_readers(self):
        cache = AudioCache(self.root, max_bytes=3 * BLOCK_SIZE, target_bytes=BLOCK_SIZE)
        entries = []
        for i in range(3):
            entry = cache.activate(str(i), "source")
            cache.representation(entry, BLOCK_SIZE, "audio/mp4", "")
            cache.write_block(entry, 0, bytes([i]) * BLOCK_SIZE)
            cache.deactivate(entry)
            entry.accessed = i
            entries.append(entry)
        cache.acquire(entries[0])
        fourth = cache.activate("active", "source")
        cache.representation(fourth, BLOCK_SIZE * 2, "audio/mp4", "")
        cache.write_block(fourth, 0, b"z" * BLOCK_SIZE)
        self.assertTrue(entries[0].directory.exists())
        self.assertTrue(fourth.directory.exists())
        self.assertFalse(entries[1].directory.exists())
        self.assertFalse(entries[2].directory.exists())
        self.assertEqual(cache.info()["bytes"], BLOCK_SIZE * 2)
        cache.write_block(fourth, 1, b"z" * BLOCK_SIZE)
        cache.release(entries[0])
        cache.deactivate(fourth)
        self.assertLessEqual(cache.info()["bytes"], cache.max_bytes)

    def test_clear_defers_active_and_reader_deletion_and_blocks_persistence(self):
        first = self.entry("active")
        self.cache.write_block(first, 0, b"a" * BLOCK_SIZE)
        other = self.entry("other", length=BLOCK_SIZE)
        self.cache.write_block(other, 0, b"b" * BLOCK_SIZE)
        self.cache.deactivate(other)
        self.cache.acquire(first)
        result = self.cache.clear()
        self.assertEqual(result, {"removed_bytes": BLOCK_SIZE, "pending_bytes": BLOCK_SIZE})
        self.assertFalse(self.cache.write_block(first, 1, b"c" * BLOCK_SIZE))
        self.assertEqual(self.cache.read_block(first, 0), b"a" * BLOCK_SIZE)
        self.cache.deactivate(first)
        self.assertTrue(first.directory.exists())
        self.cache.release(first)
        self.assertFalse(first.directory.exists())
        self.assertEqual(self.cache.info()["pending_bytes"], 0)

    def test_pending_clear_survives_restart(self):
        entry = self.entry(length=BLOCK_SIZE)
        self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE)
        self.cache.clear()
        restarted = AudioCache(self.root)
        self.assertEqual(restarted.info()["bytes"], 0)
        self.assertFalse(entry.directory.exists())

    def test_metadata_committed_only_after_completed_block_write(self):
        entry = self.entry()
        with patch("bili_player.audio_cache.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE))
        self.assertEqual(self.cache.info()["bytes"], 0)
        self.assertIsNone(AudioCache(self.root).activate("entity", "source", complete_only=True))

    def test_delete_error_returns_pending_and_retries(self):
        entry = self.entry(length=BLOCK_SIZE)
        self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE)
        self.cache.deactivate(entry)
        with patch("bili_player.audio_cache.shutil.rmtree", side_effect=OSError("busy")):
            self.assertEqual(self.cache.clear(), {"removed_bytes": 0, "pending_bytes": BLOCK_SIZE})
        self.cache.trim()
        self.assertEqual(self.cache.info()["bytes"], 0)

    def test_protected_content_never_grows_past_capacity(self):
        cache = AudioCache(self.root, max_bytes=BLOCK_SIZE, target_bytes=BLOCK_SIZE // 2)
        entry = cache.activate("long-song", "source")
        cache.representation(entry, BLOCK_SIZE * 3, "audio/mp4", "")
        self.assertTrue(cache.write_block(entry, 0, b"a" * BLOCK_SIZE))
        self.assertFalse(cache.write_block(entry, 1, b"b" * BLOCK_SIZE))
        self.assertEqual(cache.info()["bytes"], BLOCK_SIZE)
        self.assertEqual(cache.read_block(entry, 0), b"a" * BLOCK_SIZE)

    def test_malformed_metadata_and_crash_orphans_are_cleaned(self):
        for index, data in enumerate(([], {"version": 1, "block_size": BLOCK_SIZE, "key": "x",
                                            "source": "s", "size": 12, "chunks": []})):
            name = hashlib.sha256(("x" if index else "y").encode()).hexdigest() + "." + str(index) * 32
            directory = self.root / name
            directory.mkdir()
            (directory / "metadata.json").write_text(json.dumps(data), encoding="utf-8")
            (directory / "0.block").write_bytes(b"orphan")
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_text("keep", encoding="utf-8")
        loaded = AudioCache(self.root)
        self.assertEqual(loaded.info()["bytes"], 0)
        self.assertEqual(list(self.root.iterdir()), [unrelated])

    def test_wrong_length_and_uncommitted_files_are_not_coverage(self):
        entry = self.entry()
        self.assertFalse(self.cache.write_block(entry, 0, b"short"))
        self.cache.write_block(entry, 0, b"a" * BLOCK_SIZE)
        (entry.directory / "1.block").write_bytes(b"b" * BLOCK_SIZE)
        (entry.directory / "aborted.tmp").write_bytes(b"junk")
        self.cache.deactivate(entry)
        restarted = AudioCache(self.root)
        self.assertIsNone(restarted.activate("entity", "source", complete_only=True))
        self.assertFalse((entry.directory / "1.block").exists())
        self.assertFalse((entry.directory / "aborted.tmp").exists())


if __name__ == "__main__":
    unittest.main()
