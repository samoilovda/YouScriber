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

# --- Configuration & Setup ---

BASE_TEMP_DIR = pathlib.Path(tempfile.gettempdir()) / "youscriber"
DEFAULT_SUB_LANGS = os.getenv("YOUSCRIBER_SUB_LANGS", "en.*,en")
SECONDARY_SUB_LANGS = os.getenv("YOUSCRIBER_SECONDARY_SUB_LANGS", "ru.*,ru,uk.*,uk")
FALLBACK_SUB_LANGS = "all,-live_chat"

def ensure_session_dir(session_id: str) -> pathlib.Path:
    session_dir = BASE_TEMP_DIR / session_id
    if not session_dir.exists():
        session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

def clear_session_dir(session_id: str):
    session_dir = BASE_TEMP_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

def merge_files(files: list[pathlib.Path], strategy: str, session_dir: pathlib.Path) -> list[pathlib.Path]:
    """
    Merges processed files based on the selected strategy.
    Returns a list of paths to the resulting file(s).
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

# --- Core Logic: Extraction & Cleaning ---

def clean_vtt_content(content: str) -> str:
    # 1. Remove entire WEBVTT header block
    content = re.sub(r'^WEBVTT\b.*?(?=\n\n)', '', content, flags=re.DOTALL)

    # 2. Remove timestamp lines
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

class CallbackLogger:
    def __init__(self, error_callback=None):
        self.error_callback = error_callback
    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        if self.error_callback:
            self.error_callback(msg)

def sanitize_description(description: str) -> str:
    description = re.sub(r'https?://\S+', '', description)
    description = re.sub(r'www\.\S+', '', description)
    description = re.sub(r'#\w+', '', description)
    description = re.sub(r'\n{3,}', '\n\n', description)
    return description.strip()

def format_for_llm(metadata: dict, transcript: str) -> str:
    title = metadata.get('title', 'Unknown Title')
    url = metadata.get('webpage_url', metadata.get('original_url', 'Unknown URL'))
    upload_date = metadata.get('upload_date', 'Unknown Date')
    description = sanitize_description(metadata.get('description', ''))

    if len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    return f"# TITLE: {title}\n# URL: {url}\n# PUBLISH DATE: {upload_date}\n\n## METADATA & CHAPTERS\n{description}\n\n## TRANSCRIPT\n{transcript}\n"

def fetch_playlist_info(urls: list, browser: str = "None", status_callback=None, error_callback=None) -> list:
    logger = CallbackLogger(error_callback)
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

    if browser and browser != "None":
        if browser.endswith('.txt'):
            flat_opts['cookiefile'] = browser
        else:
            flat_opts['cookiesfrombrowser'] = (browser,)

    video_list = []
    
    if status_callback:
        status_callback(f"Fetching metadata using yt-dlp...")
        
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                    
                if 'entries' in info and info['entries'] is not None:
                    playlist_title = info.get('title', 'Unknown Playlist')
                    channel_name = info.get('uploader', info.get('uploader_id', info.get('channel', 'Unknown Channel')))
                    for entry in info['entries']:
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

import random
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

class RateLimitError(Exception):
    """Raised when yt-dlp hits a 429 Too Many Requests error."""
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

def _get_cookie_args(browser: str) -> list:
    """Build safe cookie CLI arguments. Handles .txt files and named browsers."""
    if not browser or browser == "None":
        return []
    if browser.endswith('.txt'):
        return ["--cookies", browser]
    return ["--cookies-from-browser", browser]

def _is_retryable_network_error(output: str) -> bool:
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
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ERROR:") or stripped.startswith("WARNING:"):
            return stripped
    return "yt-dlp failed without a detailed error line."


def _run_ydlp(cmd_args: list, cwd: pathlib.Path | None = None) -> int:
    """Run yt-dlp as a subprocess and surface key anti-bot failures as typed exceptions."""
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp"] + cmd_args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    combined = result.stdout + result.stderr

    # Print to terminal so the user can see live progress
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    lower = combined.lower()
    if "http error 429" in lower or "too many requests" in lower:
        raise RateLimitError("YouTube returned HTTP 429: Too Many Requests")
    if _is_retryable_network_error(combined):
        raise TransientNetworkError(_brief_ydlp_error(combined))
    if "sign in to confirm you’re not a bot" in lower or "sign in to confirm you're not a bot" in lower:
        raise BotCheckError(_brief_ydlp_error(combined))
    if "po token" in lower and "youtube" in lower:
        raise PoTokenError(_brief_ydlp_error(combined))
    if "http error 403" in lower and "youtube" in lower:
        raise PoTokenError(_brief_ydlp_error(combined))
    if result.returncode != 0:
        raise YtDlpCommandError(_brief_ydlp_error(combined), combined)

    return result.returncode

@retry(
    retry=retry_if_exception_type((RateLimitError, TransientNetworkError)),
    wait=wait_exponential(multiplier=1, min=15, max=90),  # 15s → 30s → 60s → 90s back-off
    stop=stop_after_attempt(4),
    reraise=True,
)
def _run_with_retry(cmd_args: list, cwd: pathlib.Path | None = None):
    """Wrap _run_ydlp with exponential back-off on 429 and transient network failures."""
    _run_ydlp(cmd_args, cwd)

def _resolve_extractor_args(player_client: str) -> str:
    """
    Build extractor args.
    If YOUSCRIBER_YT_EXTRACTOR_ARGS is set, it is used verbatim (without `youtube:` prefix).
    """
    env_override = os.getenv("YOUSCRIBER_YT_EXTRACTOR_ARGS", "").strip()
    if env_override:
        return env_override
    return f"player_client={player_client};fetch_pot=auto"


def _build_download_cmd(
    video_url: str,
    out_dir: pathlib.Path,
    browser: str,
    with_subs: bool = True,
    player_client: str = "android_vr",
    sub_langs: str = DEFAULT_SUB_LANGS,
    impersonate_target: str = "",
) -> list:
    """Construct the yt-dlp CLI args for retrieving metadata (and optionally subtitles)."""
    # Randomize sleep per video so we look human
    sleep_min = str(random.randint(2, 4))    # 2–4 s between requests
    sleep_max = str(random.randint(5, 9))    # 5–9 s max jitter
    sub_sleep  = str(random.randint(2, 5))   # 2–5 s between subtitle tracks

    args = [
        "--extractor-retries", "5",
        "--remote-components", "ejs:github",
        "--extractor-args", f"youtube:{_resolve_extractor_args(player_client)}",
        "--min-sleep-interval", sleep_min,
        "--max-sleep-interval", sleep_max,
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

    args += _get_cookie_args(browser)
    args.append(video_url)
    return args


def _subtitle_attempt_plan() -> list[dict]:
    """
    Ordered from safest/least brittle to more permissive fallbacks.
    Each attempt controls player client, subtitle language pattern and impersonation.
    """
    env_clients = os.getenv("YOUSCRIBER_YT_PLAYER_CLIENT", "").strip()
    if env_clients:
        return [
            {"label": f"custom clients ({env_clients})", "player_client": env_clients, "sub_langs": DEFAULT_SUB_LANGS, "impersonate": ""},
            {"label": f"custom clients ({env_clients}) + secondary langs", "player_client": env_clients, "sub_langs": SECONDARY_SUB_LANGS, "impersonate": ""},
            {"label": f"custom clients ({env_clients}) + all langs", "player_client": env_clients, "sub_langs": FALLBACK_SUB_LANGS, "impersonate": ""},
        ]

    return [
        {"label": "android_vr english", "player_client": "android_vr", "sub_langs": DEFAULT_SUB_LANGS, "impersonate": ""},
        {"label": "android_vr secondary langs", "player_client": "android_vr", "sub_langs": SECONDARY_SUB_LANGS, "impersonate": ""},
        {"label": "android_vr all langs", "player_client": "android_vr", "sub_langs": FALLBACK_SUB_LANGS, "impersonate": ""},
        {"label": "android_vr,tv english", "player_client": "android_vr,tv", "sub_langs": DEFAULT_SUB_LANGS, "impersonate": ""},
        {"label": "android_vr,web_safari english", "player_client": "android_vr,web_safari", "sub_langs": DEFAULT_SUB_LANGS, "impersonate": "chrome"},
    ]


def _find_downloaded_files(video_out_dir: pathlib.Path, video_id: str, title: str) -> list[pathlib.Path]:
    files_found = list(video_out_dir.glob(f"*{video_id}*")) if video_id else []
    if files_found:
        return files_found

    safe_t = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    if safe_t:
        files_found = list(video_out_dir.glob(f"*{safe_t}*"))
    return files_found


def _pick_best_sub_file(files_found: list[pathlib.Path]) -> pathlib.Path | None:
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


def _pick_info_json(files_found: list[pathlib.Path]) -> pathlib.Path | None:
    info_json = next((f for f in files_found if f.name.endswith(".info.json")), None)
    if info_json:
        return info_json
    return next((f for f in files_found if f.suffix == ".json"), None)


def download_videos(video_list: list, group_by_playlist: bool, session_id: str,
                    browser: str = "None", progress_callback=None,
                    status_callback=None, error_callback=None) -> list:
    """
    Download subtitles + metadata for every video in video_list.
    Implements: browser cookies, human-like delays, exponential back-off retries,
    metadata-only fallback on subtitle failure, and private-video detection.
    """
    processed_files = []
    session_dir = ensure_session_dir(session_id)

    total_videos = len(video_list)
    if total_videos == 0:
        return []

    if status_callback:
        status_callback(f"Starting download for {total_videos} videos…")

    for i, video_info in enumerate(video_list):
        url      = video_info.get('url', '')
        title    = video_info.get('title', 'Video')
        video_id = video_info.get('id', '')
        pl_title = video_info.get('playlist_title', '')
        channel_name = video_info.get('channel_name', '')

        # --- Skip obviously private/unavailable entries ---
        if not url or title.startswith('[Private') or title.startswith('[Deleted'):
            if error_callback:
                error_callback(f"Skipping unavailable video: {title} ({video_id})")
            continue

        if progress_callback:
            progress_callback(i / total_videos, f"Processing {i+1}/{total_videos}: {title}")

        # --- Build output directory ---
        video_out_dir = session_dir
        if group_by_playlist:
            if channel_name and channel_name not in ('Unknown Channel', ''):
                safe_ch = "".join(c for c in channel_name if c.isalnum() or c in ' -_').strip()
                video_out_dir = video_out_dir / safe_ch
            if pl_title:
                safe_pl = "".join(c for c in pl_title if c.isalnum() or c in ' -_').strip()
                video_out_dir = video_out_dir / safe_pl
        video_out_dir.mkdir(parents=True, exist_ok=True)

        # --- Try full download (subs + metadata) with multiple client/language fallbacks ---
        subtitle_download_succeeded = False
        subtitle_attempt_errors = []
        attempts = _subtitle_attempt_plan()
        for attempt_no, attempt in enumerate(attempts, start=1):
            if status_callback:
                status_callback(
                    f"Attempt {attempt_no}/{len(attempts)} for '{title}': {attempt['label']}"
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
                _run_with_retry(cmd_args)
            except RateLimitError as e:
                subtitle_attempt_errors.append(str(e))
                continue
            except (TransientNetworkError, BotCheckError, PoTokenError, YtDlpCommandError) as e:
                subtitle_attempt_errors.append(str(e))
                continue
            except Exception as e:
                subtitle_attempt_errors.append(str(e))
                continue

            files_found = _find_downloaded_files(video_out_dir, video_id, title)
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

        # --- Find downloaded files ---
        files_found = _find_downloaded_files(video_out_dir, video_id, title)
        json_file = _pick_info_json(files_found)

        # --- Metadata-only fallback if JSON is missing after 429 ---
        if not json_file:
            if status_callback:
                status_callback(f"Retrying metadata-only for '{title}'…")
            try:
                fallback_args = _build_download_cmd(
                    url,
                    video_out_dir,
                    browser,
                    with_subs=False,
                    player_client="android_vr",
                    impersonate_target="",
                )
                _run_with_retry(fallback_args)
            except Exception as e:
                if error_callback:
                    error_callback(f"Fallback also failed for '{title}': {e}")

            files_found = _find_downloaded_files(video_out_dir, video_id, title)
            json_file   = _pick_info_json(files_found)

        if not json_file:
            if error_callback:
                error_callback(f"Could not retrieve metadata for '{title}' — skipping.")
            continue

        # --- Parse metadata ---
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                meta_data = json.load(f)
        except Exception as e:
            if error_callback:
                error_callback(f"Failed to parse JSON for '{title}': {e}")
            continue

        # --- Find best available subtitle (en-orig > en > ru > uk > any) ---
        sub_file = _pick_best_sub_file(files_found)

        if _has_usable_subtitles(sub_file):
            with open(sub_file, 'r', encoding='utf-8', errors='ignore') as f:
                transcript_text = clean_vtt_content(f.read())
        else:
            transcript_text = "[No subtitles available — blocked by YouTube or not uploaded by creator]"

        # --- Write processed text ---
        final_content = format_for_llm(meta_data, transcript_text)

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

        # --- Human-like inter-video pause (beyond yt-dlp's own sleep) ---
        pause = random.uniform(1.0, 3.0)
        time.sleep(pause)

    if progress_callback:
        progress_callback(1.0, f"Done! Harvested {len(processed_files)}/{total_videos} videos.")

    return processed_files


def process_local_files(file_paths: list, session_id: str, progress_callback=None, status_callback=None, error_callback=None) -> list:
    session_dir = ensure_session_dir(session_id)
    output_dir = session_dir / "local_processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    file_map = {}
    total_files = len(file_paths)
    
    if status_callback:
         status_callback(f"Analyzing {total_files} local files...")
         
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
        if progress_callback:
            progress_callback(count / max(1, total_stems), f"Processing local file: {stem}")
            
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
        progress_callback(1.0, f"Finished processing local files.")
        
    return processed_files
