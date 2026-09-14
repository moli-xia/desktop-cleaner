"""Real Tk smoke tests in an isolated directory; no personal desktop operations."""
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from desktop_cleaner import DesktopCleaner


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.folder = self.base / "sample"
        self.folder.mkdir()
        (self.folder / "sample.txt").write_text("content")
        self.app = DesktopCleaner(directory=self.folder, storage=self.base / "data")
        self.errors = []
        self.app.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.pump(lambda: self.app.preview_valid)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def pump(self, predicate, timeout=10):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.app.root.update()
            if predicate():
                break
            time.sleep(0.01)
        self.assertTrue(predicate(), "Timed out waiting for UI worker")
        self.assertEqual(self.errors, [])

    def test_scan_selection_filter_and_busy_controls(self):
        self.assertEqual(len(self.app.rows), 1)
        self.assertEqual(len(self.app.checked), 1)
        self.app.toggle(0)
        self.assertEqual(len(self.app.checked), 0)
        self.assertTrue(self.app.organize_button.instate(["disabled"]))
        self.app.select_visible(True)
        self.app.search_var.set("not-present")
        self.assertEqual(len(self.app.tree.get_children()), 0)
        self.app.search_var.set("sample")
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.app.refresh()
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.organize_button.instate(["disabled"]))
        self.pump(lambda: not self.app.busy)

    def test_ui_organize_restore_and_persist_settings(self):
        with patch("desktop_cleaner.messagebox.askyesno", return_value=True):
            self.app.clean_desktop()
            self.pump(lambda: not self.app.busy)
            self.assertFalse((self.folder / "sample.txt").exists())
            self.assertEqual(len(self.app.records), 1)
            self.app.restore_from(next(iter(self.app.records.values())))
            self.pump(lambda: not self.app.busy)
        self.assertEqual((self.folder / "sample.txt").read_text(), "content")
        self.app.size_var.set("0")
        self.app.backup_folder_var.set(True)
        self.app.save_settings()
        self.assertTrue((self.base / "data/config.json").exists())
        self.assertEqual(self.app.config["max_file_size_mb"], 0)

    def test_modified_space_does_not_toggle_file(self):
        self.app.tree.focus("0")
        before = set(self.app.checked)
        for state in (0x4, 0x8, 0x20000):
            self.app.toggle_focus(SimpleNamespace(state=state))
            self.assertEqual(self.app.checked, before)
        self.app.toggle_focus(SimpleNamespace(state=0))
        self.assertEqual(self.app.checked, set())

    def test_navigation_buttons_fit_default_and_minimum_window(self):
        for geometry in ("1120x780", "900x640"):
            self.app.root.geometry(geometry)
            for name, page in self.app.pages.items():
                self.app.show_page(name)
                self.app.root.update()
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                for widget in descendants(page):
                    if isinstance(widget, (ttk.Button, ttk.Entry, tk.Checkbutton)):
                        with self.subTest(size=geometry, page=name, widget=widget.cget("text") if isinstance(widget, (ttk.Button, tk.Checkbutton)) else "entry"):
                            self.assertTrue(widget.winfo_ismapped())
                            self.assertGreaterEqual(widget.winfo_rootx(), self.app.root.winfo_rootx())
                            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.app.root.winfo_rootx() + self.app.root.winfo_width())
                            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.app.root.winfo_rooty() + self.app.root.winfo_height())

    def test_file_list_shows_rows_at_minimum_window(self):
        for number in range(12):
            (self.folder / "extra{}.txt".format(number)).write_text("x")
        self.app.refresh()
        self.pump(lambda: not self.app.busy)
        self.app.root.geometry("900x640")
        self.app.show_page("整理文件")
        self.app.root.update()
        rowheight = ttk.Style(self.app.root).lookup("Treeview", "rowheight")
        visible = self.app.tree.winfo_height() // int(rowheight)
        self.assertGreaterEqual(visible, 4, "file list should keep several rows visible at the minimum window size")
        self.assertFalse(self.app.tree.scrollbars[1].winfo_ismapped(), "columns must fit without a horizontal scrollbar")

    def test_busy_state_disables_native_checkbuttons_and_restores(self):
        self.app.refresh()
        self.assertTrue(self.app.busy)
        checks = [w for w in self.app.controls if isinstance(w, tk.Checkbutton)]
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(str(w.cget("state")) == "disabled" for w in checks))
        self.pump(lambda: not self.app.busy)
        self.assertTrue(all(str(w.cget("state")) == "normal" for w in checks))

    def test_category_display_hides_variation_selector_but_keeps_key(self):
        self.app.show_page("分类规则")
        keys = self.app.category_tree.get_children()
        self.assertIn("🖼️ 图片", keys)
        shown = self.app.category_tree.item("🖼️ 图片", "values")[0]
        self.assertNotIn(chr(0xFE0F), shown)
        self.app.category_tree.selection_set("🖼️ 图片")
        self.assertEqual(self.app.selected_category(), "🖼️ 图片")

    def test_rows_use_drawn_icons_and_checkbox_hit_area(self):
        item = self.app.tree.item("0")
        self.assertTrue(item["image"], "row should carry a composed checkbox + category bitmap")
        self.assertEqual(item["text"], "sample.txt")
        self.assertEqual(item["values"][1], "文档")
        x, y, width, height = self.app.tree.bbox("0", "#0")
        self.app.toggle_click(SimpleNamespace(x=x + 4, y=y + height // 2))
        self.assertEqual(self.app.checked, set())
        self.app.toggle_click(SimpleNamespace(x=x + width - 10, y=y + height // 2))
        self.assertEqual(self.app.checked, set(), "clicking the file name must not toggle")
        self.app.toggle_click(SimpleNamespace(x=x + 4, y=y + height // 2))
        self.assertEqual(self.app.checked, {0})

    def test_category_icon_choice_is_saved_as_emoji(self):
        self.app.category_dialog("📄 文档")
        dialog = [w for w in self.app.root.winfo_children() if w.winfo_class() == "Toplevel"][0]
        swatches = [w for w in self.walk(dialog) if isinstance(w, tk.Button) and w.cget("image")]
        self.assertEqual(len(swatches), 14)
        swatches[-1].invoke()  # star
        save = [w for w in self.walk(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "保存分类"]
        save[0].invoke()
        self.assertEqual(self.app.config["categories"]["📄 文档"]["icon"], "⭐")
        self.assertEqual(self.app.icon_key("📄 文档"), "star")
        self.assertEqual(self.app.icon_key("🖼️ 图片"), "image")
        self.assertEqual(self.app.icon_key("—"), "skipped")

    def test_display_name_strips_leading_symbols_only(self):
        from desktop_cleaner import display_name
        self.assertEqual(display_name("🖼️ 图片"), "图片")
        self.assertEqual(display_name("⭐ 收藏"), "收藏")
        self.assertEqual(display_name("Projects"), "Projects")
        self.assertEqual(display_name("📁"), "📁")
        self.assertEqual(display_name("—"), "—")

    def test_interface_survives_missing_assets(self):
        self.app.close()
        with patch("cleaner_core.resource_path", lambda name: self.base / "missing" / name):
            self.app = DesktopCleaner(directory=self.folder, storage=self.base / "data")
        self.app.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.pump(lambda: self.app.preview_valid)
        self.assertIsNone(self.app.assets.get("check_on"))
        self.assertTrue(self.app.tree.item("0")["text"].endswith("sample.txt"))
        self.app.toggle(0)
        self.assertEqual(self.app.checked, set())
        for name in self.app.pages:
            self.app.show_page(name)
            self.app.root.update()

    @staticmethod
    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from GuiTests.walk(child)

    def test_invalid_size_limit_is_reported_not_raised(self):
        self.app.size_var.set("abc")
        with patch("desktop_cleaner.messagebox.showerror") as error:
            self.app.save_settings()
        self.assertTrue(error.called)
        self.assertEqual(self.app.config["max_file_size_mb"], 100)


if __name__ == "__main__":
    unittest.main()
