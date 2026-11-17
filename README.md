[简体中文](README.zh-CN.md)

# BILI: Bilibili Integrated Listening Infrastructure (Bilibili Music Player)

A small PyQt6 desktop music player for publicly accessible Bilibili videos. Import BV IDs, build editable albums from different videos, and play audio in an independent queue. Switch between light/dark themes and Simplified Chinese/English without interrupting playback.

## Setup and run

Install Miniconda or Anaconda first. Run these commands from the project directory in Anaconda Prompt or a terminal where `conda` is available. Scripts also accept `CONDA_EXE` and resolve the project directory from their own location, so an absolute script path works from any working directory.

Windows:

```bat
scripts\setup.bat
scripts\run.bat
```

Linux / macOS:

```bash
bash scripts/setup.sh
bash scripts/run.sh
```

Setup finds the named `bili` environment or creates it, ensures Python 3.12, then installs [requirements.txt](requirements.txt). The application and tests always use `conda run --no-capture-output -n bili`; they do not run in the default or `base` Python environment.

PyQt6 on Windows includes the Qt Multimedia FFmpeg backend; no separate `ffmpeg` executable is required. Linux may need system display and audio libraries supplied by your distribution, as well as a working desktop session and audio device.

## Using the player

### Create and import

- Click **+ New album** to create a named, empty album. A BV ID is not required; you can add songs later.
- Click **+ Import BV** and enter a BV ID, such as `BV1xx411c7mD`, or a full `https://www.bilibili.com/video/BV1xx411c7mD/` link. Choose **Single · Import one part** for one numbered part (P), or **Album · Import all parts** for every P in that video.
- Import reads the video's metadata and tracks immediately, using its cover image by default. Leave the name blank to use the video's title for an album or the selected part's title for a single; you can also enter a custom name.
- Importing an album starts with all parts of one BV video. You can then mix in songs from other videos. This does not import Bilibili collections or `ugc_season` series.

### Edit an album

Click an album's cover, or open its **··· → Edit album** menu. Changes stay in the editor until you click **Save**; **Cancel** leaves the saved album unchanged.

- Rename the album and replace its cover with an image URL or a local image selected through **Choose image**.
- Use **Add BV** to add a single part or every part of another video.
- Use **Add library singles** and check the singles to copy into the album. The originals remain in your library. You can also use a single card's **··· → Add to album…** action to choose an existing album.
- Drag tracks to reorder them, or use **Move up / Move down**. Double-click a track or choose **Edit track** to edit its title, artist, source BV ID, and part number. Changing the source BV or part reads the new audio metadata before the change is accepted.
- Select tracks and choose **Remove selected** to remove them from the album. An album can remain empty, including after you remove every song from an imported album; opening or playing it will not restore the original parts.

The saved track list preserves your chosen sources, titles, artists, and order. Later album edits do not change its source singles or tracks already copied into the queue.

### Playback and appearance

- A card's **Play** action appends its saved tracks in their edited order and starts the first newly added track. **+ Queue** appends without interrupting playback. Empty albums add no tracks.
- Double-click a queued track to play it, drag to reorder, or right-click to remove it. Library card menus also support deletion and opening the source video on Bilibili when one is available.
- Use play/pause, previous/next, seek, volume, and **Sequential / Loop queue / Repeat track** for playback control.
- Use the selectors at the top right to switch between **Light / Dark** and **简体中文 / English**. Changes apply immediately, are saved for the next launch, and preserve the current playback and queue. Language selection changes interface labels and dialogs; user-entered names, original video metadata, and server-provided messages retain their original text.

## Saved data and upgrades

The library, album track lists and covers, queue order, volume, theme, and language are saved locally. On first launch the library is empty, with the light theme and Simplified Chinese interface. Restored queues start paused; playback is not resumed automatically.

Data lives in `library.json` under Qt's per-user `QStandardPaths.AppDataLocation`, with both organization and application names set to `BiliMusic`. The exact directory depends on the operating system. Cover images are cached in a `covers` directory next to the library. When you save a local album cover, it is copied into that managed cache so removing the original image does not remove the saved artwork.

Existing version 1 libraries can still be loaded without changing the file. The first subsequent save writes version 2 and first creates a byte-for-byte `library.json.v1.bak` alongside the library; an existing backup is not overwritten. Legacy albums import their parts once when first opened or played, then retain an editable saved track list. Saving uses an atomic file replacement; a corrupt existing store is preserved and reported instead of silently overwritten.

<details>
<summary>Historical version 1 instructions (retained for reference; use the current instructions above)</summary>

## Using the player

- Click **添加音乐** and enter a BV ID, such as `BV1xx411c7mD`, or a full `https://www.bilibili.com/video/BV1xx411c7mD/` link. A custom display title is optional; leaving it blank displays the BV ID on the library card. Queue track titles come from the video's part titles.
- Choose **单曲** to select one numbered part (P), or **专辑** to expand every P within that one BV video. Albums here do not mean Bilibili collections or `ugc_season` series.
- A card's **播放** action appends its tracks to the queue and starts the first newly added track. **＋ 队列** appends without interrupting playback.
- Double-click a queued track to play it, drag to reorder, or right-click to remove it. Library cards support editing, deletion, and opening the video on Bilibili.
- Use play/pause, previous/next, seek, volume, and **顺序播放 / 列表循环 / 单曲循环** for sequential, queue loop, or single-track loop playback.

The library, queue order, and volume are saved locally. On first launch the library is empty. Restored queues start paused; playback is not resumed automatically. Data lives in `library.json` under Qt's per-user `QStandardPaths.AppDataLocation`, with both organization and application names set to `BiliMusic`. The exact directory depends on the operating system. Saving uses an atomic file replacement; a corrupt existing store is preserved and reported instead of silently overwritten.

</details>

## Playback and access

No login or cookies are required. Only publicly accessible videos can be played; region restrictions, payment requirements, or login requirements can prevent playback. Bilibili may throttle API requests, and deleted or private videos cannot be resolved.

The client prefers DASH audio and falls back to the API's `durl` stream. A local relay binds to `127.0.0.1` on a random port, adds the required User-Agent and Referer headers, and forwards Range requests for seeking. Stream addresses are resolved when playback is requested rather than stored in the library.

## Tests

```bat
scripts\test.bat
```

```bash
bash scripts/test.sh
```

Both launch `python -m unittest discover -s tests -v` inside `bili`. API parsing and errors are tested with mocks; GUI tests select Qt's offscreen mode internally. These tests do not establish that any particular live Bilibili video is available.

For an optional live metadata, stream, and Qt media-loading check, supply a publicly accessible BV ID:

```bat
scripts\check_stream.bat BV1GJ411x7h7
```

```bash
bash scripts/check_stream.sh BV1GJ411x7h7
```

The check uses a temporary library, resolves through the GUI's background tasks, and briefly plays muted audio before pausing when the position advances. It does not modify your saved library. It requires network access and may fail when the video or API is unavailable. Add `--screenshot path.png` to save the check window, or `--help` to see the arguments.

To also check editable album import, the default video cover, and appearance changes during playback, add `--album`. Use `--theme light|dark` and `--language zh_CN|en_US` to choose the initial interface. `--preview-pair` saves light/Chinese, dark/English, and album-editor screenshots to the chosen directory:

```bat
scripts\check_stream.bat BV1GJ411x7h7 --album --preview-pair .cache/appearance
```

```bash
bash scripts/check_stream.sh BV1GJ411x7h7 --album --preview-pair .cache/appearance
```

