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

## To-do

- Support multiple sources (local files)
- Add music downloads

## Usage

- Import music or albums from Bilibili using a BV ID or playlist.

- Import music from Youtube

- Search lyrics from LRCLIB

- **Export selected** appears in the multi-select toolbar. The ZIP includes the selected entries, songs referenced by selected albums, and their local covers. Use **Import library** beside **New album** to import the ZIP.
  进入多选后，在工具栏点击 **批量导出**。ZIP 包包含所选条目、所选专辑引用的歌曲及其本地封面。点击 **新建专辑** 旁的 **导入音乐库** 即可导入 ZIP。

- Mini music player

  <img src="README.assets/image-20261121010742141.png" alt="image-20261121010742141" style="zoom:50%;" /><img src="README.assets/image-20261121010751239.png" alt="image-20261121010751239" style="zoom:50%;" />
