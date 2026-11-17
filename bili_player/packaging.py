"""Build a standalone player and create a portable release archive."""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME = "BiliMusic"


def build() -> Path:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--windowed", "--onedir", "--noupx", "--name", NAME,
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "main.py"),
    ], cwd=ROOT, check=True)
    artifact = ROOT / "dist" / (NAME + ".app" if sys.platform == "darwin" else NAME)
    if not artifact.is_dir():
        raise RuntimeError(f"Build output is missing: {artifact}")
    print(f"Build ready / 构建完成: {artifact}", flush=True)
    return artifact


def deploy(artifact: Path, output: Path) -> Path:
    output = output.expanduser().resolve()
    # Keep the archive outside the application tree so it never includes itself.
    if output == artifact or artifact in output.parents:
        raise ValueError("Release output must be outside the application directory / 分发目录不能位于程序目录内")
    output.mkdir(parents=True, exist_ok=True)
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    archive_name = f"{NAME}-{system}-{platform.machine().lower()}"
    archive = Path(shutil.make_archive(
        str(output / archive_name), "zip", root_dir=artifact.parent, base_dir=artifact.name,
    ))
    print(f"Release ready / 分发包已生成: {archive}", flush=True)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and package Bili Music / 构建与打包 Bili Music")
    parser.add_argument("command", choices=("build", "deploy"))
    parser.add_argument("--output", type=Path, default=ROOT / "dist",
                        help="Release archive directory (deploy only) / 分发包目录（仅 deploy）")
    args = parser.parse_args()
    try:
        artifact = build()
        if args.command == "deploy":
            deploy(artifact, args.output)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Build failed / 构建失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
