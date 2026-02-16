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

def process_youtube(urls: list, group_by_playlist: bool, progress_bar, status_text):
    """
    Downloads metadata and subtitles from YouTube using yt-dlp,
    cleans them, and prepares final text files.
    """
    processed_files = []
    
    session_dir = ensure_session_dir()
    
    ydl_opts = {
        'skip_download': True,
        'write_sub': True,
        'write_auto_sub': True, # Prefer auto-subs if manual not available
        'sub_langs': ['en', 'ru', 'uk'], # Priority order
        'write_info_json': True,
        'write_description': False, # We get description from info.json
        'outtmpl': str(session_dir / '%(title)s [%(id)s].%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True, # Critical to not crash on one failed video
    }

    if group_by_playlist:
        ydl_opts['outtmpl'] = str(session_dir / '%(playlist_title)s/%(title)s [%(id)s].%(ext)s')

    status_text.text("Starting extraction...")
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            status_text.text(f"Processing URL: {url}")
            try:
                # 1. Extract Info & Download Subs
                info = ydl.extract_info(url, download=True)
                
                # Handle playlists vs single video
                entries = []
                if 'entries' in info:
                    entries = info['entries']
                else:
                    entries = [info]

                total_entries = len(entries)
                for i, entry in enumerate(entries):
                    if not entry: continue # Skip None entries from ignoreerrors

                    title = entry.get('title', 'video')
                    # Update progress
                    progress_bar.progress((i + 1) / total_entries, text=f"Processing: {title}")

                    # 2. Find the generated files
                    # yt-dlp doesn't return the exact paths of downloaded subs easily in the return dict
                    # So we search the temp dir for files matching the ID.
                    video_id = entry.get('id')
                    
                    # Search pattern depends on whether we grouped by playlist or not
                    # But simpler is to walk the TEMP_DIR and look for the ID
                    files_found = list(session_dir.rglob(f"*[{video_id}]*"))
                    
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
                    # We'll save it to a 'processed' subdir to separate from raw downloads
                    output_dir = session_dir / "processed"
                    if group_by_playlist and 'playlist_title' in entry and entry['playlist_title']:
                        safe_playlist_title = "".join([c for c in entry['playlist_title'] if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                        output_dir = output_dir / safe_playlist_title
                    
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    safe_title = "".join([c for c in title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
                    final_filename = output_dir / f"{safe_title}.txt"
                    
                    with open(final_filename, 'w', encoding='utf-8') as f:
                        f.write(final_content)
                    
                    processed_files.append(final_filename)

            except Exception as e:
                st.error(f"Error processing {url}: {str(e)}")

    return processed_files

# --- UI Layout ---

def main():
    st.sidebar.title("Available Settings")
    
    # Session State for accumulating files
    if 'processed_files' not in st.session_state:
        st.session_state['processed_files'] = []

    tab1, tab2 = st.tabs(["YouTube Import", "Local Import"])

    # --- TAB 1: YouTube ---
    with tab1:
        st.header("YouTube Downloader & Extractor")
        
        urls_input = st.text_area("Enter YouTube URLs (one per line):", height=150)
        group_playlist = st.checkbox("Group by Playlist/Channel", value=True)
        
        if st.button("Start Harvesting", type="primary"):
            if not urls_input.strip():
                st.error("Please enter at least one URL.")
            else:
                urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
                
                # UI Elements for progress
                progress_bar = st.progress(0, text="Ready to start...")
                status_text = st.empty()
                
                # clear previous run? maybe optional, but safer to avoid mixing
                # For now let's just process and add to session.
                # Actually, user might want to accumulate. 
                # Let's verify TEMP_DIR is clean or handle conflicts.
                # For simplicity in this session-based app, we'll clear temp on new run if requested
                # but 'processed_files' relies on them existing. 
                # Ideally, we should copy processed files to a persistent list.
                
                # Let's just clear temp dir at start of a batch? 
                # If we clear temp dir, we lose previous files if the user didn't download.
                # Let's NOT clear, just append.
                
                new_files = process_youtube(urls, group_playlist, progress_bar, status_text)
                
                if new_files:
                    st.session_state['processed_files'].extend(new_files)
                    st.success(f"Successfully processed {len(new_files)} videos!")
                else:
                    st.warning("No videos were processed successfully.")
                
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
        st.write(f"Total Processed Files: **{len(st.session_state['processed_files'])}**")
        
        # Display list of files (optional)
        with st.expander("Show File List"):
            for p in st.session_state['processed_files']:
                st.text(p.name)

        # Create ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in st.session_state['processed_files']:
                # Compute arcname (archive name)
                # If it's in a subdir (like playlist), preserve that structure relative to 'processed'
                # Our paths are like: .../temp/processed/PlaylistName/Video.txt
                # or .../temp/local_processed/Video.txt
                
                # Naive approach: just filename, but collisions possible.
                # Better: relative to TEMP_DIR
                try:
                    session_dir = get_session_dir()
                    rel_path = file_path.relative_to(session_dir)
                    zf.write(str(file_path), arcname=str(rel_path))
                except ValueError:
                    # Fallback if path manipulation fails
                    zf.write(str(file_path), arcname=file_path.name)
        
        st.download_button(
            label="Download All as ZIP",
            data=zip_buffer.getvalue(),
            file_name="youscriber_export.zip",
            mime="application/zip",
            type="primary"
        )
    else:
        st.info("No files processed yet. Import from YouTube or upload local files to begin.")

if __name__ == "__main__":
    main()
