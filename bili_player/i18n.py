"""Application-owned Chinese and English strings."""
from __future__ import annotations

import re

ENGLISH = {
    "播放中断，正在重新连接（{attempt}/3）…": "Playback interrupted. Reconnecting ({attempt}/3)…",
    "音频重连失败，请检查网络后点击播放重试。": "Could not reconnect. Check your network and press Play to retry.",
    "播放队列": "Play queue", "展开 / 收起播放队列": "Show / Hide play queue",
    "折叠": "Collapse", "展开": "Expand",
    "切换到迷你播放器": "Switch to mini player", "返回完整播放器": "Return to full player",
    "显示播放器": "Show player", "迷你播放器": "Mini player", "退出": "Quit",
    "Bili Music · B站音乐播放器": "Bili Music · Bilibili Music Player",
    "让喜欢的声音，陪你久一点": "Keep your favorite sounds close",
    "＋ 导入 BV": "+ Import BV",
    "＋ 新建专辑": "+ New album",
    "我的音乐库": "My library",
    "搜索名称 / BV": "Search title / BV",
    "全部": "All", "专辑": "Albums", "单曲": "Singles",
    "白天模式": "Light", "夜晚模式": "Dark",
    "主题": "Theme", "语言": "Language",
    "{total} 项收藏 · 当前显示 {shown} 项": "{total} items · Showing {shown}",
    "给喜欢的音乐，留一个位置": "Make room for your favorite music",
    "导入 BV，或新建一个空专辑开始整理。": "Import a BV or create an empty album to get started.",
    "没有匹配的音乐，换个关键词试试。": "No matching music. Try another search.",
    "专辑 · {count} 首": "Album · {count} tracks",
    "专辑 · 等待导入": "Album · Not imported yet",
    "单曲 · P{page}": "Single · P{page}",
    "自建专辑": "Custom album", "打开专辑": "Open album",
    "点击播放": "Click to play", "播放": "Play", "＋ 队列": "+ Queue",
    "编辑 / 删除 / 在 B站打开": "Edit / Delete / Open on Bilibili",
    "编辑": "Edit", "编辑专辑": "Edit album", "加入专辑…": "Add to album…",
    "在 B站打开": "Open on Bilibili", "从音乐库删除": "Delete from library",
    "播放队列 · {count}": "Queue · {count}", "清空": "Clear",
    "双击播放 · 拖动排序 · 右键移除": "Double-click to play · Drag to reorder · Right-click to remove",
    "还没有歌曲，去音乐库选一首吧。": "No tracks yet. Pick something from your library.",
    "准备就绪 · 添加 BV 号，开始收集你的音乐": "Ready · Import a BV to start your music library",
    "还没有正在播放的音乐": "Nothing playing",
    "从音乐库点击播放": "Play something from your library",
    "上一首": "Previous", "下一首": "Next", "播放 / 暂停（空格）": "Play / Pause (Space)",
    "顺序播放": "Sequential", "列表循环": "Loop queue", "单曲循环": "Repeat track",
    "音量": "Volume", "正在播放": "Playing", "已暂停": "Paused",
    "正在读取 {title}…": "Loading {title}…",
    "已加入队列 · {count} 首": "Added {count} tracks to queue",
    "正在获取音频地址…": "Resolving audio stream…", "正在缓冲音频…": "Buffering audio…",
    "已播放到队列末尾": "Reached the end of the queue", "队列已清空": "Queue cleared",
    "移出队列": "Remove from queue", "这个专辑还没有歌曲，先添加几首吧。": "This album is empty. Add a few tracks first.",
    "正在导入 {title}…": "Importing {title}…", "已导入 {title}": "Imported {title}",
    "专辑已保存": "Album saved", "已创建空专辑": "Empty album created",
    "新建空专辑": "New empty album", "专辑名称": "Album name",
    "请选择专辑": "Choose an album", "加入专辑": "Add to album",
    "还没有专辑，请先新建一个。": "Create an album first.",
    "已将 {title} 加入 {album}": "Added {title} to {album}",
    "封面暂时无法加载": "Cover could not be loaded",
    "播放失败：{message}。可重新双击歌曲获取地址。": "Playback failed: {message}. Double-click the track to refresh its stream.",
    "保存失败：{message}": "Save failed: {message}",
    "无法保存音乐库：{message}": "Could not save library: {message}",
    "无法读取音乐库，原文件已保留：\n{message}": "Could not read library; the original file was preserved:\n{message}",
    "操作失败": "Operation failed", "网络或数据错误：{message}": "Network or data error: {message}",
    "添加音乐": "Import music", "编辑音乐": "Edit music",
    "例如：我的睡前歌单（可留空）": "For example: Bedtime mix (optional)",
    "BV 号或 bilibili.com/video/BV… 链接": "BV ID or bilibili.com/video/BV… URL",
    "单曲 · 播放指定分 P": "Single · Choose one part",
    "专辑 · 导入全部分 P": "Album · Import all parts",
    "专辑 · 播放全部分 P": "Album · Import all parts",
    "名称": "Title", "BV / 链接": "BV / URL", "类型": "Type", "分 P": "Part (P)",
    "保存": "Save", "取消": "Cancel", "确定": "OK", "关闭": "Close",
    "专辑会将同一 BV 的所有分 P 作为歌曲加入队列。": "An album imports all parts from one BV video.",
    "导入后可以自由编辑专辑曲目，默认使用视频封面。": "Edit album tracks after import. The video cover is used by default.",
    "请输入 BV 号或哔哩哔哩视频链接": "Enter a BV ID or a Bilibili video URL",
    "链接必须是哔哩哔哩视频链接，或直接输入 BV 号": "Enter a Bilibili video URL or a BV ID",
    "视频链接中没有有效的 BV 号": "The video URL does not contain a valid BV ID",
    "请输入专辑名称": "Enter an album name",
    "封面": "Cover", "选择图片…": "Choose image…", "图片文件": "Image files",
    "封面图片或网址（可留空）": "Cover image path or URL (optional)",
    "歌曲": "Tracks", "{count} 首歌曲": "{count} tracks", "添加 BV": "Add BV",
    "添加已有单曲": "Add library singles", "移除": "Remove", "上移": "Move up", "下移": "Move down",
    "编辑曲目": "Edit track", "曲目名称": "Track title", "歌手 / 作者": "Artist / Creator",
    "拖动调整顺序，双击编辑曲目。": "Drag to reorder. Double-click to edit a track.",
    "添加歌曲": "Add tracks", "选择单曲": "Choose singles",
    "没有可添加的单曲": "There are no library singles to add",
    "没有可添加的单曲，请先在音乐库导入单曲。": "Import singles into your library first.",
    "正在读取歌曲…": "Loading tracks…", "正在处理…": "Working…",
    "请等待歌曲导入完成": "Wait for the track import to finish",
    "请选择要编辑的曲目": "Select a track to edit",
    "请选择单曲": "Select one or more singles",
    "请输入曲目名称": "Enter a track title",
    "选择封面": "Choose cover", "选择封面图片": "Choose cover image",
    "名称可留空，导入时自动读取": "Leave blank to use the video title",
    "BV 号或哔哩哔哩视频链接": "BV ID or Bilibili video URL",
    "单曲 · 导入指定分 P": "Single · Import one part",
    "选择专辑会导入该视频的全部分 P。": "An album imports all parts of the video. You can edit them afterwards.",
    "请输入有效的 BV 号或哔哩哔哩视频链接。": "Enter a valid BV ID or Bilibili video URL.",
    "艺术家": "Artist",
    "更改 BV 或分 P 后会重新读取音频信息。": "Changing the BV or part reloads its audio metadata.",
    "请输入曲目名称。": "Enter a track title.",
    "勾选要复制到专辑的单曲，原单曲会保留。": "Select singles to copy into this album. The originals stay in your library.",
    "添加所选": "Add selected",
    "图片链接或本地图片路径": "Image URL or local image path",
    "选择图片": "Choose image", "移除所选": "Remove selected",
    "拖动曲目可调整顺序；双击可编辑名称、艺术家和来源。": "Drag to reorder; double-click to edit title, artist, or source.",
    "{count} 首曲目": "{count} tracks", "正在处理 {count} 项任务…": "Processing {count} tasks…",
    "未知艺术家": "Unknown artist", "选择专辑封面": "Choose album cover",
    "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*)": "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
    "当前窗口无法启动后台任务。": "This window cannot start a background task.",
    "无法完成操作，请稍后重试。\n{error}": "Could not complete the operation. Try again later.\n{error}",
    "这个视频没有可播放的分 P。": "This video has no playable parts.",
    "请输入专辑名称。": "Enter an album name.",
    "封面图片超过 8 MB": "Cover image exceeds 8 MB",
    "封面不是有效图片或尺寸过大": "Invalid or oversized cover image",
    "移除仅影响此专辑，不会删除音乐库中的单曲。": "Removing a track affects this album only, not your library singles.",
}


def translate(text: str, language: str = "zh_CN", **values) -> str:
    result = ENGLISH.get(text, text) if language == "en_US" else text
    result = result.format(**values) if values else result
    if language == "en_US":
        if values.get("count") == 1:
            result = result.replace("1 tracks", "1 track").replace("1 tasks", "1 task")
        if values.get("total") == 1:
            result = result.replace("1 items", "1 item")
    return result


def translate_error(message: str, language: str) -> str:
    if language != "en_US":
        return message
    if message in ENGLISH:
        return ENGLISH[message]
    replacements = {
        "请求哔哩哔哩超时，请稍后重试": "Bilibili request timed out. Try again later.",
        "连接哔哩哔哩失败：": "Could not connect to Bilibili: ",
        "哔哩哔哩返回了无效的 JSON 数据": "Bilibili returned invalid JSON.",
        "哔哩哔哩响应格式不正确": "Unexpected Bilibili response format.",
        "哔哩哔哩响应状态码格式不正确": "Invalid Bilibili response code.",
        "哔哩哔哩返回的数据为空或格式不正确": "Bilibili returned empty or invalid data.",
        "视频没有可播放的分 P": "This video has no playable parts.",
        "没有找到可播放的音频地址，视频可能受地区或权限限制": "No playable audio was found. This video may have access or region restrictions.",
        "音乐库文件损坏，原文件已保留：": "The library is corrupt; the original file was preserved: ",
        "音乐库文件编码损坏，原文件已保留": "The library encoding is invalid; the original file was preserved.",
        "视频不存在或已删除": "The video does not exist or was deleted",
        "请求被暂时限制，请稍后重试": "Requests are temporarily limited. Try again later",
        "该内容需要登录": "This content requires login", "访问被拒绝": "Access denied",
        "接口请求失败": "API request failed", "哔哩哔哩错误 ": "Bilibili error ",
        "视频暂不可见": "Video unavailable", "视频仅作者可见": "Private video",
        "请求参数错误": "Invalid request parameters", "视频分 P 数据不完整：": "Incomplete part metadata: ",
    }
    for source, replacement in replacements.items():
        message = message.replace(source, replacement)
    return re.sub(r"视频不存在第 (\d+) P", r"Video part P\1 does not exist", message)
