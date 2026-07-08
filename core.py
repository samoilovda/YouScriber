"""
YouScriber - Core Business Logic Layer

This module contains the core ETL business logic for YouTube subtitle extraction,
cleaning, and LLM-RAG preparation. It is used by the CustomTkinter desktop
interface (gui.py) and by the batch CLI script (grab_subs.py).

RATE-LIMIT / ANTI-BOT HANDLING:
- Browser cookie authentication is supported but disabled by default
  (browser="None"); pass a browser name or a cookies.txt path to enable it.
- A short randomized delay (1-2 seconds) is inserted between video downloads.
- Retries and the android_vr player client help avoid PO-token/429 errors.
"""

import yt_dlp
import pathlib
import re
import shutil
import tempfile
import json
import os
import fnmatch
import random
import time


# =============================================================================
# CONFIGURATION & SESSION MANAGEMENT
# =============================================================================

BASE_TEMP_DIR = pathlib.Path(tempfile.gettempdir()) / "youscriber"

# Placeholder transcript text written when a video has no usable subtitle
# track. Exported so callers (e.g. grab_subs.py) can detect this case without
# duplicating the literal string.
NO_SUBTITLES_PLACEHOLDER = "[No subtitles available — not uploaded by creator or blocked by YouTube]"

# Default subtitle language preferences (configurable via environment variables)
# Prefer the original-language auto-caption track (-orig) first, then plain
# language tracks, then English. This works for Russian-language channels as
# well as English ones; yt-dlp simply picks whichever tracks exist.
DEFAULT_SUB_LANGS = os.getenv("YOUSCRIBER_SUB_LANGS", "ru-orig,ru,en-orig,en")


def ensure_session_dir(session_id: str) -> pathlib.Path:
    """Create and return a session directory if it doesn't exist."""
    session_dir = BASE_TEMP_DIR / session_id
    if not session_dir.exists():
        session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def cleanup_old_sessions(max_age_days: int = 7) -> None:
    """
    Remove session directories under BASE_TEMP_DIR older than max_age_days.

    Session dirs are never cleaned up automatically otherwise, so long-running
    use of the app would let temp files grow unbounded. Safe to call at
    startup; failures for individual dirs are ignored (e.g. file in use).
    """
    if not BASE_TEMP_DIR.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for session_dir in BASE_TEMP_DIR.iterdir():
        try:
            if session_dir.is_dir() and session_dir.stat().st_mtime < cutoff:
                shutil.rmtree(session_dir, ignore_errors=True)
        except OSError:
            pass


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

class CallbackLogger:
    """Simple callback logger for yt-dlp."""
    
    def __init__(self, error_callback=None, status_callback=None):
        self.error_callback = error_callback
        self.status_callback = status_callback
    
    def debug(self, msg):
        pass

    def _emit_status(self, msg):
        """Emit a status message while tolerating 1-arg or 2-arg callbacks."""
        if not self.status_callback:
            return
        try:
            self.status_callback(msg, 0.0)
        except TypeError:
            self.status_callback(msg)
    
    def warning(self, msg):
        self._emit_status(msg)
    
    def error(self, msg):
        if self.error_callback:
            self.error_callback(msg)
    
    def info(self, msg):
        self._emit_status(msg)


def clean_vtt_content(content: str) -> str:
    """
    Clean VTT/SRT subtitle content by removing timestamps, tags, and deduplicating.
    
    Args:
        content: Raw VTT or SRT subtitle content
    
    Returns:
        Cleaned text content
    """
    # 1. Remove entire WEBVTT header block
    content = re.sub(r'^WEBVTT\b.*?(?=\n\n)', '', content, flags=re.DOTALL)
    
    # 2. Remove timestamp lines (VTT and SRT formats)
    content = re.sub(r'^\d{2}:\d{2}:\d{2}[\.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[\.,]\d{3}.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^\d{2}:\d{2}[\.,]\d{3}\s*-->\s*\d{2}:\d{2}[\.,]\d{3}.*$', '', content, flags=re.MULTILINE)
    
    # 3. Remove inline VTT tags
    content = re.sub(r'<[^>]+>', '', content)
    
    # 4. Remove SRT numeric indices
    content = re.sub(r'^\d+\s*$', '', content, flags=re.MULTILINE)
    
    # 5. Process line by line: deduplicate rolling-window duplicates
    lines = content.splitlines()
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if cleaned_lines:
            prev = cleaned_lines[-1]
            if line.startswith(prev):
                cleaned_lines[-1] = line
                continue
            if prev.startswith(line) or line == prev:
                continue
        
        cleaned_lines.append(line)
    
    return "\n".join(cleaned_lines)


def sanitize_description(description: str) -> str:
    """
    Sanitize video description by removing URLs and excessive formatting.
    
    Args:
        description: Raw video description
    
    Returns:
        Sanitized description string
    """
    description = re.sub(r'https?://\S+', '', description)
    description = re.sub(r'www\.\S+', '', description)
    description = re.sub(r'#\w+', '', description)
    description = re.sub(r'\n{3,}', '\n\n', description)
    return description.strip()


def format_for_llm(metadata: dict, transcript: str) -> str:
    """
    Format video metadata and transcript for LLM RAG pipelines.
    
    Args:
        metadata: Video metadata dictionary from yt-dlp
        transcript: Cleaned subtitle text content
    
    Returns:
        Formatted string ready for LLM consumption
    """
    title = metadata.get('title', 'Unknown Title')
    url = metadata.get('webpage_url', metadata.get('original_url', 'Unknown URL'))
    upload_date = metadata.get('upload_date', 'Unknown Date')
    description = sanitize_description(metadata.get('description', ''))
    
    # Format upload date (YYYYMMDD -> YYYY-MM-DD)
    if len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    
    return f"# TITLE: {title}\n# URL: {url}\n# PUBLISH DATE: {upload_date}\n\n## METADATA & CHAPTERS\n{description}\n\n## TRANSCRIPT\n{transcript}\n"


# =============================================================================
# YOUTUBE VIDEO LIST EXTRACTION
# =============================================================================

def fetch_video_list(urls: list, browser: str = "None", player_client: str = "android_vr",
                     progress_callback=None, error_callback=None, cancel_event=None) -> list:
    """
    Fetch metadata for a list of YouTube URLs (videos, playlists, or channels).
    
    Args:
        urls: List of YouTube URLs to fetch
        browser: Browser name for cookie extraction (default: 'None' to disable)
        player_client: Player client to impersonate
        progress_callback: Callback(status_text, percentage) for progress updates
        error_callback: Callback(status_text, error_message) for error updates
    
    Returns:
        List of video info dictionaries with title, url, id, playlist info
    
    Note: This function runs synchronously by default. For threading, see
        download_video_subtitles() which handles async execution.
    """
    logger = CallbackLogger(error_callback=error_callback, status_callback=progress_callback)
    
    flat_opts = {
        'extract_flat': True,
        'quiet': True,
        'ignoreerrors': True,
        'extractor_retries': 5,
        'remote_components': ['ejs:github'],
        'extractor_args': {
            'youtube': {
                'player_client': [player_client],
                'fetch_pot': ['auto'],
            }
        },
        'logger': logger,
    }
    
    if browser and browser != "None" and browser.endswith('.txt'):
        flat_opts['cookiefile'] = browser
    elif browser and browser != "None":
        flat_opts['cookiesfrombrowser'] = (browser,)
    
    video_list = []
    
    if progress_callback:
        progress_callback("Fetching metadata using yt-dlp...", 0.0)
    
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        for url in urls:
            if cancel_event and cancel_event.is_set():
                if error_callback:
                    error_callback("Fetch cancelled by user.")
                break
            try:
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                
                if 'entries' in info and info['entries'] is not None:
                    playlist_title = info.get('title', 'Unknown Playlist')
                    channel_name = info.get('uploader', info.get('uploader_id', info.get('channel', 'Unknown Channel')))
                    
                    for entry in info['entries']:
                        if cancel_event and cancel_event.is_set():
                            break
                        if entry:
                            safe_entry = {
                                'title': entry.get('title', 'Unknown Title'),
                                'id': entry.get('id', ''),
                                'playlist_title': playlist_title,
                                'channel_name': channel_name
                            }
                            if not entry.get('url') and entry.get('id'):
                                safe_entry['url'] = f"https://www.youtube.com/watch?v={entry['id']}"
                            elif not entry.get('url') and entry.get('webpage_url'):
                                safe_entry['url'] = entry.get('webpage_url')
                            else:
                                safe_entry['url'] = entry.get('url', '')
                            
                            if safe_entry['id'] or safe_entry['url']:
                                video_list.append(safe_entry)
                else:
                    channel_name = info.get('uploader', info.get('uploader_id', info.get('channel', 'Unknown Channel')))
                    safe_entry = {
                        'title': info.get('title', 'Unknown Title'),
                        'id': info.get('id', ''),
                        'playlist_title': info.get('playlist_title', 'Single Video'),
                        'channel_name': channel_name
                    }
                    if not info.get('url') and info.get('webpage_url'):
                        safe_entry['url'] = info.get('webpage_url')
                    elif not info.get('url') and info.get('id'):
                        safe_entry['url'] = f"https://www.youtube.com/watch?v={info['id']}"
                    else:
                        safe_entry['url'] = info.get('url', '')
                    
                    if safe_entry['id'] or safe_entry['url']:
                        video_list.append(safe_entry)
                        
            except Exception as e:
                if error_callback:
                    error_callback(f"Error gathering metadata for {url}: {str(e)}")
    
    return video_list


# =============================================================================
# EXCEPTIONS
# =============================================================================

class OperationCancelled(Exception):
    """Raised when the current operation is cancelled by user request."""
    pass


# =============================================================================
# YOUTUBE SUBTITLE DOWNLOAD
# =============================================================================

def _safe_name(text: str) -> str:
    """Reduce an arbitrary string to a filesystem-safe path segment."""
    return "".join(c for c in text if c.isalnum() or c in " -_()").strip()


def _ydlp_download(url: str, out_dir: pathlib.Path, browser: str,
                   sub_langs: str = DEFAULT_SUB_LANGS, player_client: str = "android_vr"):
    """
    Fetch metadata + best available subtitle tracks in a single yt-dlp call.

    A single request covering all requested languages avoids the request-storm
    that triggers YouTube's HTTP 429 rate limiting. ``ignoreerrors`` keeps a
    failure on one language (e.g. a 429 on a secondary track) from discarding
    the rest of the download. Returns the metadata dict from extract_info.
    """
    langs = [lang.strip() for lang in sub_langs.split(",") if lang.strip()]
    opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt",
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "remote_components": ["ejs:github"],
        "extractor_args": {"youtube": {"player_client": [player_client], "fetch_pot": ["auto"]}},
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
    }
    if browser and browser != "None":
        if browser.endswith(".txt"):
            opts["cookiefile"] = browser
        else:
            opts["cookiesfrombrowser"] = (browser,)

    with yt_dlp.YoutubeDL(opts) as ydl:
        # extract_info(download=True) both downloads the files and returns the
        # metadata dict, so the caller knows the exact video id/title to locate
        # the written files (several videos may share one session dir).
        return ydl.extract_info(url, download=True)


def _find_downloaded_files(video_out_dir: pathlib.Path, video_id: str, title: str) -> list:
    """Find all downloaded files matching video ID or title."""
    files_found = list(video_out_dir.glob(f"*{video_id}*")) if video_id else []
    
    if not files_found:
        safe_t = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        if safe_t:
            files_found = list(video_out_dir.glob(f"*{safe_t}*"))
    
    return files_found


def _pick_best_sub_file(files_found: list) -> pathlib.Path | None:
    """Select the best subtitle file from available downloads."""
    subtitle_files = [f for f in files_found if f.suffix in (".vtt", ".srt")]
    if not subtitle_files:
        return None
    
    subtitle_files = [f for f in subtitle_files if ".live_chat." not in f.name]
    if not subtitle_files:
        return None
    
    ranked_patterns = (
        "*.ru-orig.*",
        "*.ru.*",
        "*.en-orig.*",
        "*.en.*",
        "*.uk.*",
        "*.vtt",
        "*.srt",
    )
    for pattern in ranked_patterns:
        for f in subtitle_files:
            if fnmatch.fnmatch(f.name.lower(), pattern):
                return f
    return subtitle_files[0]


def _has_usable_subtitles(sub_file: pathlib.Path | None) -> bool:
    """Check if a subtitle file contains usable content."""
    if not sub_file or not sub_file.exists():
        return False
    if sub_file.stat().st_size < 64:
        return False
    try:
        raw = sub_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    cleaned = clean_vtt_content(raw).strip()
    return len(cleaned) > 25


def download_video_subtitles(url: str, session_id: str, browser: str = "None",
                              player_client: str = "android_vr", sub_langs: str = DEFAULT_SUB_LANGS,
                              group_by_playlist: bool = True, progress_callback=None,
                              error_callback=None, cancel_event=None) -> list:
    """
    Download subtitles + metadata for a single YouTube video and write a
    cleaned, LLM-ready transcript.

    Uses one yt-dlp call (see _ydlp_download), cleans the best available
    subtitle track, and writes a .txt under the session's ``processed`` dir.

    Args:
        url: YouTube video URL
        session_id: Session directory identifier
        browser: Browser for cookie extraction (default: 'None' to disable)
        player_client: Player client to impersonate (default: 'android_vr')
        sub_langs: Subtitle language pattern (default: DEFAULT_SUB_LANGS)
        group_by_playlist: Whether to group output by playlist/channel
        progress_callback: Callback(status_text, percentage) for progress updates
        error_callback: Callback(status_text, error_message) for error updates
    
    Returns:
        List of paths to processed subtitle files
    
    Note: This function internally runs on the main thread. For threading,
        wrap it with threading.Thread().
    """
    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("Download cancelled before start.")

    session_dir = ensure_session_dir(session_id)
    title = "YouTube Video"  # Updated from metadata below

    if progress_callback:
        progress_callback(f"Downloading subtitles for '{title}'...", 0.1)

    # One yt-dlp call fetches metadata + the best available subtitle track.
    # Retried a few times: transient curl/TLS/network hiccups can make a single
    # call return no metadata or an empty subtitle even when the video is fine.
    meta_data = None
    sub_file = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled("Download cancelled after fetch.")
        try:
            meta_data = _ydlp_download(url, session_dir, browser, sub_langs, player_client)
        except OperationCancelled:
            raise
        except Exception as e:
            meta_data = None
            if error_callback and attempt == max_attempts:
                error_callback(f"yt-dlp error for {url}: {e}")

        if meta_data:
            files_found = _find_downloaded_files(
                session_dir, meta_data.get('id', ''), meta_data.get('title', ''))
            sub_file = _pick_best_sub_file(files_found)
            if _has_usable_subtitles(sub_file):
                break  # got usable subtitles — stop retrying
        if attempt < max_attempts:
            time.sleep(2 * attempt)  # brief backoff before retrying

    if not meta_data:
        if error_callback:
            error_callback(f"Could not retrieve metadata for {url} — skipping.")
        return []

    title = meta_data.get('title', 'Unknown Title')
    pl_title = meta_data.get('playlist_title', '')
    channel_name = meta_data.get('uploader', meta_data.get('uploader_id', ''))

    if _has_usable_subtitles(sub_file):
        transcript_text = clean_vtt_content(sub_file.read_text(encoding='utf-8', errors='ignore'))
    else:
        transcript_text = NO_SUBTITLES_PLACEHOLDER
        if error_callback:
            error_callback(f"No usable subtitles for '{title}'.")

    final_content = format_for_llm(meta_data, transcript_text)

    output_dir = session_dir / "processed"
    if group_by_playlist:
        if channel_name and channel_name not in ('Unknown Channel', ''):
            output_dir = output_dir / _safe_name(channel_name)
        if pl_title:
            output_dir = output_dir / _safe_name(pl_title)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_path = output_dir / f"{_safe_name(title)}.txt"
    final_path.write_text(final_content, encoding='utf-8')

    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("Download cancelled after file write.")
    time.sleep(random.uniform(1.0, 2.0))  # be polite between videos

    if progress_callback:
        progress_callback(f"Done! Harvested '{title}'.", 1.0)

    return [final_path]


# =============================================================================
# LOCAL FILE PROCESSING
# =============================================================================

def process_local_files(file_paths: list, session_id: str, progress_callback=None,
                        error_callback=None, cancel_event=None) -> list:
    """
    Process local subtitle files (.vtt, .srt, .txt) with their metadata.
    
    Args:
        file_paths: List of file paths (strings)
        session_id: Session directory identifier
        progress_callback: Callback(status_text, percentage) for progress updates
        error_callback: Callback(status_text, error_message) for error updates
    
    Returns:
        List of paths to processed files
    """
    session_dir = ensure_session_dir(session_id)
    output_dir = session_dir / "local_processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    file_map = {}
    total_files = len(file_paths)
    
    if progress_callback:
        progress_callback(f"Analyzing {total_files} local files...", 0.0)
    
    for path_str in file_paths:
        path = pathlib.Path(path_str)
        stem = path.stem
        if stem not in file_map:
            file_map[stem] = {}
        
        if path.suffix == '.json':
            file_map[stem]['json'] = path
        elif path.suffix in ['.vtt', '.srt', '.txt']:
            file_map[stem]['sub'] = path
    
    processed_files = []
    count = 0
    total_stems = len(file_map)
    
    for stem, files in file_map.items():
        if cancel_event and cancel_event.is_set():
            if error_callback:
                error_callback("Local processing cancelled by user.")
            break

        if progress_callback:
            progress_callback(f"Processing local file: {stem}", count / max(1, total_stems))
            
        if 'sub' in files:
            sub_file = files['sub']
            try:
                with open(sub_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                cleaned_text = clean_vtt_content(content)
                
                metadata = {}
                if 'json' in files:
                    try:
                        with open(files['json'], 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                    except Exception as e:
                        if error_callback:
                            error_callback(f"Could not parse JSON for {stem}: {e}")
                
                if not metadata:
                    metadata = {
                        'title': stem,
                        'original_url': 'Local File',
                        'upload_date': 'Unknown',
                        'description': 'Imported from local file.'
                    }
                
                final_content = format_for_llm(metadata, cleaned_text)
                final_path = output_dir / f"{stem}.txt"
                with open(final_path, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                processed_files.append(final_path)
            except Exception as e:
                if error_callback:
                    error_callback(f"Failed to process {stem}: {str(e)}")
        count += 1
    
    if progress_callback:
        if cancel_event and cancel_event.is_set():
            progress_callback("Local processing cancelled.", 1.0)
        else:
            progress_callback("Finished processing local files.", 1.0)
    
    return processed_files


# =============================================================================
# FILE MERGING
# =============================================================================

def merge_files(files: list, strategy: str, session_dir: pathlib.Path) -> list:
    """
    Merge processed files based on the selected strategy.
    
    Args:
        files: List of file paths to merge
        strategy: Merge strategy ('No Merge', 'One File', 'Medium Chunks (~50k chars)', 'Large Chunks (~200k chars)')
        session_dir: Session directory for output
    
    Returns:
        List of paths to merged output files
    """
    if not files or strategy == "No Merge":
        return files
    
    sorted_files = sorted(files, key=lambda p: p.name)
    merged_output_dir = session_dir / "merged"
    merged_output_dir.mkdir(parents=True, exist_ok=True)
    
    CHUNK_LIMITS = {
        "Medium Chunks (~50k chars)": 50_000,
        "Large Chunks (~200k chars)": 200_000
    }
    
    if strategy == "One File":
        output_file = merged_output_dir / "All_Processed_Videos.txt"
        with open(output_file, 'w', encoding='utf-8') as outfile:
            for i, fpath in enumerate(sorted_files):
                if i > 0:
                    outfile.write("\n\n" + "="*40 + "\n\n")
                with open(fpath, 'r', encoding='utf-8') as infile:
                    outfile.write(infile.read())
        return [output_file]
    
    elif strategy in CHUNK_LIMITS:
        limit = CHUNK_LIMITS[strategy]
        chunks = []
        current_chunk_idx = 1
        current_char_count = 0
        current_chunk_content = []
        
        def save_chunk(idx, content_list):
            if not content_list: return None
            fname = merged_output_dir / f"Merged_Batch_{idx:03d}.txt"
            with open(fname, 'w', encoding='utf-8') as f:
                f.write(("\n\n" + "="*40 + "\n\n").join(content_list))
            return fname
        
        for fpath in sorted_files:
            with open(fpath, 'r', encoding='utf-8') as infile:
                text = infile.read()
                text_len = len(text)
                
                if current_char_count + text_len > limit and current_chunk_content:
                    chunks.append(save_chunk(current_chunk_idx, current_chunk_content))
                    current_chunk_idx += 1
                    current_chunk_content = []
                    current_char_count = 0
                
                current_chunk_content.append(text)
                current_char_count += text_len
        
        if current_chunk_content:
            chunks.append(save_chunk(current_chunk_idx, current_chunk_content))
            
        return [c for c in chunks if c]
    
    return files


def download_videos(video_list: list, group_by_playlist: bool, session_id: str, browser: str = "None",
                    progress_callback=None, status_callback=None, error_callback=None, cancel_event=None) -> list:
    """
    Downloads subtitles for a list of videos by delegating to download_video_subtitles.
    """
    processed_files = []
    total_videos = len(video_list)
    
    for i, video in enumerate(video_list):
        if cancel_event and cancel_event.is_set():
            if error_callback:
                error_callback("Download cancelled by user.")
            break
            
        url = video.get('url')
        if not url:
            continue
            
        title = video.get('title', 'Unknown Title')
        
        if progress_callback:
            progress_callback(f"Processing {i+1}/{total_videos}: {title}", i / max(1, total_videos))
            
        try:
            files = download_video_subtitles(
                url=url,
                session_id=session_id,
                browser=browser,
                player_client="android_vr",
                sub_langs=DEFAULT_SUB_LANGS,
                group_by_playlist=group_by_playlist,
                progress_callback=status_callback,
                error_callback=error_callback,
                cancel_event=cancel_event
            )
            if files:
                processed_files.extend(files)
        except OperationCancelled:
            if error_callback:
                error_callback("Download cancelled.")
            break
        except Exception as e:
            if error_callback:
                error_callback(f"Failed to process {title}: {e}")
                
    if progress_callback and not (cancel_event and cancel_event.is_set()):
        progress_callback("All downloads complete!", 1.0)
        
    return processed_files
