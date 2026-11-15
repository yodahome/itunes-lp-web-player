# itunes-lp-web-player

A zero-dependency Python batch patcher and local HTTP range server that brings archived **Apple iTunes LP (`.itlp`)** interactive albums back to life in modern web browsers—complete with audio playback, fullscreen bonus videos, embedded cover art extraction, and a searchable collection overview.

**Please note:** The scripts are mostly generated using Google Gemini through analysing the original iTunes LP files. They qualify as vibe coded.

I have tested this with 22 releases that I own, including Yellow Submarine by The Beatles (2009 re-release), Fearless by Taylor Swift, auch by Die Ärzte or Goodbye Lullaby by Avril Lavigne. So far I still have issues correctly playing Kraniche bei Bosse. But it mostly works ;-)

When Apple discontinued iTunes, `.itlp` packages lost their native JavaScript bridge (`window.iTunes` and `TuneKit`), leaving menus, lyrics, and bonus features silent in regular browsers. This tool injects a lightweight compatibility shim into each `.itlp` bundle and links it to your local audio and video files.

---

## Features

- **Full `window.iTunes` & `TuneKit` Emulation:** Supports both first-generation 2009 LPs (`createTempPlaylist()` / `addTracks()`) and 2010+ TuneKit releases (`bookletController.play()`).
- **Automatic Audio & Video Linking:** Matches local audio (`.m4a`, `.mp3`, `.aac`, `.wav`, `.ogg`, `.flac`) and video (`.m4v`, `.mp4`, `.mov`, `.webm`) files to the LP's internal XIDs using `manifest.xml` and controller scripts.
- **Built-in Cover Art Extraction:** Reads embedded `covr` artwork directly from `.m4a` files using only Python's standard library (falling back to folder images or the largest LP artwork).
- **Floating Media Bar & Video Lightbox:** Adds a draggable playback controller with a Home button (`⌂`) to return to your collection overview, plus a fullscreen video player with keyboard shortcuts (`Space` to play/pause, `←`/`→` to skip ±10s, `Esc` to close).
- **Smooth Video Seeking (`HTTP 206`):** Includes `serve_itlp.py` with byte-range request support so bonus videos can be scrubbed freely in Chrome, Edge, Firefox, and Safari.
- **Non-Destructive & Reversible:** Creates an untouched `index.html.orig.bak` backup before modifying any `.itlp` package.
- **Automatic Bilingual UI (EN / DE):** Uses German automatically on German operating systems and browsers, and English everywhere else.

---

## Prerequisites

1. **Python 3.10 or newer** must be installed on your system:
   - **Windows:** Download from [python.org](https://www.python.org/downloads/) (make sure to check **"Add Python to PATH"** during installation) or install via `winget install Python.Python.3.12`.
   - **macOS:** Pre-installed on many systems, or install via Homebrew (`brew install python`) or [python.org](https://www.python.org/downloads/).
   - **Linux:** Usually pre-installed (`python3`); otherwise install via your package manager (e.g., `sudo apt install python3`).
   - *No external `pip` packages are required—everything runs on Python's built-in standard library.*
2. **A modern web browser** (Chrome, Edge, Firefox, Brave, or Safari).

---

## Expected Folder Structure

For the patcher to automatically pair each `.itlp` booklet with its corresponding songs and bonus videos, **the audio files must sit inside the same album folder as the `.itlp` bundle** (which is the standard layout created by iTunes).

Place the repository scripts (`patch_itlp_batch.py`, `serve_itlp.py`, `start_windows.bat`, `start_unix.sh`) in the **root folder** of your music collection:

```text
Music-Collection/                  <-- Place the scripts here
├── patch_itlp_batch.py
├── serve_itlp.py
├── start_windows.bat
├── start_unix.sh
│
├── Artist A/
│   └── Album One/
│       ├── 01 First Song.m4a      <-- Audio tracks in the album folder
│       ├── 02 Second Song.m4a
│       ├── Bonus Documentary.m4v  <-- Optional bonus video in the album folder
│       └── Album One.itlp/        <-- Interactive iTunes LP bundle
│           ├── index.html
│           ├── manifest.xml
│           └── ...
│
└── Artist B/
    └── Album Two/
        ├── 01 Opening Track.mp3
        └── Album Two.itlp/
```

> **Note on Bonus Videos:** iTunes occasionally stored purchased bonus videos or music videos in a separate `Music Videos/` folder rather than the album directory. If a bonus video does not play, copy or move its `.m4v`/`.mp4` file into the album folder next to the `.itlp` bundle and re-run `patch_itlp_batch.py`.

---

## Step-by-Step Setup & Usage

### 1. Copy the Scripts to Your Collection
Download or clone this repository and copy `patch_itlp_batch.py`, `serve_itlp.py`, and the launcher script for your OS (`start_windows.bat` or `start_unix.sh`) into the root directory of your music folder.

### 2. Run the Batch Patcher (Once)
Open a terminal or command prompt inside your music collection root folder and run:

- **Windows (PowerShell or Command Prompt):**
  ```powershell
  python patch_itlp_batch.py
  ```
- **macOS / Linux:**
  ```bash
  python3 patch_itlp_batch.py
  ```

The script will scan all subfolders for `.itlp` packages, back up each original `index.html` to `index.html.orig.bak`, link the audio/video tracks, extract album covers, and generate a searchable `index.html` overview page in your root folder.

> **Tip for macOS & Linux users:** By default, audio and video files are copied into `_local_audio/` and `_local_media/` subfolders inside each `.itlp` package so browsers can access them without sandbox restrictions. To create lightweight symlinks instead of copying files, run:
> ```bash
> python3 patch_itlp_batch.py --no-copy
> ```

### 3. Start the Local Server & Open in Browser
Because modern browsers block local JavaScript and media requests over `file://`, the albums must be served via the included local HTTP server (`http://127.0.0.1:8000`):

- **Windows:**
  Double-click **`start_windows.bat`**. A console window will open to keep the server running, and your default browser will automatically open `http://127.0.0.1:8000`.
- **macOS / Linux (Bash, Zsh, or Fish):**
  Make the launcher executable once and start it:
  ```bash
  chmod +x start_unix.sh
  ./start_unix.sh
  ```

Keep the terminal/console window open while listening. Close it (or press `Ctrl+C`) when you are done.

---

## Command-Line Options (`patch_itlp_batch.py`)

```text
python3 patch_itlp_batch.py [COLLECTION_PATH] [--no-copy] [--lang de|en]
```

| Option | Description |
| :--- | :--- |
| `COLLECTION_PATH` | Optional path to your music root folder (defaults to the current directory `.`) or to a single `.itlp` bundle. |
| `--no-copy` | Uses symbolic links instead of copying audio/video files (recommended on macOS/Linux to save disk space). |
| `--lang de\|en` | Forces terminal output language to German (`de`) or English (`en`). By default, the system locale is detected automatically. |

---

## Keyboard Shortcuts & Controls

- **Floating Control Bar (bottom-right inside any LP):**
  - `⌂` — Return to the main album collection overview
  - `⏮` / `▶` / `⏭` — Previous track, Play/Pause, Next track
  - `⠿` — Drag handle to reposition the bar anywhere on screen
  - `–` / `+` — Collapse or expand the control bar
- **Fullscreen Bonus Video Player:**
  - `Space` — Play / Pause
  - `←` / `→` — Rewind / Fast-forward 10 seconds
  - `Esc` (or click backdrop) — Close video player

---

## Troubleshooting

- **New albums added later:** Simply place the new album folder (containing the `.m4a` files and `.itlp` bundle) into your collection and run `python3 patch_itlp_batch.py` again. Already patched LPs are cleanly rebuilt from their `index.html.orig.bak` backup.
- **Changes don't show up in the browser:** Perform a hard refresh (`Ctrl + F5` on Windows/Linux, `Cmd + Shift + R` on macOS) so the browser reloads the updated `index.html`.
- **Restoring the untouched original `.itlp`:** Delete the patched `index.html` inside the `.itlp` folder, rename `index.html.orig.bak` back to `index.html`, and remove the `_local_audio` and `_local_media` folders.
