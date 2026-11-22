# BILI: Basic Integrated Listening Infrastructure (Bilibili/Youtube Music Player)

![image-20261121010931349](README.assets/image-20261121010931349.png)

## Setup and run

Windows:

```bat
scripts\setup.bat
scripts\run.bat
scripts\deploy.bat
```

Linux / macOS:

```bash
bash scripts/setup.sh
bash scripts/run.sh
bash scripts/deploy.sh
```

## Usage

[中文说明](#中文说明)

- Import music or albums from Bilibili using a BV ID or playlist.
- Import music from YouTube and search lyrics with LRCLIB.
- Use **Import local music** to select one or more audio files. The player keeps its own copies. For an online song, open its details and click **Download audio**. When the details show **Available offline**, playback opens the saved file directly.
- In multi-select mode, use **Export selected**. The ZIP includes the selected entries, songs referenced by selected albums, online and local cover images, and music imported from local files. Check **Include downloaded audio** to also pack offline copies of online songs. Use **Import library** beside **New album** to restore the ZIP.
- Switch to the mini player to save screen space.

## 中文说明

- 使用 BV 号或列表导入哔哩哔哩的歌曲和专辑。
- 支持导入 YouTube 音乐及通过 LRCLIB 搜索歌词。
- 点击 **导入本地音乐** 可一次选择多个音频文件，播放器会保留副本。在线歌曲可在单曲详情中点击 **下载到本地**，显示 **已在本地 · 可离线播放** 后，播放时会直接打开保存的文件。
- 进入多选后点击 **批量导出**。ZIP 包含所选条目、所选专辑引用的歌曲、在线和本地封面，以及从本地导入的音频。勾选 **包含已下载的音频** 可一并保存在线歌曲的离线副本。点击 **新建专辑** 旁的 **导入音乐库** 恢复 ZIP。
- 可切换到迷你播放器节省屏幕空间。

  <img src="README.assets/image-20261121010742141.png" alt="image-20261121010742141" style="zoom:50%;" /><img src="README.assets/image-20261121010751239.png" alt="image-20261121010751239" style="zoom:50%;" />
