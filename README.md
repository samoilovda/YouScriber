# YouScriber Instructions

## Installation & Setup

1.  **Prerequisites**:
    *   Python 3.8+ installed.
    *   `ffmpeg` is recommended for optimal `yt-dlp` performance, though often optional for just subtitles.
        *   **macOS**: `brew install ffmpeg`
        *   **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

2.  **Install Dependencies**:
    Open your terminal or command prompt in the project folder and run:
    ```bash
    pip install -r requirements.txt
    ```

## Running the App

Run the following command in your terminal:

```bash
streamlit run app.py
```

This will open the application in your default web browser (usually at `http://localhost:8501`).

## Usage Guide

### Tab 1: YouTube Import
1.  Paste YouTube URLs into the text area (one per line). Supported:
    *   Single video URLs
    *   Playlist URLs
    *   Channel URLs
2.  (Optional) Uncheck "Group by Playlist" if you want all files in one flat structure.
3.  Click **"Start Harvesting"**.
4.  Wait for the process to complete.

### Tab 2: Local Import
1.  If you have manually downloaded `.vtt` or `.srt` files and their accompanying `.info.json` files, upload them here.
2.  You can select multiple files at once.
3.  The app will automatically pair subtitle files with metadata JSONs if they share the same filename.
4.  Click **"Process Local Files"**.

### Export & Merging
1.  **Merge Strategy**: In the sidebar, choose how you want your files:
    *   **No Merge**: Standard behavior. One text file per video, downloaded as a ZIP.
    *   **One File**: All videos are concatenated into a single `All_Processed_Videos.txt` file.
    *   **Medium/Large Chunks**: Videos are grouped into batches (approx 50k or 200k characters) to fit context windows, downloaded as a ZIP of batches.
2.  Once files are processed, click the **"Download"** button at the bottom. The button label will adapt to your chosen strategy.
