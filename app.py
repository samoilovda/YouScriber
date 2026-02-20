import streamlit as st
import yt_dlp
import pathlib
import re
import zipfile
import io
import tempfile
import json
import shutil
import time
import traceback
import uuid

# --- Configuration & Setup ---
st.set_page_config(
    page_title="YouScriber - YouTube Knowledge Harvester",
    page_icon="📚",
    layout="wide"
)

# Cross-platform temporary directory handling
# We use a base temp dir, but will extend it with session ID in main()
BASE_TEMP_DIR = pathlib.Path(tempfile.gettempdir()) / "youscriber"

def get_session_dir():
    if 'session_id' not in st.session_state:
        st.session_state['session_id'] = str(uuid.uuid4())
    return BASE_TEMP_DIR / st.session_state['session_id']

def ensure_session_dir():
    session_dir = get_session_dir()
    if not session_dir.exists():
        session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir

def clear_session_dir():
    session_dir = get_session_dir()
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
    
    # Sort files to ensure deterministic order (e.g. by name)
    # This matches typical playlist order if they have numbering or alphabetic titles
    sorted_files = sorted(files, key=lambda p: p.name)
    
    merged_output_dir = session_dir / "merged"
    merged_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Strategy constants
    CHUNK_LIMITS = {
        "Medium Chunks (~50k chars)": 50_000,
        "Large Chunks (~200k chars)": 200_000
    }
    
    if strategy == "One File":
        output_file = merged_output_dir / "All_Processed_Videos.txt"
        with open(output_file, 'w', encoding='utf-8') as outfile:
            for i, fpath in enumerate(sorted_files):
                # Add separator between files
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
                
                # If adding this file exceeds limit and we have content, save current chunk
                if current_char_count + text_len > limit and current_chunk_content:
                    chunks.append(save_chunk(current_chunk_idx, current_chunk_content))
                    current_chunk_idx += 1
                    current_chunk_content = []
                    current_char_count = 0
                
                current_chunk_content.append(text)
                current_char_count += text_len
        
        # Save remaining
        if current_chunk_content:
            chunks.append(save_chunk(current_chunk_idx, current_chunk_content))
            
        return [c for c in chunks if c]

    return files

# --- Core Logic: Extraction & Cleaning ---

def clean_vtt_content(content: str) -> str:
    """
    Cleans WebVTT/SRT content to pure text.
    Removes timestamps, formatting tags, and duplicate lines.
    """
    # 1. Remove WebVTT header if present
    content = re.sub(r'WEBVTT.*?\n', '', content, flags=re.DOTALL)

    # 2. Remove timestamps (e.g., 00:00:01.500 --> 00:00:03.000)
    # Flexible regex to catch various time formats
    content = re.sub(r'\d{2}:\d{2}:\d{2}[\.,]\d{3} --> \d{2}:\d{2}:\d{2}[\.,]\d{3}.*?\n', '', content)
    content = re.sub(r'\d{2}:\d{2}[\.,]\d{3} --> \d{2}:\d{2}[\.,]\d{3}.*?\n', '', content) # shorter format

    # 3. Remove HTML-like tags (e.g., <c.colorE5E5E5>, <00:00:00.609><c>)
    content = re.sub(r'<[^>]+>', '', content)

    # 4. Remove simple numeric indices often found in SRT
    content = re.sub(r'^\d+\s*$', '', content, flags=re.MULTILINE)

    # 5. Process line by line to remove duplicates and empty lines
    lines = content.splitlines()
    cleaned_lines = []
    seen_lines = set()
    last_line = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Deduplication strategy:
        # A common issue in auto-subs is the same line appearing purely duplicated
        # or shifting slightly. We'll do strict previous-line checking.
        if line == last_line:
            continue
        
        cleaned_lines.append(line)
        last_line = line

    return "\n".join(cleaned_lines)

class MyLogger:
    def __init__(self):
        self.errors = []
    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        self.errors.append(msg)

def format_for_llm(metadata: dict, transcript: str) -> str:
    """
    Formats the final output string for LLM ingestion.
    """
    title = metadata.get('title', 'Unknown Title')
    url = metadata.get('webpage_url', metadata.get('original_url', 'Unknown URL'))
    upload_date = metadata.get('upload_date', 'Unknown Date')
    description = metadata.get('description', '')

    # Format date if strictly YYYYMMDD
    if len(upload_date) == 8 and upload_date.isdigit():
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    return f"""# TITLE: {title}
# URL: {url}
# PUBLISH DATE: {upload_date}

## METADATA & CHAPTERS
{description}

## TRANSCRIPT
{transcript}
"""

def fetch_playlist_info(urls: list, browser: str = "None") -> list:
    """
    Fetches playlist metadata quickly using extract_flat=True.
    Returns a list of dictionaries with video details (Title, URL, ID, etc.)
    """
    logger = MyLogger()
    flat_opts = {
        'extract_flat': True,
        'dump_single_json': True,
        'quiet': True,
        'ignoreerrors': True,
        'logger': logger,
    }

    if browser and browser != "None":
        flat_opts['cookiesfrombrowser'] = browser

    video_list = []
    
    with yt_dlp.YoutubeDL(flat_opts) as ydl:
        for url in urls:
            try:
                info = ydl.extract_info(url, download=False)
                if not info:
                    continue
                    
                if 'entries' in info:
                    # It's a playlist or channel
                    playlist_title = info.get('title', 'Unknown Playlist')
                    for entry in info['entries']:
                        if entry:
                            entry['playlist_title'] = playlist_title
                            # Ensure we have a valid URL or ID to construct one
                            if not entry.get('url') and entry.get('id'):
                                entry['url'] = f"https://www.youtube.com/watch?v={entry['id']}"
                            elif not entry.get('url') and entry.get('webpage_url'):
                                entry['url'] = entry['webpage_url']
                                
                            video_list.append(entry)
                else:
                    # Single video
                    # Ensure consistency in keys
                    if not info.get('url') and info.get('webpage_url'):
                        info['url'] = info['webpage_url']
                    elif not info.get('url') and info.get('id'):
                        info['url'] = f"https://www.youtube.com/watch?v={info['id']}"
                        
                    video_list.append(info)
            except Exception as e:
                st.error(f"Error gathering metadata for {url}: {str(e)}")
    
    return video_list

def download_videos(video_list: list, group_by_playlist: bool, progress_bar, status_text, browser: str = "None"):
    """
    Downloads metadata and subtitles for a specific list of videos,
    cleans them, and prepares final text files.
    """
    processed_files = []
    session_dir = ensure_session_dir()
    logger = MyLogger()

    # Options for actual downloading of individual videos
    download_opts = {
        'skip_download': True,
        'write_sub': True,
        'write_auto_sub': True,
        'sub_langs': ['en', 'ru', 'uk'],
        'write_info_json': True,
        'write_description': False,
        'outtmpl': str(session_dir / '%(title)s [%(id)s].%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'logger': logger,
    }

    if group_by_playlist:
        download_opts['outtmpl'] = str(session_dir / '%(playlist_title)s/%(title)s [%(id)s].%(ext)s')

    if browser and browser != "None":
        download_opts['cookiesfrombrowser'] = browser

    total_videos = len(video_list)
    if total_videos == 0:
        st.warning("No videos to process.")
        return []

    status_text.text(f"Starting download for {total_videos} videos...")
    
    # Process each video
    with yt_dlp.YoutubeDL(download_opts) as ydl:
        for i, video_info in enumerate(video_list):
            url = video_info.get('url')
            title = video_info.get('title', 'Video')
            
            progress_bar.progress((i) / total_videos, text=f"Processing {i+1}/{total_videos}: {title}")
            
            try:
                # 1. Download for this specific video

                # Note: We re-extract info with download=True for full metadata + subs
                info = ydl.extract_info(url, download=True)
                if not info:
                    print(f"Failed to extract info for {title}")
                    continue
                
                # 2. Find generated files
                video_id = info.get('id')
                # Debugging: Print what we are looking for
                print(f"DEBUG: Looking for files with video_id: {video_id} in {session_dir}")
                
                # We need to be careful. If group_by_playlist is on, files are in subfolders.
                # rglob should find them.
                # The template is '... [%(id)s].%(ext)s', so we look for the ID.
                # Sometimes file systems or yt-dlp might behave slightly differently with brackets.
                
                # First try explicit bracket match which is most accurate
                files_found = list(session_dir.rglob(f"*[{video_id}]*"))
                
                if not files_found:
                    # Fallback: search for just the ID, but be careful of partial matches
                    # YouTube IDs are 11 chars.
                    all_matches = list(session_dir.rglob(f"*{video_id}*"))
                    # Filter to ensure it's likely the ID (e.g. at end of name or preceded by space/bracket)
                    files_found = all_matches
                    if files_found:
                        print(f"DEBUG: Found files via fallback for {video_id}: {[f.name for f in files_found]}")

                # Debugging
                if not files_found:
                     print(f"DEBUG: No files found for video_id: {video_id} in {session_dir}. Files in dir:")
                     # List a few files to see what's happening
                     for i, f in enumerate(session_dir.rglob("*")):
                         if i < 5: print(f"  {f.name}")
                
                json_file = next((f for f in files_found if f.suffix == '.json'), None)
                sub_file = next((f for f in files_found if f.suffix in ['.vtt', '.srt']), None)

                if not json_file:
                    st.warning(f"Metadata not found for {title}. Skipping.")
                    continue
                
                # Load Metadata
                with open(json_file, 'r', encoding='utf-8') as f:
                    meta_data = json.load(f)

                # Load and Clean Transcript
                transcript_text = ""
                if sub_file:
                     with open(sub_file, 'r', encoding='utf-8') as f:
                        raw_subs = f.read()
                        transcript_text = clean_vtt_content(raw_subs)
                else:
                    transcript_text = "[No subtitles available]"

                # Format Final Output
                final_content = format_for_llm(meta_data, transcript_text)
                
                # Save Final File
                output_dir = session_dir / "processed"
                
                # Use playlist title from initial metadata if available and grouping enabled
                pl_title = video_info.get('playlist_title')
                if group_by_playlist:
                     # Check if we have playlist info from the individual extraction or the flat one
                     if 'playlist_title' in info and info['playlist_title']:
                         pl_title = info['playlist_title']
                     
                     if pl_title:
                        safe_playlist_title = "".join([c for c in pl_title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                        output_dir = output_dir / safe_playlist_title
                
                output_dir.mkdir(parents=True, exist_ok=True)
                
                safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                final_filename = output_dir / f"{safe_title}.txt"
                
                with open(final_filename, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                processed_files.append(final_filename)
                
            except Exception as e:
                logger.error(f"Failed to process {title}: {str(e)}")
                print(f"Failed to process {title}: {e}")
                traceback.print_exc()
                pass

    progress_bar.progress(1.0, text="Done!")

    if logger.errors:
        with st.expander("Show Detailed Error Log"):
            for err in logger.errors:
                st.error(err)

    return processed_files

# --- UI Layout ---

def main():
    st.sidebar.title("Available Settings")
    
    st.sidebar.markdown("### YouTube Bot Bypass")
    st.sidebar.info("If you get 'Sign in to confirm you’re not a bot', select your browser below to use its cookies.")
    browser_options = ["None", "chrome", "firefox", "safari", "edge", "opera", "vivaldi"]
    browser_choice = st.sidebar.selectbox("Cookies from Browser:", browser_options, index=0)
    
    # merge_options in Export section now
    
    # Session State for accumulating files
    if 'processed_files' not in st.session_state:
        st.session_state['processed_files'] = []

    tab1, tab2 = st.tabs(["YouTube Import", "Local Import"])

    # --- TAB 1: YouTube ---
    with tab1:
        st.header("YouTube Downloader & Extractor")
        
        col1, col2 = st.columns([0.8, 0.2])
        with col1:
             urls_input = st.text_area("Enter YouTube URLs (one per line):", height=100)
        with col2:
             st.write("")
             st.write("")
             if st.button("Clear/Reset", type="secondary"):
                 if 'video_list' in st.session_state:
                     del st.session_state['video_list']
                 st.rerun()

        group_playlist = st.checkbox("Group by Playlist/Channel", value=True)
        
        # Initialize video_list in session state if not present
        if 'video_list' not in st.session_state:
            st.session_state.video_list = None

        if st.button("Fetch Video List", type="primary"):
            if not urls_input.strip():
                st.error("Please enter at least one URL.")
            else:
                urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
                with st.spinner("Fetching playlist info..."):
                    videos = fetch_playlist_info(urls, browser=browser_choice)
                    
                if videos:
                    # Add a 'selected' key to each video for the checkbox
                    for v in videos:
                        v['selected'] = True
                    st.session_state.video_list = videos
                    st.success(f"Found {len(videos)} videos.")
                else:
                     st.warning("No videos found or unable to fetch metadata.")

        # If we have a fetched list, show the selection UI
        if st.session_state.video_list:
            st.divider()
            st.subheader("Select Videos to Download")
            
            # Prepare data for data_editor
            # We want to edit the 'selected' boolean.
            # Convert list of dicts to a format suitable for data_editor?
            # Actually, st.data_editor works well with list of dicts or dataframes.
            # Let's use the list of dicts directly if possible, but we need to ensure it updates session state.
            
            edited_data = st.data_editor(
                st.session_state.video_list,
                column_config={
                    "selected": st.column_config.CheckboxColumn(
                        "Download?",
                        help="Select to download this video",
                        default=True,
                    ),
                    "title": "Video Title",
                    "url": st.column_config.LinkColumn("URL"),
                    "playlist_title": "Playlist/Channel",
                    # Hide internal keys if needed, but data_editor shows all by default unless configured.
                    "id": None, # Hide ID
                    "entries": None,
                },
                disabled=["title", "url", "playlist_title", "id", "entries"],
                hide_index=True,
                use_container_width=True,
                key="video_editor"
            )
            
            # Count selected
            selected_videos = [v for v in edited_data if v.get('selected', True)]
            st.write(f"Selected: **{len(selected_videos)}** / {len(edited_data)}")
            
            if st.button("Start Harvesting Selected Videos", type="primary", disabled=len(selected_videos)==0):
                 result_container = st.container()
                 with result_container:
                    progress_bar = st.progress(0, text="Ready to start...")
                    status_text = st.empty()
                    
                    new_files = download_videos(selected_videos, group_playlist, progress_bar, status_text, browser=browser_choice)
                    
                    if new_files:
                        st.session_state['processed_files'].extend(new_files)
                        st.success(f"Successfully processed {len(new_files)} videos!")
                    else:
                        st.warning("No videos were processed.")
                    
                    progress_bar.empty()
                    status_text.empty()

    # --- TAB 2: Local Files ---
    with tab2:
        st.header("Local File Processor")
        st.info("Upload .vtt, .srt, or matching .json metadata files.")
        
        uploaded_files = st.file_uploader("Choose files", accept_multiple_files=True, type=['vtt', 'srt', 'json', 'txt'])
        
        if st.button("Process Local Files"):
            if not uploaded_files:
                st.error("Please upload files first.")
            else:
                # Logic to pair files or process individually
                # If we have just a VTT, we process it with dummy metadata
                # If we have JSON + VTT, we pair them.
                # This can be tricky with Streamlit's file_uploader returning objects, not paths.
                
                # We will process each subtitle file found.
                count = 0
                session_dir = ensure_session_dir()
                output_dir = session_dir / "local_processed"
                output_dir.mkdir(parents=True, exist_ok=True)

                # Organize uploaded files by name (without extension) to find pairs
                file_map = {}
                for up_file in uploaded_files:
                    path = pathlib.Path(up_file.name)
                    stem = path.stem
                    if stem not in file_map:
                        file_map[stem] = {}
                    
                    if path.suffix in ['.json']:
                        file_map[stem]['json'] = up_file
                    elif path.suffix in ['.vtt', '.srt', '.txt']:
                        file_map[stem]['sub'] = up_file

                for stem, files in file_map.items():
                    if 'sub' in files:
                        # We have a subtitle file
                        sub_file = files['sub']
                        content = sub_file.getvalue().decode("utf-8")
                        cleaned_text = clean_vtt_content(content)
                        
                        metadata = {}
                        if 'json' in files:
                            # Try to parse associated JSON
                            try:
                                metadata = json.loads(files['json'].read().decode('utf-8'))
                            except Exception as e:
                                st.warning(f"Could not parse JSON for {stem}: {e}")
                                pass # formatting error or not actual metadata
                        
                        # Fallback metadata
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
                        
                        st.session_state['processed_files'].append(final_path)
                        count += 1
                
                st.success(f"Processed {count} local files.")

    # --- Global Download Section ---
    st.markdown("---")
    st.subheader("Export")
    
    if st.session_state['processed_files']:
        # Filter out files that might have been deleted or don't exist
        valid_files = [p for p in st.session_state['processed_files'] if p.exists()]
        st.session_state['processed_files'] = valid_files
        
        st.write(f"Total Processed Files: **{len(valid_files)}**")
        
        # Display list of files (optional)
        with st.expander("Show File List"):
            for p in valid_files:
                st.text(p.name)

        st.markdown("### Merge Options")
        merge_options = ["No Merge", "One File", "Medium Chunks (~50k chars)", "Large Chunks (~200k chars)"]
        # Use session state to remember strategy between runs, or just default to 0
        if 'merge_strategy_idx' not in st.session_state: st.session_state.merge_strategy_idx = 0
        
        merge_strategy = st.radio(
            "Select Merge Strategy:", 
            merge_options, 
            index=st.session_state.merge_strategy_idx,
            horizontal=True
        )
        
        # Save choice to session state to persist it
        st.session_state.merge_strategy_idx = merge_options.index(merge_strategy)

        # "Apply" button to trigger merge logic explicitly
        # We use a session state flag to show/hide the download button to avoid flicker
        if st.button("Apply Merge & Prepare Download", type="primary"):
            st.session_state['merge_ready'] = True
            st.session_state['current_strategy'] = merge_strategy
        
        # If merge is ready (either clicked just now or preserved), show download
        if st.session_state.get('merge_ready'):
             # Check if strategy changed since last merge? 
             # For simplicity, if they change radio, they should click Apply again.
             # But to be nice, if they assume auto-update, strictly following "Check and add button" request means explicit is better.
             # We will only show download if 'current_strategy' matches 'merge_strategy' or we just re-run.
             # Let's just re-run merge logic if the flag is True.
             
            session_dir = ensure_session_dir()
            # Re-run merge logic (it's fast enough for text files usually)
            final_files = merge_files(valid_files, st.session_state['current_strategy'], session_dir)
            
            st.success(f"Files prepared using: **{st.session_state['current_strategy']}**")
            
            if len(final_files) == 1 and st.session_state['current_strategy'] == "One File":
                # Direct download for single file
                with open(final_files[0], "rb") as f:
                    st.download_button(
                        label="⬇️ Download Merged File",
                        data=f,
                        file_name="All_Processed_Videos.txt",
                        mime="text/plain",
                        type="primary"
                    )
            else:
                # Create ZIP
                zip_buffer = io.BytesIO()
                zip_name = "youscriber_export.zip"
                if "Chunks" in st.session_state['current_strategy']:
                    zip_name = "youscriber_merged_batches.zip"

                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    for file_path in final_files:
                        try:
                            if st.session_state['current_strategy'] != "No Merge":
                                 zf.write(str(file_path), arcname=file_path.name)
                            else:
                                session_dir = get_session_dir()
                                rel_path = file_path.relative_to(session_dir)
                                zf.write(str(file_path), arcname=str(rel_path))
                        except ValueError:
                            zf.write(str(file_path), arcname=file_path.name)
                
                st.download_button(
                    label="⬇️ Download ZIP",
                    data=zip_buffer.getvalue(),
                    file_name=zip_name,
                    mime="application/zip",
                    type="primary"
                )
    else:
        st.info("No files processed yet. Import from YouTube or upload local files to begin.")

if __name__ == "__main__":
    main()
