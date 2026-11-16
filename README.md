# itunes-lp-web-player

A zero-dependency Python live middleware and HTTP byte-range server that brings archived **Apple iTunes LP (`.itlp`)** interactive albums back to life in modern web browsers—**without modifying a single file on disk**.

**Please note:** The script was mostly generated using Google Gemini through analysing the original iTunes LP files. So this heavily qualifies as vibe-coded.

I have tested this with 22 releases that I own, including Yellow Submarine by The Beatles (2009 re-release), Fearless by Taylor Swift, auch by Die Ärzte or Goodbye Lullaby by Avril Lavigne. So far I still have issues correctly playing Kraniche bei Bosse. But it mostly works ;-)

When Apple discontinued iTunes, `.itlp` packages lost their native JavaScript bridge (`window.iTunes` and `TuneKit`), leaving menus, lyrics, and bonus features silent or broken in modern browsers. Instead of altering your archived files, `serve_itlp.py` acts as an on-the-fly proxy: it intercepts browser requests, injects the compatibility shim and floating player in memory, rewrites legacy WebKit syntax incompatibilities on the fly, and streams your local audio and video files directly.

---

## Features

- **100% Non-Destructive (On-the-Fly Middleware):** Your `.itlp` bundles, HTML files, and scripts remain completely untouched on disk—no pre-patching step, no `.bak` files, and no duplicated audio/video folders.
- **Full `window.iTunes` & `TuneKit` Emulation:** Supports both first-generation 2009 LPs (`createTempPlaylist()` / `addTracks()`) and 2010+ TuneKit releases (`bookletController.play()`).
- **Live Legacy Compatibility Fixes:** Automatically upgrades legacy `HTML 4.01` doctypes, shims 0-argument `setTimeout()` / `setInterval()` calls, and rewrites conflicting global `const` declarations (such as `SOUND_EXIT` in `TuneKit.js`) to `var` in memory.
- **Automatic Audio & Video Linking:** Matches local audio (`.m4a`, `.mp3`, `.aac`, `.wav`, `.ogg`, `.flac`) and video (`.m4v`, `.mp4`, `.mov`, `.webm`) files to internal LP track XIDs using `manifest.xml` and controller scripts.
- **In-Memory Cover Art Extraction:** Reads embedded `covr` artwork directly from `.m4a` files using Python's standard library (falling back to folder images or LP artwork) to build a searchable collection overview at `/`.
- **Floating Media Bar & Fullscreen Video Lightbox:** Adds a draggable playback controller with a Home button (`⌂`) and a video player supporting keyboard shortcuts (`Space`, `←`/`→`, `Esc`) and `HTTP 206` byte-range seeking.
- **Automatic Bilingual UI (EN / DE):** Uses German automatically on German operating systems and browsers, and English everywhere else.

---

## Repository Contents

```text
itunes-lp-web-player/
├── serve_itlp.py        # Live middleware & HTTP 206 byte-range server
├── start_windows.bat    # One-click launcher for Windows
├── start_unix.sh        # Launcher for macOS & Linux (Bash, Zsh, Fish)
└── README.md
```

---

## Prerequisites

1. **Python 3.10 or newer** must be installed on your system:
   - **Windows:** Download from [python.org](https://www.python.org/downloads/) (check **"Add Python to PATH"** during installation) or run `winget install Python.Python.3.12`.
   - **macOS:** Pre-installed on many systems, or install via Homebrew (`brew install python`) or [python.org](https://www.python.org/downloads/).
   - **Linux:** Usually pre-installed (`python3`); otherwise install via your package manager (e.g., `sudo apt install python3`).
   - *No external `pip` packages are required—everything runs on Python's built-in standard library.*
2. **A modern web browser** (Chrome, Edge, Firefox, Brave, or Safari).

---

## Expected Folder Structure

For the middleware to automatically pair each `.itlp` booklet with its songs and bonus videos, **the audio files must reside inside the same album folder as the `.itlp` bundle** (the standard folder structure created by iTunes).

Place `serve_itlp.py` and the launcher script for your OS (`start_windows.bat` or `start_unix.sh`) into the **root folder** of your music collection:

```text
Music-Collection/                  <-- Place serve_itlp.py & launcher here
├── serve_itlp.py
├── start_windows.bat
├── start_unix.sh
│
├── Artist A/
│   └── Album One/
│       ├── 01 First Song.m4a      <-- Audio tracks in the album folder
│       ├── 02 Second Song.m4a
│       ├── Bonus Documentary.m4v  <-- Optional bonus video in the album folder
│       └── Album One.itlp/        <-- Untouched original iTunes LP bundle
│           ├── index.html
│           ├── manifest.xml
│           └── ...
│
└── Artist B/
    └── Album Two/
        ├── 01 Opening Track.mp3
        └── Album Two.itlp/
```

> **Note on Bonus Videos:** iTunes occasionally saved purchased bonus videos or music videos in a separate `Music Videos/` folder rather than the album folder. If a bonus video does not appear, copy or move its `.m4v`/`.mp4` file into the album folder next to the `.itlp` bundle and reload the page in your browser.

---

## Quick Start

1. **Copy the server files** (`serve_itlp.py` and `start_windows.bat` or `start_unix.sh`) into the root folder of your music collection.
2. **Start the live server:**
   - **Windows:** Double-click **`start_windows.bat`**. A console window will open to keep the server running, and your default browser will automatically open `http://127.0.0.1:8000`.
   - **macOS / Linux (Bash, Zsh, or Fish):**
     Make the launcher executable once and run it:
```bash
chmod +x start_unix.sh
./start_unix.sh
```
3. **Browse & Play:** Your browser opens `http://127.0.0.1:8000` with a searchable overview of all detected iTunes LP albums. Click any album cover to launch its interactive experience.
4. Keep the terminal/console window open while listening. Close it (or press `Ctrl+C`) when you are finished.

---

## Command-Line Options (`serve_itlp.py`)

You can also start the server directly from any directory without moving the script into your music folder:

```text
python3 serve_itlp.py [--dir COLLECTION_PATH] [--port 8000]
```

| Option | Description |
| :--- | :--- |
| `--dir COLLECTION_PATH` | Path to the root folder of your music collection (defaults to the directory where `serve_itlp.py` is located). |
| `--port PORT` | Local port to bind the HTTP server to (default: `8000`). |

---

## Keyboard Shortcuts & Controls

- **Floating Control Bar (bottom-right inside any LP):**
  - `⌂` — Return to the main album collection overview (`/`)
  - `⏮` / `▶` / `⏭` — Previous track, Play/Pause, Next track
  - `⠿` — Drag handle to reposition the bar anywhere on screen
  - `–` / `+` — Collapse or expand the control bar
- **Fullscreen Bonus Video Player:**
  - `Space` — Play / Pause
  - `←` / `→` — Rewind / Fast-forward 10 seconds
  - `Esc` (or click backdrop) — Close video player

---

## Troubleshooting

- **Adding new albums:** Drop any new album folder (containing `.m4a`/`.mp3` files and its `.itlp` bundle) into your music directory and refresh `http://127.0.0.1:8000` in your browser—no re-patching required.
- **Browser cached an older version:** If you update `serve_itlp.py`, perform a hard refresh (`Ctrl + F5` on Windows/Linux, `Cmd + Shift + R` on macOS) or enable *"Disable cache"* in your browser's DevTools (`F12` → Network).
- **Port `8000` already in use:** Run `python3 serve_itlp.py --port 8080` and open `http://127.0.0.1:8080`.
