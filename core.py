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

# --- Configuration & Setup ---

BASE_TEMP_DIR = pathlib.Path(tempfile.gettempdir()) / "youscriber"

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
        'logger': logger,
    }

    if browser and browser != "None":
        if browser.endswith('.txt'):
            flat_opts['cookiefile'] = browser
        else:
            flat_opts['cookiesfrombrowser'] = (browser,)
        flat_opts['remote_components'] = ['ejs:github']

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

def _get_cookie_args(browser: str) -> list:
    """Build safe cookie CLI arguments. Handles .txt files and named browsers."""
    if not browser or browser == "None":
        return []
    if browser.endswith('.txt'):
        return ["--cookies", browser]
    return ["--cookies-from-browser", browser]

def _run_ydlp(cmd_args: list, cwd: pathlib.Path | None = None) -> int:
    """Run yt-dlp as a subprocess and raise RateLimitError on HTTP 429."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "yt_dlp"] + cmd_args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    # Surface 429 so tenacity can retry
    combined = result.stdout + result.stderr
    if "HTTP Error 429" in combined:
        raise RateLimitError("YouTube returned HTTP 429: Too Many Requests")
    # Print to terminal so the user can see live progress
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    return result.returncode

@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_exponential(multiplier=1, min=30, max=120),  # 30s → 60s → 120s back-off
    stop=stop_after_attempt(4),
    reraise=True,
)
def _run_with_retry(cmd_args: list, cwd: pathlib.Path | None = None):
    """Wrap _run_ydlp with exponential back-off on 429 errors."""
    _run_ydlp(cmd_args, cwd)

def _build_download_cmd(video_url: str, out_dir: pathlib.Path, browser: str, with_subs: bool = True) -> list:
    """Construct the yt-dlp CLI args for retrieving metadata (and optionally subtitles)."""
    # Randomize sleep per video so we look human
    sleep_min = str(random.randint(2, 4))    # 2–4 s between requests
    sleep_max = str(random.randint(5, 9))    # 5–9 s max jitter
    sub_sleep  = str(random.randint(2, 5))   # 2–5 s between subtitle tracks

    args = [
        "--impersonate", "chrome",            # TLS fingerprint spoofing
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

    if with_subs:
        args += [
            "--write-sub",
            "--write-auto-sub",
            "--sub-langs", "en,ru,uk",
            "--sleep-subtitles", sub_sleep,
        ]

    args += _get_cookie_args(browser)
    args.append(video_url)
    return args


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

        # --- Try full download (subs + metadata) with exponential back-off ---
        try:
            cmd_args = _build_download_cmd(url, video_out_dir, browser, with_subs=True)
            _run_with_retry(cmd_args)
        except RateLimitError:
            if error_callback:
                error_callback(f"429 persisted after retries for '{title}'. Falling back to metadata-only.")
        except Exception as e:
            if error_callback:
                error_callback(f"Unexpected error for '{title}': {e}")

        # --- Find downloaded files ---
        files_found = list(video_out_dir.glob(f"*{video_id}*")) if video_id else []
        if not files_found:
            safe_t = "".join(c for c in title if c.isalnum() or c in ' -_').strip()
            files_found = list(video_out_dir.glob(f"*{safe_t}*"))

        json_file = next((f for f in files_found if f.suffix == '.json'), None)

        # --- Metadata-only fallback if JSON is missing after 429 ---
        if not json_file:
            if status_callback:
                status_callback(f"Retrying metadata-only for '{title}'…")
            try:
                fallback_args = _build_download_cmd(url, video_out_dir, browser, with_subs=False)
                _run_with_retry(fallback_args)
            except Exception as e:
                if error_callback:
                    error_callback(f"Fallback also failed for '{title}': {e}")

            files_found = list(video_out_dir.glob(f"*{video_id}*")) if video_id else []
            json_file   = next((f for f in files_found if f.suffix == '.json'), None)

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

        # --- Find best available subtitle (en > ru > uk > any) ---
        sub_file = None
        for lang in ('en', 'ru', 'uk', ''):
            suffix = f'.{lang}.vtt' if lang else ''
            candidates = [f for f in files_found if f.name.endswith(suffix) or
                                                     f.name.endswith(suffix.replace('.vtt', '.srt'))]
            if candidates:
                sub_file = candidates[0]
                break
        if sub_file is None:
            sub_file = next((f for f in files_found if f.suffix in ('.vtt', '.srt')), None)

        if sub_file:
            with open(sub_file, 'r', encoding='utf-8') as f:
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
