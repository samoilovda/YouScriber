"""
YouScriber - Streamlit Web Interface

This module provides the Streamlit web UI for the YouTube subtitle ETL tool.
It imports all business logic from the shared core.py module.

Run with: streamlit run web/app_streamlit.py
"""

import streamlit as st
import pathlib
import zipfile
import os
import tempfile
import uuid
from pathlib import Path

# Import shared business logic
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import (
    fetch_video_list,
    download_video_subtitles,
    process_local_files,
    merge_files,
    zip_files,
    DEFAULT_SUB_LANGS,
    SECONDARY_SUB_LANGS,
)


# =============================================================================
# SESSION STATE MANAGEMENT
# =============================================================================

def init_session_state():
    """Initialize Streamlit session state for form storage."""
    if 'youtube_urls' not in st.session_state:
        st.session_state['youtube_urls'] = []
    if 'fetched_videos' not in st.session_state:
        st.session_state['fetched_videos'] = []
    if 'processed_files' not in st.session_state:
        st.session_state['processed_files'] = []
    if 'group_by_playlist' not in st.session_state:
        st.session_state['group_by_playlist'] = True
    if 'merge_strategy' not in st.session_state:
        st.session_state['merge_strategy'] = 'No Merge'
    if 'browser' not in st.session_state:
        st.session_state['browser'] = 'None'
    if 'player_client' not in st.session_state:
        st.session_state['player_client'] = 'android_vr'
    if 'session_id' not in st.session_state:
        run_id = st.session_state.get('run_id')
        st.session_state['session_id'] = str(run_id) if run_id else f"web_{uuid.uuid4().hex}"
    if 'progress' not in st.session_state:
        st.session_state['progress'] = 0.0
    if 'last_status' not in st.session_state:
        st.session_state['last_status'] = ''
    if 'log_entries' not in st.session_state:
        st.session_state['log_entries'] = []
    if 'excluded_videos' not in st.session_state:
        st.session_state['excluded_videos'] = []


# =============================================================================
# LAYOUT HELPERS
# =============================================================================

def update_progress(status_text: str, percentage: float = 1.0):
    """Update the progress bar in the sidebar."""
    st.session_state['progress'] = min(1.0, st.session_state.get('progress', 0) + percentage * 0.1)
    st.session_state['last_status'] = status_text


def add_log(message: str):
    """Add a message to the log entries."""
    st.session_state['log_entries'].insert(0, message)
    # Keep only last 50 entries
    if len(st.session_state['log_entries']) > 50:
        st.session_state['log_entries'] = st.session_state['log_entries'][:50]
    
    # Update the log text in session state for display
    log_text = '\n'.join(st.session_state['log_entries'])
    st.session_state['log_text'] = log_text


# =============================================================================
# WEB TAB 1: YOUTUBE IMPORT
# =============================================================================

def youtube_import_tab():
    """Render the YouTube Import tab."""
    st.markdown("### YouTube Import")
    st.markdown("Paste YouTube URLs below to fetch and harvest subtitles.")
    st.markdown("---")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        urls_text = st.text_area(
            "Paste YouTube URLs (one per line):",
            height=150,
            placeholder="https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/playlist?list=..."
        )
        st.session_state['youtube_urls'] = [
            url.strip() for url in urls_text.split('\n') 
            if url.strip() and 'youtube.com' in url
        ]
    
    with col2:
        # Browser selection dropdown
        browsers = ['None', 'safari', 'chrome', 'firefox', 'edge']
        selected_browser = st.selectbox(
            "Browser Cookies (optional):",
            browsers,
            index=browsers.index(st.session_state['browser'])
        )
        st.session_state['browser'] = selected_browser
        
        # Player client selection
        player_clients = ['android_vr', 'android', 'web', 'tv', 'web_safari']
        selected_client = st.selectbox(
            "Player Client:",
            player_clients,
            index=player_clients.index(st.session_state['player_client'])
        )
        st.session_state['player_client'] = selected_client
    
    st.divider()
    
    # Fetch List Button
    if st.button("Fetch List", type="primary", use_container_width=True):
        if not st.session_state['youtube_urls']:
            add_log("⚠️ Please enter at least one YouTube URL.")
            return
        
        urls = st.session_state['youtube_urls']
        add_log(f"Fetching list for {len(urls)} URL(s)...")
        
        try:
            with st.spinner("Connecting to YouTube..."):
                video_list = fetch_video_list(
                    urls,
                    browser=st.session_state['browser'],
                    player_client=st.session_state['player_client'],
                    progress_callback=update_progress,
                    error_callback=add_log
                )
            
            st.session_state['fetched_videos'] = video_list
            add_log(f"✓ Fetched {len(video_list)} video(s)")
            
            if video_list:
                st.success(f"Found {len(video_list)} video(s)!")
        except Exception as e:
            add_log(f"❌ Error: {str(e)}")
            st.error(f"Error fetching videos: {str(e)}")
    
    # Display Fetched Videos with Checkboxes
    if st.session_state['fetched_videos']:
        st.subheader("Fetched Videos")
        
        selected_videos = []
        for idx, video in enumerate(st.session_state['fetched_videos']):
            checkbox = st.checkbox(
                video['title'],
                key=f"video_{idx}",
                value=video['title'] not in st.session_state.get('excluded_videos', [])
            )
            if checkbox:
                selected_videos.append(video)
        
        st.session_state['selected_videos'] = selected_videos
        
        # Excluded videos display
        excluded = st.session_state.get('excluded_videos', [])
        if excluded:
            with st.expander("View Excluded Videos"):
                for exc_video in excluded:
                    st.warning(f"✗ {exc_video['title']}")
        
        st.divider()
    
    # Start Harvesting Button
    if st.session_state.get('fetched_videos'):
        if st.button("Start Harvesting", type="primary", use_container_width=True, 
                     disabled=len(st.session_state['fetched_videos']) == 0):
            selected_videos = st.session_state.get('selected_videos', [])
            
            if not selected_videos:
                add_log("⚠️ Please select at least one video to harvest.")
                return
            
            total_videos = len(selected_videos)
            
            # Reset processed files
            st.session_state['processed_files'] = []
            
            st.info(f"🌱 Starting to harvest subtitles from {total_videos} video(s)...")
            
            # Process each video
            for i, video in enumerate(selected_videos):
                video_id = video.get('id', '')
                url = video.get('url', '')
                
                # Update progress
                progress = (i / total_videos) * 0.9
                update_progress(f"Processing: {video['title']}", 
                               (progress - st.session_state.get('progress', 0)) / total_videos * 10)
                
                try:
                    # Process single video (yt-dlp may process multiple at once)
                    result = download_video_subtitles(
                        url,
                        session_id=st.session_state['session_id'],
                        browser=st.session_state['browser'],
                        player_client=st.session_state['player_client'],
                        group_by_playlist=st.session_state['group_by_playlist'],
                        progress_callback=update_progress,
                        error_callback=add_log
                    )
                    
                    if result:
                        st.session_state['processed_files'].extend(result)
                        add_log(f"✓ Downloaded: {video['title'][:50]}...")
                    else:
                        add_log(f"⚠️ No subtitles found for: {video['title'][:50]}")
                        
                except Exception as e:
                    add_log(f"❌ Error processing {video['title'][:50]}: {str(e)}")
            
            # Update final progress
            update_progress("Processing complete", 0.1)
            
            st.session_state['progress'] = 1.0
            st.success(f"✓ Harvesting complete! Processed {len(st.session_state['processed_files'])} video(s).")
    
    st.divider()
    
    # Group by Playlist toggle
    col1, col2, col3 = st.columns(3)
    with col1:
        st.session_state['group_by_playlist'] = st.checkbox(
            "Group by Playlist",
            value=st.session_state['group_by_playlist'],
            key="group_checkbox"
        )
    
    # =============================================================================
    # WEB TAB 2: LOCAL IMPORT
    # =============================================================================

def local_import_tab():
    """Render the Local Import tab."""
    st.markdown("### Local Import")
    st.markdown("Upload local subtitle files (.vtt, .srt, .txt) with their metadata JSON files.")
    st.markdown("---")
    
    # File upload section
    uploaded_files = st.file_uploader(
        "Choose subtitle files",
        type=['vtt', 'srt', 'txt', 'json'],
        accept_multiple_files=True,
        help="Upload .vtt, .srt, or .txt files. Include .info.json files for metadata."
    )
    
    if uploaded_files:
        st.info(f"✓ Uploaded {len(uploaded_files)} file(s)")
        
        # Display uploaded files
        for file in uploaded_files:
            st.text(f"- {file.name}")
        
        if st.button("Process Local Files", type="primary", use_container_width=True):
            add_log(f"Processing {len(uploaded_files)} local file(s)...")
            
            try:
                # Save uploaded files to temp directory
                temp_dir = Path(tempfile.gettempdir()) / "youscriber" / st.session_state['session_id'] / "local_upload"
                temp_dir.mkdir(parents=True, exist_ok=True)
                
                saved_paths = []
                for uploaded_file in uploaded_files:
                    # Save to temp directory
                    save_path = temp_dir / uploaded_file.name
                    save_path.write_bytes(uploaded_file.read())
                    saved_paths.append(str(save_path))
                
                add_log(f"✓ Saved {len(saved_paths)} file(s) to temp directory")
                
                # Process local files
                processed = process_local_files(
                    saved_paths,
                    session_id=st.session_state['session_id'],
                    progress_callback=update_progress,
                    error_callback=add_log
                )
                
                st.session_state['processed_files'].extend(processed)
                add_log(f"✓ Processed {len(processed)} file(s)")
                
                st.success(f"✓ Local files processed! Total processed: {len(st.session_state['processed_files'])}")
                
            except Exception as e:
                add_log(f"❌ Error: {str(e)}")
                st.error(f"Error processing local files: {str(e)}")
    
    st.divider()
    
    # Merge Strategy Selection
    st.subheader("Export Settings")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        merge_options = [
            ("No Merge", "One text file per video, downloaded as ZIP"),
            ("One File", "Single combined file for all videos"),
            ("Medium Chunks", "Batch files (~50k chars each)"),
            ("Large Chunks", "Batch files (~200k chars each)")
        ]
        selected_merge = st.selectbox(
            "Merge Strategy:",
            [opt[0] for opt in merge_options],
            index=[opt[0] == st.session_state['merge_strategy'] for opt in merge_options].index(True) if st.session_state['merge_strategy'] else 0
        )
        st.session_state['merge_strategy'] = selected_merge
    
    # =============================================================================
    # DOWNLOAD SECTION
    # =============================================================================

def download_section():
    """Render the download section."""
    if st.session_state['processed_files']:
        st.divider()
        st.subheader("Download Processed Files")
        
        # Show progress
        progress = st.session_state.get('progress', 0)
        st.progress(progress)
        
        st.text(st.session_state.get('last_status', 'Ready to download'))
        
        # Merge files first
        merge_strategy = st.session_state['merge_strategy']
        
        # Get session directory for merging
        session_dir = pathlib.Path(tempfile.gettempdir()) / "youscriber" / st.session_state['session_id']
        
        merged_files = merge_files(
            st.session_state['processed_files'],
            merge_strategy,
            session_dir
        )
        
        # Create output ZIP
        output_zip = f"youscriber_output_{st.session_state['session_id']}.zip"
        output_path = pathlib.Path(tempfile.gettempdir()) / output_zip
        
        try:
            zip_files(merged_files, output_path)
            add_log(f"✓ Created ZIP: {output_zip}")
            
            # Display download button
            with open(output_path, 'rb') as f:
                btn = st.download_button(
                    label=f"Download ({len(merged_files)} file(s))",
                    data=f,
                    file_name=output_zip,
                    mime='application/zip'
                )
            
            if btn:
                add_log(f"✓ Download started: {output_zip}")
                
        except Exception as e:
            st.error(f"Error creating ZIP: {str(e)}")
            add_log(f"❌ ZIP creation failed: {str(e)}")


# =============================================================================
# MAIN APP
# =============================================================================

def main():
    """Main Streamlit application."""
    # Page config
    st.set_page_config(
        page_title="YouScriber - YouTube Subtitle Extractor",
        page_icon="🎬",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Title
    st.markdown("<h1 style='text-align: center;'>🎬 YouScriber</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #666;'>YouTube Subtitle ETL for LLM RAG</p>", unsafe_allow_html=True)
    st.divider()
    
    # Initialize session state
    init_session_state()
    
    # Create tabs
    tab1, tab2 = st.tabs(["📥 YouTube Import", "📂 Local Import"])
    
    with tab1:
        youtube_import_tab()
    
    with tab2:
        local_import_tab()
    
    # Sidebar for merge strategy and download
    with st.sidebar:
        st.header("⚙️ Settings")
        
        st.subheader("Grouping")
        st.session_state['group_by_playlist'] = st.checkbox(
            "Group by Playlist",
            value=st.session_state['group_by_playlist']
        )
        
        st.divider()
        
        st.subheader("Merge Strategy")
        merge_options = [
            "No Merge",
            "One File",
            "Medium Chunks (~50k chars)",
            "Large Chunks (~200k chars)"
        ]
        selected_merge = st.selectbox(
            "Strategy:",
            merge_options,
            index=merge_options.index(st.session_state['merge_strategy'])
        )
        st.session_state['merge_strategy'] = selected_merge
    
    # Download section (below main content)
    download_section()
    
    # Progress indicator
    if 'log_text' in st.session_state:
        st.caption(st.session_state['log_text'])


if __name__ == "__main__":
    main()
