"""Inspect a public space list without library changes; Chinese follows.

查看公开空间列表；默认仅获取列表元数据，--import 在内存中解析完整专辑。
Optional --output creates a NEW review JSON / --output 仅新建独立审阅 JSON。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bili_player.api import BilibiliError
from bili_player.lists import BilibiliListClient, parse_list_url


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public space list URL / 公开空间合集或系列链接")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--metadata-only", action="store_true", help="Default: fetch all listing pages only / 默认仅获取列表分页")
    mode.add_argument("--import", dest="import_album", action="store_true", help="Resolve every video in memory / 在内存中解析所有视频")
    parser.add_argument("--first-part-only", action="store_true", help="With --import, use P1 only / 配合 --import 仅导入每个视频第一 P")
    parser.add_argument("--output", type=Path, help="Create a new separate review JSON; never overwrite / 新建独立审阅 JSON，不覆盖文件")
    args = parser.parse_args()
    if args.first_part_only and not args.import_album:
        parser.error("--first-part-only requires --import / 需要同时指定 --import")
    try:
        source = parse_list_url(args.url)
        client = BilibiliListClient()
        try:
            payload = {"source": asdict(source)}
            if args.import_album:
                def progress(done: int, total: int, bvid: str) -> None:
                    print(f"Videos / 视频: {done}/{total} {bvid}", file=sys.stderr)
                result = client.import_album(source, expand_parts=not args.first_part_only, progress=progress)
                payload["import"] = asdict(result)
                print(f"Snapshot / 专辑快照: {result.album.title}; tracks / 曲目={len(result.album.tracks)}; skipped / 跳过={len(result.skipped)}", file=sys.stderr)
            else:
                payload["listing"] = asdict(client.get_listing(source))
        finally:
            client.session.close()
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Exclusive creation protects an existing library even if its path is supplied.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(text)
            print(f"Review JSON / 审阅文件: {args.output}")
        else:
            print(text, end="")
    except (BilibiliError, ValueError, OSError) as exc:
        print(f"Failed / 失败: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled; no library changes / 已取消，音乐库未作更改", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
