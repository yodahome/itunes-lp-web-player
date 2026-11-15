#!/usr/bin/env python3
"""
iTunes LP (.itlp) Batch Patcher & Browser Bridge
================================================

Restores archived Apple iTunes LP packages (.itlp) in modern web browsers
without requiring the discontinued iTunes desktop application.
"""

import argparse
import ctypes
import datetime
import html as html_lib
import json
import locale
import os
import re
import shutil
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

GENERIC_PLACEHOLDERS = {"music", "artist", "album", "itunes lp", "itunes lp for music"}
MP4_CONTAINERS = {b"moov", b"udta", b"meta", b"ilst", b"covr"}

STRINGS: dict[str, dict[str, str]] = {
    "de": {
        "cli_desc": "Patcht rekursiv iTunes LP (.itlp) Ordner für moderne Webbrowser.",
        "cli_root_help": "Stammordner der Musiksammlung (Standard: aktuelles Verzeichnis) oder einzelner .itlp-Ordner",
        "cli_nocopy_help": "Symlinks statt Dateikopien verwenden (spart Speicherplatz auf macOS/Linux)",
        "cli_lang_help": "Ausgabesprache erzwingen (de oder en; Standard: automatische Systemerkennung)",
        "err_path_missing": "[Fehler] Pfad existiert nicht: {path}",
        "err_no_itlp": "Keine .itlp-Ordner unter {path} gefunden.",
        "found_packages": "Gefundene iTunes LP Pakete: {count}",
        "processing": "Verarbeite: {path}",
        "skip_no_index": "  [Übersprungen] Keine index.html vorhanden.",
        "album_label": "  Album:        {title}",
        "audio_stats": "  Audio in LP:  {lp_count} | Audiodateien: {file_count}",
        "video_stats": "  Videos in LP: {lp_count} | Videodateien: {file_count}",
        "skip_no_media": "  [Übersprungen] Keine Audio- oder Videodateien gefunden.",
        "audio_ok": "    [Audio OK] {name} -> {filename}",
        "audio_miss": "    [Audio !!] {name} -> Keine passende Datei",
        "video_ok_int": "    [Video OK] {name} -> {filename} (intern)",
        "video_ok": "    [Video OK] {name} -> {filename}",
        "video_miss": "    [Video !!] {name} -> Keine Videodatei gefunden",
        "video_added": "    [Video + ] {filename} eingebunden",
        "skip_unlinked": "  [Übersprungen] Keine Tracks oder Videos verknüpfbar.",
        "bak_orig_from_legacy": "    [Backup] Ur-Backup aus {src} übernommen: {dst}",
        "bak_orig_created": "    [Backup] Unverändertes Ur-Backup erstellt: {dst}",
        "bak_prev_saved": "    [Backup] Vorheriger Stand gesichert: {dst}",
        "info_rebuild": "    [Info] Vorhandener Patch erkannt -> baue sauber auf index.html.orig.bak neu auf.",
        "warn_no_orig": "    [Warnung] Kein Ur-Backup vorhanden -> bereinige vorhandenen Patch-Block.",
        "warn_manifest": "    [Warnung] manifest.xml Parse-Fehler: {err}",
        "cover_extracted": "    [Cover] Eingebettetes M4A-Artwork extrahiert -> {filename}",
        "done_single": "  [Fertig] index.html erfolgreich gepatcht.",
        "bak_root_saved": "\n[Backup] Vorhandene Stamm-index.html gesichert: {dst}",
        "overview_created": "[Übersicht] Startseite erstellt: {path}",
        "summary": "Abgeschlossen: {patched} von {total} iTunes LPs erfolgreich gepatcht.",
    },
    "en": {
        "cli_desc": "Recursively patches iTunes LP (.itlp) packages for modern web browsers.",
        "cli_root_help": "Root folder of the music collection (default: current directory) or a single .itlp folder",
        "cli_nocopy_help": "Use symlinks instead of copying media files (saves disk space on macOS/Linux)",
        "cli_lang_help": "Force output language (de or en; default: auto-detect system locale)",
        "err_path_missing": "[Error] Path does not exist: {path}",
        "err_no_itlp": "No .itlp folders found under {path}.",
        "found_packages": "Found iTunes LP packages: {count}",
        "processing": "Processing: {path}",
        "skip_no_index": "  [Skipped] No index.html found.",
        "album_label": "  Album:        {title}",
        "audio_stats": "  Audio in LP:  {lp_count} | Audio files: {file_count}",
        "video_stats": "  Videos in LP: {lp_count} | Video files: {file_count}",
        "skip_no_media": "  [Skipped] No audio or video files found.",
        "audio_ok": "    [Audio OK] {name} -> {filename}",
        "audio_miss": "    [Audio !!] {name} -> No matching file found",
        "video_ok_int": "    [Video OK] {name} -> {filename} (internal)",
        "video_ok": "    [Video OK] {name} -> {filename}",
        "video_miss": "    [Video !!] {name} -> No matching video file found",
        "video_added": "    [Video + ] {filename} added",
        "skip_unlinked": "  [Skipped] No tracks or videos could be linked.",
        "bak_orig_from_legacy": "    [Backup] Adopted original backup from {src}: {dst}",
        "bak_orig_created": "    [Backup] Created untouched original backup: {dst}",
        "bak_prev_saved": "    [Backup] Saved previous state: {dst}",
        "info_rebuild": "    [Info] Existing patch detected -> rebuilding cleanly from index.html.orig.bak.",
        "warn_no_orig": "    [Warning] No original backup found -> stripping existing patch block.",
        "warn_manifest": "    [Warning] manifest.xml parse error: {err}",
        "cover_extracted": "    [Cover] Extracted embedded M4A artwork -> {filename}",
        "done_single": "  [Done] index.html patched successfully.",
        "bak_root_saved": "\n[Backup] Saved existing root index.html: {dst}",
        "overview_created": "[Overview] Generated index page: {path}",
        "summary": "Completed: {patched} of {total} iTunes LPs patched successfully.",
    },
}


def detect_system_lang() -> str:
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(env_var, "").strip().lower()
        if val.startswith("de"):
            return "de"
        if val and val not in ("c", "posix"):
            return "en"

    if sys.platform == "win32":
        try:
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lang_id & 0xFF) == 0x07:
                return "de"
        except Exception:
            pass

    try:
        loc = locale.getlocale()[0] or ""
        if loc.lower().startswith("de"):
            return "de"
    except Exception:
        pass

    return "en"


ACTIVE_LANG = detect_system_lang()


def tr(key: str, **kwargs) -> str:
    template = STRINGS.get(ACTIVE_LANG, STRINGS["en"])[key]
    return template.format(**kwargs) if kwargs else template


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"^\d+[-.\s_]+\d*\s*", "", text)
    text = re.sub(r"\(\d{4}\s*-?\s*remaster(?:ed)?\)", "", text)
    text = re.sub(r"[^a-z0-9äöüß]+", "", text)
    return text


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
                return img_bytes, ".png"
            if type_ind == 13 or img_bytes.startswith(b"\xff\xd8"):
                return img_bytes, ".jpg"
            if len(img_bytes) > 512:
                return img_bytes, ".jpg"

        elif atom_type in MP4_CONTAINERS:
            child_start = payload_start + 4 if atom_type == b"meta" else payload_start
            found = _scan_mp4_atoms(data, child_start, payload_end)
            if found:
                return found

        pos += size
    return None


def extract_m4a_cover(audio_files: list[Path], output_dir: Path) -> Path | None:
    m4a_files = [f for f in audio_files if f.suffix.lower() in {".m4a", ".mp4", ".m4b"}]
    for f in m4a_files[:5]:
        try:
            raw = f.read_bytes()
            result = _scan_mp4_atoms(raw, 0, len(raw))
            if result:
                img_bytes, ext = result
                output_dir.mkdir(exist_ok=True)
                cover_path = output_dir / f"_extracted_cover{ext}"
                cover_path.write_bytes(img_bytes)
                return cover_path
        except OSError:
            continue
    return None


def extract_metadata_and_media(itlp_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    fallback_album = itlp_dir.stem
    fallback_artist = (
        itlp_dir.parent.parent.name if itlp_dir.parent.parent != itlp_dir.parent else ""
    )
    meta = {
        "album": fallback_album,
        "artist": fallback_artist,
    }
    tracks: list[dict] = []
    videos: list[dict] = []

    manifest_path = itlp_dir / "manifest.xml"
    if manifest_path.exists():
        try:
            tree = ET.parse(manifest_path)
            for elem in tree.getroot().iter():
                tag_name = elem.tag.split("}")[-1].lower()
                if tag_name == "library_item":
                    item_type = elem.attrib.get("type", "").lower()
                    xid = elem.attrib.get("xid", "").strip()
                    title = (elem.attrib.get("name") or elem.attrib.get("title") or "").strip()
                    if not xid:
                        continue
                    if item_type == "song" and not any(t["xid"] == xid for t in tracks):
                        tracks.append({"xid": xid, "title": title})
                    elif item_type == "video" and not any(v["xid"] == xid for v in videos):
                        videos.append({"xid": xid, "title": title, "src": "", "key": ""})
        except ET.ParseError as e:
            print(tr("warn_manifest", err=e))

    for js_file in itlp_dir.rglob("*.js"):
        if "_local_audio" in js_file.parts or "_local_media" in js_file.parts:
            continue
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
            vblock = videos_obj_m.group(1)
            for vm in re.finditer(r"""([a-zA-Z0-9_]+)\s*:\s*\{([^{}]*)\}""", vblock, re.DOTALL):
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
            vblock = videos_arr_m.group(1)
            for idx, vm in enumerate(re.finditer(r"""\{([^{}]*)\}""", vblock, re.DOTALL), start=1):
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


def resolve_best_cover(
    itlp_dir: Path, album_dir: Path, root_dir: Path, audio_files: list[Path]
) -> str | None:
    local_media_dir = itlp_dir / "_local_media"
    img_exts = {".jpg", ".jpeg", ".png", ".webp"}

    extracted = extract_m4a_cover(audio_files, local_media_dir)
    if extracted and extracted.is_file():
        print(tr("cover_extracted", filename=extracted.name))
        rel = extracted.relative_to(root_dir).as_posix()
        return "/".join(urllib.parse.quote(part) for part in rel.split("/"))

    album_imgs = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in img_exts]
    if album_imgs:
        album_imgs.sort(
            key=lambda p: (
                0 if any(k in p.stem.lower() for k in ("cover", "folder", "front")) else 1,
                -p.stat().st_size,
            )
        )
        rel = album_imgs[0].relative_to(root_dir).as_posix()
        return "/".join(urllib.parse.quote(part) for part in rel.split("/"))

    ui_blacklist = ("btn", "button", "icon", "sprite", "overlay", "text", "title", "rule", "arrow")
    lp_imgs = [
        p
        for p in itlp_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in img_exts
        and "_local_media" not in p.parts
        and not any(b in p.stem.lower() for b in ui_blacklist)
    ]
    if not lp_imgs:
        return None

    lp_imgs.sort(
        key=lambda p: (
            0 if any(k in p.stem.lower() for k in ("cover", "album", "artwork")) else 1,
            -p.stat().st_size,
        )
    )
    rel = lp_imgs[0].relative_to(root_dir).as_posix()
    return "/".join(urllib.parse.quote(part) for part in rel.split("/"))


def collect_audio_files(album_dir: Path) -> list[Path]:
    exts = {".m4a", ".mp3", ".aac", ".wav", ".ogg", ".flac"}
    files = []
    for p in album_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if (
            "_local_audio" in p.parts
            or "_local_media" in p.parts
            or any(part.lower().endswith(".itlp") for part in p.parts)
        ):
            continue
        files.append(p)
    return sorted(files, key=lambda p: p.name.lower())


def collect_video_files(album_dir: Path, itlp_dir: Path) -> list[Path]:
    exts = {".m4v", ".mp4", ".mov", ".webm"}
    files: list[Path] = []

    for p in album_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if (
            "_local_audio" in p.parts
            or "_local_media" in p.parts
            or any(part.lower().endswith(".itlp") for part in p.parts)
        ):
            continue
        files.append(p)

    for p in itlp_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if "_local_audio" in p.parts or "_local_media" in p.parts:
            continue
        files.append(p)

    return sorted(files, key=lambda p: p.name.lower())


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


def prepare_clean_html_source(itlp_dir: Path, index_html: Path) -> str:
    orig_bak = itlp_dir / "index.html.orig.bak"
    legacy_bak = itlp_dir / "index.html.bak"
    current_html = index_html.read_text(encoding="utf-8", errors="ignore")
    already_patched = "itlp-local-audio-shim" in current_html

    if not orig_bak.exists():
        if legacy_bak.exists():
            legacy_content = legacy_bak.read_text(encoding="utf-8", errors="ignore")
            if "itlp-local-audio-shim" not in legacy_content:
                shutil.copy2(legacy_bak, orig_bak)
                print(tr("bak_orig_from_legacy", src=legacy_bak.name, dst=orig_bak.name))
        elif not already_patched:
            shutil.copy2(index_html, orig_bak)
            print(tr("bak_orig_created", dst=orig_bak.name))

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_bak = itlp_dir / f"index.html.{stamp}.bak"
    shutil.copy2(index_html, ts_bak)
    print(tr("bak_prev_saved", dst=ts_bak.name))

    if orig_bak.exists():
        if already_patched:
            print(tr("info_rebuild"))
        return orig_bak.read_text(encoding="utf-8", errors="ignore")

    print(tr("warn_no_orig"))
    return re.sub(
        r"<!-- INJECTED BY patch_itlp(?:_batch)?\.py.*?-->.*?</script>\s*",
        "",
        current_html,
        flags=re.DOTALL,
    )


def build_shim_script(xid_entries: list[dict], video_entries: list[dict], home_href: str) -> str:
    """Builds the injected CSS/JS shim (floating player bar, video lightbox, iTunes API bridge)."""
    entries_json = json.dumps(xid_entries, indent=2, ensure_ascii=False)
    videos_json = json.dumps(video_entries, indent=2, ensure_ascii=False)
    home_json = json.dumps(home_href)

    return f"""<!-- INJECTED BY patch_itlp_batch.py -->
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

  /* Fullscreen Video Lightbox for Bonus Videos */
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
  const TRACKS = {entries_json};
  const VIDEOS = {videos_json};
  const HOME_HREF = {home_json};

  const IS_DE = /^de\b/i.test(navigator.language || "");
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

  function toSafeUrl(rawPath) {{
    return rawPath.split("/").map(encodeURIComponent).join("/");
  }}

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
    vplayer.src = toSafeUrl(videoObj.url);
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
    const safeUrl = toSafeUrl(trackObj.url);
    audio.src = safeUrl;
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
      <a id="itlp-fp-home" class="itlp-fp-btn" href="${{HOME_HREF}}" title="${{UI.homeTitle}}">⌂</a>
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
      addTracks: function(tracksOrChapter) {{
        append(tracksOrChapter);
      }},
      addTrack: function(trackOrChapter) {{
        append(trackOrChapter);
      }},
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
    findTracksByPersistentID: function(pid) {{
      return this.findTracksByXID(pid);
    }},
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

  function getActiveNavigationView() {{
    const views = Array.from(document.querySelectorAll("#navigation > div"));
    const visible = views.filter(el => {{
      const cs = window.getComputedStyle(el);
      return cs.display !== "none" && cs.visibility !== "hidden" && !el.classList.contains("tk-inactive");
    }});
    return visible.map(el => el.id || el.className).join("|");
  }}

  function navigateToHomeFallback() {{
    const controllers = [
      window.bookletController,
      window.appController,
      window.navigationController,
      window.TKNavigationController && window.TKNavigationController.sharedNavigationController
    ].filter(Boolean);

    for (const ctrl of controllers) {{
      if (typeof ctrl.navigateTo === "function") {{
        try {{ ctrl.navigateTo("home"); return true; }} catch (_) {{}}
      }}
      if (typeof ctrl.popView === "function") {{
        try {{ ctrl.popView(); return true; }} catch (_) {{}}
      }}
      if (typeof ctrl.popToRootView === "function") {{
        try {{ ctrl.popToRootView(); return true; }} catch (_) {{}}
      }}
    }}

    const homeView = document.querySelector("#navigation > #home, #home");
    if (homeView) {{
      document.querySelectorAll("#navigation > div").forEach(v => {{
        if (v !== homeView) {{
          v.classList.add("tk-inactive");
          v.style.display = "none";
        }}
      }});
      homeView.classList.remove("tk-inactive");
      homeView.style.display = "block";
      homeView.style.visibility = "visible";
      homeView.style.opacity = "1";
      const menuEl = homeView.querySelector("#menu");
      if (menuEl) {{
        menuEl.classList.remove("tk-inactive");
        menuEl.style.visibility = "visible";
        menuEl.style.opacity = "1";
      }}
      return true;
    }}
    return false;
  }}

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

    // 1. Subpage Home / Back button (`.home`, `#home .image-fader`, `.back`)
    const homeBtn = target.closest(".home, #home .image-fader, .back");
    if (homeBtn && !homeBtn.closest("#menu")) {{
      const beforeView = getActiveNavigationView();
      setTimeout(() => {{
        const afterView = getActiveNavigationView();
        if (afterView === beforeView) {{
          navigateToHomeFallback();
        }}
      }}, 220);
      return;
    }}

    // 2. Main menu Videos button (`.btn_videos`) -> Open videos view first, fallback to player if view missing
    const videosMenuBtn = target.closest(".btn_videos");
    if (videosMenuBtn) {{
      const beforeView = getActiveNavigationView();
      setTimeout(() => {{
        const afterView = getActiveNavigationView();
        const overlayOpen = document.getElementById("itlp-video-overlay")?.classList.contains("open");
        if (!overlayOpen && afterView === beforeView) {{
          if (window.bookletController && typeof window.bookletController.navigateTo === "function") {{
            try {{ window.bookletController.navigateTo("videos"); }} catch (_) {{}}
          }}
          const videosView = document.querySelector("#navigation > #videos, #videos");
          if (videosView) {{
            const homeView = document.querySelector("#navigation > #home, #home");
            if (homeView) {{
              homeView.classList.add("tk-inactive");
              homeView.style.display = "none";
            }}
            videosView.classList.remove("tk-inactive");
            videosView.style.display = "block";
            videosView.style.visibility = "visible";
            videosView.style.opacity = "1";
          }} else if (VIDEOS.length > 0) {{
            openVideoOverlay(VIDEOS[0]);
          }}
        }}
      }}, 220);
      return;
    }}

    // 3. Play All / Audio play triggers (.play_all, .btn_anhoeren, or general play actions)
    const playTrigger = target.closest(".play_all, .btn_anhoeren, #play_all");
    if (playTrigger) {{
      if (TRACKS.length > 0) {{
        playTrackObj(TRACKS[0], TRACKS);
      }}
      return;
    }}

    // 4. Individual video buttons inside videos.html (`#video01` .. `#video10` or `.video-button`)
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

def link_or_copy_file(src: Path, target: Path, copy_files: bool) -> None:
    if target.exists():
        return
    if copy_files:
        shutil.copy2(src, target)
    else:
        try:
            target.symlink_to(src)
        except OSError:
            shutil.copy2(src, target)


def process_single_itlp(itlp_dir: Path, root_dir: Path, copy_files: bool) -> dict | None:
    print("\n==========================================")
    print(tr("processing", path=itlp_dir))
    index_html = itlp_dir / "index.html"
    if not index_html.exists():
        print(tr("skip_no_index"))
        return None

    album_dir = itlp_dir.parent
    meta, tracks, videos = extract_metadata_and_media(itlp_dir)
    audio_files = collect_audio_files(album_dir)
    video_files = collect_video_files(album_dir, itlp_dir)

    display_title = f"{meta['artist']} – {meta['album']}" if meta["artist"] else meta["album"]
    print(tr("album_label", title=display_title))
    print(tr("audio_stats", lp_count=len(tracks), file_count=len(audio_files)))
    print(tr("video_stats", lp_count=len(videos), file_count=len(video_files)))

    if not audio_files and not video_files:
        print(tr("skip_no_media"))
        return None

    local_audio_dir = itlp_dir / "_local_audio"
    local_audio_dir.mkdir(exist_ok=True)
    for f in audio_files:
        link_or_copy_file(f, local_audio_dir / f.name, copy_files)

    xid_entries: list[dict] = []
    if tracks:
        matched = match_items_to_files(tracks, audio_files)
        for t in tracks:
            f = matched.get(t["xid"])
            if f:
                xid_entries.append({
                    "xid": t["xid"],
                    "title": t["title"] or f.stem,
                    "url": f"_local_audio/{f.name}",
                })
                print(tr("audio_ok", name=t["title"] or t["xid"], filename=f.name))
            else:
                print(tr("audio_miss", name=t["title"] or t["xid"]))
    else:
        for idx, f in enumerate(audio_files, start=1):
            xid_entries.append({
                "xid": f"local:track:{idx}",
                "title": f.stem,
                "url": f"_local_audio/{f.name}",
            })

    video_entries: list[dict] = []
    if video_files or videos:
        local_media_dir = itlp_dir / "_local_media"
        matched_videos = match_items_to_files(videos, video_files) if videos else {}

        for v in videos:
            if v.get("src") and (itlp_dir / v["src"]).is_file():
                video_entries.append({
                    "xid": v["xid"],
                    "title": v["title"] or Path(v["src"]).stem,
                    "url": v["src"],
                    "src": v["src"],
                    "key": v.get("key", ""),
                })
                print(tr("video_ok_int", name=v["title"], filename=v["src"]))
                continue

            vf = matched_videos.get(v["xid"])
            if vf:
                if itlp_dir in vf.parents:
                    rel_url = vf.relative_to(itlp_dir).as_posix()
                else:
                    local_media_dir.mkdir(exist_ok=True)
                    link_or_copy_file(vf, local_media_dir / vf.name, copy_files)
                    rel_url = f"_local_media/{vf.name}"
                video_entries.append({
                    "xid": v["xid"],
                    "title": v["title"] or vf.stem,
                    "url": rel_url,
                    "src": v.get("src", ""),
                    "key": v.get("key", ""),
                })
                print(tr("video_ok", name=v["title"] or v["xid"], filename=vf.name))
            else:
                print(tr("video_miss", name=v["title"] or v["xid"]))

        used_video_names = {Path(ve["url"]).name for ve in video_entries}
        for idx, vf in enumerate(video_files, start=1):
            if vf.name not in used_video_names:
                if itlp_dir in vf.parents:
                    rel_url = vf.relative_to(itlp_dir).as_posix()
                else:
                    local_media_dir.mkdir(exist_ok=True)
                    link_or_copy_file(vf, local_media_dir / vf.name, copy_files)
                    rel_url = f"_local_media/{vf.name}"
                video_entries.append({
                    "xid": f"local:video:{idx}",
                    "title": vf.stem,
                    "url": rel_url,
                    "src": "",
                    "key": f"video{idx:02d}",
                })
                print(tr("video_added", filename=vf.name))

    if not xid_entries and not video_entries:
        print(tr("skip_unlinked"))
        return None

    rel_home = os.path.relpath(root_dir / "index.html", itlp_dir).replace("\\", "/")

    base_html = prepare_clean_html_source(itlp_dir, index_html)
    base_html = re.sub(
        r'<!DOCTYPE\s+HTML\s+PUBLIC\s+"-//W3C//DTD HTML 4\.01//EN"[^>]*>',
        "<!DOCTYPE html>",
        base_html,
        count=1,
        flags=re.IGNORECASE,
    )
    shim = build_shim_script(xid_entries, video_entries, rel_home)

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

    index_html.write_text(patched_html, encoding="utf-8")
    print(tr("done_single"))

    rel_index = index_html.relative_to(root_dir).as_posix()
    href = "/".join(urllib.parse.quote(part) for part in rel_index.split("/"))
    cover_href = resolve_best_cover(itlp_dir, album_dir, root_dir, audio_files)

    return {
        "album": meta["album"],
        "artist": meta["artist"],
        "track_count": len(xid_entries),
        "video_count": len(video_entries),
        "href": href,
        "cover_href": cover_href,
    }


def generate_root_index(root_dir: Path, albums: list[dict]) -> None:
    root_index = root_dir / "index.html"
    if root_index.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = root_dir / f"index.html.{stamp}.bak"
        shutil.copy2(root_index, bak)
        print(tr("bak_root_saved", dst=bak.name))

    albums_sorted = sorted(albums, key=lambda a: (a["artist"].lower(), a["album"].lower()))
    count = len(albums_sorted)
    cards_html = []

    for item in albums_sorted:
        album_esc = html_lib.escape(item["album"])
        artist_raw = item["artist"]
        artist_esc = html_lib.escape(artist_raw) if artist_raw else ""
        artist_attr = ' data-unknown="1"' if not artist_raw else ""
        search_attr = html_lib.escape(f"{artist_raw} {item['album']}".lower())

        cover_block = (
            f'<img src="{item["cover_href"]}" alt="{album_esc}" loading="lazy">'
            if item["cover_href"]
            else '<div class="no-cover">♫</div>'
        )
        t_cnt = item["track_count"]
        v_cnt = item.get("video_count", 0)

        meta_en = f"{t_cnt} Track{'s' if t_cnt != 1 else ''}" + (
            f" · {v_cnt} Video{'s' if v_cnt != 1 else ''}" if v_cnt else ""
        ) + " · iTunes LP"
        meta_de = f"{t_cnt} Titel" + (
            f" · {v_cnt} Video{'s' if v_cnt > 1 else ''}" if v_cnt else ""
        ) + " · iTunes LP"

        cards_html.append(
            f"""
            <a class="album-card" href="{item['href']}" data-search="{search_attr}">
              <div class="cover-wrap">{cover_block}</div>
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
    h1 {{
      margin: 0;
      font-size: 24px;
      font-weight: 700;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 14px;
      margin-top: 4px;
    }}
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
    #search:focus {{
      border-color: var(--accent);
    }}
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
    .cover-wrap img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .no-cover {{
      font-size: 48px;
      color: #4b5563;
    }}
    .info {{
      padding: 12px 14px 14px;
    }}
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
    .meta {{
      color: var(--accent);
      font-size: 11px;
      margin-top: 8px;
      font-weight: 500;
    }}
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
      const isDe = /^de\b/i.test(navigator.language || "");
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
    root_index.write_text(page, encoding="utf-8")
    print(tr("overview_created", path=root_index))


def main() -> None:
    global ACTIVE_LANG

    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--lang", choices=["de", "en"], default=None)
    pre_args, _ = pre_parser.parse_known_args()
    if pre_args.lang:
        ACTIVE_LANG = pre_args.lang

    parser = argparse.ArgumentParser(description=tr("cli_desc"))
    parser.add_argument(
        "root_path",
        nargs="?",
        default=".",
        type=Path,
        help=tr("cli_root_help"),
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help=tr("cli_nocopy_help"),
    )
    parser.add_argument(
        "--lang",
        choices=["de", "en"],
        default=ACTIVE_LANG,
        help=tr("cli_lang_help"),
    )
    args = parser.parse_args()
    ACTIVE_LANG = args.lang

    root = args.root_path.resolve()
    if not root.exists():
        sys.exit(tr("err_path_missing", path=root))

    single_mode = root.is_dir() and root.name.lower().endswith(".itlp")
    if single_mode:
        itlp_dirs = [root]
        index_root = root.parent.parent if root.parent.parent != root.parent else root.parent
    else:
        itlp_dirs = sorted(
            [p for p in root.rglob("*.itlp") if p.is_dir()],
            key=lambda p: str(p).lower(),
        )
        index_root = root

    if not itlp_dirs:
        sys.exit(tr("err_no_itlp", path=root))

    print(tr("found_packages", count=len(itlp_dirs)))
    patched_albums: list[dict] = []
    for itlp_dir in itlp_dirs:
        result = process_single_itlp(itlp_dir, index_root, copy_files=not args.no_copy)
        if result:
            patched_albums.append(result)

    if patched_albums and not single_mode:
        generate_root_index(index_root, patched_albums)

    print("\n==========================================")
    print(tr("summary", patched=len(patched_albums), total=len(itlp_dirs)))


if __name__ == "__main__":
    main()