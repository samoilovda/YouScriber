"""
YouScriber - Shared Business Logic Layer

This module contains the core ETL business logic for YouTube subtitle extraction,
cleaning, and LLM-RAG preparation. It is shared between both the Streamlit web
interface and the CustomTkinter desktop interface.

PRODUCTION ANTI-BAN CONFIGURATION:
- Uses Safari browser cookies for authentication
- Implements randomized sleep intervals (3-8 seconds) to avoid rate limiting
- Handles YouTube's anti-bot measures including PO tokens and 429 errors
"""

import yt_dlp
import pathlib
import re
import zipfile
import io
import tempfile
import json
import shutil
import traceback
import subprocess
import os
import sys
import fnmatch
import random
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


# =============================================================================
# CONFIGURATION & SESSION MANAGEMENT
# =============================================================================

BASE_TEMP_DIR = pathlib.Path(tempfile.gettempdir()) / "youscriber"

# Default subtitle language preferences (configurable via environment variables)
DEFAULT_SUB_LANGS = os.getenv("YOUSCRIBER_SUB_LANGS", "en.*,en")
SECONDARY_SUB_LANGS = os.getenv("YOUSCRIBER_SECONDARY_SUB_LANGS", "ru.*,ru,uk.*,uk")
FALLBACK_SUB_LANGS = "all,-live_chat"


def ensure_session_dir(session_id: str) -> pathlib.Path:
    """Create and return a session directory if it doesn't exist."""
    session_dir = BASE_TEMP_DIR / session_id
    if not session_dir.exists():
        session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def clear_session_dir(session_id: str):
    """Clear and recreate a session directory."""
    session_dir = BASE_TEMP_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)


# =============================================================================
# ANTI-BAN YOUTUBE EXTRACTION CONFIGURATION
# =============================================================================

def get_yt_dlp_opts(browser: str = "None", player_client: str = "android_vr",
                    sub_langs: str = DEFAULT_SUB_LANGS, impersonate_target: str = "") -> dict:
    """
    Get yt-dlp options with production-level anti-ban configuration.
    
    CRITICAL ANTI-BAN SETTINGS:
    - cookiesfrombrowser: Uses selected browser cookies to bypass bot detection
    - sleep_interval_requests: Randomized 3-8 second delay between requests
    - min_sleep / max_sleep: Range for exponential backoff
    
    Args:
        browser: Browser name for cookie extraction (default: 'None' to disable)
        player_client: Player client to impersonate (android_vr, web_safari, etc.)
        sub_langs: Subtitle language pattern
        impersonate_target: Target browser for impersonation
    
    Returns:
        Dictionary of yt-dlp options ready for YoutubeDL()
    """
    # Resolve extractor args based on environment or defaults
    env_override = os.getenv("YOUSCRIBER_YT_EXTRACTOR_ARGS", "").strip()
    if env_override:
        extractor_args = {"youtube": env_override}
    else:
        extractor_args = {
            "youtube": {
                "player_client": [player_client],
                "fetch_pot": ["auto"]
            }
        }
    
    return {
        'extractor_retries': 5,
        'remote_components': ['ejs:github'],
        'extractor_args': extractor_args,
        # Anti-ban: Use browser cookies for authentication
        'cookiesfrombrowser': (browser,) if browser and browser != "None" else [],
        # Anti-ban: Randomized sleep intervals to avoid rate limiting
        'sleep_interval_requests': 3,
        'min_sleep': 3,
        'max_sleep': 8,
        # Ignore errors to continue processing
        'ignoreerrors': True,
        # Don't download video, just subtitles and metadata
        'skip_download': True,
        'quiet': True,
        # Retries for network resilience
        'retries': 10,
        'fragment_retries': 10,
        # Subtitle extraction settings
        'write_sub': True,
        'write_auto_sub': True,
        'sub_langs': sub_langs,
        'sleep_subtitles': 2,
        # Output template for downloaded files
        'outtmpl': '%(title)s [%(id)s].%(ext)s',
    }


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
                'player_client': ['android_vr'],
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
# EXCEPTION CLASSES FOR ANTI-BAN ERRORS
# =============================================================================

class RateLimitError(Exception):
    """Raised when YouTube returns HTTP 429 Too Many Requests."""
    pass


class TransientNetworkError(Exception):
    """Raised for temporary network resolution/transport failures."""
    pass


class BotCheckError(Exception):
    """Raised when YouTube asks to confirm the requester is not a bot."""
    pass


class PoTokenError(Exception):
    """Raised when extraction likely failed due to PO token enforcement."""
    pass


class YtDlpCommandError(Exception):
    """Raised for non-zero yt-dlp command failures."""
    
    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


class OperationCancelled(Exception):
    """Raised when the current operation is cancelled by user request."""
    pass


# =============================================================================
# YOUTUBE DOWNLOAD WITH ANTI-BAN MEASURES
# =============================================================================

@retry(
    retry=retry_if_exception_type((RateLimitError, TransientNetworkError)),
    wait=wait_exponential(multiplier=1, min=15, max=90),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _run_ydlp_with_retry(cmd_args: list, cwd: pathlib.Path | None = None, cancel_event=None):
    """
    Wrap yt-dlp subprocess call with exponential back-off on rate limits.
    
    Args:
        cmd_args: Command line arguments for yt-dlp
        cwd: Working directory (optional)
    """
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="ignore") as output_stream:
        proc = subprocess.Popen(
            [sys.executable, "-m", "yt_dlp"] + cmd_args,
            stdout=output_stream,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
        )

        while proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                raise OperationCancelled("yt-dlp run cancelled by user.")
            time.sleep(0.25)

        output_stream.flush()
        output_stream.seek(0)
        combined = output_stream.read()

    if combined:
        print(combined, end="")
    
    lower = combined.lower()
    if "http error 429" in lower or "too many requests" in lower:
        raise RateLimitError("YouTube returned HTTP 429: Too Many Requests")
    if _is_retryable_network_error(combined):
        raise TransientNetworkError(_brief_ydlp_error(combined))
    if "sign in to confirm you're not a bot" in lower:
        raise BotCheckError(_brief_ydlp_error(combined))
    if "po token" in lower and "youtube" in lower:
        raise PoTokenError(_brief_ydlp_error(combined))
    if "http error 403" in lower and "youtube" in lower:
        raise PoTokenError(_brief_ydlp_error(combined))
    if proc.returncode != 0:
        raise YtDlpCommandError(_brief_ydlp_error(combined), combined)
    
    return proc.returncode


def _is_retryable_network_error(output: str) -> bool:
    """Check if the output contains retryable network errors."""
    markers = (
        "failed to resolve",
        "temporary failure in name resolution",
        "timed out",
        "connection reset",
        "network is unreachable",
        "transporterror",
    )
    text = output.lower()
    return any(marker in text for marker in markers)


def _brief_ydlp_error(output: str) -> str:
    """Extract brief error from yt-dlp output."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:") or stripped.startswith("WARNING:"):
            return stripped
    return "yt-dlp failed without a detailed error line."


def _build_download_cmd(video_url: str, out_dir: pathlib.Path, browser: str,
                        with_subs: bool = True, player_client: str = "android_vr",
                        sub_langs: str = DEFAULT_SUB_LANGS, impersonate_target: str = "") -> list:
    """
    Build yt-dlp CLI arguments for retrieving metadata (and optionally subtitles).
    
    Args:
        video_url: YouTube video URL
        out_dir: Output directory for downloaded files
        browser: Browser for cookie extraction
        with_subs: Whether to download subtitles
        player_client: Player client to impersonate
        sub_langs: Subtitle language pattern
        impersonate_target: Target browser for impersonation
    
    Returns:
        List of command line arguments
    """
    # Randomize sleep per video so we look human
    sleep_min = str(random.randint(3, 8))    # 3-8 s between requests (anti-ban)
    sub_sleep  = str(random.randint(2, 5))   # 2-5 s between subtitle tracks
    
    args = [
        "--extractor-retries", "5",
        "--remote-components", "ejs:github",
        "--extractor-args", f"youtube:player_client={player_client};fetch_pot=auto",
        # Anti-ban: Sleep intervals for rate limiting protection
        "--min-sleep", sleep_min,
        "--max-sleep", sleep_min,
        "--retries", "10",                    # network retry on transient errors
        "--fragment-retries", "10",
        "--ignore-errors",
        "--no-abort-on-error",
        "--skip-download",
        "--ignore-no-formats-error",
        "--write-info-json",
        "-o", str(out_dir / "%(title)s [%(id)s].%(ext)s"),
    ]
    
    if impersonate_target:
        args += ["--impersonate", impersonate_target]
    
    if with_subs:
        args += [
            "--write-sub",
            "--write-auto-sub",
            "--sub-langs", sub_langs,
            "--sleep-subtitles", sub_sleep,
        ]
    
    if browser and browser != "None":
        if browser.endswith('.txt'):
            args += ["--cookies", browser]
        else:
            args += ["--cookies-from-browser", browser]
    
    args.append(video_url)
    return args


def _subtitle_attempt_plan(player_client: str = "android_vr", sub_langs: str = DEFAULT_SUB_LANGS) -> list:
    """
    Generate ordered subtitle extraction attempts from safest to most permissive.
    
    Args:
        player_client: Base player client to use
        sub_langs: Base subtitle language pattern
    
    Returns:
        List of attempt dictionaries with labels, player_client, sub_langs, impersonate
    """
    return [
        {"label": f"{player_client} english", "player_client": player_client, "sub_langs": sub_langs, "impersonate": ""},
        {"label": f"{player_client} secondary langs", "player_client": player_client, "sub_langs": SECONDARY_SUB_LANGS, "impersonate": ""},
        {"label": f"{player_client} all langs", "player_client": player_client, "sub_langs": FALLBACK_SUB_LANGS, "impersonate": ""},
        {"label": f"{player_client},tv english", "player_client": f"{player_client},tv", "sub_langs": sub_langs, "impersonate": ""},
        {"label": f"{player_client},tv english (chrome)", "player_client": f"{player_client},tv", "sub_langs": sub_langs, "impersonate": "chrome"},
    ]


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
        "*.en-orig.*",
        "*.en.*",
        "*.ru.*",
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


def _pick_info_json(files_found: list) -> pathlib.Path | None:
    """Select the info.json file from available downloads."""
    info_json = next((f for f in files_found if f.name.endswith(".info.json")), None)
    if info_json:
        return info_json
    return next((f for f in files_found if f.suffix == ".json"), None)


def download_video_subtitles(url: str, session_id: str, browser: str = "None",
                              player_client: str = "android_vr", sub_langs: str = DEFAULT_SUB_LANGS,
                              group_by_playlist: bool = True, progress_callback=None,
                              error_callback=None, cancel_event=None) -> list:
    """
    Download subtitles + metadata for a YouTube video with anti-ban protection.
    
    Implements: browser cookies, human-like delays, exponential back-off retries,
    metadata-only fallback on subtitle failure, and private-video detection.
    
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
    processed_files = []
    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("Download cancelled before start.")

    session_dir = ensure_session_dir(session_id)
    
    title = "YouTube Video"  # Will be updated below
    
    if progress_callback:
        progress_callback(f"Starting download for '{title}'...", 0.0)
    
    # Build output directory
    video_out_dir = session_dir
    channel_name = ""
    pl_title = ""
    
    # Try full download (subs + metadata) with multiple client/language fallbacks
    subtitle_download_succeeded = False
    subtitle_attempt_errors = []
    attempts = _subtitle_attempt_plan(player_client, sub_langs)
    
    for attempt_no, attempt in enumerate(attempts, start=1):
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled("Download cancelled during subtitle attempts.")

        if progress_callback:
            progress_callback(
                f"Attempt {attempt_no}/{len(attempts)} for '{title}': {attempt['label']}",
                0.1
            )
        
        try:
            cmd_args = _build_download_cmd(
                url,
                video_out_dir,
                browser,
                with_subs=True,
                player_client=attempt["player_client"],
                sub_langs=attempt["sub_langs"],
                impersonate_target=attempt["impersonate"],
            )
            _run_ydlp_with_retry(cmd_args, cancel_event=cancel_event)
        except RateLimitError as e:
            subtitle_attempt_errors.append(str(e))
            continue
        except (TransientNetworkError, BotCheckError, PoTokenError, YtDlpCommandError) as e:
            subtitle_attempt_errors.append(str(e))
            continue
        except OperationCancelled:
            raise
        except Exception as e:
            subtitle_attempt_errors.append(str(e))
            continue
        
        files_found = _find_downloaded_files(video_out_dir, "", title)
        sub_file = _pick_best_sub_file(files_found)
        
        if _has_usable_subtitles(sub_file):
            subtitle_download_succeeded = True
            break
        subtitle_attempt_errors.append(f"Downloaded subtitle file was missing/empty on attempt '{attempt['label']}'")
    
    if not subtitle_download_succeeded and error_callback and subtitle_attempt_errors:
        error_callback(
            f"Subtitle extraction failed for '{title}' after {len(subtitle_attempt_errors)} attempts. "
            f"Last error: {subtitle_attempt_errors[-1]}"
        )
    
    # Find downloaded files
    files_found = _find_downloaded_files(video_out_dir, "", title)
    json_file = _pick_info_json(files_found)
    
    # Metadata-only fallback if JSON is missing after failures
    if not json_file:
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled("Download cancelled before metadata fallback.")

        if progress_callback:
            progress_callback(f"Retrying metadata-only for '{title}'...", 0.3)
        
        try:
            fallback_args = _build_download_cmd(
                url,
                video_out_dir,
                browser,
                with_subs=False,
                player_client="android_vr",
                impersonate_target="",
            )
            _run_ydlp_with_retry(fallback_args, cancel_event=cancel_event)
        except OperationCancelled:
            raise
        except Exception as e:
            if error_callback:
                error_callback(f"Fallback also failed for '{title}': {e}")
        
        files_found = _find_downloaded_files(video_out_dir, "", title)
        json_file   = _pick_info_json(files_found)
    
    if not json_file:
        if error_callback:
            error_callback(f"Could not retrieve metadata for '{title}' — skipping.")
        return []
    
    # Parse metadata
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            meta_data = json.load(f)
    except Exception as e:
        if error_callback:
            error_callback(f"Failed to parse JSON for '{title}': {e}")
        return []
    
    # Parse title from metadata
    title = meta_data.get('title', 'Unknown Title')
    video_id = meta_data.get('id', '')
    pl_title = meta_data.get('playlist_title', '')
    channel_name = meta_data.get('uploader', meta_data.get('uploader_id', ''))
    
    # Find best available subtitle
    sub_file = _pick_best_sub_file(files_found)
    
    if _has_usable_subtitles(sub_file):
        with open(sub_file, 'r', encoding='utf-8', errors='ignore') as f:
            transcript_text = clean_vtt_content(f.read())
    else:
        transcript_text = "[No subtitles available — blocked by YouTube or not uploaded by creator]"
    
    # Write processed text
    final_content = format_for_llm(meta_data, transcript_text)
    
    # Build output directory path
    output_dir = session_dir / "processed"
    if group_by_playlist:
        if channel_name and channel_name not in ('Unknown Channel', ''):
            safe_ch2 = "".join(c for c in channel_name if c.isalnum() or c in ' -_').strip()
            output_dir = output_dir / safe_ch2
        if pl_title:
            safe_pl2 = "".join(c for c in pl_title if c.isalnum() or c in ' -_').strip()
            output_dir = output_dir / safe_pl2
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    safe_title = "".join(c for c in title if c.isalnum() or c in ' -_').strip()
    final_path = output_dir / f"{safe_title}.txt"
    
    with open(final_path, 'w', encoding='utf-8') as f:
        f.write(final_content)
    
    processed_files.append(final_path)
    
    # Human-like pause between videos (anti-bot measure)
    pause = random.uniform(1.0, 3.0)
    if cancel_event and cancel_event.is_set():
        raise OperationCancelled("Download cancelled after file write.")
    time.sleep(pause)
    
    if progress_callback:
        progress_callback(f"Done! Harvested '{title}'.", 1.0)
    
    return processed_files


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
                f.write("\n\n" + "="*40 + "\n\n".join(content_list))
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


# =============================================================================
# ZIP EXPORT
# =============================================================================

def zip_files(file_paths: list, output_path: pathlib.Path):
    """
    Create a ZIP archive from a list of file paths.
    
    Args:
        file_paths: List of file paths to include in the ZIP
        output_path: Output path for the ZIP file
    """
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in file_paths:
            zf.write(path, arcname=pathlib.Path(path).name)
