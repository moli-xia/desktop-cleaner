"""File operations independent of Tk. All mutations are journaled and never overwrite."""
import copy
import json
import math
import os
import re
import stat
import sys
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

VERSION = "2.2.0"
OTHER = "📁 其他"
FOLDERS = "📂 桌面文件夹"
DEFAULT_CONFIG = {
    "excluded_extensions": [".lnk", ".url"],
    "max_file_size_mb": 100,
    "include_folders_in_organize": False,
    "include_folders_in_backup": True,
    "categories": {
        "📄 文档": {"extensions": [".txt", ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx", ".md", ".csv", ".rtf", ".odt"], "icon": "📄"},
        "🖼️ 图片": {"extensions": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".ico", ".webp", ".heic"], "icon": "🖼️"},
        "🎬 视频": {"extensions": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"], "icon": "🎬"},
        "🎵 音频": {"extensions": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"], "icon": "🎵"},
        "📦 压缩包": {"extensions": [".zip", ".rar", ".7z", ".tar", ".gz", ".tar.gz"], "icon": "📦"},
        "💻 程序": {"extensions": [".exe", ".msi", ".deb", ".dmg"], "icon": "💻"},
        FOLDERS: {"extensions": ["__FOLDER__"], "icon": "📂"},
        OTHER: {"extensions": [], "icon": "📁"},
    },
}


def resource_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def data_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "DesktopCleaner"


def get_desktop_path():
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
        except OSError:
            pass
    return Path.home() / "Desktop"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def extensions(value):
    if isinstance(value, str):
        value = re.split(r"[,，;；\s]+", value.strip())
    if not isinstance(value, list):
        raise ValueError("扩展名必须是列表或以逗号分隔的文本")
    result = []
    for ext in value:
        if not isinstance(ext, str):
            raise ValueError("扩展名必须是文本")
        ext = ext.strip().lower()
        if not ext:
            continue
        if ext == "__folder__":
            ext = "__FOLDER__"
        else:
            ext = "." + ext.lstrip(".")
            if not re.fullmatch(r"\.[\w+-]+(?:\.[\w+-]+)*", ext):
                raise ValueError("无效的扩展名：" + ext)
        if ext not in result:
            result.append(ext)
    return result


def valid_name(name):
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError("分类名称不能为空或带有首尾空格")
    if len(name) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith("."):
        raise ValueError("分类名称不能包含路径符号、控制字符或结尾句点")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *["COM" + str(i) for i in range(10)], *["LPT" + str(i) for i in range(10)]}:
        raise ValueError("不能使用 Windows 保留名称")
    if name in (".", ".."):
        raise ValueError("无效分类名称")


def validate_config(raw):
    if not isinstance(raw, dict):
        raise ValueError("配置必须是 JSON 对象")
    raw = raw.get("config", raw)
    if not isinstance(raw, dict):
        raise ValueError("config 必须是 JSON 对象")
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(raw)
    result["excluded_extensions"] = extensions(result["excluded_extensions"])
    limit = result["max_file_size_mb"]
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or limit < 0:
        raise ValueError("大小上限必须为非负数；0 表示不限大小")
    for key in ("include_folders_in_organize", "include_folders_in_backup"):
        if not isinstance(result[key], bool):
            raise ValueError("文件夹选项必须为 true 或 false")
    categories = result["categories"]
    if not isinstance(categories, dict) or not categories:
        raise ValueError("至少需要一个分类")
    normalized, seen, names = {}, {}, set()
    for name, info in categories.items():
        valid_name(name)
        if name.casefold() in names:
            raise ValueError("分类名称重复：" + name)
        names.add(name.casefold())
        if isinstance(info, list):
            info = {"extensions": info, "icon": "📁"}
        if not isinstance(info, dict) or "extensions" not in info:
            raise ValueError("分类缺少扩展名：" + name)
        info = copy.deepcopy(info)
        info["extensions"] = extensions(info["extensions"])
        for ext in info["extensions"]:
            if ext in seen:
                raise ValueError("扩展名 {} 同时属于 {} 和 {}".format(ext, seen[ext], name))
            seen[ext] = name
        normalized[name] = info
    if not any(not info["extensions"] for info in normalized.values()):
        normalized.setdefault(OTHER, {"extensions": [], "icon": "📁"})
    if "__FOLDER__" not in seen:
        normalized.setdefault(FOLDERS, {"extensions": ["__FOLDER__"], "icon": "📂"})
    fallbacks = [n for n, i in normalized.items() if not i["extensions"]]
    if len(fallbacks) != 1:
        raise ValueError("必须且只能有一个空扩展名分类，用于接收其他文件")
    if not any("__FOLDER__" in i["extensions"] for i in normalized.values()):
        raise ValueError("桌面文件夹分类名称已占用，请设置 __FOLDER__ 规则")
    result["categories"] = normalized
    return result


def load_config(directory):
    """Migrate portable/source config once; never overwrite a malformed user file."""
    path = Path(directory) / "config.json"
    candidates = [path, Path(sys.executable).parent / "config.json"] if getattr(sys, "frozen", False) else [path, Path(__file__).parent / "config.json"]
    for candidate in candidates:
        if candidate.exists():
            try:
                return validate_config(read_json(candidate)), None
            except (ValueError, OSError, TypeError) as exc:
                return copy.deepcopy(DEFAULT_CONFIG), "无法读取 {}，暂用默认设置；原文件已保留。\n{}".format(candidate, exc)
    return copy.deepcopy(DEFAULT_CONFIG), None


def is_link(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_path(root, path):
    """Reject escaping paths and every reparse-point component under the root."""
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("路径不在所选目录内：" + str(path))
    if ".." in relative.parts or not relative.parts:
        raise ValueError("不允许操作根目录或上级路径")
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and is_link(current):
            raise ValueError("跳过链接或重解析点：" + str(current))
    return path


def fingerprint(path):
    info = Path(path).stat()
    return [info.st_size, info.st_mtime_ns, info.st_ino]


def unique_path(path, reserved=None):
    path = Path(path)
    reserved = reserved if reserved is not None else set()
    candidate, number = path, 1
    while os.path.lexists(candidate) or str(candidate).casefold() in reserved:
        candidate = path.with_name("{} ({}){}".format(path.stem, number, path.suffix))
        number += 1
    reserved.add(str(candidate).casefold())
    return candidate


def move_no_replace(source, target):
    if os.path.lexists(target):
        raise FileExistsError("目标已存在，请重新扫描：" + str(target))
    # Windows rename fails if another process creates the target in the meantime.
    os.rename(str(source), str(target))


def scan(root, config, cancel=None, progress=None, protected_paths=()):
    root = Path(root).resolve(strict=True)
    config = validate_config(config)
    cancel = cancel or threading.Event()
    categories = config["categories"]
    folder_category = next(n for n, i in categories.items() if "__FOLDER__" in i["extensions"])
    fallback = next(n for n, i in categories.items() if not i["extensions"])
    rules = sorted([(ext, name) for name, info in categories.items() for ext in info["extensions"] if ext != "__FOLDER__"], key=lambda x: -len(x[0]))
    rows, reserved = [], set()
    protected_paths = [Path(path).resolve() for path in protected_paths]
    with os.scandir(root) as iterator:
        for index, entry in enumerate(iterator):
            if cancel.is_set():
                break
            source = Path(entry.path)
            row = {"source": str(source), "name": entry.name, "size": 0, "category": "—", "target": "", "reason": "", "folder": False}
            try:
                safe_path(root, source)
                info = source.stat()
                row["folder"] = source.is_dir()
                row["size"] = 0 if row["folder"] else info.st_size
                row["fingerprint"] = fingerprint(source)
                lower = entry.name.casefold()
                protected = any(source == path or source in path.parents for path in protected_paths)
                if protected:
                    row["reason"] = "程序数据或整理记录目录"
                elif lower in ("desktop.ini", "thumbs.db") or lower.startswith(".") or bool(getattr(info, "st_file_attributes", 0) & 6):
                    row["reason"] = "系统或隐藏项目"
                elif row["folder"] and lower in {n.casefold() for n in categories}:
                    row["reason"] = "已有分类文件夹"
                elif source.resolve() in (Path(sys.executable).resolve(), Path(__file__).resolve(), resource_path("desktop_cleaner.py").resolve()):
                    row["reason"] = "程序自身"
                elif row["folder"] and not config["include_folders_in_organize"]:
                    row["reason"] = "保留文件夹"
                elif not row["folder"] and any(lower.endswith(ext) for ext in config["excluded_extensions"]):
                    row["reason"] = "排除的扩展名"
                elif not row["folder"] and config["max_file_size_mb"] and row["size"] > config["max_file_size_mb"] * 1024 * 1024:
                    row["reason"] = "超过大小上限"
                if not row["reason"]:
                    category = folder_category if row["folder"] else next((name for ext, name in rules if lower.endswith(ext)), fallback)
                    parent = safe_path(root, root / category)
                    if parent.exists() and not parent.is_dir():
                        raise ValueError("分类目标被同名文件占用")
                    row["category"] = category
                    row["target"] = str(unique_path(parent / entry.name, reserved))
            except (OSError, ValueError) as exc:
                row["reason"] = str(exc)
            rows.append(row)
            if progress and index % 100 == 0:
                progress(index + 1, 0, "正在扫描：" + entry.name)
    return sorted(rows, key=lambda row: (bool(row["reason"]), row["name"].casefold()))


def organize(root, rows, record_dir, cancel=None, progress=None):
    root = Path(root).resolve(strict=True)
    cancel = cancel or threading.Event()
    stamp = datetime.now()
    record_path = Path(record_dir) / (stamp.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8] + ".json")
    record = {"version": 2, "root": str(root), "datetime": stamp.strftime("%Y-%m-%d %H:%M:%S"), "status": "running", "files": [], "created_dirs": []}
    atomic_json(record_path, record)
    done, errors = 0, []
    for index, row in enumerate(rows):
        if cancel.is_set():
            break
        item = None
        try:
            if row.get("reason"):
                continue
            source = safe_path(root, row["source"])
            target = safe_path(root, row["target"])
            if source.parent != root or target.parent.parent != root or source.name == target.parent.name:
                raise ValueError("整理计划路径无效")
            if source.is_dir() and (source == Path(record_dir).resolve() or source in Path(record_dir).resolve().parents):
                raise ValueError("不能移动保存整理记录的目录")
            if fingerprint(source) != row["fingerprint"]:
                raise ValueError("文件在预览后发生变化，请重新扫描")
            if not target.parent.exists():
                record["created_dirs"].append(str(target.parent))
                atomic_json(record_path, record)
                target.parent.mkdir()
            item = {"original": str(source), "new": str(target), "category": target.parent.name, "fingerprint": row["fingerprint"], "state": "pending"}
            record["files"].append(item)
            atomic_json(record_path, record)  # Write intent BEFORE moving.
            move_no_replace(source, target)
            item["state"] = "moved"
            done += 1
        except (OSError, ValueError) as exc:
            errors.append("{}：{}".format(row["name"], exc))
            if item is not None:
                item["state"] = "failed"
                item["error"] = str(exc)
        # A journal persistence failure aborts immediately. Its durable pending intent remains recoverable.
        atomic_json(record_path, record)
        if progress:
            progress(index + 1, len(rows), "已整理 {} 项 · {}".format(done, row["name"]))
    record["status"] = "cancelled" if cancel.is_set() else "partial" if errors else "completed"
    record["total_files"] = done
    record["errors"] = errors
    atomic_json(record_path, record)
    return {"done": done, "errors": errors, "cancelled": cancel.is_set(), "path": str(record_path)}


def load_record(path, root):
    root = Path(root).resolve(strict=True)
    raw = read_json(path)
    record = {"files": raw, "version": 1} if isinstance(raw, list) else raw
    if not isinstance(record, dict) or not isinstance(record.get("files"), list):
        raise ValueError("不是有效的整理记录")
    if record.get("root") and (not isinstance(record["root"], str) or Path(record["root"]).resolve() != root):
        raise ValueError("记录属于其他目录，请先切换到：" + str(record["root"]))
    if not isinstance(record.get("created_dirs", []), list):
        raise ValueError("记录中的分类目录列表无效")
    for folder in record.get("created_dirs", []):
        if not isinstance(folder, str) or safe_path(root, folder).parent != root:
            raise ValueError("记录中的分类目录无效")
    seen = set()
    for item in record["files"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("original", "new")):
            raise ValueError("记录中的文件路径无效")
        if item.get("state", "moved") not in ("pending", "moved", "failed", "restoring", "restored"):
            raise ValueError("记录中的文件状态无效")
        for key in ("fingerprint", "restore_fingerprint"):
            value = item.get(key)
            if value is not None and (not isinstance(value, list) or len(value) != 3 or not all(type(v) is int and v >= 0 for v in value)):
                raise ValueError("记录中的文件身份信息无效")
        if item.get("state") == "restoring":
            value = item.get("restored_to")
            if not isinstance(value, str) or safe_path(root, value).parent != root:
                raise ValueError("记录中的恢复目标无效")
        source = safe_path(root, item["new"])
        target = safe_path(root, item["original"])
        if target.parent != root or source.parent.parent != root or source == target:
            raise ValueError("记录路径必须指向当前目录及其分类文件夹")
        if str(source).casefold() in seen:
            raise ValueError("记录包含重复文件")
        seen.add(str(source).casefold())
    return record


def restore(root, record_path, record_dir, cancel=None, progress=None):
    root = Path(root).resolve(strict=True)
    cancel = cancel or threading.Event()
    record = load_record(record_path, root)
    # External/legacy records remain untouched; keep a persistent working copy keyed by source.
    import hashlib
    if Path(record_path).resolve().parent != Path(record_dir).resolve():
        digest = hashlib.sha256(Path(record_path).read_bytes()).hexdigest()[:24]
        record_path = Path(record_dir) / ("import_" + digest + ".json")
        if record_path.exists():
            record = load_record(record_path, root)
        else:
            record["root"] = str(root)
            record.setdefault("datetime", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            atomic_json(record_path, record)
    done, errors, skipped = 0, [], 0
    for index, item in enumerate(record["files"]):
        if cancel.is_set():
            break
        try:
            state = item.get("state", "moved")
            source = safe_path(root, item["new"])
            if state in ("restored", "failed"):
                skipped += 1
                continue
            if state == "restoring" and not source.exists():
                previous = safe_path(root, item["restored_to"])
                if previous.exists() and fingerprint(previous) == item.get("restore_fingerprint"):
                    item["state"] = "restored"
                    skipped += 1
                    atomic_json(record_path, record)
                    continue
            if not source.exists():
                raise ValueError("整理后的文件不存在，可能已被手动移动")
            if state == "pending" and (Path(item["original"]).exists() or fingerprint(source) != item.get("fingerprint")):
                raise ValueError("中断记录无法确认文件身份，请人工检查")
            if record.get("version") == 2 and item.get("fingerprint") and source.stat().st_ino != item["fingerprint"][2]:
                raise ValueError("目标已被其他文件替换，未恢复")
            target = unique_path(Path(item["original"]))
            safe_path(root, target)
            item["state"] = "restoring"
            item["restored_to"] = str(target)
            item["restore_fingerprint"] = fingerprint(source)
            atomic_json(record_path, record)
            move_no_replace(source, target)
            item["state"] = "restored"
            done += 1
        except (OSError, ValueError) as exc:
            errors.append("{}：{}".format(Path(item["original"]).name, exc))
        atomic_json(record_path, record)
        if progress:
            progress(index + 1, len(record["files"]), "已恢复 {} 项".format(done))
    for folder in record.get("created_dirs", []):
        try:
            folder = safe_path(root, folder)
            if folder.parent == root:
                folder.rmdir()  # Only empty directories; never delete desktop.ini or user contents.
        except (OSError, ValueError):
            pass
    record["status"] = "restore_cancelled" if cancel.is_set() else "restore_partial" if errors else "restored"
    atomic_json(record_path, record)
    return {"done": done, "errors": errors, "skipped": skipped, "cancelled": cancel.is_set(), "path": str(record_path)}


def backup(root, destination, include_folders=True, cancel=None, progress=None):
    root, destination = Path(root).resolve(strict=True), Path(destination).absolute()
    cancel = cancel or threading.Event()
    if destination.exists():
        raise FileExistsError("备份文件已存在，请选择其他名称")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".partial")
    errors, skipped, done = [], [], 0

    def entries(folder):
        with os.scandir(folder) as iterator:
            for entry in iterator:
                if cancel.is_set():
                    return
                path = Path(entry.path)
                try:
                    safe_path(root, path)
                    if path.resolve() in (destination.resolve(), temp.resolve()):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if include_folders:
                            yield path, True
                            yield from entries(path)
                        else:
                            skipped.append(str(path.relative_to(root)))
                    elif entry.is_file(follow_symlinks=False):
                        yield path, False
                except ValueError:
                    skipped.append(str(path.relative_to(root)) + "（链接或重解析点）")
                except OSError as exc:
                    errors.append("{}：{}".format(path, exc))
    try:
        with zipfile.ZipFile(temp, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path, directory in entries(root):
                if cancel.is_set():
                    break
                safe_path(root, path)
                relative = path.relative_to(root).as_posix()
                if directory:
                    archive.writestr(relative + "/", b"")
                    continue
                before = fingerprint(path)
                # Fail the archive on read/write failure rather than publish a truncated file entry.
                zip_info = zipfile.ZipInfo.from_file(path, relative)
                zip_info.compress_type = zipfile.ZIP_DEFLATED
                with path.open("rb") as reader, archive.open(zip_info, "w", force_zip64=True) as writer:
                    while not cancel.is_set():
                        block = reader.read(1024 * 1024)
                        if not block:
                            break
                        writer.write(block)
                        if progress:
                            progress(done, 0, "正在备份：" + relative)
                if not cancel.is_set() and fingerprint(path) != before:
                    raise OSError("备份期间文件发生变化，请重试：" + relative)
                done += 1
        if cancel.is_set():
            return {"done": 0, "errors": errors, "skipped": skipped, "cancelled": True, "path": ""}
        move_no_replace(temp, destination)
        return {"done": done, "errors": errors, "skipped": skipped, "cancelled": False, "path": str(destination)}
    finally:
        if temp.exists():
            temp.unlink()


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "{:.0f} {}".format(size, unit) if unit == "B" else "{:.1f} {}".format(size, unit)
        size /= 1024
