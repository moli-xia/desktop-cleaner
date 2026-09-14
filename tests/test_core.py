import copy
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch
import zipfile

import cleaner_core as core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Desktop"
        self.root.mkdir()
        self.records = self.base / "records"
        self.config = copy.deepcopy(core.DEFAULT_CONFIG)

    def file(self, name, data=b"sample"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self):
        return [row for row in core.scan(self.root, self.config) if not row["reason"]]

    def test_scan_filters_and_does_not_mutate(self):
        self.file("report.PDF")
        self.file("app.lnk")
        self.file("desktop.ini")
        self.file(".hidden")
        self.file("folder/nested.txt")
        before = sorted(str(p) for p in self.root.rglob("*"))
        rows = core.scan(self.root, self.config)
        self.assertEqual([r["name"] for r in rows if not r["reason"]], ["report.PDF"])
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))

    def test_normalize_and_reject_duplicate_rules(self):
        self.assertEqual(core.extensions("PDF，txt; .TAR.GZ"), [".pdf", ".txt", ".tar.gz"])
        self.config["categories"]["重复"] = {"extensions": ["PDF"]}
        with self.assertRaises(ValueError):
            core.validate_config(self.config)

    def test_invalid_names_and_numbers(self):
        for name in ("../oops", "bad/name", "NUL", "COM1", "trailing.", "..", " leading"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                core.valid_name(name)
        for size in (-1, float("nan"), float("inf"), True, "100"):
            self.config["max_file_size_mb"] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                core.validate_config(self.config)

    def test_size_limit_zero_means_unlimited(self):
        self.file("big.bin", b"X" * 4096)
        self.config["max_file_size_mb"] = 0.001
        self.assertEqual(self.plan(), [])
        self.config["max_file_size_mb"] = 0
        self.assertEqual(len(self.plan()), 1)

    def test_longest_extension(self):
        self.config["categories"]["📦 压缩包"]["extensions"].remove(".tar.gz")
        self.config["categories"]["复合"] = {"extensions": [".tar.gz"]}
        self.file("test.tar.gz")
        self.assertEqual(self.plan()[0]["category"], "复合")

    def test_round_trip_and_collisions(self):
        self.file("note.txt", b"original")
        self.file("📄 文档/note.txt", b"existing")
        result = core.organize(self.root, self.plan(), self.records)
        self.assertEqual(result["done"], 1)
        self.assertEqual((self.root / "📄 文档/note.txt").read_bytes(), b"existing")
        self.file("note.txt", b"new")
        restored = core.restore(self.root, result["path"], self.records)
        self.assertEqual(restored["done"], 1)
        self.assertEqual((self.root / "note.txt").read_bytes(), b"new")
        self.assertEqual((self.root / "note (1).txt").read_bytes(), b"original")
        self.assertEqual(core.restore(self.root, result["path"], self.records)["done"], 0)

    def test_folder_round_trip_and_empty_cleanup(self):
        self.config["include_folders_in_organize"] = True
        self.file("project/sub/file.bin", b"inside")
        result = core.organize(self.root, self.plan(), self.records)
        self.assertEqual(result["done"], 1)
        core.restore(self.root, result["path"], self.records)
        self.assertEqual((self.root / "project/sub/file.bin").read_bytes(), b"inside")
        self.assertFalse((self.root / core.FOLDERS).exists())

    def test_changed_since_preview_is_not_moved(self):
        source = self.file("note.txt")
        plan = self.plan()
        source.write_bytes(b"changed content")
        result = core.organize(self.root, plan, self.records)
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertTrue(source.exists())

    def test_destination_created_after_preview_is_preserved(self):
        source = self.file("note.txt")
        plan = self.plan()
        self.file("📄 文档/note.txt", b"new arrival")
        result = core.organize(self.root, plan, self.records)
        self.assertEqual(result["done"], 0)
        self.assertTrue(source.exists())
        self.assertEqual((self.root / "📄 文档/note.txt").read_bytes(), b"new arrival")

    def test_journal_failure_before_move_keeps_source(self):
        source = self.file("note.txt")
        with patch.object(core, "atomic_json", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                core.organize(self.root, self.plan(), self.records)
        self.assertTrue(source.exists())

    def test_failure_after_move_has_recoverable_pending_record(self):
        source = self.file("note.txt")
        writer = core.atomic_json
        def fail_committed(path, value):
            if any(item["state"] == "moved" for item in value.get("files", [])):
                raise OSError("disk full")
            writer(path, value)
        with patch.object(core, "atomic_json", side_effect=fail_committed):
            with self.assertRaises(OSError):
                core.organize(self.root, self.plan(), self.records)
        record = next(self.records.glob("*.json"))
        self.assertFalse(source.exists())
        self.assertEqual(core.read_json(record)["files"][0]["state"], "pending")
        self.assertEqual(core.restore(self.root, record, self.records)["done"], 1)
        self.assertTrue(source.exists())

    def test_cancel_retains_completed_records(self):
        for number in range(3):
            self.file(str(number) + ".txt")
        cancel = threading.Event()
        result = core.organize(self.root, self.plan(), self.records, cancel, lambda *_: cancel.set())
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["done"], 1)
        self.assertEqual(core.restore(self.root, result["path"], self.records)["done"], 1)
        self.assertEqual(len(list(self.root.glob("*.txt"))), 3)

    def test_legacy_record_is_imported_and_repeat_safe(self):
        source = self.file("旧分类/测试.txt", b"legacy")
        legacy = self.base / "old.json"
        core.atomic_json(legacy, [{"original": str(self.root / "测试.txt"), "new": str(source), "category": "旧分类"}])
        original_bytes = legacy.read_bytes()
        self.assertEqual(core.restore(self.root, legacy, self.records)["done"], 1)
        self.assertEqual(core.restore(self.root, legacy, self.records)["done"], 0)
        self.assertEqual(legacy.read_bytes(), original_bytes)

    def test_restore_rejects_external_source(self):
        outside = self.base / "private.txt"
        outside.write_text("private")
        record = self.base / "bad.json"
        core.atomic_json(record, [{"original": str(self.root / "private.txt"), "new": str(outside), "category": ".."}])
        with self.assertRaises(ValueError):
            core.restore(self.root, record, self.records)
        self.assertEqual(outside.read_text(), "private")

    def test_restore_does_not_remove_user_desktop_ini(self):
        self.file("note.txt")
        result = core.organize(self.root, self.plan(), self.records)
        self.file("📄 文档/desktop.ini", b"user settings")
        core.restore(self.root, result["path"], self.records)
        self.assertEqual((self.root / "📄 文档/desktop.ini").read_bytes(), b"user settings")

    def test_backup_includes_shortcuts_large_files_and_empty_folders(self):
        self.file("shortcut.lnk", b"shortcut")
        self.file("big.bin", b"X" * 1048576)
        self.file("nested/content.txt", b"data")
        (self.root / "empty").mkdir()
        destination = self.root / "backup.zip"
        result = core.backup(self.root, destination)
        self.assertEqual(result["done"], 3)
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("empty/", archive.namelist())
            self.assertEqual(archive.read("big.bin"), b"X" * 1048576)
            self.assertEqual(archive.read("shortcut.lnk"), b"shortcut")
            self.assertNotIn("backup.zip", archive.namelist())
            self.assertFalse(any(name.endswith(".partial") for name in archive.namelist()))

    def test_backup_cancel_does_not_publish_partial_archive(self):
        self.file("large.bin", b"X" * (3 * 1048576))
        cancel = threading.Event()
        destination = self.base / "backup.zip"
        result = core.backup(self.root, destination, cancel=cancel, progress=lambda *_: cancel.set())
        self.assertTrue(result["cancelled"])
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.base.glob("*.partial")), [])

    def test_backup_existing_destination_untouched(self):
        destination = self.file("backup.zip", b"original")
        with self.assertRaises(FileExistsError):
            core.backup(self.root, destination)
        self.assertEqual(destination.read_bytes(), b"original")

    def test_backup_without_subfolders_reports_skipped(self):
        self.file("sub/file.txt")
        self.file("plain.txt")
        result = core.backup(self.root, self.base / "flat.zip", include_folders=False)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["skipped"], ["sub"])

    def test_link_is_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private")
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        rows = core.scan(self.root, self.config)
        self.assertTrue(rows[0]["reason"])
        result = core.backup(self.root, self.base / "links.zip")
        self.assertEqual(result["done"], 0)
        self.assertEqual(len(result["skipped"]), 1)

    def test_old_config_and_boolean_options_migrate(self):
        result = core.validate_config({"config": {"categories": {"文档": ["TXT"], "其他": []}, "include_folders_in_backup": False}})
        self.assertEqual(result["categories"]["文档"]["extensions"], [".txt"])
        self.assertIn(core.FOLDERS, result["categories"])
        self.assertFalse(result["include_folders_in_backup"])

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_is_skipped(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep")
        link = self.root / "junction"
        environment = dict(os.environ, CLEANER_TEST_LINK=str(link), CLEANER_TEST_TARGET=str(outside))
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", "New-Item -ItemType Junction -Path $env:CLEANER_TEST_LINK -Target $env:CLEANER_TEST_TARGET | Out-Null"], env=environment, check=True, capture_output=True)
        try:
            self.assertTrue(core.is_link(link))
            self.assertTrue(core.scan(self.root, self.config)[0]["reason"])
            result = core.backup(self.root, self.base / "junction.zip")
            self.assertEqual(result["done"], 0)
            self.assertEqual(len(result["skipped"]), 1)
            self.assertEqual((outside / "secret.txt").read_text(), "keep")
        finally:
            link.rmdir()

    def test_storage_directory_is_protected(self):
        storage = self.root / "data"
        storage.mkdir()
        self.config["include_folders_in_organize"] = True
        rows = core.scan(self.root, self.config, protected_paths=[storage])
        self.assertEqual(rows[0]["reason"], "程序数据或整理记录目录")
        result = core.organize(self.root, self.plan(), storage / "records")
        self.assertEqual(result["done"], 0)
        self.assertTrue(storage.exists())

    def test_permission_denied_item_does_not_lose_other_records(self):
        self.file("a.txt")
        self.file("b.txt")
        mover = core.move_no_replace
        def deny_first(source, target):
            if Path(source).name == "a.txt":
                raise PermissionError("file in use")
            mover(source, target)
        with patch.object(core, "move_no_replace", side_effect=deny_first):
            result = core.organize(self.root, self.plan(), self.records)
        self.assertEqual(result["done"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertTrue((self.root / "a.txt").exists())
        self.assertEqual(core.restore(self.root, result["path"], self.records)["done"], 1)

    def test_restore_interrupted_after_move_is_idempotent(self):
        self.file("a.txt")
        result = core.organize(self.root, self.plan(), self.records)
        writer = core.atomic_json
        def fail_restored(path, value):
            if any(item.get("state") == "restored" for item in value["files"]):
                raise OSError("disk full")
            writer(path, value)
        with patch.object(core, "atomic_json", side_effect=fail_restored):
            with self.assertRaises(OSError):
                core.restore(self.root, result["path"], self.records)
        self.assertTrue((self.root / "a.txt").exists())
        self.assertEqual(core.restore(self.root, result["path"], self.records)["done"], 0)
        self.assertEqual(len(list(self.root.glob("*.txt"))), 1)


if __name__ == "__main__":
    unittest.main()
