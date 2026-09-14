"""Native desktop interface; all file work runs outside Tk's event loop."""
import argparse
import copy
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cleaner_core as core

BG, WHITE, INK, MUTED, BLUE, LINE = "#F3F5FA", "#FFFFFF", "#18243B", "#66758C", "#4263EB", "#E3E8F1"
SOFT, SOFT_HOVER, SOFT_BORDER = "#F6F8FC", "#E9EEFB", "#D3DAE8"
SIDEBAR, SIDEBAR_ACTIVE, SIDEBAR_HOVER, SIDEBAR_TEXT, SIDEBAR_ACCENT = "#17243C", "#2C3E63", "#22314F", "#BBC7DC", "#7B93FF"
FONT = "Microsoft YaHei UI"
PAGES = (("整理文件", "nav_home", "▦"), ("整理历史", "nav_history", "↶"), ("分类规则", "nav_rules", "≡"), ("偏好设置", "nav_settings", "⚙"), ("关于", "nav_about", "ⓘ"))
VARIATION_SELECTOR = chr(0xFE0F)
# Text fallbacks used only when the bitmap assets are missing (U+2611 renders as a slashed box in YaHei).
CHECKED, UNCHECKED = "\U0001F5F9", "☐"
# Drawn category icons: key, emoji stored in config, picker label. Keep in sync with tools/make_assets.py.
CATEGORY_ICONS = (("doc", "📄", "文档"), ("image", "🖼️", "图片"), ("video", "🎬", "视频"), ("audio", "🎵", "音频"), ("archive", "📦", "压缩包"), ("program", "💻", "程序"), ("folder", "📂", "文件夹"), ("other", "📁", "其他"), ("code", "🧩", "代码"), ("sheet", "📊", "表格"), ("design", "🎨", "设计"), ("download", "📥", "下载"), ("book", "📚", "书籍"), ("star", "⭐", "收藏"))
EMOJI_TO_ICON = {emoji.replace(VARIATION_SELECTOR, ""): key for key, emoji, _ in CATEGORY_ICONS}
ICON_TO_EMOJI = {key: emoji for key, emoji, _ in CATEGORY_ICONS}


def display_name(name):
    """Category folder names may start with an emoji; the interface shows a drawn icon instead."""
    text = name.replace(VARIATION_SELECTOR, "")
    stripped = text.lstrip()
    while stripped and (ord(stripped[0]) > 0xFFFF or unicodedata.category(stripped[0]) in ("So", "Sk", "Mn", "Cf")):
        stripped = stripped[1:].lstrip()
    return stripped or text or name


def set_enabled(widget, enabled):
    if isinstance(widget, ttk.Widget):
        widget.state(["!disabled" if enabled else "disabled"])
    else:
        widget.configure(state="normal" if enabled else "disabled")


class Assets:
    """Pre-rendered bitmaps for the current DPI. Lookups return None when a file is missing so the interface can fall back to text."""

    def __init__(self, master, scale):
        self.set_name = "1x" if scale < 1.25 else "1.5x" if scale < 1.75 else "2x"
        self.factor = {"1x": 1.0, "1.5x": 1.5, "2x": 2.0}[self.set_name]
        self.folder = core.resource_path("assets") / self.set_name
        self.master, self.cache = master, {}

    def get(self, name):
        if name not in self.cache:
            try:
                self.cache[name] = tk.PhotoImage(master=self.master, file=str(self.folder / (name + ".png")))
            except tk.TclError:
                self.cache[name] = None
        return self.cache[name]

    def large(self, name):
        """The next DPI set up, for pickers where a bigger swatch reads better."""
        larger = {"1x": "1.5x", "1.5x": "2x", "2x": "2x"}[self.set_name]
        key = "large:" + name
        if key not in self.cache:
            try:
                self.cache[key] = tk.PhotoImage(master=self.master, file=str(self.folder.parent / larger / (name + ".png")))
            except tk.TclError:
                self.cache[key] = self.get(name)
        return self.cache[key]

    def compose(self, name, parts, gap):
        """Side-by-side composite with a trailing gap; a Treeview cell can show only one image."""
        if name in self.cache:
            return self.cache[name]
        images = [self.get(part) for part in parts]
        if any(image is None for image in images):
            self.cache[name] = None
            return None
        width = sum(image.width() for image in images) + gap * len(images)
        height = max(image.height() for image in images)
        result = tk.PhotoImage(master=self.master, width=width, height=height)
        x = 0
        for image in images:
            result.tk.call(result.name, "copy", image.name, "-to", x, (height - image.height()) // 2)
            x += image.width() + gap
        self.cache[name] = result
        return result


class DesktopCleaner:
    def __init__(self, root=None, directory=None, storage=None):
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.root.title("桌面整理工具 · {}".format(core.VERSION.rsplit(".", 1)[0]))
        # Fonts follow Windows DPI automatically; pixel geometry must be scaled by hand.
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.minsize(self.px(900), self.px(640))
        # Fit small laptop screens (e.g. 1366x768 with a taskbar) and open centered.
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width = max(self.px(900), min(self.px(1120), screen_w - self.px(80)))
        height = max(self.px(640), min(self.px(780), screen_h - self.px(120)))
        self.root.geometry("{}x{}+{}+{}".format(width, height, max(0, (screen_w - width) // 2), max(0, (screen_h - height) // 2 - self.px(20))))
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(default=str(core.resource_path("app_icon.ico")))
        except tk.TclError:
            pass
        self.assets = Assets(self.root, self.scale)
        self.logo = self.assets.get("logo")
        self.storage = Path(storage) if storage else core.data_path()
        self.record_dir = self.storage / "records"
        self.config, warning = core.load_config(self.storage)
        self.desktop_path = Path(directory) if directory else core.get_desktop_path()
        self.rows, self.checked, self.records = [], set(), {}
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy, self.preview_valid = False, False
        self.last_result = ""
        self.controls, self.pages, self.nav = [], {}, {}
        self.current_page = None
        self.path_var = tk.StringVar(value=str(self.desktop_path))
        self.status_var = tk.StringVar(value="准备就绪")
        self.result_var = tk.StringVar(value="先预览，再整理。你的文件由你决定。")
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="全部项目")
        self.setup_style()
        self.setup_ui()
        self.show_page("整理文件")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.report_exception
        self.root.bind("<F5>", lambda event: self.refresh())
        self.root.bind("<Control-f>", self.focus_search)
        self.root.bind("<Control-F>", self.focus_search)
        self.root.bind("<Escape>", lambda event: self.cancel() if self.busy else None)
        self.poll_id = self.root.after(80, self.poll)
        self.root.deiconify()
        if warning:
            self.root.after(150, lambda: messagebox.showwarning("设置读取提醒", warning, parent=self.root))
        self.root.after(250, self.refresh)

    def px(self, value):
        return int(round(value * self.scale))

    # ----------------------------------------------------------------------------- styling
    def setup_style(self):
        px = self.px
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=BG, foreground=INK)
        rounded = self.create_rounded_elements(style)
        if rounded:
            label = [("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]
            style.layout("TButton", [("Rounded.Button.border", {"sticky": "nswe", "children": label})])
            style.layout("Primary.TButton", [("Rounded.Primary.border", {"sticky": "nswe", "children": label})])
            style.configure("TButton", padding=(px(14), px(7)), foreground=INK, background=BG, borderwidth=0, relief="flat")
            style.map("TButton", foreground=[("disabled", "#A3ACBB")], background=[])
            style.configure("Primary.TButton", foreground=WHITE)
            style.map("Primary.TButton", foreground=[("disabled", WHITE)])
            entry = [("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]
            style.layout("TEntry", [("Rounded.Entry.field", {"sticky": "nswe", "children": entry})])
            style.layout("Small.TEntry", [("Rounded.SmallEntry.field", {"sticky": "nswe", "children": entry})])
            style.configure("TEntry", padding=(px(9), px(6)), fieldbackground=WHITE, insertcolor=INK, borderwidth=0)
            style.map("TEntry", fieldbackground=[("readonly", SOFT), ("disabled", "#F1F3F7")], foreground=[("disabled", "#8A94A6")])
            combo = [("Combobox.downarrow", {"side": "right", "sticky": "ns"}), ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]
            style.layout("TCombobox", [("Rounded.Combobox.field", {"sticky": "nswe", "children": combo})])
            style.configure("TCombobox", padding=(px(9), px(6)), fieldbackground=WHITE, background=WHITE, bordercolor=WHITE, lightcolor=WHITE, darkcolor=WHITE, arrowcolor=MUTED, arrowsize=px(14), borderwidth=0)
            style.map("TCombobox", fieldbackground=[("readonly", WHITE)], background=[("readonly", WHITE)], foreground=[("readonly", INK)], selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        else:
            style.configure("TButton", padding=(px(12), px(7)), background=SOFT, foreground=INK, bordercolor=SOFT_BORDER, lightcolor=SOFT, darkcolor=SOFT, relief="solid", borderwidth=1, focuscolor=SOFT)
            style.map("TButton", background=[("disabled", "#F1F3F7"), ("pressed", "#DCE3F5"), ("active", SOFT_HOVER)], foreground=[("disabled", "#A3ACBB")], bordercolor=[("disabled", "#E6EAF1"), ("active", "#B9C6EA")])
            style.configure("Primary.TButton", background=BLUE, foreground=WHITE, bordercolor=BLUE, lightcolor=BLUE, darkcolor=BLUE, focuscolor=BLUE)
            style.map("Primary.TButton", background=[("disabled", "#C5CFF7"), ("pressed", "#2E4BC7"), ("active", "#3453CF")], foreground=[("disabled", WHITE)], bordercolor=[("disabled", "#C5CFF7"), ("pressed", "#2E4BC7"), ("active", "#3453CF")])
            style.configure("TEntry", padding=px(7), fieldbackground=WHITE, bordercolor=LINE, lightcolor=WHITE, darkcolor=WHITE, insertcolor=INK)
            style.map("TEntry", bordercolor=[("focus", BLUE)], lightcolor=[("focus", WHITE)], darkcolor=[("focus", WHITE)])
            style.configure("TCombobox", padding=px(6), fieldbackground=WHITE, background=SOFT, bordercolor=LINE, lightcolor=WHITE, darkcolor=WHITE, arrowcolor=MUTED, arrowsize=px(14))
            style.map("TCombobox", fieldbackground=[("readonly", WHITE)], foreground=[("readonly", INK)], selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)], bordercolor=[("focus", BLUE)])
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, rowheight=px(30), borderwidth=0, relief="flat", font=(FONT, 10))
        style.configure("Treeview.Heading", background="#F0F3F9", foreground=MUTED, font=(FONT, 9, "bold"), padding=(px(8), px(8)), relief="flat", bordercolor="#F0F3F9", lightcolor="#F0F3F9", darkcolor="#F0F3F9")
        style.map("Treeview.Heading", background=[("active", "#E6EBF5")])
        style.map("Treeview", background=[("selected", "#E9EEFF")], foreground=[("selected", INK)])
        # Drop the expand indicator so flat lists start at the left edge with the row image.
        style.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [("Treeitem.image", {"side": "left", "sticky": ""}), ("Treeitem.focus", {"side": "left", "sticky": "", "children": [("Treeitem.text", {"side": "left", "sticky": ""})]})]})])
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor=LINE, bordercolor=LINE, lightcolor=BLUE, darkcolor=BLUE, borderwidth=0, thickness=px(8))
        for orient in ("Vertical", "Horizontal"):
            style.configure(orient + ".TScrollbar", background="#CBD3E3", troughcolor="#F3F5FA", bordercolor="#F3F5FA", lightcolor="#CBD3E3", darkcolor="#CBD3E3", arrowcolor=MUTED, arrowsize=px(12), gripcount=0, relief="flat")
            style.map(orient + ".TScrollbar", background=[("active", "#B4BFD3"), ("pressed", "#9FABC3")])
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#E9EEFF")
        self.root.option_add("*TCombobox*Listbox.selectForeground", INK)

    def create_rounded_elements(self, style):
        """Nine-slice bitmaps give real rounded corners; returns False when any asset is missing."""
        names = ("btn_secondary", "btn_secondary_hover", "btn_secondary_pressed", "btn_secondary_disabled", "btn_primary", "btn_primary_hover", "btn_primary_pressed", "btn_primary_disabled", "field", "field_focus", "field_readonly", "field_disabled", "field_small", "field_small_focus", "field_small_readonly", "field_small_disabled")
        images = {name: self.assets.get(name) for name in names}
        if any(image is None for image in images.values()):
            return False
        # Nine-slice split one pixel inside the corner radius; padding=0 keeps the border
        # from also counting as interior padding (the widgets' own padding insets the text).
        border = int(round(9 * self.assets.factor))
        field_border = int(round(7 * self.assets.factor))
        inset = 0
        specs = (
            ("Rounded.Button.border", "btn_secondary", (("disabled", "btn_secondary_disabled"), ("pressed", "btn_secondary_pressed"), ("active", "btn_secondary_hover")), border),
            ("Rounded.Primary.border", "btn_primary", (("disabled", "btn_primary_disabled"), ("pressed", "btn_primary_pressed"), ("active", "btn_primary_hover")), border),
            ("Rounded.Entry.field", "field", (("disabled", "field_disabled"), ("readonly", "field_readonly"), ("focus", "field_focus")), field_border),
            ("Rounded.SmallEntry.field", "field_small", (("disabled", "field_small_disabled"), ("readonly", "field_small_readonly"), ("focus", "field_small_focus")), field_border),
            ("Rounded.Combobox.field", "field_small", (("disabled", "field_small_disabled"), ("focus", "field_small_focus")), field_border),
        )
        try:
            for element, base, states, size in specs:
                style.element_create(element, "image", images[base], *[(state, images[name]) for state, name in states], border=size, padding=inset, sticky="nsew")
        except tk.TclError:
            return False
        return True

    def label(self, parent, text=None, size=10, color=INK, bold=False, **kwargs):
        if "wraplength" in kwargs:
            kwargs["wraplength"] = self.px(kwargs["wraplength"])
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text, command, primary=False, guarded=True):
        widget = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton", cursor="hand2")
        if guarded:
            self.controls.append(widget)
        return widget

    def checkbox(self, parent, text, variable):
        on, off = self.assets.get("check_on"), self.assets.get("check_off")
        # Drawn boxes match the file list; indicatoron=False turns the widget into a flat image button.
        options = {"image": off, "selectimage": on, "compound": "left", "indicatoron": False, "offrelief": "flat", "overrelief": "flat", "text": "  " + text, "padx": self.px(2), "pady": self.px(3)} if on is not None and off is not None else {"text": text}
        widget = tk.Checkbutton(parent, variable=variable, bg=WHITE, fg=INK, activebackground=WHITE, activeforeground=INK, selectcolor=WHITE, font=(FONT, 10), anchor="w", cursor="hand2", highlightthickness=0, bd=0, relief="flat", **options)
        self.controls.append(widget)
        return widget

    def card(self, parent, **kwargs):
        for key in ("padx", "pady"):
            if key in kwargs:
                kwargs[key] = self.px(kwargs[key])
        return tk.Frame(parent, bg=WHITE, highlightbackground=LINE, highlightthickness=1, **kwargs)

    def icon_key(self, category):
        """Map a category to a drawn icon via its configured emoji, then its name prefix."""
        info = self.config["categories"].get(category)
        if info is None:
            return "skipped" if category == "—" else "other"
        for text in (str(info.get("icon", "")), category):
            text = text.replace(VARIATION_SELECTOR, "").strip()
            for emoji, key in EMOJI_TO_ICON.items():
                if text.startswith(emoji):
                    return key
        return "folder" if "__FOLDER__" in info["extensions"] else "other"

    def row_image(self, state, key):
        return self.assets.compose("row_{}_{}".format(state, key), ("check_" + state, "cat_" + key), self.px(6))

    # ----------------------------------------------------------------------------- layout
    def setup_ui(self):
        px = self.px
        sidebar = tk.Frame(self.root, bg=SIDEBAR, width=px(188))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(anchor="w", fill="x", padx=px(22), pady=(px(24), px(24)))
        if self.logo is not None:
            tk.Label(brand, image=self.logo, bg=SIDEBAR, bd=0).pack(anchor="w", pady=(0, px(10)))
        else:
            self.label(brand, "▦", size=30, color="#91A5FF").pack(anchor="w")
        self.label(brand, "桌面整理", size=18, color=WHITE, bold=True).pack(anchor="w")
        self.label(brand, "让桌面回归清爽", size=9, color="#A5B3CB").pack(anchor="w", pady=(px(4), 0))
        for name, icon, glyph in PAGES:
            item = tk.Frame(sidebar, bg=SIDEBAR)
            item.pack(fill="x", padx=(0, px(14)), pady=px(2))
            accent = tk.Frame(item, bg=SIDEBAR, width=px(3))
            accent.pack(side="left", fill="y")
            image = self.assets.get(icon)
            button = tk.Button(item, text=("  " + name) if image is not None else "{}   {}".format(glyph, name), image=image, compound="left", anchor="w", padx=px(17), pady=px(10), font=(FONT, 11), bg=SIDEBAR, fg=SIDEBAR_TEXT, activebackground=SIDEBAR_ACTIVE, activeforeground=WHITE, relief="flat", bd=0, highlightthickness=0, cursor="hand2", command=lambda n=name: self.show_page(n))
            button.pack(side="left", fill="both", expand=True)
            button.bind("<Enter>", lambda event, n=name: self.nav_hover(n, True))
            button.bind("<Leave>", lambda event, n=name: self.nav_hover(n, False))
            self.nav[name] = (item, accent, button, icon)
        self.label(sidebar, "本地处理 · 文件不上传\nv{}".format(core.VERSION), size=9, color="#A5B3CB", justify="left").pack(side="bottom", anchor="w", padx=px(22), pady=px(20))
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        footer = tk.Frame(shell, bg=WHITE, padx=px(22), pady=px(8), highlightbackground=LINE, highlightthickness=1)
        footer.pack(side="bottom", fill="x")
        self.cancel_button = self.button(footer, "取消任务", self.cancel, guarded=False)
        self.cancel_button.pack(side="right")
        self.cancel_button.state(["disabled"])
        self.progress = ttk.Progressbar(footer, length=px(150), mode="determinate")
        self.progress.pack(side="right", padx=px(14))
        self.label(footer, textvariable=self.status_var, color=MUTED, anchor="w").pack(side="left", fill="x", expand=True)
        content = tk.Frame(shell, bg=BG)
        content.pack(fill="both", expand=True, padx=px(24), pady=px(16))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        for name in self.nav:
            page = tk.Frame(content, bg=BG)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.setup_home(self.pages["整理文件"])
        self.setup_history(self.pages["整理历史"])
        self.setup_categories(self.pages["分类规则"])
        self.setup_settings(self.pages["偏好设置"])
        self.setup_about(self.pages["关于"])

    def heading(self, parent, title, subtitle):
        self.label(parent, title, size=20, bold=True).pack(anchor="w")
        self.label(parent, subtitle, color=MUTED).pack(anchor="w", pady=(self.px(3), self.px(14)))

    def setup_home(self, page):
        px = self.px
        self.heading(page, "给桌面，留一点空间", "预览分类去向，选中需要的项目，再开始整理。")
        location = self.card(page, padx=14, pady=9)
        location.pack(fill="x")
        self.label(location, "当前位置", color=MUTED).pack(side="left", padx=(0, px(10)))
        self.button(location, "更换目录", self.choose_directory).pack(side="right", padx=(px(8), 0))
        self.button(location, "打开目录", lambda: self.open_path(self.desktop_path), guarded=False).pack(side="right", padx=(px(10), 0))
        ttk.Entry(location, textvariable=self.path_var, state="readonly").pack(side="left", fill="x", expand=True)
        stats = tk.Frame(page, bg=BG)
        stats.pack(fill="x", pady=px(10))
        self.stat_vars = []
        for col, (name, color, icon) in enumerate((("可整理项目", BLUE, "stat_ready"), ("保持原位", MUTED, "stat_keep"), ("已选文件大小", "#128577", "stat_size"))):
            stats.columnconfigure(col, weight=1, uniform="stat")
            card = self.card(stats, padx=14, pady=9)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else px(8), 0))
            image = self.assets.get(icon)
            if image is not None:
                tk.Label(card, image=image, bg=WHITE, bd=0).pack(side="left", padx=(0, px(12)))
            body = tk.Frame(card, bg=WHITE)
            body.pack(side="left", fill="x", expand=True)
            self.label(body, name, size=9, color=MUTED).pack(anchor="w")
            variable = tk.StringVar(value="—")
            self.stat_vars.append(variable)
            self.label(body, textvariable=variable, size=19, color=color, bold=True).pack(anchor="w")
        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x")
        self.organize_button = self.button(actions, "整理已选项目", self.clean_desktop, primary=True)
        self.organize_button.pack(side="left")
        self.button(actions, "重新扫描 · F5", self.refresh).pack(side="left", padx=px(8))
        self.button(actions, "备份为 ZIP", self.backup_desktop).pack(side="right")
        self.label(page, textvariable=self.result_var, color=MUTED, anchor="w").pack(fill="x", pady=(px(8), px(8)))
        table_card = self.card(page, padx=10, pady=10)
        table_card.pack(fill="both", expand=True)
        filters = tk.Frame(table_card, bg=WHITE)
        filters.pack(fill="x", pady=(0, px(8)))
        self.label(filters, "搜索", color=MUTED).pack(side="left", padx=(0, px(8)))
        self.search_entry = ttk.Entry(filters, textvariable=self.search_var, width=22)
        self.search_entry.pack(side="left", fill="x", expand=True)
        ttk.Combobox(filters, textvariable=self.filter_var, values=("全部项目", "可整理", "已跳过", "已勾选"), state="readonly", width=9).pack(side="left", padx=px(8))
        self.button(filters, "全选", lambda: self.select_visible(True)).pack(side="left")
        self.button(filters, "清空", lambda: self.select_visible(False)).pack(side="left", padx=(px(6), 0))
        self.search_var.trace_add("write", lambda *_: self.render_rows())
        self.filter_var.trace_add("write", lambda *_: self.render_rows())
        self.tree = self.make_tree(table_card, [("size", "大小", 80), ("category", "分类", 110), ("target", "目标 / 跳过原因", 190)], tree_column=("文件名称", 230))
        self.tree.column("size", stretch=False, anchor="e")
        self.tree.heading("size", anchor="e")
        self.tree.tag_configure("skipped", foreground="#8792A3")
        self.tree.tag_configure("stripe", background="#F8FAFE")
        check = self.assets.get("check_on")
        self.check_hit_width = (check.width() + px(10)) if check is not None else px(28)
        self.tree.bind("<Button-1>", self.toggle_click)
        self.tree.bind("<space>", self.toggle_focus)
        self.tree.bind("<Double-1>", self.show_file_details)
        self.tree.bind("<Control-a>", lambda event: (self.select_visible(True), "break")[1])
        self.label(table_card, "点击勾选框选择 · 空格切换当前项 · 双击查看完整路径 · 文件夹大小不计入统计", size=8, color=MUTED).pack(anchor="w", pady=(px(6), 0))

    def make_tree(self, parent, columns, tree_column=None):
        frame = tk.Frame(parent, bg=WHITE)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="tree headings" if tree_column else "headings", selectmode="browse", height=5)
        if tree_column:
            tree.heading("#0", text=tree_column[0], anchor="w")
            tree.column("#0", width=self.px(tree_column[1]), minwidth=self.px(120), anchor="w")
        for key, title, width in columns:
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=self.px(width), minwidth=self.px(45), anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=lambda first, last: self.autohide(vertical, first, last), xscrollcommand=lambda first, last: self.autohide(horizontal, first, last))
        tree.scrollbars = (vertical, horizontal)
        return tree

    @staticmethod
    def autohide(scrollbar, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            scrollbar.grid_remove()
        else:
            scrollbar.grid()
        scrollbar.set(first, last)

    def setup_history(self, page):
        px = self.px
        self.heading(page, "每次整理，都有来路", "整理记录自动保存。选中一条记录，即可将文件恢复到原目录。")
        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=(0, px(12)))
        self.button(actions, "恢复选中记录", self.restore_selected, primary=True).pack(side="left")
        self.button(actions, "查看详情", self.record_details, guarded=False).pack(side="left", padx=px(8))
        self.button(actions, "导入旧记录", self.import_record).pack(side="left")
        self.button(actions, "导出记录", self.export_record).pack(side="left", padx=px(8))
        self.button(actions, "刷新", self.refresh_history).pack(side="right")
        box = self.card(page, padx=10, pady=10)
        box.pack(fill="both", expand=True)
        self.history_tree = self.make_tree(box, [("time", "整理时间", 160), ("count", "文件数", 70), ("status", "状态", 120), ("root", "原目录", 260)])
        self.history_tree.column("count", anchor="e", stretch=False)
        self.history_tree.heading("count", anchor="e")
        self.history_tree.bind("<Double-1>", lambda event: self.record_details())
        self.history_hint = tk.StringVar(value="暂无整理记录，第一次整理后会自动出现在这里。")
        self.label(page, textvariable=self.history_hint, color=MUTED, wraplength=760, justify="left").pack(anchor="w", pady=(px(12), px(4)))
        self.label(page, "恢复遇到同名文件会自动改名；只清理本次创建且已经为空的分类文件夹。", size=9, color=MUTED, wraplength=760, justify="left").pack(anchor="w")

    def setup_categories(self, page):
        px = self.px
        self.heading(page, "按你的习惯分类", "自定义分类名称、图标与扩展名，支持复合扩展名，如 .tar.gz。")
        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", pady=(0, px(12)))
        self.button(bar, "添加分类", self.add_category, primary=True).pack(side="left")
        self.button(bar, "编辑", self.edit_category).pack(side="left", padx=px(8))
        self.button(bar, "删除", self.delete_category).pack(side="left")
        self.button(bar, "恢复默认规则", self.reset_categories).pack(side="right")
        box = self.card(page, padx=10, pady=10)
        box.pack(fill="both", expand=True)
        self.category_tree = self.make_tree(box, [("extensions", "匹配扩展名", 380)], tree_column=("分类 / 文件夹名称", 220))
        self.category_tree.bind("<Double-1>", lambda event: self.edit_category())
        self.label(page, "规则保存后需重新扫描。修改分类名称不会重命名已有文件夹；扩展名按最长后缀优先匹配。", size=9, color=MUTED, wraplength=760, justify="left").pack(anchor="w", pady=(px(12), 0))
        self.refresh_category_list()

    def setup_settings(self, page):
        px = self.px
        self.heading(page, "少一点设置，多一点省心", "整理与备份分别设置，避免把需要备份的文件漏掉。")
        card = self.card(page, padx=20, pady=14)
        card.pack(fill="x")
        self.label(card, "整理选项", size=13, bold=True).pack(anchor="w", pady=(0, px(10)))
        self.label(card, "保留这些扩展名（逗号分隔，不参与整理）", color=MUTED).pack(anchor="w")
        self.ext_var = tk.StringVar(value=", ".join(self.config["excluded_extensions"]))
        entry = ttk.Entry(card, textvariable=self.ext_var)
        entry.pack(fill="x", pady=(px(4), px(10)))
        self.controls.append(entry)
        row = tk.Frame(card, bg=WHITE)
        row.pack(fill="x")
        self.label(row, "单个文件大小上限", color=MUTED).pack(side="left")
        self.size_var = tk.StringVar(value=self.format_limit(self.config["max_file_size_mb"]))
        entry = ttk.Entry(row, textvariable=self.size_var, width=9, style="Small.TEntry")
        entry.pack(side="left", padx=px(10))
        self.controls.append(entry)
        self.label(row, "MB，0 表示不限大小", color=MUTED).pack(side="left")
        self.folder_var = tk.BooleanVar(value=self.config["include_folders_in_organize"])
        self.backup_folder_var = tk.BooleanVar(value=self.config["include_folders_in_backup"])
        self.checkbox(card, "整理时包含普通文件夹（整体移动，不拆分内容）", self.folder_var).pack(anchor="w", pady=(px(10), 0))
        self.checkbox(card, "备份时包含子文件夹及其内容", self.backup_folder_var).pack(anchor="w", pady=(px(2), 0))
        self.label(card, "备份不受扩展名和大小上限影响。链接及重解析点会跳过，并在结果中列出。", size=9, color=MUTED, wraplength=640, justify="left").pack(anchor="w", pady=(px(8), px(10)))
        self.button(card, "保存设置", self.save_settings, primary=True).pack(anchor="w")
        data = self.card(page, padx=14, pady=9)
        data.pack(fill="x", pady=(px(12), 0))
        self.label(data, "数据目录", color=MUTED).pack(side="left", padx=(0, px(10)))
        self.button(data, "打开数据目录", self.open_storage, guarded=False).pack(side="right", padx=(px(10), 0))
        self.storage_var = tk.StringVar(value=str(self.storage))
        ttk.Entry(data, textvariable=self.storage_var, state="readonly").pack(side="left", fill="x", expand=True)
        bar = tk.Frame(page, bg=BG)
        bar.pack(fill="x", pady=px(12))
        self.button(bar, "导入配置", self.import_config).pack(side="left")
        self.button(bar, "导出配置", self.export_config).pack(side="left", padx=px(8))
        self.label(page, "设置与历史记录保存在数据目录中，更新程序后仍然保留；也可用 --data-dir 参数指定便携目录。", size=9, color=MUTED, wraplength=760, justify="left").pack(anchor="w")

    def setup_about(self, page):
        px = self.px
        self.heading(page, "小工具，也值得认真打磨", "桌面整理工具  v{}".format(core.VERSION))
        card = self.card(page, padx=24, pady=18)
        card.pack(fill="both", expand=True)
        head = tk.Frame(card, bg=WHITE)
        head.pack(anchor="w", fill="x")
        if self.logo is not None:
            tk.Label(head, image=self.logo, bg=WHITE, bd=0).pack(side="left", padx=(0, px(14)))
        self.label(head, "整理有预览，恢复有记录", size=16, bold=True).pack(side="left")
        self.label(card, "文件始终留在你的电脑上。\n• 先看分类去向，再选择要整理的项目\n• 同名自动保留两份，逐项记录移动结果\n• 后台执行，支持取消与中断后的记录恢复\n• ZIP 备份包含完整文件内容，可用常见解压软件打开", color=MUTED, justify="left", wraplength=620).pack(anchor="w", pady=(px(14), px(12)))
        self.label(card, "快捷键", size=11, bold=True).pack(anchor="w", pady=(0, px(3)))
        self.label(card, "F5 重新扫描 · Ctrl+F 搜索 · 空格 切换勾选 · Ctrl+A 全选当前列表 · Esc 取消任务", size=9, color=MUTED, justify="left", wraplength=620).pack(anchor="w", pady=(0, px(12)))
        self.label(card, "开源许可 · MIT", size=11, bold=True).pack(anchor="w", pady=(0, px(3)))
        self.label(card, "原作者：Bin。允许使用、修改、分发及商业用途，须保留版权及许可声明；软件按原样提供，完整条款见 LICENSE。", size=9, color=MUTED, justify="left", wraplength=620).pack(anchor="w")
        self.button(card, "查看完整许可", self.show_license, guarded=False).pack(anchor="w", pady=(px(10), 0))

    # ----------------------------------------------------------------------------- navigation
    def nav_hover(self, name, inside):
        if name != self.current_page:
            item, accent, button, _ = self.nav[name]
            for widget in (item, button):
                widget.configure(bg=SIDEBAR_HOVER if inside else SIDEBAR)

    def show_page(self, name):
        self.current_page = name
        self.pages[name].tkraise()
        for key, (item, accent, button, icon) in self.nav.items():
            active = key == name
            item.configure(bg=SIDEBAR_ACTIVE if active else SIDEBAR)
            accent.configure(bg=SIDEBAR_ACCENT if active else SIDEBAR)
            button.configure(bg=SIDEBAR_ACTIVE if active else SIDEBAR, fg=WHITE if active else SIDEBAR_TEXT)
            image = self.assets.get(icon + ("_active" if active else ""))
            if image is not None:
                button.configure(image=image)
        if name == "整理历史" and not self.busy:
            self.refresh_history()

    def focus_search(self, event=None):
        self.show_page("整理文件")
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    @staticmethod
    def format_limit(value):
        return str(int(value)) if float(value).is_integer() else str(value)

    # ----------------------------------------------------------------------------- file list
    def visible_indices(self):
        term, mode = self.search_var.get().strip().casefold(), self.filter_var.get()
        for index, row in enumerate(self.rows):
            if term and term not in (row["name"] + row["category"] + row["reason"]).casefold():
                continue
            if mode == "可整理" and row["reason"] or mode == "已跳过" and not row["reason"] or mode == "已勾选" and index not in self.checked:
                continue
            yield index

    def render_rows(self):
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        for position, index in enumerate(self.visible_indices()):
            row = self.rows[index]
            state = "none" if row["reason"] else "on" if index in self.checked else "off"
            image = self.row_image(state, "skipped" if row["reason"] else self.icon_key(row["category"]))
            text = row["name"] if image is not None else "{}  {}".format("—" if row["reason"] else CHECKED if state == "on" else UNCHECKED, row["name"])
            category = display_name(row["category"])
            destination = row["reason"] or str(Path(category) / Path(row["target"]).name)
            options = {"image": image} if image is not None else {}
            self.tree.insert("", "end", iid=str(index), text=text, values=("文件夹" if row["folder"] else core.format_size(row["size"]), category, destination), tags=("skipped" if row["reason"] else "ready", "stripe" if position % 2 else "plain"), **options)
        eligible = sum(not row["reason"] for row in self.rows)
        self.stat_vars[0].set(str(eligible))
        self.stat_vars[1].set(str(len(self.rows) - eligible))
        self.stat_vars[2].set(core.format_size(sum(self.rows[i]["size"] for i in self.checked)))
        self.organize_button.configure(text="整理已选 {} 项".format(len(self.checked)))
        self.organize_button.state(["!disabled"] if self.checked and self.preview_valid and not self.busy else ["disabled"])
        if self.preview_valid and not self.busy:
            selection_text = "已选 {} 项；重名文件自动改名。".format(len(self.checked)) if self.checked else "当前没有选中项目。"
            self.result_var.set((self.last_result + "。" if self.last_result else "") + selection_text)

    def toggle_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "tree" or self.tree.identify_column(event.x) != "#0":
            return None
        iid = self.tree.identify_row(event.y)
        bbox = self.tree.bbox(iid, "#0") if iid else None
        if bbox and event.x - bbox[0] <= self.check_hit_width:
            self.toggle(int(iid))
            return "break"
        return None

    def toggle_focus(self, event):
        # Preserve native Alt+Space and other modified shortcuts.
        if event.state & (0x000C | 0x20000):
            return None
        if self.tree.focus():
            self.toggle(int(self.tree.focus()))
        return "break"

    def toggle(self, index):
        if self.busy or self.rows[index]["reason"]:
            return
        self.checked.remove(index) if index in self.checked else self.checked.add(index)
        self.render_rows()
        if self.tree.exists(str(index)):
            self.tree.focus(str(index))
            self.tree.selection_set(str(index))
            self.tree.see(str(index))

    def select_visible(self, value):
        if self.busy:
            return
        for index in list(self.visible_indices()):
            if not self.rows[index]["reason"]:
                self.checked.add(index) if value else self.checked.discard(index)
        self.render_rows()

    def choose_directory(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.root, title="选择要整理的目录", initialdir=str(self.desktop_path), mustexist=True)
        if selected:
            self.desktop_path = Path(selected).resolve()
            self.last_result = ""
            self.path_var.set(str(self.desktop_path))
            self.refresh()

    # ----------------------------------------------------------------------------- background jobs
    def start_job(self, title, work, on_success):
        if self.busy:
            return
        self.busy = True
        self.cancel_event = threading.Event()
        for widget in self.controls:
            set_enabled(widget, False)
        self.cancel_button.state(["!disabled"])
        self.status_var.set(title)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        last = [0.0]
        def progress(done, total, text):
            now = time.monotonic()
            if now - last[0] > 0.1 or total and done == total:
                self.events.put(("progress", (done, total, text)))
                last[0] = now
        def run():
            try:
                result = work(self.cancel_event, progress)
                self.events.put(("success", (on_success, result)))
            except Exception as exc:
                self.events.put(("error", str(exc) or exc.__class__.__name__))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            for _ in range(50):
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    done, total, text = payload
                    self.status_var.set(text[:65])
                    if total:
                        self.progress.stop()
                        self.progress.configure(mode="determinate", maximum=total, value=done)
                else:
                    self.busy = False
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.cancel_button.state(["disabled"])
                    for widget in self.controls:
                        set_enabled(widget, True)
                    if kind == "success":
                        callback, result = payload
                        callback(result)
                    else:
                        self.preview_valid = False
                        self.status_var.set("任务未完成，请查看详情")
                        self.result_var.set("操作遇到问题；已完成的移动可在整理历史中恢复。")
                        self.show_text("操作未完成", payload)
                    self.render_rows()
        except queue.Empty:
            pass
        self.poll_id = self.root.after(80, self.poll)

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.status_var.set("正在取消，等待当前文件操作安全结束…")
            self.cancel_button.state(["disabled"])

    def refresh(self):
        if self.busy:
            return
        if not self.desktop_path.is_dir():
            self.status_var.set("目录不存在，请更换目录")
            self.result_var.set("找不到目录：{}".format(self.desktop_path))
            self.preview_valid = False
            self.rows, self.checked = [], set()
            self.render_rows()
            return
        self.preview_valid = False
        self.rows, self.checked = [], set()
        self.render_rows()
        config, root = copy.deepcopy(self.config), self.desktop_path
        def complete(rows):
            self.rows = rows
            self.preview_valid = not self.cancel_event.is_set()
            self.checked = {i for i, row in enumerate(rows) if not row["reason"]} if self.preview_valid else set()
            self.status_var.set("扫描已取消，请重新扫描" if not self.preview_valid else "扫描完成 · {} 个项目".format(len(rows)))
            self.result_var.set("扫描未完成，重新扫描后再整理。" if not self.preview_valid else "没有需要整理的文件，可以更换目录或调整规则。" if not self.checked else "已选 {} 项；整理前会再次确认，重名文件自动改名。".format(len(self.checked)))
        self.start_job("正在扫描目录…", lambda cancel, progress: core.scan(root, config, cancel, progress, protected_paths=[self.storage]), complete)

    def clean_desktop(self):
        if self.busy or not self.preview_valid or not self.checked:
            return
        rows = [copy.deepcopy(self.rows[i]) for i in sorted(self.checked)]
        folders = sum(row["folder"] for row in rows)
        text = "将整理 {} 项到当前目录的分类文件夹中。\n\n目录：{}\n\n整理记录会自动保存，之后可从“整理历史”恢复。".format(len(rows), self.desktop_path)
        if folders:
            text += "\n\n包含 {} 个文件夹，将整体移动其内容。".format(folders)
        if not messagebox.askyesno("确认整理", text, parent=self.root):
            return
        self.preview_valid = False
        root = self.desktop_path
        self.start_job("正在整理…", lambda cancel, progress: core.organize(root, rows, self.record_dir, cancel, progress), lambda result: self.operation_complete("整理", result))

    def operation_complete(self, action, result):
        self.preview_valid = False
        self.rows, self.checked = [], set()
        errors, skipped = result.get("errors", []), result.get("skipped", [])
        count = len(skipped) if isinstance(skipped, list) else skipped
        text = "{}{} · 完成 {} 项".format(action, "已取消" if result.get("cancelled") else "结束", result["done"])
        if errors:
            text += " · {} 项问题".format(len(errors))
        if count:
            text += " · 跳过 {} 项".format(count)
        self.status_var.set(text)
        self.last_result = text
        self.result_var.set(text + "。重新扫描可查看最新目录。")
        details = text + "\n\n" + ("保存位置：" + result["path"] if result.get("path") else "")
        if errors:
            details += "\n\n问题详情：\n" + "\n".join(errors)
        if isinstance(skipped, list) and skipped:
            details += "\n\n跳过的项目：\n" + "\n".join(skipped)
        self.show_text(action + "结果", details)
        self.refresh_history()
        self.refresh()

    def backup_desktop(self):
        if self.busy:
            return
        destination = filedialog.asksaveasfilename(parent=self.root, title="保存 ZIP 备份", initialfile="桌面备份_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip", defaultextension=".zip", filetypes=[("ZIP 备份", "*.zip")])
        if destination:
            root, include = self.desktop_path, self.config["include_folders_in_backup"]
            self.start_job("正在备份…", lambda cancel, progress: core.backup(root, destination, include, cancel, progress), lambda result: self.operation_complete("备份", result))

    # ----------------------------------------------------------------------------- history
    def refresh_history(self):
        if self.busy:
            return
        selected = self.history_tree.selection()
        previous = self.records.get(selected[0]) if selected else None
        self.history_tree.delete(*self.history_tree.get_children())
        self.records, invalid = {}, 0
        translations = {"completed": "整理完成", "running": "中断 / 待检查", "partial": "部分整理", "cancelled": "整理已取消", "restored": "已恢复", "restore_partial": "部分恢复", "restore_cancelled": "恢复已取消"}
        for path in sorted(self.record_dir.glob("*.json"), reverse=True):
            try:
                record = core.read_json(path)
                if not isinstance(record, dict) or not isinstance(record.get("files"), list):
                    raise ValueError("无效记录")
                iid = str(len(self.records))
                self.records[iid] = path
                self.history_tree.insert("", "end", iid=iid, values=(record.get("datetime", path.stem), len(record["files"]), translations.get(record.get("status"), "旧版记录"), record.get("root", "—")))
                if path == previous:
                    self.history_tree.selection_set(iid)
            except (OSError, ValueError, TypeError):
                invalid += 1
        self.history_hint.set("共 {} 条记录{}。双击查看记录详情。".format(len(self.records), "，{} 条损坏记录已保留但未加载".format(invalid) if invalid else "") if self.records or invalid else "暂无整理记录，第一次整理后会自动出现在这里。")

    def selected_record(self):
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("选择记录", "请先选中一条整理记录。", parent=self.root)
            return None
        return self.records[selected[0]]

    def record_details(self):
        path = self.selected_record()
        if path:
            try:
                self.show_text("整理记录", json.dumps(core.read_json(path), ensure_ascii=False, indent=2))
            except (OSError, ValueError) as exc:
                messagebox.showerror("读取失败", str(exc), parent=self.root)

    def restore_selected(self):
        if not self.busy:
            path = self.selected_record()
            if path:
                self.restore_from(path)

    def import_record(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="选择旧版或导出的整理记录", filetypes=[("JSON 记录", "*.json")])
        if path:
            self.restore_from(path)

    def restore_from(self, path):
        try:
            record = core.load_record(path, self.desktop_path)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("无法恢复记录", str(exc), parent=self.root)
            return
        if messagebox.askyesno("确认恢复", "将按记录恢复最多 {} 项。\n\n目录：{}\n\n已有同名文件会保留，恢复文件将自动改名。".format(len(record["files"]), self.desktop_path), parent=self.root):
            root = self.desktop_path
            self.start_job("正在恢复…", lambda cancel, progress: core.restore(root, path, self.record_dir, cancel, progress), lambda result: self.operation_complete("恢复", result))

    def export_record(self):
        if self.busy:
            return
        source = self.selected_record()
        if source:
            destination = filedialog.asksaveasfilename(parent=self.root, initialfile=source.name, defaultextension=".json", filetypes=[("JSON 记录", "*.json")])
            if destination:
                try:
                    core.atomic_json(destination, core.read_json(source))
                    self.status_var.set("整理记录已导出")
                except (OSError, ValueError) as exc:
                    messagebox.showerror("导出失败", str(exc), parent=self.root)

    # ----------------------------------------------------------------------------- settings & rules
    def commit_config(self, config):
        config = core.validate_config(config)
        core.atomic_json(self.storage / "config.json", config)
        self.config = config
        self.preview_valid = False
        self.checked.clear()
        self.render_rows()
        self.refresh_category_list()
        self.result_var.set("规则已更新，请重新扫描以生成最新预览。")
        self.status_var.set("设置已保存 · 重新扫描后生效")

    def sync_settings_fields(self):
        self.ext_var.set(", ".join(self.config["excluded_extensions"]))
        self.size_var.set(self.format_limit(self.config["max_file_size_mb"]))
        self.folder_var.set(self.config["include_folders_in_organize"])
        self.backup_folder_var.set(self.config["include_folders_in_backup"])

    def save_settings(self):
        try:
            config = copy.deepcopy(self.config)
            try:
                limit = float(self.size_var.get().strip() or "0")
            except ValueError:
                raise ValueError("大小上限必须是数字，例如 100；0 表示不限大小")
            config.update(excluded_extensions=core.extensions(self.ext_var.get()), max_file_size_mb=limit, include_folders_in_organize=self.folder_var.get(), include_folders_in_backup=self.backup_folder_var.get())
            self.commit_config(config)
            self.sync_settings_fields()
        except (OSError, ValueError) as exc:
            messagebox.showerror("设置未保存", str(exc), parent=self.root)

    def refresh_category_list(self):
        if not hasattr(self, "category_tree"):
            return
        self.category_tree.delete(*self.category_tree.get_children())
        for name, info in self.config["categories"].items():
            ext = info["extensions"]
            image = self.assets.get("cat_" + self.icon_key(name))
            options = {"image": image} if image is not None else {}
            self.category_tree.insert("", "end", iid=name, text=(" " if image is not None else "") + display_name(name), values=("未匹配的其他文件" if not ext else "普通文件夹（整体移动）" if ext == ["__FOLDER__"] else ", ".join(ext),), **options)

    def selected_category(self):
        selected = self.category_tree.selection()
        return selected[0] if selected else None

    def add_category(self):
        if not self.busy:
            self.category_dialog()

    def edit_category(self):
        if self.busy:
            return
        name = self.selected_category()
        if name:
            self.category_dialog(name)
        else:
            messagebox.showinfo("选择分类", "请先选择要编辑的分类。", parent=self.root)

    def delete_category(self):
        if self.busy:
            return
        name = self.selected_category()
        if not name:
            messagebox.showinfo("选择分类", "请先选择要删除的分类。", parent=self.root)
            return
        info = self.config["categories"][name]
        if not info["extensions"] or "__FOLDER__" in info["extensions"]:
            messagebox.showinfo("保留必要分类", "其他文件和文件夹分类需要保留，可以编辑名称和图标。", parent=self.root)
            return
        if messagebox.askyesno("删除分类规则", "删除“{}”规则？\n已有文件和文件夹会保留。".format(display_name(name)), parent=self.root):
            config = copy.deepcopy(self.config)
            del config["categories"][name]
            try:
                self.commit_config(config)
            except (OSError, ValueError) as exc:
                messagebox.showerror("删除失败", str(exc), parent=self.root)

    def reset_categories(self):
        if self.busy:
            return
        if messagebox.askyesno("恢复默认规则", "将分类规则恢复为默认值？\n保留扩展名、大小上限等其他设置不变，已有文件保持原位。", parent=self.root):
            config = copy.deepcopy(self.config)
            config["categories"] = copy.deepcopy(core.DEFAULT_CONFIG["categories"])
            try:
                self.commit_config(config)
            except (OSError, ValueError) as exc:
                messagebox.showerror("恢复失败", str(exc), parent=self.root)

    def category_dialog(self, existing=None):
        px = self.px
        dialog = self.dialog("编辑分类" if existing else "添加分类", 560, 440, bg=WHITE)
        body = tk.Frame(dialog, bg=WHITE, padx=px(24), pady=px(18))
        body.pack(fill="both", expand=True)
        info = self.config["categories"].get(existing, {"extensions": [], "icon": "📁"})
        self.label(body, "分类名称（也是目标文件夹名称）", bold=True).pack(anchor="w")
        name_var = tk.StringVar(value=existing or "")
        name_entry = ttk.Entry(body, textvariable=name_var)
        name_entry.pack(fill="x", pady=(px(6), px(12)))
        self.label(body, "图标", color=MUTED).pack(anchor="w")
        picker = tk.Frame(body, bg=WHITE)
        picker.pack(anchor="w", pady=(px(6), px(12)))
        icon_var = tk.StringVar(value=self.icon_key(existing) if existing else "other")
        swatches = {}
        def choose(key):
            icon_var.set(key)
            for other, swatch in swatches.items():
                swatch.configure(bg="#E9EEFF" if other == key else WHITE, highlightbackground=BLUE if other == key else WHITE)
        for position, (key, emoji, caption) in enumerate(CATEGORY_ICONS):
            image = self.assets.large("cat_" + key)
            swatch = tk.Button(picker, image=image, text=caption if image is None else "", bg=WHITE, activebackground="#E9EEFF", relief="flat", bd=0, highlightthickness=2, highlightbackground=WHITE, cursor="hand2", padx=px(5), pady=px(4), font=(FONT, 9), command=lambda k=key: choose(k))
            swatch.grid(row=position // 7, column=position % 7, padx=px(3), pady=px(3))
            swatches[key] = swatch
        choose(icon_var.get())
        self.label(body, "扩展名 · 支持 txt、.pdf、.tar.gz，逗号分隔", color=MUTED).pack(anchor="w")
        ext_var = tk.StringVar(value=", ".join(info["extensions"]))
        ext_entry = ttk.Entry(body, textvariable=ext_var)
        ext_entry.pack(fill="x", pady=(px(6), px(8)))
        special = existing and (not info["extensions"] or "__FOLDER__" in info["extensions"])
        if special:
            ext_var.set("此分类的规则固定，只能修改名称和图标。")
            ext_entry.state(["disabled"])
        self.label(body, "扩展名不能属于多个分类；名称不能包含 /、\\ 等路径符号。", color=MUTED, size=9, wraplength=480, justify="left").pack(anchor="w")
        error_var = tk.StringVar()
        self.label(body, textvariable=error_var, color="#BD354B", wraplength=480, justify="left").pack(anchor="w", pady=px(4))
        def save():
            try:
                name = name_var.get().strip()
                core.valid_name(name)
                if name != existing and name.casefold() in {n.casefold() for n in self.config["categories"]}:
                    raise ValueError("分类名称已存在")
                ext = info["extensions"] if special else core.extensions(ext_var.get())
                if not special and not ext:
                    raise ValueError("请输入至少一个扩展名")
                config = copy.deepcopy(self.config)
                if existing:
                    del config["categories"][existing]
                config["categories"][name] = dict(info, extensions=ext, icon=ICON_TO_EMOJI[icon_var.get()])
                self.commit_config(config)
                dialog.destroy()
            except (OSError, ValueError) as exc:
                error_var.set(str(exc))
        bar = tk.Frame(body, bg=WHITE)
        bar.pack(side="bottom", fill="x", pady=(px(6), 0))
        self.button(bar, "保存分类", save, primary=True, guarded=False).pack(side="right")
        self.button(bar, "取消", dialog.destroy, guarded=False).pack(side="right", padx=px(8))
        dialog.bind("<Escape>", lambda event: dialog.destroy())
        dialog.bind("<Return>", lambda event: save())
        name_entry.focus_set()

    def import_config(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="导入配置", filetypes=[("JSON 配置", "*.json")])
        if not path:
            return
        try:
            config = core.validate_config(core.read_json(path))
            if messagebox.askyesno("导入配置", "将替换当前规则与设置，已有文件保持原位。\n是否继续？", parent=self.root):
                self.commit_config(config)
                self.sync_settings_fields()
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)

    def export_config(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="导出已保存配置", initialfile="桌面整理配置.json", defaultextension=".json", filetypes=[("JSON 配置", "*.json")])
        if path:
            try:
                core.atomic_json(path, {"app": "桌面整理工具", "version": core.VERSION, "config": self.config})
                self.status_var.set("已保存的配置已导出")
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    # ----------------------------------------------------------------------------- dialogs
    def show_file_details(self, event=None):
        selected = self.tree.selection()
        if selected:
            row = self.rows[int(selected[0])]
            self.show_text("文件详情", "名称：{}\n\n原路径：{}\n\n{}".format(row["name"], row["source"], "跳过原因：" + row["reason"] if row["reason"] else "目标路径：" + row["target"]))

    def show_text(self, title, content):
        px = self.px
        dialog = self.dialog(title, 640, 400, bg=BG)
        bar = tk.Frame(dialog, bg=BG, padx=px(14), pady=px(12))
        bar.pack(side="bottom", fill="x")
        self.button(bar, "关闭", dialog.destroy, guarded=False).pack(side="right")
        def copy_text():
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
        self.button(bar, "复制全部", copy_text, guarded=False).pack(side="left")
        frame = tk.Frame(dialog, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=px(14), pady=(px(14), 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word", font=(FONT, 10), relief="flat", padx=px(14), pady=px(12), bg=WHITE, fg=INK, highlightthickness=0)
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=lambda first, last: self.autohide(scroll, first, last))
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        text.insert("1.0", content)
        text.configure(state="disabled")
        text.focus_set()
        dialog.bind("<Escape>", lambda event: dialog.destroy())

    def dialog(self, title, width, height, bg):
        """Centered, transient, modal Toplevel sharing the application icon."""
        width, height = self.px(width), self.px(height)
        dialog = tk.Toplevel(self.root, bg=bg)
        dialog.withdraw()
        dialog.title(title)
        dialog.minsize(self.px(420), self.px(260))
        dialog.transient(self.root)
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        dialog.geometry("{}x{}+{}+{}".format(width, height, max(0, x), max(0, y)))
        try:
            dialog.iconbitmap(str(core.resource_path("app_icon.ico")))
        except tk.TclError:
            pass
        dialog.deiconify()
        try:
            dialog.wait_visibility()
            dialog.grab_set()
        except tk.TclError:
            pass
        dialog.focus_set()
        return dialog

    def show_license(self):
        try:
            self.show_text("MIT License", core.resource_path("LICENSE").read_text(encoding="utf-8"))
        except OSError as exc:
            messagebox.showerror("无法读取许可", str(exc), parent=self.root)

    def open_path(self, path):
        try:
            os.startfile(str(Path(path).resolve()))
        except OSError as exc:
            messagebox.showerror("无法打开目录", str(exc), parent=self.root)

    def open_storage(self):
        try:
            self.storage.mkdir(parents=True, exist_ok=True)
            self.open_path(self.storage)
        except OSError as exc:
            messagebox.showerror("无法打开数据目录", str(exc), parent=self.root)

    def report_exception(self, kind, value, trace):
        """Surface UI-thread errors instead of swallowing them in a windowed build."""
        details = "".join(traceback.format_exception(kind, value, trace))
        try:
            self.show_text("程序遇到问题", "界面操作出现未处理的错误，文件不会因此丢失。\n\n" + details)
        except tk.TclError:
            pass

    def close(self):
        if self.busy:
            if messagebox.askyesno("任务仍在运行", "先取消当前任务？\n任务结束后可安全关闭窗口。", parent=self.root):
                self.cancel()
            return
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="桌面整理工具")
    parser.add_argument("--directory", type=Path, help="指定启动时预览的目录")
    parser.add_argument("--data-dir", type=Path, help="指定独立配置和记录目录（用于便携或测试）")
    args = parser.parse_args()
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    try:
        DesktopCleaner(directory=args.directory, storage=args.data_dir).run()
    except Exception:
        # A windowed executable has no console; make startup failures visible.
        details = traceback.format_exc()
        try:
            hidden = tk.Tk()
            hidden.withdraw()
            messagebox.showerror("桌面整理工具无法启动", details, parent=hidden)
            hidden.destroy()
        except tk.TclError:
            sys.stderr.write(details)
        sys.exit(1)


if __name__ == "__main__":
    main()
