[简体中文](README.zh-CN.md)

# BILI: Basic Integrated Listening Infrastructure (Bilibili Music Player)

A small PyQt6 desktop music player for publicly accessible Bilibili videos. Import BV IDs or public space lists, build editable albums from different videos, and play audio in an independent queue. Switch between light/dark themes and Simplified Chinese/English without interrupting playback.

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

The main instructions describe the current release. Collapsed historical sections retain older behavior for reference; their independent song copies, three-mode controls, and older save formats no longer apply.

### Songs and album folders

Each song is stored once, identified by its **BV ID + part number (P)**. Albums are folders containing ordered references to these shared songs. Importing all parts of a BV video or importing a space list also registers each song in the single-song library; importing the same BV/P again reuses that song.

The **All** tab shows every album plus only singles that are not in any album. The **Singles** tab still shows the complete song library. Removing the last album membership makes a single visible in All again; this view rule does not change JSON exports or saved songs.

- Editing a song's title, author, date, or source from its single card or from any album updates the same song throughout the library and its information pages.
- Removing a song from an album removes only that membership. Deleting an album leaves its songs in the library and in other albums. Deleting the single itself removes the song and its references from every album.
- Queue entries remain separate playback occurrences: the same song can appear several times, each with its own queue ID and a link to the shared song.

### Create and import

- Click **+ New album** to create a named, empty album. A BV ID is not required; you can add songs later.
- Click **+ Import BV** and enter a BV ID, such as `BV1xx411c7mD`, or a full `https://www.bilibili.com/video/BV1xx411c7mD/` link. Choose **Single · Import one part** for one numbered part (P), or **Album · Import all parts** for every P in that video.
- Import reads the video's metadata and tracks immediately, using its cover image by default. Leave the name blank to use the video's title for an album. For songs, an original single-part video uses the main video title; a multi-part video uses each part's title, even when importing only one selected P. This also applies when adding BV tracks to albums or importing lists. Custom names remain as entered.
- **Author** and **Date** are optional manual text fields when importing or editing a single. Both may be blank, and the date accepts any text without a required format. New imports leave these fields blank; neither the UP uploader nor publication timestamps are copied into them.

<details>
<summary>Earlier title and artist guidance (retained verbatim; use the current instructions above)</summary>

- Import reads the video's metadata and tracks immediately, using its cover image by default. Leave the name blank to use the video's title for an album or the selected part's title for a single; you can also enter a custom name.
- Newly imported album and single tracks start with a blank artist; the video's UP uploader name is not copied into that field. You can enter the artist in the album's track editor. Artists already saved or entered by you are preserved.

</details>

- Importing an album through **+ Import BV** starts with all parts of one BV video. You can then mix in songs from other videos. Use **Import list** for a space collection or series containing multiple videos.

<details>
<summary>Earlier BV-only import scope (retained verbatim; use the current list import instructions below)</summary>

- Importing an album starts with all parts of one BV video. You can then mix in songs from other videos. This does not import Bilibili collections or `ugc_season` series.

</details>

### Import a space collection or series

1. Click **Import list** in the top bar and paste a public space list URL, for example `https://space.bilibili.com/1459104794/lists/2029798`.
2. Click **Read list** to preview its original title, video count, and cover. Modern `/MID/lists/ID` links are recognized automatically; explicit `?type=season` or `?type=series` and older space collection/series detail links are also supported.
3. Edit **Album name** if needed. **Import all parts (uncheck to import only P1 of each video)** is checked by default: every P from every listed video becomes a track. Uncheck it to import only P1 of each video.
4. Click **Import as album**. Import runs in the background with progress and **Cancel** available. The new album is added only after the import completes; canceling leaves the library unchanged and does not save a partial album.

Videos keep the list's server order, duplicate BV IDs are removed, and each video is resolved for its actual part/CID metadata. The album uses the list title or your edited name and its cover, falling back to a video cover. Songs are registered in the single-song library or reused by BV/P, preserving existing manual information; newly imported authors remain blank. The album holds ordered references to those shared songs. You can edit its name, membership, and order; future changes to the remote list are not synchronized.

<details>
<summary>Historical list snapshot wording; songs now use shared references</summary>

Videos keep the list's server order, duplicate BV IDs are removed, and each video is resolved for its actual part/CID metadata. The album initially uses the list title, or your edited name, and the list cover; if the list has no cover, an available video cover is used. Imported artists remain blank. After import, use the normal album editor to rename, reorder, remove, or add tracks. The result is an independent editable snapshot; future changes to the Bilibili list are not synchronized automatically.

</details>

Unavailable or deleted videos are skipped with an itemized report. An empty list or a list with no playable tracks fails; connection failures and API throttling stop the import without adding a new album. Existing library data remains unchanged.

The example link was verified as the collection **MyGO!!!!! 原创MV** with **25 videos**; a complete import produced **25 tracks, 0 skipped**. Public list contents and availability can change.

### Export and import the entire library as JSON

Use the **JSON** button beside the search field to export or import the entire library.

- Portable exports use `format: "bili-music-library"`, `version: 2`: all unique songs and album folders are included, with ordered album references to shared song IDs. Search, filters, selection, and the play queue do not restrict export; empty albums are included.
- Song titles, BV/P and CID metadata, durations, manual author/date text, covers, and resolution state are retained. Audio, the playback queue, volume, and settings are excluded.
- Import accepts portable versions 1 and 2 and player-store versions 1, 2, and 3. Files must be UTF-8 JSON, optionally with a BOM, and at most 16 MiB. Invalid graphs or unsupported versions are rejected before changing the library.
- Import merges songs by BV/P, reusing existing songs and preserving their saved metadata. New songs and album folders are appended while existing library order remains intact. Album references are remapped to the merged songs; importing repeatedly does not create duplicate single-song entities.
- Cover fields retain only valid HTTP(S) URLs without credentials; protocol-relative URLs become HTTPS. Local paths, cached images, `file:` URLs, and embedded image data are omitted. Paths in imported files are never opened; export does not read or copy local cover files.
- Choose a separate export destination. Existing exports require overwrite confirmation, exporting over the active library is blocked, and the selected file is replaced atomically.
- JSON import does not request Bilibili metadata. Resolved songs are saved immediately; unresolved songs remain unresolved until opened or played. Source-free and empty albums remain resolved local folders.

<details>
<summary>Historical portable version 1 behavior; use the version 2 instructions above</summary>

Use the **JSON** button beside the search field, then choose **Export entire library to JSON…** or **Import tracks from JSON…**.

- Export includes every album and single in the player's library, in saved library order, together with each entry's nested tracks in their saved order. Search text, the album/single filter, the selected card, and the play queue do not limit the export. Empty albums are included.
- The JSON preserves titles, source BV IDs and part/CID metadata, durations, manual author and arbitrary date text, and saved track lists and resolution state. It contains metadata only: no audio, playback queue, volume, or player settings.
- Covers retain only valid HTTP(S) URLs without credentials; protocol-relative URLs are normalized to HTTPS. Local paths, cached image files, `file:` URLs, and embedded image data are omitted from the exported cover field. Export does not open or copy those files, and the original library and its covers stay unchanged.
- Choose a separate destination file. The save dialog asks before overwriting an existing export, and the player blocks exporting over its active library file. Export replaces the selected file atomically.
- Import accepts this export format (`format: "bili-music-library"`, `version: 1`) and version 1 or 2 player-store snapshots, extracting only their library entries. Files must be UTF-8 JSON, optionally with a BOM, and at most 16 MiB. Unsupported formats or versions and malformed entries are rejected before anything is added.
- Valid imported entries are appended and saved while existing entries retain their order. Every imported entry and nested track receives a new ID; importing the same file again deliberately adds independent duplicates. Imported covers follow the same URL-only rule, and paths named in the file are never opened.
- JSON import makes no Bilibili metadata API requests. Resolved snapshots are saved immediately; unresolved entries with valid BV IDs remain unresolved until opened or played. Albums with no source BV ID, including empty albums, remain local snapshots and are marked resolved.

</details>

### Current library cards and details

The player uses a pink accent palette in both light and dark modes. Each album or single card places square artwork above its title and subtitle. Cards have a neutral, borderless resting background and a panel highlight on hover. The only hover control is the pink circular **Play** button at the artwork's bottom right.

- Click a cover to open read-only details without starting playback. A single opens an information page with a large cover, title, manual author and date, duration, and source. Missing author or date shows **Not set**; there is no duplicate one-song list.
- Album details retain their track list and scrolling header: artwork and metadata shrink and fade as you scroll, then a pinned header shows the album name. Scrolling back up restores the full header.
- Each album song row has **Play** and **Play next** buttons. **Play** appends an independent copy to the queue and starts that song; **Play next** inserts it after the current song without interrupting playback, or appends if none is current. Copies preserve manual metadata and receive fresh IDs. Full and mini mode share the queue.
- Double-click an album song to open the shared song's information page. This is the same song shown by its single card, with the same title, author, date, source, and cover; opening it creates no duplicate single. **Edit** opens the song editor directly. Saving updates every album that references the song and its information pages; deleting an album leaves the song available.

<details>
<summary>Historical album-copy details behavior; use the shared-song instructions above</summary>

- Double-click an album song to open its information page using that album's saved track title, author, date, source, and cover. It does not add a library single or use another single's metadata. **Edit** opens the source album editor; saving refreshes the page, and deleting the source track or album closes it safely.

</details>

- The details footer's **Play** and **Add to queue** actions operate on the displayed album or single in saved track order.

<details>
<summary>Earlier combined album/single details guidance (retained verbatim; use the current instructions above)</summary>

- Click a cover to open the album or single's read-only details without starting playback. Details show the artwork, title, type, track count, duration, saved artists, source BV when available, and track list. Scroll the details page to gradually shrink and fade the album artwork and metadata; farther down, a pinned header shows only the album name. Scrolling back up restores the full header.
- Each song row has **Play** and **Play next** buttons below its information. **Play** appends an independent copy to the queue's tail and starts only that song. **Play next** inserts an independent copy immediately after the currently playing song without interrupting playback or changing the selection; if there is no current song, it appends to the tail. Each copy receives a fresh queue ID, and the saved source track stays unchanged. Full and mini mode use the same queue.
- Double-click a song in album details to open its single details without starting playback. The single details use that album's current track title, artist, source, and cover, even if the library has a standalone single from the same BV video. Opening this view does not add a library entry. Its **Edit** action opens the source album's original editor; returning refreshes the details, and removing the source track or album closes the single details safely.
- The details footer's **Play** and **Add to queue** actions continue to operate on the whole displayed album or single, using its saved track order.

</details>

<details>
<summary>Earlier details double-click behavior (retained verbatim; use the current instructions above)</summary>

The player uses a pink accent palette in both light and dark modes. Each album or single card places square artwork above its title and subtitle. Cards have a neutral, borderless resting background and a panel highlight on hover. The only hover control is the pink circular **Play** button at the artwork's bottom right.

- Click a cover to open the album or single's read-only details without starting playback. Details show the artwork, title, type, track count, duration, saved artists, source BV when available, and track list. Double-click a track in details to append and play only that song; queue copies keep their own IDs.

</details>

- Use the hover **Play** button or right-click **Play** to append the entry's saved tracks in their edited order and start the first newly added track. Right-click **Add to play queue** appends without interrupting playback. Empty albums add no tracks.
- Right-click the card, artwork, or title for playback, adding to the play queue, details, editing, **Add to album…** for singles, opening the source on Bilibili when a BV ID is available, and deleting the library entry. These actions are in the context menu; there are no permanent footer buttons or **···** button.
- Drag a card's artwork or title onto another card to swap their exact library positions. There is no separate six-dot handle in the top-right corner. The order is saved across restarts. Album-only, single-only, and search-filtered views also support swapping: only those two entries change positions in the full library.
- Hold **Ctrl** and turn the mouse wheel over the library, a cover, a title, or a card button to resize cards from **70% to 160% in 10-percentage-point steps**. Card size is remembered across restarts; the interface does not display the zoom percentage. Without Ctrl, the wheel scrolls normally.

<details>
<summary>Earlier library card appearance and drag instructions (retained verbatim; use the current instructions above)</summary>

### Current library cards and details

Each album or single card places square artwork above its title and subtitle. Cards have a neutral, borderless resting background and a panel highlight on hover. Hover over a card to reveal the green circular **Play** button at the artwork's bottom right.

- Click a cover to open the album or single's read-only details without starting playback. Details show the artwork, title, type, track count, duration, saved artists, source BV when available, and track list. Double-click a track in details to append and play only that song; queue copies keep their own IDs.
- Use the hover **Play** button or right-click **Play** to append the entry's saved tracks in their edited order and start the first newly added track. Right-click **Add to play queue** appends without interrupting playback. Empty albums add no tracks.
- Right-click the card, artwork, or title for playback, adding to the play queue, details, editing, **Add to album…** for singles, opening the source on Bilibili when a BV ID is available, and deleting the library entry. These actions are in the context menu; there are no permanent footer buttons or **···** button.
- Drag a card's artwork, title, or top-right handle onto another card to swap their exact library positions. The order is saved across restarts. Album-only, single-only, and search-filtered views also support swapping: only those two entries change positions in the full library.

</details>

### Current album and single editing

Open details and choose **Edit**, or right-click a card and choose **Edit album** or **Edit**. The album editor manages its folder; the song editor manages the shared song. **Edit** on a song's information page opens that song's editor directly.

- Rename an album or change its cover using an image URL or **Choose image**. Use **Add BV** to register or reuse songs from one or all parts. **Add library singles** and a single's **Add to album…** action add references to existing songs.
- Double-click a song in the album editor or choose **Edit track** to edit the shared song's title, optional author/date, source BV, or P. Changing source or P reloads audio metadata while retaining manual author/date. Saving updates the single, every album reference, and the song's information pages.
- Drag songs or use **Move up / Move down** to set this album's order. Double-clicking a song in read-only album details opens its information page; its row's **Play** button starts playback.
- **Remove selected** removes membership only. Deleting an album preserves its songs and their other memberships. Deleting the single itself removes the shared song and every album reference. Empty albums stay empty when opened or played.
- **Save** commits the album name, cover, membership, and order. **Cancel** discards unsaved album changes. Song edits saved in their separate editor are already shared across the library; canceling the album window does not undo them.

Newly imported songs have a blank author; saved or manually entered authors are retained.

<details>
<summary>Historical independent album-copy editing; use the shared-song instructions above</summary>

### Current album and single editing

Open details and click **Edit**, or right-click a card, artwork, or title and choose **Edit album** or **Edit**. This opens the original editor. Saving refreshes details; canceling the editor keeps details open and leaves the saved entry unchanged.

- In the album editor, rename the album and replace its cover using an image URL or **Choose image** for a local image.
- Use **Add BV** to add a single part or every part of another video. Use **Add library singles** to copy selected singles into the album while keeping the originals. A single's right-click **Add to album…** action also lets you choose an existing album.
- When importing or editing a single, use the optional **Author** and **Date** fields to enter its information. In the album editor, double-click a track or choose **Edit track** to edit its title, author, date, source BV, and part number. Source or part changes reload audio metadata while preserving your manual author and date.
- Drag album tracks to reorder them or use **Move up / Move down**. Double-clicking a song in the album's read-only details opens its information page; use that row's **Play** button to start playback.

<details>
<summary>Earlier track-editing guidance (retained verbatim; use the current instructions above)</summary>

- Drag album tracks to reorder them or use **Move up / Move down**. Double-click a track in the album editor, or choose **Edit track**, to change its title, artist, source BV, and part number. Changing the source or part reloads the corresponding audio metadata. Double-clicking a song in the album's read-only details opens its single details; use that row's **Play** button to start the song.

</details>

<details>
<summary>Earlier editor/details double-click comparison (retained verbatim; use the current instructions above)</summary>

- Drag album tracks to reorder them or use **Move up / Move down**. Double-click a track in the album editor, or choose **Edit track**, to change its title, artist, source BV, and part number. Changing the source or part reloads the corresponding audio metadata. Double-clicking in the read-only details window plays that song instead.

</details>

- Use **Remove selected** to remove album tracks. An album may remain empty; opening or playing it will not restore removed parts. Album edits preserve your chosen sources, titles, artists, and order without changing the original singles or existing queue copies.

New imports leave the artist blank rather than copying the UP uploader's name. Previously saved or manually entered artists remain unchanged.

<details>
<summary>Historical card interaction instructions (retained verbatim; use the current instructions above)</summary>

### Edit an album

Click an album's cover, or open its **··· → Edit album** menu. Changes stay in the editor until you click **Save**; **Cancel** leaves the saved album unchanged.

- Rename the album and replace its cover with an image URL or a local image selected through **Choose image**.
- Use **Add BV** to add a single part or every part of another video.
- Use **Add library singles** and check the singles to copy into the album. The originals remain in your library. You can also use a single card's **··· → Add to album…** action to choose an existing album.
- Drag tracks to reorder them, or use **Move up / Move down**. Double-click a track or choose **Edit track** to edit its title, artist, source BV ID, and part number. Changing the source BV or part reads the new audio metadata before the change is accepted.
- Select tracks and choose **Remove selected** to remove them from the album. An album can remain empty, including after you remove every song from an imported album; opening or playing it will not restore the original parts.

The saved track list preserves your chosen sources, titles, artists, and order. Later album edits do not change its source singles or tracks already copied into the queue.

### Playback and appearance

- Albums and singles appear as square library cards. Drag the handle at a card's top right onto another card to swap their exact library positions. The library order is saved and restored after restarting. This also works in album-only, single-only, and search-filtered views: only the two corresponding entries in the full library swap; all other entries keep their positions.

- A card's **Play** action appends its saved tracks in their edited order and starts the first newly added track. **+ Queue** appends without interrupting playback. Empty albums add no tracks.
- Double-click a queued track to play it, drag to reorder, or right-click to remove it. Library card menus also support deletion and opening the source video on Bilibili when one is available.
</details>

</details>

### Playback controls and appearance

- Full and mini mode share **Sequential / Loop queue / Repeat track / Shuffle**. The selected mode is saved for the next launch. In either mode, click the seek bar or drag and release its handle to seek while preserving the current play/pause state.
- **Shuffle** chooses queue occurrences without rearranging the visible queue. It draws from a shuffled bag of remaining occurrences and avoids immediately repeating the same occurrence when there is more than one. Duplicate occurrences of the same song remain distinct, so the same song can still be heard from consecutive different occurrences. **Previous** follows actual playback history.
- In mini mode, the **44×44** mode button on the left balances the same-size queue button on the right. Its menu offers all four modes, marks the selected mode with a check, and stays synchronized with the full-mode dropdown.
- Full and mini mode share a frameless title bar with custom **Minimize / Maximize (Restore) / Close** controls at the top right. Drag the header to move the window, double-click its title or header to maximize/restore, and drag window edges to resize while the window is restored.
- **Close** retains the existing tray behavior: when the system tray is available, it hides the window and keeps playback running. Use **Quit** in the tray menu to exit the player.

The queue overlay, pin button, and theme/language selectors described below remain available. The earlier three-mode overview is retained as historical reference; the four-mode list above is current.

<details>
<summary>Earlier playback control overview; use the four-mode instructions above</summary>

- Double-click a queued track to play it, drag to reorder, or right-click to remove it.

- Use play/pause, previous/next, volume, and **Sequential / Loop queue / Repeat track** for playback control. In both full and mini mode, click anywhere on the seek bar to jump directly to that time, or drag its handle and release to seek. Seeking preserves the current play/pause state; seeking while paused does not start playback.
- Collapse the player into mini mode, or expand it to return to the full window. In mini mode, the borderless queue icon on the right toggles a panel above the playback buttons, over the album artwork. Opening it leaves the window's size and position unchanged. The panel uses the same queue, order, and selection as full mode; double-click to play, drag to reorder, and right-click to remove tracks as usual. Click outside the panel, press **Escape**, or click the queue icon again to close it. Returning to full mode also closes the panel.

<details>
<summary>Earlier playback and mini queue overview (retained verbatim; use the current instructions above)</summary>

- Use play/pause, previous/next, seek, volume, and **Sequential / Loop queue / Repeat track** for playback control.
- Collapse the player into mini mode, or expand it to return to the full window. In mini mode, the borderless queue icon on the right shows or hides the queue.

</details>

</details>

Use **Collapse** to switch to mini mode and **Expand** to return to the full window. When the system tray is unavailable, closing the window exits normally.

In mini mode, the right-hand queue button opens a panel above the playback controls, covering part of the artwork without changing window size or position. It shares the full-mode queue, order, and selection. Double-click to play, drag to reorder, or right-click to remove. Click outside, press **Escape**, click the queue button again, or return to full mode to close the panel.

- Click the pushpin icon in either full or mini mode to keep the player above other windows; click it again to turn this off. Both modes share the pin state, which lasts for the current run and is not saved after quitting.
- Use the selectors at the top right to switch between **Light / Dark** and **简体中文 / English**. Changes apply immediately, are saved for the next launch, and preserve the current playback and queue. Language selection changes interface labels and dialogs; user-entered names, original video metadata, and server-provided messages retain their original text.

## Saved data and upgrades

The library, album membership and covers, queue order, volume, playback mode, theme, language, and card size are saved locally. A new library starts empty with the light theme and Simplified Chinese interface. Restored queues start paused.

The current store is **version 3**. Singles own songs uniquely identified by BV/P; album folders store ordered `song_ids` references instead of duplicate song metadata. Queue occurrences retain separate IDs and link to their source song through `song_id`; migration does not replace occurrence IDs with song IDs. The selected playback mode is saved as `play_mode`, with Shuffle as the fourth mode.

Versions **1, 2, and 3** can be read. Loading a version 1 or 2 library migrates it in memory without rewriting the file. The first subsequent save writes version 3 and first preserves the original bytes in `library.json.v1.bak` or `library.json.v2.bak`; existing backups are never overwritten.

When legacy copies of the same BV/P disagree, the earliest explicit standalone single is authoritative. If there is no standalone single, the first album copy supplies the shared song. The original copies remain available in the backup created before the first save. Migration makes no metadata requests or automatic song renames; missing author/date fields load as blank. Unresolved legacy albums still resolve when first opened or played, then retain saved song references and order.

The store is `library.json` under Qt's per-user `QStandardPaths.AppDataLocation`, with organization and application names both set to `BiliMusic`. Covers are cached in the adjacent `covers` directory; a saved local album cover is copied there so deleting the original image does not remove the saved artwork. Card size is stored as `card_scale`, defaulting to `1.0`; Ctrl + wheel adjusts it without a numerical indicator. Saves use atomic replacement, and corrupt existing stores are preserved and reported.

<details>
<summary>Historical nested-track stores and version 2 upgrade guidance; use version 3 above</summary>

The library, album track lists and covers, queue order, volume, theme, and language are saved locally. On first launch the library is empty, with the light theme and Simplified Chinese interface. Restored queues start paused; playback is not resumed automatically.

Manual author and date are saved with library entries, album tracks, and queue copies. Copying a single into an album or adding tracks to the queue preserves these fields with independent IDs; later edits do not alter the source or existing copies. Version 1 and 2 libraries missing the new fields load them as blank. Existing saved names and manual authors are preserved; this change does not fetch remote metadata or rename saved tracks during migration.

Card size is also saved in the appearance settings as `card_scale`, with a default of `1.0` (100%). Ctrl + wheel changes the saved size without displaying a numerical zoom indicator.

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

Current preview output also includes the library cards at rest and on hover, with the pink circular play button visible in the hover capture, plus the read-only details window. These views are separate from the album editor preview. Cards have no top-right drag handle; drag their artwork or title to swap positions.

<details>
<summary>Earlier preview color note (retained verbatim; current screenshots use the pink palette)</summary>

Current preview output also includes the library cards at rest and on hover, with the green circular play button visible in the hover capture, plus the read-only details window. These views are separate from the album editor preview.

</details>

```bat
scripts\check_stream.bat BV1GJ411x7h7 --album --preview-pair .cache/appearance
```

```bash
bash scripts/check_stream.sh BV1GJ411x7h7 --album --preview-pair .cache/appearance
```

### Optional live space-list checks

Inspect the sample's title, cover, video count, and ordered BV IDs without opening the GUI or changing your library. The default mode reads all listing pages only; `--metadata-only` makes that choice explicit.

```bat
scripts\check_list.bat "https://space.bilibili.com/1459104794/lists/2029798"
```

```bash
bash scripts/check_list.sh "https://space.bilibili.com/1459104794/lists/2029798"
```

Add `--import` to resolve a complete album snapshot in memory. Optional `--first-part-only` with `--import` selects P1 from each video. Optional `--output` creates a separate, new review JSON; its parent directory must exist, and an existing file is never overwritten. This command does not add anything to your saved music library.

```bat
scripts\check_list.bat "https://space.bilibili.com/1459104794/lists/2029798" --import --output mygo-list-review.json
```

```bash
bash scripts/check_list.sh "https://space.bilibili.com/1459104794/lists/2029798" --import --output mygo-list-review.json
```

To exercise the actual dialog, list preview, full import, and save/reopen flow in a temporary library, use the GUI check. `--previews` optionally saves the list preview, pink hover control, library at 130% card size, and imported album details. Your saved library is not modified.

```bat
scripts\check_list_gui.bat "https://space.bilibili.com/1459104794/lists/2029798" --previews .cache/list-import-ui
```

```bash
bash scripts/check_list_gui.sh "https://space.bilibili.com/1459104794/lists/2029798" --previews .cache/list-import-ui
```

Both checks use the named `bili` environment and require network access; the GUI check also needs Qt display support. The sample's full GUI import and save/reopen check completed with 25 tracks and no skipped videos. These are metadata and playback-library checks; they do not download media files.

