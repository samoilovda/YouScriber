import sys
import os
import pathlib
import json
import traceback
from unittest.mock import MagicMock

# Mock streamlit to allow importing app.py which calls st.set_page_config
sys.modules["streamlit"] = MagicMock()
import streamlit as st

# Setup mock for ensure_session_dir and other st functions used in app.py
def mock_ensure_session_dir():
    path = pathlib.Path(os.getcwd()) / "output"
    path.mkdir(parents=True, exist_ok=True)
    print(f"DEBUG: Session dir is {path}")
    return path

class MockProgressBar:
    def progress(self, val, text=""):
        print(f"Progress: {val*100:.1f}% - {text}")
    def empty(self):
        pass

class MockStatusText:
    def text(self, text):
        print(f"Status: {text}")
    def empty(self):
        pass

class MockLogger:
    def __init__(self):
        self.errors = []
    def debug(self, msg):
        print(f"DEBUG: {msg}")
    def warning(self, msg):
        print(f"WARNING: {msg}")
    def error(self, msg):
        print(f"ERROR: {msg}")
        self.errors.append(msg)

# Import the actual logic
import app

# Override the logger class
app.MyLogger = MockLogger

# Override the ensure_session_dir to use a local folder instead of temp
app.ensure_session_dir = mock_ensure_session_dir

# Override st.error to print to console
st.error = lambda msg: print(f"ST_ERROR: {msg}")
st.warning = lambda msg: print(f"ST_WARNING: {msg}")

def main():
    playlist_url = "https://youtube.com/playlist?list=PLTWnQB38Se1sGGeJ0zcrStzgNGXKWBdW6"
    browser = "chrome"
    
    print(f"Fetching playlist info for: {playlist_url} using browser: {browser}")
    try:
        video_list = app.fetch_playlist_info([playlist_url], browser=browser)
    except Exception as e:
        print(f"Exception in fetch_playlist_info: {e}")
        traceback.print_exc()
        return
    
    if not video_list:
        print("No videos found. Check if the playlist URL is correct or if Chrome cookies are accessible.")
        return

    print(f"Found {len(video_list)} videos.")
    
    # Process all videos
    progress_bar = MockProgressBar()
    status_text = MockStatusText()
    
    # We can try to monkeypatch app.download_videos to be more verbose if needed
    # but let's just run it and see if the errors are clearer now.
    
    # Actually, let's try to add 'format': 'best' to the internal download_opts 
    # since we are skip_download=True anyway.
    
    processed_files = app.download_videos(
        video_list, 
        group_by_playlist=True, 
        progress_bar=progress_bar, 
        status_text=status_text, 
        browser=browser
    )
    
    print(f"\nSuccessfully processed {len(processed_files)} files.")
    print(f"Output directory: {os.path.abspath('output/processed')}")

if __name__ == "__main__":
    main()
