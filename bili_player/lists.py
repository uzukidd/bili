"""Public space-list snapshots; see Chinese notes below.

公开空间合集/系列导入为独立、可编辑专辑快照；不写入音乐库，不保存来源字段。
Only the caller decides whether a completed snapshot is saved / 由调用者确认后保存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .api import BilibiliClient, BilibiliError
from .models import LibraryEntry, Track


_SEASON_URL = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
_SERIES_META_URL = "https://api.bilibili.com/x/series/series"
_SERIES_URL = "https://api.bilibili.com/x/series/archives"
_PAGE_SIZE = 30
_BVID = re.compile(r"BV[0-9A-Za-z]{10}\Z")
_ERROR_CODE = re.compile(r"哔哩哔哩错误\s+(-?\d+)[：:]")
Progress = Callable[[int, int, str], None]
Cancelled = Callable[[], bool]


@dataclass(frozen=True)
class ListSource:
    mid: int
    list_id: int
    kind: str = "auto"
    url: str = ""

    def __post_init__(self) -> None:
        if type(self.mid) is not int or self.mid <= 0:
            raise ValueError("空间 MID 必须是正整数")
        if type(self.list_id) is not int or self.list_id <= 0:
            raise ValueError("列表 ID 必须是正整数")
        if self.kind not in ("auto", "season", "series"):
            raise ValueError("列表类型必须是 auto、season 或 series")
        if not isinstance(self.url, str):
            raise ValueError("来源链接必须是字符串")


@dataclass
class Listing:
    title: str
    cover: str
    bvids: list[str]
    total: int
    kind: str


@dataclass
class ListImportResult:
    album: LibraryEntry
    skipped: list[str]
    total: int


class ListImportCancelled(BilibiliError):
    """Cancellation never returns a partial album / 取消时不返回半成品专辑。"""


def parse_list_url(text: str) -> ListSource:
    """Parse modern and legacy space links / 解析新版及旧版空间列表链接。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入哔哩哔哩空间合集或系列链接")
    value = text.strip()
    if any(char.isspace() for char in value) or "\\" in value:
        raise ValueError("空间列表链接格式不正确")
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        valid_host = (
            parsed.scheme in ("http", "https")
            and parsed.hostname == "space.bilibili.com"
            and parsed.username is None and parsed.password is None
            and parsed.port is None
        )
    except ValueError as exc:
        raise ValueError("空间列表链接格式不正确") from exc
    if not valid_host:
        raise ValueError("链接必须来自 space.bilibili.com")
    query = parse_qs(parsed.query, keep_blank_values=True)
    types = query.get("type", [])
    if len(types) > 1 or (types and types[0] not in ("season", "series")):
        raise ValueError("链接 type 参数必须是 season 或 series")
    modern = re.fullmatch(r"/([0-9]+)/lists/([0-9]+)/?", parsed.path)
    if modern:
        mid, list_id = map(int, modern.groups())
        kind = types[0] if types else "auto"
    else:
        legacy = re.fullmatch(r"/([0-9]+)/channel/(collectiondetail|seriesdetail)/?", parsed.path)
        ids = query.get("sid", [])
        if not legacy or len(ids) != 1 or not re.fullmatch(r"[0-9]+", ids[0]):
            raise ValueError("链接中没有有效的空间合集或系列 ID")
        mid, list_id = int(legacy[1]), int(ids[0])
        kind = "season" if legacy[2] == "collectiondetail" else "series"
        if types and types[0] != kind:
            raise ValueError("链接路径和 type 参数的列表类型不一致")
    canonical = f"https://space.bilibili.com/{mid}/lists/{list_id}"
    if kind != "auto":
        canonical += f"?type={kind}"
    return ListSource(mid, list_id, kind, canonical)


def _check_cancelled(cancelled: Cancelled | None) -> None:
    if cancelled is not None and cancelled():
        raise ListImportCancelled("已取消列表导入，音乐库未作更改")


def _code(exc: BilibiliError) -> int | None:
    # The existing API exposes its status in this stable error prefix.
    match = _ERROR_CODE.search(str(exc))
    return int(match[1]) if match else None


def _unavailable(exc: BilibiliError) -> bool:
    if exc.__cause__ is not None and not isinstance(exc.__cause__, (ValueError, KeyError, TypeError)):
        return False
    if _code(exc) in (-404, -101, 62002, 62012):
        return True
    return str(exc).startswith((
        "视频没有可播放的分 P", "视频不存在第", "视频分 P 数据不完整", "视频标题或封面格式不正确",
    ))


def _nonnegative(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise BilibiliError(f"列表{label}格式不正确")
    return value


def _cover(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BilibiliError("列表封面格式不正确")
    return "https:" + value if value.startswith("//") else value


class BilibiliListClient(BilibiliClient):
    """Use one public API session; no persistence / 复用公开接口会话，不执行持久化。"""

    def _request(self, url: str, params: dict[str, Any], cancelled: Cancelled | None) -> dict[str, Any]:
        _check_cancelled(cancelled)
        data = self._get(url, params)
        _check_cancelled(cancelled)
        return data

    def get_listing(self, source: ListSource, *, cancelled: Cancelled | None = None) -> Listing:
        """Read all pages in server order / 按服务端顺序读取全部分页并去重。

        Explicit kinds never fall back. Auto falls back only on a missing season,
        never on connection, HTTP, throttling or malformed-response errors.
        显式类型不回退；自动类型仅在合集不存在时尝试系列，不吞掉网络和限流错误。
        """
        if not isinstance(source, ListSource):
            raise TypeError("source 必须是 ListSource")
        source.__post_init__()
        if source.kind != "auto":
            return self._listing(source, source.kind, cancelled)
        try:
            first_page = self._request(_SEASON_URL, self._season_params(source, 1), cancelled)
        except ListImportCancelled:
            raise
        except BilibiliError as exc:
            if _code(exc) != -404:
                raise
            return self._listing(source, "series", cancelled)
        return self._listing(source, "season", cancelled, first_page=first_page)

    @staticmethod
    def _season_params(source: ListSource, page_number: int) -> dict[str, Any]:
        return {
            "mid": source.mid, "season_id": source.list_id,
            "page_num": page_number, "page_size": _PAGE_SIZE, "sort_reverse": "false",
        }

    def _listing(
        self, source: ListSource, kind: str, cancelled: Cancelled | None,
        *, first_page: dict[str, Any] | None = None,
    ) -> Listing:
        metadata: dict[str, Any] | None = None
        if kind == "series":
            data = self._request(_SERIES_META_URL, {"series_id": source.list_id}, cancelled)
            metadata = data.get("meta")
            if not isinstance(metadata, dict):
                raise BilibiliError("系列列表元数据格式不正确")
            self._validate_owner(metadata, source, "series_id")
        bvids: list[str] = []
        seen: set[str] = set()
        page_number, received = 1, 0
        total: int | None = None
        title, cover = "", ""
        while True:
            if kind == "season":
                data = first_page if page_number == 1 and first_page is not None else self._request(
                    _SEASON_URL, self._season_params(source, page_number), cancelled,
                )
                metadata = data.get("meta")
                if not isinstance(metadata, dict):
                    raise BilibiliError("合集列表元数据格式不正确")
                self._validate_owner(metadata, source, "season_id")
            else:
                data = self._request(_SERIES_URL, {
                    "mid": source.mid, "series_id": source.list_id,
                    "only_normal": "true", "sort": "asc", "pn": page_number, "ps": _PAGE_SIZE,
                }, cancelled)
            page = data.get("page")
            if not isinstance(page, dict):
                raise BilibiliError("列表分页信息格式不正确")
            current_total = _nonnegative(page.get("total"), "视频总数")
            if total is None:
                total = current_total
                title = metadata.get("title") or metadata.get("name") or ""
                if not isinstance(title, str) or not title.strip():
                    raise BilibiliError("列表标题为空或格式不正确")
                cover = _cover(metadata.get("cover"))
                if total == 0:
                    raise BilibiliError("列表为空，没有可导入的视频")
            elif total != current_total:
                raise BilibiliError("导入期间列表数量发生变化，请重试")
            archives = data.get("archives")
            if not isinstance(archives, list) or not archives:
                raise BilibiliError("列表分页不完整，未返回全部视频，请重试")
            for archive in archives:
                bvid = archive.get("bvid") if isinstance(archive, dict) else None
                if not isinstance(bvid, str) or not _BVID.fullmatch(bvid):
                    raise BilibiliError("列表视频 BV 号缺失或格式不正确")
                if bvid not in seen:
                    seen.add(bvid)
                    bvids.append(bvid)
            received += len(archives)
            if received >= total:
                break
            page_number += 1
        _check_cancelled(cancelled)
        return Listing(title, cover, bvids, total, kind)

    @staticmethod
    def _validate_owner(metadata: dict[str, Any], source: ListSource, id_key: str) -> None:
        for key, expected in (("mid", source.mid), (id_key, source.list_id)):
            if key in metadata and metadata[key] != expected:
                raise BilibiliError("列表来源与空间用户或列表 ID 不一致")

    def import_album(
        self, source: ListSource, expand_parts: bool = True,
        progress: Progress | None = None, cancelled: Cancelled | None = None,
    ) -> ListImportResult:
        """Build a complete independent album; never save partial work.

        Progress is (completed BVs, unique BV count, current BV); an initial
        (0, count, '') precedes video requests. Unavailable BVs add readable notes.
        进度为（已完成 BV 数、去重后 BV 总数、当前 BV）；失效视频有逐项说明。
        网络/限流/格式故障抛出异常；调用者仅在完整返回并确认后保存。
        """
        if type(expand_parts) is not bool:
            raise ValueError("expand_parts 必须是布尔值")
        listing = self.get_listing(source, cancelled=cancelled)
        count = len(listing.bvids)
        tracks: list[Track] = []
        skipped: list[str] = []
        cover = listing.cover
        _check_cancelled(cancelled)
        if progress:
            progress(0, count, "")
        for index, bvid in enumerate(listing.bvids, 1):
            _check_cancelled(cancelled)
            entry = LibraryEntry(bvid, bvid, "album" if expand_parts else "single", page=1)
            try:
                video = self.get_video(entry)
                _check_cancelled(cancelled)
                selected = video.tracks if expand_parts else [track for track in video.tracks if track.page == 1]
                if not selected:
                    raise BilibiliError("视频没有可播放的分 P")
                # Reconstruct atomically per video, with independent IDs and blank artists.
                copied = [Track(
                    title=track.title if expand_parts else video.title,
                    bvid=track.bvid, cid=track.cid, page=track.page, duration=track.duration,
                ) for track in selected]
            except ListImportCancelled:
                raise
            except BilibiliError as exc:
                _check_cancelled(cancelled)
                if not _unavailable(exc):
                    raise BilibiliError(f"导入 {bvid} 失败，未保存专辑：{exc}") from exc
                skipped.append(f"{bvid}：{exc}")
            except (ValueError, TypeError, AttributeError) as exc:
                skipped.append(f"{bvid}：视频分 P 数据不完整：{exc}")
            else:
                tracks.extend(copied)
                if not cover:
                    cover = _cover(video.cover)
            if progress:
                progress(index, count, bvid)
            _check_cancelled(cancelled)
        if not tracks:
            notes = "；".join(skipped)
            raise BilibiliError("列表中没有可播放的曲目" + (f"：{notes}" if notes else ""))
        album = LibraryEntry(listing.title, kind="album", tracks=tracks, cover=cover, resolved=True)
        return ListImportResult(album, skipped, listing.total)
