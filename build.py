"""Build the single-file Windows executable with the current interpreter's PyInstaller.

    python build.py            # writes dist/桌面整理工具.exe and dist/SHA256.txt
    python build.py --no-zip   # skip the release archive

Python 3.8 keeps the executable runnable on Windows 7+; newer Pythons work too but raise
the minimum Windows version. The application itself has no third-party dependencies.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cleaner_core  # noqa: E402  (version string only)

PROJECT = Path(__file__).resolve().parent
NAME = "桌面整理工具"
DATA_FILES = ("app_icon.ico", "LICENSE")
ASSET_DIR = "assets"


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("正在安装 PyInstaller…")
        requirement = "pyinstaller==5.13.2" if sys.version_info < (3, 9) else "pyinstaller>=6,<7"
        subprocess.check_call([sys.executable, "-m", "pip", "install", requirement])


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def build_exe(make_zip=True):
    print("开始打包 {} {} (Python {}.{}.{})…".format(NAME, cleaner_core.VERSION, *sys.version_info[:3]))
    for name in DATA_FILES + ("version_info.txt", "desktop_cleaner.py", "cleaner_core.py"):
        if not (PROJECT / name).exists():
            raise SystemExit("缺少构建所需文件：" + name)
    if not list((PROJECT / ASSET_DIR).glob("*/cat_doc.png")):
        raise SystemExit("缺少界面图片资源，请先运行 python tools/make_assets.py")
    ensure_pyinstaller()
    dist, work = PROJECT / "dist", PROJECT / "build"
    exe = dist / (NAME + ".exe")
    if exe.exists():
        exe.unlink()  # a running instance would make the copy fail late; surface it now
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name=" + NAME,
        "--clean",
        "--noconfirm",
        "--icon=" + str(PROJECT / "app_icon.ico"),
        "--version-file=" + str(PROJECT / "version_info.txt"),
        "--distpath=" + str(dist),
        "--workpath=" + str(work),
        "--specpath=" + str(work),
    ]
    for name in DATA_FILES:
        cmd.append("--add-data=" + str(PROJECT / name) + os.pathsep + ".")
    cmd.append("--add-data=" + str(PROJECT / ASSET_DIR) + os.pathsep + ASSET_DIR)
    cmd.append(str(PROJECT / "desktop_cleaner.py"))
    try:
        subprocess.run(cmd, check=True, cwd=str(PROJECT))
    except subprocess.CalledProcessError as exc:
        print("打包失败: {}".format(exc))
        raise SystemExit(exc.returncode)
    except FileNotFoundError:
        print("PyInstaller未找到，请确保已正确安装")
        raise SystemExit(1)
    if not exe.exists():
        raise SystemExit("打包结束但未找到产物：" + str(exe))
    digest = sha256(exe)
    lines = ["{}  {}".format(digest, exe.name)]
    if make_zip:
        archive = dist / "{}-{}-Windows-x64.zip".format(NAME, cleaner_core.VERSION)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(exe, exe.name)
            bundle.write(PROJECT / "README.md", "README.md")
            bundle.write(PROJECT / "LICENSE", "LICENSE")
        lines.append("{}  {}".format(sha256(archive), archive.name))
        print("发布压缩包: {}".format(archive))
    (dist / "SHA256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("打包成功！")
    print("可执行文件: {} ({:.1f} MB)".format(exe, exe.stat().st_size / 1048576))
    print("SHA256: {}".format(digest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打包桌面整理工具为单文件 EXE")
    parser.add_argument("--no-zip", action="store_true", help="不生成发布压缩包")
    build_exe(make_zip=not parser.parse_args().no_zip)
