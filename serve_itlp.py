#!/usr/bin/env python3
"""
iTunes LP (.itlp) Live Middleware & HTTP Range Server
=====================================================

Serves untouched Apple iTunes LP (.itlp) packages directly in modern browsers
by applying all compatibility patches on-the-fly in memory:
- Dynamically generates the searchable collection overview at `/` with embedded
  M4A cover art streamed from memory (zero files written to disk).
- Injects the `window.iTunes` & `TuneKit` compatibility bridge, floating media
  bar, fullscreen video lightbox, and legacy `setTimeout()` shims into `.itlp`
  HTML responses on the fly.
- Rewrites conflicting legacy `const` declarations (e.g., `SOUND_EXIT` in
  `TuneKit.js`) to `var` on the fly when serving `.js` files.
- Streams local audio and video files with full HTTP 206 byte-range support
  for instant seeking/scrubbing.

Usage:
    python serve_itlp.py [--dir COLLECTION_PATH] [--port 8000]
"""

import argparse
import ctypes
import html as html_lib
import http.server
import io
import json
import locale
import mimetypes
import os
import re
import socketserver
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

GENERIC_PLACEHOLDERS = {"music", "artist", "album", "itunes lp", "itunes lp for music"}
MP4_CONTAINERS = {b"moov", b"udta", b"meta", b"ilst", b"covr"}
AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".wav", ".ogg", ".flac"}
VIDEO_EXTS = {".m4v", ".mp4", ".mov", ".webm"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def is_german_system() -> bool:
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(env_var, "").strip().lower()
        if val.startswith("de"):
            return True
        if val and val not in ("c", "posix"):
            return False

    if sys.platform == "win32":
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lang_id & 0xFF) == 0x07:
                return True
        except Exception:
            pass

    try:
        loc = locale.getlocale()[0] or ""
        if loc.lower().startswith("de"):
            return True
    except Exception:
        pass

    return False


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"^\d+[-.\s_]+\d*\s*", "", text)
    text = re.sub(r"\(\d{4}\s*-?\s*remaster(?:ed)?\)", "", text)
    text = re.sub(r"[^a-z0-9äöüß]+", "", text)
    return text


# ---------------------------------------------------------------------------
# In-Memory MP4 Cover Extraction
# ---------------------------------------------------------------------------

def _scan_mp4_atoms(data: bytes, start: int, end: int) -> tuple[bytes, str] | None:
    pos = start
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        atom_type = data[pos + 4 : pos + 8]
        header_len = 8

        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header_len = 16
        elif size == 0:
            size = end - pos

        if size < header_len or pos + size > end:
            break

        payload_start = pos + header_len
        payload_end = pos + size

        if atom_type == b"data" and payload_end - payload_start > 8:
            type_ind = struct.unpack(">I", data[payload_start : payload_start + 4])[0]
            img_bytes = data[payload_start + 8 : payload_end]
            if type_ind == 14 or img_bytes.startswith(b"\x89PNG"):
                return img_bytes, "image/png"
            if type_ind == 13 or img_bytes.startswith(b"\xff\xd8"):
                return img_bytes, "image/jpeg"
            if len(img_bytes) > 512:
                return img_bytes, "image/jpeg"

        elif atom_type in MP4_CONTAINERS:
            child_start = payload_start + 4 if atom_type == b"meta" else payload_start
            found = _scan_mp4_atoms(data, child_start, payload_end)
            if found:
                return found

        pos += size
    return None


def extract_cover_bytes(itlp_dir: Path, album_dir: Path, audio_files: list[Path]) -> tuple[bytes, str] | None:
    """Returns `(image_bytes, mime_type)` in memory without writing temporary files."""
    m4a_files = [f for f in audio_files if f.suffix.lower() in {".m4a", ".mp4", ".m4b"}]
    for f in m4a_files[:5]:
        try:
            raw = f.read_bytes()
            found = _scan_mp4_atoms(raw, 0, len(raw))
            if found:
                return found
        except OSError:
            continue

    album_imgs = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS]
    if album_imgs:
        album_imgs.sort(
            key=lambda p: (
                0 if any(k in p.stem.lower() for k in ("cover", "folder", "front")) else 1,
                -p.stat().st_size,
            )
        )
        mime = mimetypes.guess_type(album_imgs[0].name)[0] or "image/jpeg"
        return album_imgs[0].read_bytes(), mime

    ui_blacklist = ("btn", "button", "icon", "sprite", "overlay", "text", "title", "rule", "arrow")
    lp_imgs = [
        p
        for p in itlp_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMG_EXTS
        and not any(b in p.stem.lower() for b in ui_blacklist)
    ]
    if lp_imgs:
        lp_imgs.sort(
            key=lambda p: (
                0 if any(k in p.stem.lower() for k in ("cover", "album", "artwork")) else 1,
                -p.stat().st_size,
            )
        )
        mime = mimetypes.guess_type(lp_imgs[0].name)[0] or "image/jpeg"
        return lp_imgs[0].read_bytes(), mime

    return None


# ---------------------------------------------------------------------------
# Metadata & Media Discovery
# ---------------------------------------------------------------------------

def collect_media_files(album_dir: Path, itlp_dir: Path) -> tuple[list[Path], list[Path]]:
    audio_files: list[Path] = []
    video_files: list[Path] = []

    for p in album_dir.rglob("*"):
        if not p.is_file():
            continue
        if any(part.lower().endswith(".itlp") for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext in AUDIO_EXTS:
            audio_files.append(p)
        elif ext in VIDEO_EXTS:
            video_files.append(p)

    for p in itlp_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            video_files.append(p)

    return (
        sorted(audio_files, key=lambda p: p.name.lower()),
        sorted(video_files, key=lambda p: p.name.lower()),
    )


def extract_metadata_and_media(itlp_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    fallback_album = itlp_dir.stem
    fallback_artist = (
        itlp_dir.parent.parent.name if itlp_dir.parent.parent != itlp_dir.parent else ""
    )
    meta = {"album": fallback_album, "artist": fallback_artist}
    tracks: list[dict] = []
    videos: list[dict] = []

    manifest_path = itlp_dir / "manifest.xml"
    if manifest_path.exists():
        try:
            tree = ET.parse(manifest_path)
            for elem in tree.getroot().iter():
                if elem.tag.split("}")[-1].lower() == "library_item":
                    item_type = elem.attrib.get("type", "").lower()
                    xid = elem.attrib.get("xid", "").strip()
                    title = (elem.attrib.get("name") or elem.attrib.get("title") or "").strip()
                    if not xid:
                        continue
                    if item_type == "song" and not any(t["xid"] == xid for t in tracks):
                        tracks.append({"xid": xid, "title": title})
                    elif item_type == "video" and not any(v["xid"] == xid for v in videos):
                        videos.append({"xid": xid, "title": title, "src": "", "key": ""})
        except ET.ParseError:
            pass

    for js_file in itlp_dir.rglob("*.js"):
        raw = js_file.read_text(encoding="utf-8", errors="ignore")

        feat_m = re.search(
            r"""feature\s*:\s*\{[^{}]*?title\s*:\s*['"]([^'"]+)['"][^{}]*?artist\s*:\s*['"]([^'"]+)['"]""",
            raw,
            re.DOTALL,
        )
        if feat_m:
            found_album = feat_m.group(1).strip()
            found_artist = feat_m.group(2).strip()
            if found_album and found_album.lower() not in GENERIC_PLACEHOLDERS:
                meta["album"] = found_album
            if found_artist and found_artist.lower() not in GENERIC_PLACEHOLDERS:
                meta["artist"] = found_artist

        videos_obj_m = re.search(r"""videos\s*:\s*\{(.*?)\n\s*\}""", raw, re.DOTALL)
        if videos_obj_m:
            for vm in re.finditer(r"""([a-zA-Z0-9_]+)\s*:\s*\{([^{}]*)\}""", videos_obj_m.group(1), re.DOTALL):
                vid_key = vm.group(1).strip()
                inner = vm.group(2)
                xid_m = re.search(r"""['"]?(?:XID|xid)['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                title_m = re.search(r"""['"]?(?:string|title|name)['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                src_m = re.search(r"""['"]?src['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                vxid = xid_m.group(1).strip() if xid_m else f"video:{vid_key}"
                vtitle = title_m.group(1).strip() if title_m else vid_key
                vsrc = src_m.group(1).strip() if src_m else ""
                existing = next((v for v in videos if v["xid"] == vxid), None)
                if existing:
                    if not existing["title"]:
                        existing["title"] = vtitle
                    if vsrc:
                        existing["src"] = vsrc
                    existing["key"] = vid_key
                else:
                    videos.append({"xid": vxid, "title": vtitle, "src": vsrc, "key": vid_key})

        videos_arr_m = re.search(r"""videos\s*:\s*\[(.*?)\]""", raw, re.DOTALL)
        if videos_arr_m:
            for idx, vm in enumerate(re.finditer(r"""\{([^{}]*)\}""", videos_arr_m.group(1), re.DOTALL), start=1):
                inner = vm.group(1)
                xid_m = re.search(r"""['"]?(?:XID|xid)['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                title_m = re.search(r"""['"]?(?:string|title|name)['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                src_m = re.search(r"""['"]?src['"]?\s*:\s*['"]([^'"]+)['"]""", inner)
                vxid = xid_m.group(1).strip() if xid_m else f"video:arr:{idx}"
                vtitle = title_m.group(1).strip() if title_m else f"Video {idx}"
                vsrc = src_m.group(1).strip() if src_m else ""
                vkey = f"video{idx:02d}"
                existing = next((v for v in videos if v["xid"] == vxid), None)
                if existing:
                    if not existing["title"]:
                        existing["title"] = vtitle
                    if vsrc:
                        existing["src"] = vsrc
                    if not existing.get("key"):
                        existing["key"] = vkey
                else:
                    videos.append({"xid": vxid, "title": vtitle, "src": vsrc, "key": vkey})

        if "xid" not in raw.lower():
            continue

        video_xids = {v["xid"] for v in videos}
        for m in re.finditer(
            r"""\{[^{}]{5,300}?['"]?(?:XID|xid)['"]?\s*:\s*['"]([^'"]+)['"][^{}]{0,200}?['"]?(?:string|title|name|songName)['"]?\s*:\s*['"]([^'"]+)['"]""",
            raw,
            re.DOTALL,
        ):
            xid, title = m.group(1).strip(), m.group(2).strip()
            if xid and xid not in video_xids and not any(t["xid"] == xid for t in tracks):
                tracks.append({"xid": xid, "title": title})

        for m in re.finditer(
            r"""\{[^{}]{5,300}?['"]?(?:string|title|name|songName)['"]?\s*:\s*['"]([^'"]+)['"][^{}]{0,200}?['"]?(?:XID|xid)['"]?\s*:\s*['"]([^'"]+)['"]""",
            raw,
            re.DOTALL,
        ):
            title, xid = m.group(1).strip(), m.group(2).strip()
            if xid and xid not in video_xids and not any(t["xid"] == xid for t in tracks):
                tracks.append({"xid": xid, "title": title})

    return meta, tracks, videos


def match_items_to_files(items: list[dict], files: list[Path]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    used_files: set[Path] = set()

    for item in items:
        norm_title = normalize(item.get("title", ""))
        if not norm_title:
            continue
        for f in files:
            if f in used_files:
                continue
            norm_fname = normalize(f.stem)
            if norm_title == norm_fname or (
                len(norm_title) >= 4 and (norm_title in norm_fname or norm_fname in norm_title)
            ):
                mapping[item["xid"]] = f
                used_files.add(f)
                break

    remaining_items = [t for t in items if t["xid"] not in mapping]
    remaining_files = [f for f in files if f not in used_files]
    if remaining_items and len(remaining_items) <= len(remaining_files):
        for item, f in zip(remaining_items, remaining_files):
            mapping[item["xid"]] = f
            used_files.add(f)

    return mapping


# ---------------------------------------------------------------------------
# Dynamic HTML & JS Middleware Builders
# ---------------------------------------------------------------------------

def build_shim_script(xid_entries: list[dict], video_entries: list[dict]) -> str:
    entries_json = json.dumps(xid_entries, indent=2, ensure_ascii=False)
    videos_json = json.dumps(video_entries, indent=2, ensure_ascii=False)

    return f"""<!-- INJECTED ON-THE-FLY BY serve_itlp.py -->
<style id="itlp-floating-player-style">
  #itlp-floating-bar {{
    position: fixed !important;
    bottom: 18px !important;
    right: 18px !important;
    z-index: 2147483646 !important;
    display: flex !important;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border-radius: 14px;
    background: rgba(18, 18, 22, 0.90);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.18);
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.65);
    color: #f3f4f6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 13px;
    user-select: none;
    -webkit-user-select: none;
    cursor: default;
    visibility: visible !important;
    opacity: 1 !important;
  }}
  #itlp-floating-bar.collapsed .itlp-fp-collapsible {{
    display: none !important;
  }}
  #itlp-floating-bar button,
  #itlp-floating-bar a.itlp-fp-btn {{
    background: rgba(255, 255, 255, 0.10);
    border: 1px solid rgba(255, 255, 255, 0.15);
    color: #fff;
    border-radius: 8px;
    width: 30px;
    height: 30px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 14px;
    text-decoration: none;
    padding: 0;
    transition: background 0.15s ease;
  }}
  #itlp-floating-bar button:hover,
  #itlp-floating-bar a.itlp-fp-btn:hover {{
    background: rgba(255, 255, 255, 0.25);
  }}
  #itlp-fp-drag {{
    cursor: grab;
    color: #9ca3af;
    font-size: 14px;
    padding: 0 2px;
  }}
  #itlp-fp-drag:active {{
    cursor: grabbing;
  }}
  #itlp-fp-meta {{
    display: flex;
    flex-direction: column;
    min-width: 145px;
    max-width: 220px;
    gap: 4px;
  }}
  #itlp-fp-title {{
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: #ffffff;
  }}
  #itlp-fp-progress-row {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: #9ca3af;
  }}
  #itlp-fp-seek {{
    flex: 1;
    accent-color: #60a5fa;
    height: 4px;
    cursor: pointer;
  }}
  #itlp-fp-vol-wrap {{
    display: flex;
    align-items: center;
    gap: 5px;
  }}
  #itlp-fp-vol {{
    width: 68px;
    accent-color: #60a5fa;
    height: 4px;
    cursor: pointer;
  }}
  #itlp-video-overlay {{
    position: fixed !important;
    inset: 0 !important;
    z-index: 2147483647 !important;
    background: rgba(0, 0, 0, 0.94);
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  #itlp-video-overlay.open {{
    display: flex !important;
  }}
  #itlp-video-header {{
    width: 100%;
    max-width: 1120px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    color: #f3f4f6;
  }}
  #itlp-video-title {{
    font-size: 16px;
    font-weight: 600;
  }}
  #itlp-video-hint {{
    font-size: 12px;
    color: #9ca3af;
    margin-left: auto;
    margin-right: 14px;
  }}
  #itlp-video-close {{
    background: rgba(255, 255, 255, 0.14);
    border: 1px solid rgba(255, 255, 255, 0.22);
    color: #fff;
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    cursor: pointer;
  }}
  #itlp-video-close:hover {{
    background: rgba(255, 255, 255, 0.28);
  }}
  #itlp-video-player {{
    width: 100%;
    max-width: 1120px;
    max-height: 80vh;
    background: #000;
    border-radius: 12px;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.8);
    outline: none;
  }}
</style>

<script id="itlp-local-audio-shim">
(function() {{
  const _origSetTimeout = window.setTimeout.bind(window);
  window.setTimeout = function(fn, delay, ...args) {{
    if (typeof fn !== "function" && typeof fn !== "string") return 0;
    return _origSetTimeout(fn, delay || 0, ...args);
  }};

  const _origSetInterval = window.setInterval.bind(window);
  window.setInterval = function(fn, delay, ...args) {{
    if (typeof fn !== "function" && typeof fn !== "string") return 0;
    return _origSetInterval(fn, delay || 0, ...args);
  }};

  const TRACKS = {entries_json};
  const VIDEOS = {videos_json};

  const IS_DE = /^de\\b/i.test(navigator.language || "");
  const UI = IS_DE ? {{
    ready: "Bereit",
    bonusVideo: "Bonus-Video",
    videoHint: "Leertaste: Play/Pause · ←/→: ±10 Sek. · Esc: Schließen",
    closeBtn: "✕ Schließen",
    closeTitle: "Video schließen (Esc)",
    dragTitle: "Verschieben",
    homeTitle: "Zurück zur Alben-Übersicht",
    prevTitle: "Vorheriger Titel",
    playTitle: "Play / Pause",
    nextTitle: "Nächster Titel",
    muteTitle: "Stumm / Laut",
    volTitle: "Lautstärke",
    toggleTitle: "Steuerung ein-/ausklappen"
  }} : {{
    ready: "Ready",
    bonusVideo: "Bonus Video",
    videoHint: "Space: Play/Pause · ←/→: ±10s · Esc: Close",
    closeBtn: "✕ Close",
    closeTitle: "Close video (Esc)",
    dragTitle: "Drag to move",
    homeTitle: "Back to Album Overview",
    prevTitle: "Previous Track",
    playTitle: "Play / Pause",
    nextTitle: "Next Track",
    muteTitle: "Mute / Unmute",
    volTitle: "Volume",
    toggleTitle: "Collapse / Expand Controls"
  }};

  const audio = document.createElement("audio");
  audio.id = "itlp-shim-player";
  audio.preload = "auto";
  audio.volume = 0.85;

  let currentIndex = -1;
  let activeQueue = TRACKS.slice();

  function formatTime(sec) {{
    if (!isFinite(sec) || sec < 0) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" + s : s);
  }}

  function stopBackgroundLoops() {{
    if (window.bookletController && typeof window.bookletController.stopAudioLoop === "function") {{
      try {{ window.bookletController.stopAudioLoop(); }} catch (e) {{}}
    }}
    document.querySelectorAll("audio").forEach(el => {{
      if (el.id !== "itlp-shim-player") {{
        try {{ el.pause(); }} catch (e) {{}}
      }}
    }});
  }}

  function resolveVideo(input) {{
    if (!VIDEOS.length) return null;
    if (!input) return VIDEOS[0];
    if (typeof input === "number") return VIDEOS[input] || VIDEOS[0];
    const xid = typeof input === "string"
      ? input
      : (input.XID || input.xid || input.persistentID || "");
    if (xid) {{
      const found = VIDEOS.find(v => v.xid === xid || v.key === xid);
      if (found) return found;
    }}
    const src = typeof input === "object" ? (input.src || "") : "";
    if (src) {{
      const foundBySrc = VIDEOS.find(v => v.src === src || v.url.endsWith(src));
      if (foundBySrc) return foundBySrc;
      return {{ xid: xid || "local:video", title: input.string || input.title || UI.bonusVideo, url: src }};
    }}
    const title = typeof input === "object" ? (input.string || input.title || input.name || "") : "";
    if (title) {{
      const foundByTitle = VIDEOS.find(v => v.title.toLowerCase() === title.toLowerCase());
      if (foundByTitle) return foundByTitle;
    }}
    return VIDEOS[0];
  }}

  function openVideoOverlay(videoObj) {{
    buildFloatingPlayer();
    if (!videoObj || !videoObj.url) return false;
    audio.pause();
    stopBackgroundLoops();

    const overlay = document.getElementById("itlp-video-overlay");
    const vplayer = document.getElementById("itlp-video-player");
    const vtitle = document.getElementById("itlp-video-title");
    if (!overlay || !vplayer) return false;

    vtitle.textContent = videoObj.title || UI.bonusVideo;
    vplayer.volume = audio.volume;
    vplayer.muted = audio.muted;
    vplayer.src = videoObj.url;
    overlay.classList.add("open");
    vplayer.focus();
    vplayer.play().catch(err => console.error("[iTunesShim] Video playback error:", err));
    return true;
  }}

  function closeVideoOverlay() {{
    const overlay = document.getElementById("itlp-video-overlay");
    const vplayer = document.getElementById("itlp-video-player");
    if (vplayer) {{
      vplayer.pause();
      vplayer.removeAttribute("src");
      vplayer.load();
    }}
    if (overlay) overlay.classList.remove("open");
  }}

  function isVideoInput(input) {{
    if (!input) return false;
    const xid = typeof input === "string"
      ? input
      : (input.XID || input.xid || input.persistentID || "");
    if (xid && VIDEOS.some(v => v.xid === xid || v.key === xid)) return true;
    if (typeof input === "object" && input.src && /\\.(m4v|mp4|mov|webm)$/i.test(input.src)) return true;
    return false;
  }}

  function resolveTrack(input) {{
    if (!input && input !== 0) return TRACKS[0] || null;
    if (typeof input === "number") return TRACKS[input] || null;
    if (input && input._trackObj) return input._trackObj;
    const xid = typeof input === "string"
      ? input
      : (input.XID || input.xid || input.persistentID || "");
    if (xid) {{
      const found = TRACKS.find(t => t.xid === xid);
      if (found) return found;
    }}
    const title = typeof input === "object" ? (input.string || input.title || input.name || "") : "";
    if (title) {{
      const foundByTitle = TRACKS.find(t => t.title.toLowerCase() === title.toLowerCase());
      if (foundByTitle) return foundByTitle;
    }}
    return TRACKS[0] || null;
  }}

  function updateUI() {{
    const titleEl = document.getElementById("itlp-fp-title");
    const playBtn = document.getElementById("itlp-fp-play");
    const timeEl = document.getElementById("itlp-fp-time");
    const seekEl = document.getElementById("itlp-fp-seek");
    if (!titleEl) return;

    const current = currentIndex >= 0 ? activeQueue[currentIndex] : TRACKS[0];
    titleEl.textContent = current ? current.title : UI.ready;
    if (playBtn) {{
      playBtn.textContent = (!audio.paused && !audio.ended && audio.src) ? "⏸" : "▶";
    }}
    if (timeEl) {{
      timeEl.textContent = formatTime(audio.currentTime) + " / " + formatTime(audio.duration);
    }}
    if (seekEl && isFinite(audio.duration) && audio.duration > 0) {{
      seekEl.value = Math.round((audio.currentTime / audio.duration) * 1000);
    }}
  }}

  function playTrackObj(trackObj, queue) {{
    buildFloatingPlayer();
    if (!trackObj || !trackObj.url) return;
    closeVideoOverlay();
    stopBackgroundLoops();
    if (Array.isArray(queue) && queue.length > 0) {{
      activeQueue = queue;
    }}
    currentIndex = activeQueue.findIndex(t => t.xid === trackObj.xid);
    if (currentIndex < 0) {{
      activeQueue = TRACKS.slice();
      currentIndex = activeQueue.findIndex(t => t.xid === trackObj.xid);
    }}
    audio.src = trackObj.url;
    audio.play()
      .then(updateUI)
      .catch(err => console.error("[iTunesShim] Audio playback error:", err));
  }}

  audio.addEventListener("play", updateUI);
  audio.addEventListener("pause", updateUI);
  audio.addEventListener("timeupdate", updateUI);
  audio.addEventListener("loadedmetadata", updateUI);
  audio.addEventListener("ended", () => {{
    if (currentIndex >= 0 && currentIndex + 1 < activeQueue.length) {{
      playTrackObj(activeQueue[currentIndex + 1], activeQueue);
    }} else {{
      updateUI();
    }}
  }});

  function buildFloatingPlayer() {{
    const mountTarget = document.body || document.documentElement;
    if (!mountTarget || document.getElementById("itlp-floating-bar")) return;
    mountTarget.appendChild(audio);

    const vOverlay = document.createElement("div");
    vOverlay.id = "itlp-video-overlay";
    vOverlay.innerHTML = `
      <div id="itlp-video-header">
        <div id="itlp-video-title">${{UI.bonusVideo}}</div>
        <div id="itlp-video-hint">${{UI.videoHint}}</div>
        <button id="itlp-video-close" title="${{UI.closeTitle}}">${{UI.closeBtn}}</button>
      </div>
      <video id="itlp-video-player" controls playsinline tabindex="0"></video>
    `;
    mountTarget.appendChild(vOverlay);
    document.getElementById("itlp-video-close").addEventListener("click", closeVideoOverlay);
    vOverlay.addEventListener("click", (e) => {{
      if (e.target === vOverlay) closeVideoOverlay();
    }});

    window.addEventListener("keydown", (e) => {{
      if (!vOverlay.classList.contains("open")) return;
      const vp = document.getElementById("itlp-video-player");

      if (e.key === "Escape") {{
        e.preventDefault();
        closeVideoOverlay();
      }} else if (e.code === "Space" || e.key === " ") {{
        e.preventDefault();
        if (vp) {{
          if (vp.paused) vp.play().catch(() => {{}});
          else vp.pause();
        }}
      }} else if (e.key === "ArrowLeft") {{
        e.preventDefault();
        if (vp && isFinite(vp.currentTime)) {{
          vp.currentTime = Math.max(0, vp.currentTime - 10);
        }}
      }} else if (e.key === "ArrowRight") {{
        e.preventDefault();
        if (vp && isFinite(vp.currentTime)) {{
          const maxTime = isFinite(vp.duration) ? vp.duration : vp.currentTime + 10;
          vp.currentTime = Math.min(maxTime, vp.currentTime + 10);
        }}
      }}
    }}, true);

    const bar = document.createElement("div");
    bar.id = "itlp-floating-bar";
    bar.innerHTML = `
      <span id="itlp-fp-drag" title="${{UI.dragTitle}}">⠿</span>
      <a id="itlp-fp-home" class="itlp-fp-btn" href="/" title="${{UI.homeTitle}}">⌂</a>
      <button id="itlp-fp-prev" class="itlp-fp-collapsible" title="${{UI.prevTitle}}">⏮</button>
      <button id="itlp-fp-play" title="${{UI.playTitle}}">▶</button>
      <button id="itlp-fp-next" class="itlp-fp-collapsible" title="${{UI.nextTitle}}">⏭</button>
      <div id="itlp-fp-meta" class="itlp-fp-collapsible">
        <div id="itlp-fp-title">${{UI.ready}}</div>
        <div id="itlp-fp-progress-row">
          <input id="itlp-fp-seek" type="range" min="0" max="1000" value="0">
          <span id="itlp-fp-time">0:00 / 0:00</span>
        </div>
      </div>
      <div id="itlp-fp-vol-wrap" class="itlp-fp-collapsible">
        <button id="itlp-fp-mute" title="${{UI.muteTitle}}">🔊</button>
        <input id="itlp-fp-vol" type="range" min="0" max="100" value="85" title="${{UI.volTitle}}">
      </div>
      <button id="itlp-fp-toggle" title="${{UI.toggleTitle}}">–</button>
    `;
    mountTarget.appendChild(bar);

    document.getElementById("itlp-fp-play").addEventListener("click", () => {{
      if (!audio.src) {{
        if (TRACKS[0]) playTrackObj(TRACKS[0], TRACKS);
      }} else if (audio.paused) {{
        audio.play();
      }} else {{
        audio.pause();
      }}
    }});

    document.getElementById("itlp-fp-prev").addEventListener("click", () => {{
      if (audio.currentTime > 3) {{
        audio.currentTime = 0;
      }} else if (currentIndex - 1 >= 0) {{
        playTrackObj(activeQueue[currentIndex - 1], activeQueue);
      }}
    }});

    document.getElementById("itlp-fp-next").addEventListener("click", () => {{
      if (currentIndex + 1 < activeQueue.length) {{
        playTrackObj(activeQueue[currentIndex + 1], activeQueue);
      }}
    }});

    document.getElementById("itlp-fp-seek").addEventListener("input", (e) => {{
      if (isFinite(audio.duration) && audio.duration > 0) {{
        audio.currentTime = (Number(e.target.value) / 1000) * audio.duration;
      }}
    }});

    const volSlider = document.getElementById("itlp-fp-vol");
    const muteBtn = document.getElementById("itlp-fp-mute");
    volSlider.addEventListener("input", (e) => {{
      audio.muted = false;
      audio.volume = Number(e.target.value) / 100;
      const vp = document.getElementById("itlp-video-player");
      if (vp) {{ vp.muted = false; vp.volume = audio.volume; }}
      muteBtn.textContent = audio.volume === 0 ? "🔇" : "🔊";
    }});

    muteBtn.addEventListener("click", () => {{
      audio.muted = !audio.muted;
      const vp = document.getElementById("itlp-video-player");
      if (vp) vp.muted = audio.muted;
      muteBtn.textContent = audio.muted ? "🔇" : "🔊";
    }});

    const toggleBtn = document.getElementById("itlp-fp-toggle");
    toggleBtn.addEventListener("click", () => {{
      bar.classList.toggle("collapsed");
      toggleBtn.textContent = bar.classList.contains("collapsed") ? "+" : "–";
    }});

    const dragHandle = document.getElementById("itlp-fp-drag");
    let isDragging = false, offsetX = 0, offsetY = 0;
    dragHandle.addEventListener("mousedown", (e) => {{
      isDragging = true;
      const rect = bar.getBoundingClientRect();
      offsetX = e.clientX - rect.left;
      offsetY = e.clientY - rect.top;
      bar.style.right = "auto";
      bar.style.bottom = "auto";
      bar.style.left = rect.left + "px";
      bar.style.top = rect.top + "px";
    }});
    window.addEventListener("mousemove", (e) => {{
      if (!isDragging) return;
      bar.style.left = Math.max(0, e.clientX - offsetX) + "px";
      bar.style.top = Math.max(0, e.clientY - offsetY) + "px";
    }});
    window.addEventListener("mouseup", () => {{ isDragging = false; }});

    updateUI();
  }}

  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", buildFloatingPlayer);
  }} else {{
    buildFloatingPlayer();
  }}
  window.addEventListener("load", buildFloatingPlayer);
  setInterval(buildFloatingPlayer, 1000);

  function makeiTunesTrackWrapper(trackObj) {{
    if (!trackObj) return null;
    const trackNum = TRACKS.findIndex(t => t.xid === trackObj.xid) + 1;
    return {{
      _trackObj: trackObj,
      xid: trackObj.xid,
      XID: trackObj.xid,
      persistentID: trackObj.xid,
      trackNumber: trackNum > 0 ? trackNum : 1,
      title: trackObj.title,
      name: trackObj.title,
      get duration() {{ return isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 240; }},
      get currentTime() {{ return audio.currentTime || 0; }},
      set currentTime(v) {{ audio.currentTime = v; }},
      get isPlaying() {{ return !audio.paused && !audio.ended; }},
      play: () => playTrackObj(trackObj),
      pause: () => audio.pause(),
      stop: () => {{ audio.pause(); audio.currentTime = 0; }}
    }};
  }}

  function createPlaylistInstance(initialTracks) {{
    const items = [];
    function append(input) {{
      if (!input) return;
      if (Array.isArray(input)) {{
        input.forEach(append);
        return;
      }}
      const resolved = resolveTrack(input);
      if (resolved) items.push(resolved);
    }}
    if (initialTracks) append(initialTracks);

    return {{
      _items: items,
      get length() {{ return items.length; }},
      addTracks: function(tracksOrChapter) {{ append(tracksOrChapter); }},
      addTrack: function(trackOrChapter) {{ append(trackOrChapter); }},
      play: function(indexOrItem) {{
        const queue = items.length > 0 ? items : TRACKS;
        if (typeof indexOrItem === "number") {{
          const target = queue[indexOrItem] || queue[0];
          if (target) playTrackObj(target, queue);
          return target ? makeiTunesTrackWrapper(target) : null;
        }}
        if (indexOrItem !== undefined) {{
          const target = resolveTrack(indexOrItem);
          if (target) playTrackObj(target, queue);
          return target ? makeiTunesTrackWrapper(target) : null;
        }}
        if (queue[0]) {{
          playTrackObj(queue[0], queue);
          return makeiTunesTrackWrapper(queue[0]);
        }}
        return null;
      }},
      pause: () => audio.pause(),
      stop: () => {{ audio.pause(); audio.currentTime = 0; }}
    }};
  }}

  const iTunesApi = {{
    version: "12.8.0",
    platform: "Windows",
    StoppedState: 0,
    PlayingState: 1,
    PausedState: 2,
    sources: [],
    playlists: [],
    get volume() {{ return Math.round(audio.volume * 100); }},
    set volume(v) {{
      const num = Number(v);
      if (isFinite(num)) {{
        audio.volume = Math.max(0, Math.min(1, num > 1 ? num / 100 : num));
        const vs = document.getElementById("itlp-fp-vol");
        if (vs) vs.value = Math.round(audio.volume * 100);
      }}
    }},
    get soundVolume() {{ return Math.round(audio.volume * 100); }},
    set soundVolume(v) {{ this.volume = v; }},
    get mute() {{ return audio.muted; }},
    set mute(m) {{ audio.muted = Boolean(m); }},
    get currentPlayerState() {{
      if (audio.ended || !audio.src) return 0;
      return audio.paused ? 2 : 1;
    }},
    get isPlaying() {{ return !audio.paused && !audio.ended; }},
    get currentTime() {{ return audio.currentTime || 0; }},
    set currentTime(v) {{ if (isFinite(Number(v))) audio.currentTime = Number(v); }},
    get duration() {{ return isFinite(audio.duration) ? audio.duration : 0; }},
    get currentPlayingTrack() {{
      if (currentIndex < 0 || !activeQueue[currentIndex] || audio.ended) return null;
      return makeiTunesTrackWrapper(activeQueue[currentIndex]);
    }},
    get currentTrack() {{ return this.currentPlayingTrack; }},
    getTrack: (id) => makeiTunesTrackWrapper(resolveTrack(id)),
    findTracksByXID: (xid) => {{
      if (isVideoInput(xid)) {{
        const v = resolveVideo(xid);
        return v ? [{{ xid: v.xid, XID: v.xid, persistentID: v.xid, title: v.title, play: () => openVideoOverlay(v) }}] : [];
      }}
      const t = resolveTrack(xid);
      return t ? [makeiTunesTrackWrapper(t)] : [];
    }},
    findTracksByPersistentID: function(pid) {{ return this.findTracksByXID(pid); }},
    createTempPlaylist: (items) => createPlaylistInstance(items),
    play: (target) => {{
      if (isVideoInput(target)) {{
        const v = resolveVideo(target);
        if (v && openVideoOverlay(v)) return;
      }}
      if (target !== undefined) {{
        const t = resolveTrack(target);
        if (t) return playTrackObj(t);
      }}
      if (audio.src) audio.play();
      else if (TRACKS[0]) playTrackObj(TRACKS[0], TRACKS);
    }},
    playNonLibraryContent: (contentObj) => {{
      const v = resolveVideo(contentObj);
      if (v && openVideoOverlay(v)) return;
    }},
    playVideo: (contentObj) => {{
      const v = resolveVideo(contentObj);
      if (v && openVideoOverlay(v)) return;
    }},
    pause: () => audio.pause(),
    stop: () => {{ audio.pause(); audio.currentTime = 0; }},
    nextTrack: () => {{
      if (currentIndex + 1 < activeQueue.length) {{
        playTrackObj(activeQueue[currentIndex + 1], activeQueue);
      }}
    }},
    previousTrack: () => {{
      if (currentIndex - 1 >= 0) {{
        playTrackObj(activeQueue[currentIndex - 1], activeQueue);
      }}
    }},
    setVolume: (v) => {{ iTunesApi.volume = v; }},
    getVolume: () => Math.round(audio.volume * 100),
    addEventListener: () => {{}},
    removeEventListener: () => {{}}
  }};

  window.iTunes = Object.assign(window.iTunes || {{}}, iTunesApi);

  function hookBookletController() {{
    if (typeof window.bookletController === "undefined" || window.bookletController._shimmed) {{
      return;
    }}
    window.bookletController._shimmed = true;

    const origPlay = window.bookletController.play;
    window.bookletController.play = function(songOrPlaylist) {{
      if (isVideoInput(songOrPlaylist)) {{
        const v = resolveVideo(songOrPlaylist);
        if (v && openVideoOverlay(v)) return;
      }}
      if (songOrPlaylist && Array.isArray(songOrPlaylist._items)) {{
        playTrackObj(songOrPlaylist._items[0], songOrPlaylist._items);
        return;
      }}
      const resolved = resolveTrack(songOrPlaylist);
      if (resolved) {{
        playTrackObj(resolved, TRACKS);
        return;
      }}
      if (typeof origPlay === "function") return origPlay.apply(this, arguments);
      if (TRACKS[0]) playTrackObj(TRACKS[0], TRACKS);
    }};

    window.bookletController.playNonLibraryContent = function(contentObj) {{
      const v = resolveVideo(contentObj);
      if (v && openVideoOverlay(v)) return;
    }};
    window.bookletController.playVideo = window.bookletController.playNonLibraryContent;

    window.bookletController.buildPlaylist = function(songsArray) {{
      return createPlaylistInstance(songsArray);
    }};
  }}

  document.addEventListener("click", (e) => {{
    const target = e.target;
    if (!target || target.closest("#itlp-floating-bar") || target.closest("#itlp-video-overlay")) return;

    const videoItemBtn = target.closest(".video-button, [id^='video0'], [id^='video1']");
    if (videoItemBtn && VIDEOS.length > 0) {{
      const vidId = videoItemBtn.id || "";
      const numMatch = vidId.match(/(\\d+)$/);
      const idx = numMatch ? Math.max(0, parseInt(numMatch[1], 10) - 1) : 0;
      setTimeout(() => {{
        const overlayOpen = document.getElementById("itlp-video-overlay")?.classList.contains("open");
        if (!overlayOpen) {{
          const chosen = VIDEOS.find(v => v.key === vidId) || VIDEOS[idx] || VIDEOS[0];
          openVideoOverlay(chosen);
        }}
      }}, 180);
    }}
  }}, true);

  window.addEventListener("load", hookBookletController);
  const hookTimer = setInterval(() => {{
    if (typeof window.bookletController !== "undefined") {{
      hookBookletController();
      clearInterval(hookTimer);
    }}
  }}, 100);
}})();
</script>
"""


def build_overview_html(root_dir: Path) -> bytes:
    """Dynamically builds the searchable collection overview page in memory."""
    albums: list[dict] = []
    for itlp_dir in sorted([p for p in root_dir.rglob("*.itlp") if p.is_dir()], key=lambda p: str(p).lower()):
        if not (itlp_dir / "index.html").is_file():
            continue
        album_dir = itlp_dir.parent
        meta, tracks, videos = extract_metadata_and_media(itlp_dir)
        audio_files, video_files = collect_media_files(album_dir, itlp_dir)
        if not audio_files and not video_files:
            continue

        rel_itlp = itlp_dir.relative_to(root_dir).as_posix()
        rel_index = (itlp_dir / "index.html").relative_to(root_dir).as_posix()
        href = "/" + "/".join(urllib.parse.quote(part) for part in rel_index.split("/"))
        cover_url = "/_itlp_cover/" + urllib.parse.quote(rel_itlp, safe="/")

        albums.append({
            "album": meta["album"],
            "artist": meta["artist"],
            "track_count": len(tracks) if tracks else len(audio_files),
            "video_count": len(videos) if videos else len(video_files),
            "href": href,
            "cover_url": cover_url,
        })

    albums_sorted = sorted(albums, key=lambda a: (a["artist"].lower(), a["album"].lower()))
    count = len(albums_sorted)
    cards_html = []

    for item in albums_sorted:
        album_esc = html_lib.escape(item["album"])
        artist_raw = item["artist"]
        artist_esc = html_lib.escape(artist_raw) if artist_raw else ""
        artist_attr = ' data-unknown="1"' if not artist_raw else ""
        search_attr = html_lib.escape(f"{artist_raw} {item['album']}".lower())

        t_cnt = item["track_count"]
        v_cnt = item["video_count"]
        meta_en = f"{t_cnt} Track{'s' if t_cnt != 1 else ''}" + (
            f" · {v_cnt} Video{'s' if v_cnt != 1 else ''}" if v_cnt else ""
        ) + " · iTunes LP"
        meta_de = f"{t_cnt} Titel" + (
            f" · {v_cnt} Video{'s' if v_cnt > 1 else ''}" if v_cnt else ""
        ) + " · iTunes LP"

        cards_html.append(
            f"""
            <a class="album-card" href="{item['href']}" data-search="{search_attr}">
              <div class="cover-wrap">
                <img src="{item['cover_url']}" alt="{album_esc}" loading="lazy" onerror="this.outerHTML='<div class=\\'no-cover\\'>♫</div>'">
              </div>
              <div class="info">
                <div class="album-title" title="{album_esc}">{album_esc}</div>
                <div class="artist-name"{artist_attr} title="{artist_esc}">{artist_esc or 'Unknown Artist'}</div>
                <div class="meta" data-en="{html_lib.escape(meta_en)}" data-de="{html_lib.escape(meta_de)}">{html_lib.escape(meta_en)}</div>
              </div>
            </a>"""
        )

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>iTunes LP Collection ({count})</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #181b22;
      --card-hover: #222631;
      --border: rgba(255, 255, 255, 0.1);
      --text: #f3f4f6;
      --muted: #9ca3af;
      --accent: #60a5fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px 24px 60px;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    header {{
      max-width: 1240px;
      margin: 0 auto 28px;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; }}
    .subtitle {{ color: var(--muted); font-size: 14px; margin-top: 4px; }}
    #search {{
      background: var(--card);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 10px;
      font-size: 14px;
      width: 280px;
      max-width: 100%;
      outline: none;
    }}
    #search:focus {{ border-color: var(--accent); }}
    .grid {{
      max-width: 1240px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
      gap: 20px;
    }}
    .album-card {{
      display: flex;
      flex-direction: column;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      text-decoration: none;
      color: inherit;
      transition: transform 0.16s ease, background 0.16s ease, border-color 0.16s ease;
    }}
    .album-card:hover {{
      transform: translateY(-3px);
      background: var(--card-hover);
      border-color: rgba(255, 255, 255, 0.24);
    }}
    .cover-wrap {{
      aspect-ratio: 1 / 1;
      background: #111318;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .cover-wrap img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
    .no-cover {{ font-size: 48px; color: #4b5563; }}
    .info {{ padding: 12px 14px 14px; }}
    .album-title {{
      font-weight: 600;
      font-size: 15px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .artist-name {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 3px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .meta {{ color: var(--accent); font-size: 11px; margin-top: 8px; font-weight: 500; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1 id="page-heading">iTunes LP Collection</h1>
      <div class="subtitle" id="page-subtitle">{count} interactive album{'s' if count != 1 else ''} ready to play</div>
    </div>
    <input id="search" type="search" placeholder="Filter by album or artist…">
  </header>
  <main class="grid" id="album-grid">
    {''.join(cards_html)}
  </main>
  <script>
    (function() {{
      const isDe = /^de\\b/i.test(navigator.language || "");
      const albumCount = {count};
      if (isDe) {{
        document.documentElement.lang = "de";
        document.title = "iTunes LP Sammlung (" + albumCount + ")";
        document.getElementById("page-heading").textContent = "iTunes LP Sammlung";
        document.getElementById("page-subtitle").textContent =
          albumCount + (albumCount === 1 ? " interaktives Album bereit zur Wiedergabe" : " interaktive Alben bereit zur Wiedergabe");
        document.getElementById("search").placeholder = "Album oder Interpret filtern …";
        document.querySelectorAll(".artist-name[data-unknown='1']").forEach(el => {{
          el.textContent = "Unbekannter Interpret";
          el.title = "Unbekannter Interpret";
        }});
        document.querySelectorAll(".meta").forEach(el => {{
          if (el.dataset.de) el.textContent = el.dataset.de;
        }});
      }}
      const input = document.getElementById("search");
      const cards = Array.from(document.querySelectorAll(".album-card"));
      input.addEventListener("input", () => {{
        const q = input.value.trim().toLowerCase();
        cards.forEach(card => {{
          card.style.display = !q || card.dataset.search.includes(q) ? "" : "none";
        }});
      }});
    }})();
  </script>
</body>
</html>
"""
    return page.encode("utf-8")


def patch_itlp_html_in_memory(itlp_dir: Path, index_html: Path, root_dir: Path) -> bytes:
    """Patches an `.itlp/index.html` response in memory with virtual media URLs."""
    album_dir = itlp_dir.parent
    _, tracks, videos = extract_metadata_and_media(itlp_dir)
    audio_files, video_files = collect_media_files(album_dir, itlp_dir)

    def to_virtual_media_url(file_path: Path) -> str:
        rel = file_path.relative_to(root_dir).as_posix()
        return "/_itlp_media/" + "/".join(urllib.parse.quote(part) for part in rel.split("/"))

    xid_entries: list[dict] = []
    if tracks:
        matched = match_items_to_files(tracks, audio_files)
        for t in tracks:
            f = matched.get(t["xid"])
            if f:
                xid_entries.append({
                    "xid": t["xid"],
                    "title": t["title"] or f.stem,
                    "url": to_virtual_media_url(f),
                })
    else:
        for idx, f in enumerate(audio_files, start=1):
            xid_entries.append({
                "xid": f"local:track:{idx}",
                "title": f.stem,
                "url": to_virtual_media_url(f),
            })

    video_entries: list[dict] = []
    if video_files or videos:
        matched_videos = match_items_to_files(videos, video_files) if videos else {}
        for v in videos:
            if v.get("src") and (itlp_dir / v["src"]).is_file():
                video_entries.append({
                    "xid": v["xid"],
                    "title": v["title"] or Path(v["src"]).stem,
                    "url": to_virtual_media_url(itlp_dir / v["src"]),
                    "src": v["src"],
                    "key": v.get("key", ""),
                })
                continue
            vf = matched_videos.get(v["xid"])
            if vf:
                video_entries.append({
                    "xid": v["xid"],
                    "title": v["title"] or vf.stem,
                    "url": to_virtual_media_url(vf),
                    "src": v.get("src", ""),
                    "key": v.get("key", ""),
                })

        used_names = {Path(urllib.parse.unquote(ve["url"])).name for ve in video_entries}
        for idx, vf in enumerate(video_files, start=1):
            if vf.name not in used_names:
                video_entries.append({
                    "xid": f"local:video:{idx}",
                    "title": vf.stem,
                    "url": to_virtual_media_url(vf),
                    "src": "",
                    "key": f"video{idx:02d}",
                })

    base_html = index_html.read_text(encoding="utf-8", errors="ignore")
    base_html = re.sub(
        r'<!DOCTYPE\s+HTML\s+PUBLIC\s+"-//W3C//DTD HTML 4\.01//EN"[^>]*>',
        "<!DOCTYPE html>",
        base_html,
        count=1,
        flags=re.IGNORECASE,
    )
    shim = build_shim_script(xid_entries, video_entries)

    if re.search(r"<head[^>]*>", base_html, re.IGNORECASE):
        patched_html = re.sub(
            r"(<head[^>]*>)",
            lambda m: m.group(1) + "\n" + shim,
            base_html,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        patched_html = shim + "\n" + base_html

    return patched_html.encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP Request Handler with Middleware & Byte-Range Support
# ---------------------------------------------------------------------------

class _LimitedFile:
    def __init__(self, f, remaining: int):
        self.f = f
        self.remaining = remaining

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        data = self.f.read(size)
        self.remaining -= len(data)
        return data

    def close(self) -> None:
        self.f.close()


class ITLPMiddlewareHandler(http.server.SimpleHTTPRequestHandler):
    root_dir: Path = Path(__file__).resolve().parent

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root_dir), **kwargs)

    def _send_bytes(self, payload: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return io.BytesIO(payload)

    def _send_file_with_ranges(self, file_path: Path):
        if not file_path.is_file():
            self.send_error(404, "File not found")
            return None

        ctype = self.guess_type(str(file_path))
        try:
            f = open(file_path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        file_len = fs.st_size
        range_header = self.headers.get("Range")

        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_len - 1
                end = min(end, file_len - 1)
                if start >= file_len or start > end:
                    f.close()
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return None

                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_len}")
                self.send_header("Content-Length", str(length))
                self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
                self.end_headers()
                f.seek(start)
                return _LimitedFile(f, length)

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_len))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        return f

    def _resolve_safe_subpath(self, rel_url_path: str) -> Path | None:
        clean_rel = urllib.parse.unquote(rel_url_path).lstrip("/")
        candidate = (self.root_dir / clean_rel).resolve()
        try:
            candidate.relative_to(self.root_dir.resolve())
        except ValueError:
            return None
        return candidate

    def send_head(self):
        parsed = urllib.parse.urlsplit(self.path)
        req_path = parsed.path

        # 1. Dynamic collection overview at `/` or `/index.html` (unless an explicit index.html exists)
        if req_path in ("/", "/index.html") and not (self.root_dir / "index.html").exists():
            html_bytes = build_overview_html(self.root_dir)
            return self._send_bytes(html_bytes, "text/html; charset=utf-8")

        # 2. Virtual embedded cover endpoint `/_itlp_cover/<rel_itlp_dir>`
        if req_path.startswith("/_itlp_cover/"):
            itlp_dir = self._resolve_safe_subpath(req_path[len("/_itlp_cover/") :])
            if itlp_dir and itlp_dir.is_dir():
                audio_files, _ = collect_media_files(itlp_dir.parent, itlp_dir)
                cover = extract_cover_bytes(itlp_dir, itlp_dir.parent, audio_files)
                if cover:
                    return self._send_bytes(cover[0], cover[1])
            self.send_error(404, "Cover not found")
            return None

        # 3. Virtual media streaming endpoint `/_itlp_media/<rel_media_path>`
        if req_path.startswith("/_itlp_media/"):
            media_file = self._resolve_safe_subpath(req_path[len("/_itlp_media/") :])
            if media_file:
                return self._send_file_with_ranges(media_file)
            self.send_error(403, "Forbidden")
            return None

        fs_path = Path(self.translate_path(self.path))
        if fs_path.is_dir():
            index_candidate = fs_path / "index.html"
            if index_candidate.is_file():
                fs_path = index_candidate
            else:
                return super().send_head()

        # Check if request targets a file inside an `.itlp` package
        itlp_parent = next((p for p in [fs_path, *fs_path.parents] if p.name.lower().endswith(".itlp")), None)
        if itlp_parent and fs_path.is_file():
            # 4. On-the-fly HTML patching for `.itlp/index.html`
            if fs_path == itlp_parent / "index.html":
                patched = patch_itlp_html_in_memory(itlp_parent, fs_path, self.root_dir)
                return self._send_bytes(patched, "text/html; charset=utf-8")

            # 5. On-the-fly legacy `const` -> `var` rewrite for LP JavaScript files
            if (
                fs_path.suffix.lower() == ".js"
                and fs_path.name.lower() not in ("framework.js", "prettify.js", "jquery.js")
            ):
                raw_js = fs_path.read_text(encoding="utf-8", errors="ignore")
                fixed_js = re.sub(
                    r"(^|[;{}\n\r\t ])const(\s+[A-Za-z_$][\w$]*)",
                    r"\1var\2",
                    raw_js,
                    flags=re.MULTILINE,
                )
                return self._send_bytes(fixed_js.encode("utf-8"), "application/javascript; charset=utf-8")

        # 6. Default static/range file serving
        return self._send_file_with_ranges(fs_path)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="On-the-fly middleware HTTP server for Apple iTunes LP (.itlp) collections."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Root directory of the music collection (default: script directory)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local port to bind (default: 8000)",
    )
    args = parser.parse_args()

    ITLPMiddlewareHandler.root_dir = args.dir.resolve()
    with ThreadingHTTPServer(("127.0.0.1", args.port), ITLPMiddlewareHandler) as httpd:
        url = f"http://127.0.0.1:{args.port}"
        if is_german_system():
            print(f"iTunes LP Live-Middleware aktiv unter {url} (Verzeichnis: {ITLPMiddlewareHandler.root_dir})")
        else:
            print(f"iTunes LP live middleware running at {url} (directory: {ITLPMiddlewareHandler.root_dir})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
