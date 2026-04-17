"""
YouScriber - CustomTkinter Desktop Interface

This module provides the native desktop UI for the YouTube subtitle ETL tool
using customtkinter. It imports all business logic from the shared core.py module.

Run with: python desktop/app_gui.py
"""

import customtkinter as ctk
import threading
import pathlib
import zipfile
import os
import tempfile
from pathlib import Path
from tkinter import filedialog

# Import shared business logic
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from core import (
    fetch_video_list,
    download_video_subtitles,
    process_local_files,
    merge_files,
    zip_files,
)

# Set customtkinter theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")


class YouScriberApp(ctk.CTk):
    """Main desktop application window."""
    
    def __init__(self):
        super().__init__()
        
        # Window configuration
        self.title("YouScriber - YouTube Subtitle Extractor")
        self.geometry("900x700")
        self.minsize(800, 600)
        
        # Session configuration
        self.session_id = Path(tempfile.gettempdir()).joinpath("youscriber", f"gui_{self._generate_session_id()}")
        self.session_id.mkdir(parents=True, exist_ok=True)
        
        # State tracking
        self.yt_urls = []
        self.fetched_videos = []
        self.excluded_videos = []
        self.processed_files = []
        self.browser = "None"
        self.player_client = "android_vr"
        self.group_by_playlist = True
        self.merge_strategy = "No Merge"
        self.log_entries = []
        self.progress = 0.0
        
        # Thread safety lock for UI updates
        self.ui_lock = threading.Lock()
        
        # Create UI
        self._create_ui()
    
    def _generate_session_id(self):
        """Generate a unique session ID."""
        import time
        return f"{int(time.time())}_{id(self)}"
    
    def _create_ui(self):
        """Create the main UI components."""
        # Main tabview
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Create tabs
        self.tabview.add("youtube")
        self.tabview.add("local")
        
        # YouTube tab
        self._create_youtube_tab()
        
        # Local tab
        self._create_local_tab()
        
        # Status frame (bottom)
        self.status_frame = ctk.CTkFrame(self, height=100)
        self.status_frame.pack(fill="x", padx=10, pady=(0, 10))
        
        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(self.status_frame, width=200)
        self.progress_bar.pack(side="left", padx=5, pady=10)
        
        # Progress label
        self.progress_label = ctk.CTkLabel(self.status_frame, text="Ready")
        self.progress_label.pack(side="left", padx=5, pady=10)
        
        # Log textbox
        self.log_text = ctk.CTkTextbox(self.status_frame, height=90, width=670)
        self.log_text.pack(side="right", fill="both", expand=True, padx=5)
        self.log_text.configure(state="disabled")
    
    def _create_youtube_tab(self):
        """Create the YouTube Import tab content."""
        youtube_frame = ctk.CTkFrame(self.tabview.tab("youtube"))
        youtube_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        ctk.CTkLabel(youtube_frame, text="YouTube Import", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        ctk.CTkLabel(youtube_frame, text="Paste YouTube URLs below to fetch and harvest subtitles.", 
                     text_color="gray").pack()
        
        # URL entry frame
        url_frame = ctk.CTkFrame(youtube_frame)
        url_frame.pack(fill="x", pady=10)
        
        # URL entry
        self.url_entry = ctk.CTkEntry(url_frame, height=50, placeholder_text="https://www.youtube.com/watch?v=...\nhttps://www.youtube.com/playlist?list=...")
        self.url_entry.pack(fill="x", padx=10, pady=5)
        
        # Browser and player client columns
        col_frame = ctk.CTkFrame(url_frame)
        col_frame.pack(fill="x", padx=10, pady=5)
        
        # Browser selection
        browser_var = ctk.StringVar(value=self.browser)
        self.browser_combo = ctk.CTkComboBox(col_frame, values=["None", "safari", "chrome", "firefox", "edge"], 
                                             variable=browser_var, width=150)
        self.browser_combo.pack(side="left", padx=(0, 10))
        
        # Player client selection
        player_clients = ["android_vr", "android", "web", "tv", "web_safari"]
        player_var = ctk.StringVar(value=self.player_client)
        self.player_combo = ctk.CTkComboBox(col_frame, values=player_clients, variable=player_var, width=200)
        self.player_combo.pack(side="left")
        
        # Fetch button
        self.fetch_btn = ctk.CTkButton(url_frame, text="Fetch List", command=self._fetch_list, height=50)
        self.fetch_btn.pack(fill="x", padx=10, pady=5)
        
        # Videos list frame
        self.videos_frame = ctk.CTkFrame(youtube_frame)
        self.videos_frame.pack(fill="both", expand=True, pady=10)
        
        # Scrolling canvas for video list
        self.videos_canvas = ctk.CTkCanvas(self.videos_frame)
        self.videos_canvas.pack(fill="both", expand=True)
        
        # Scrollbar
        self.videos_scrollbar = ctk.CTkScrollbar(self.videos_frame, orientation="vertical")
        self.videos_scrollbar.pack(side="right", fill="y")
        self.videos_canvas.configure(yscrollcommand=self.videos_scrollbar.set)
        self.videos_scrollbar.configure(command=self.videos_canvas.yview)
        
        # Create window for video checkboxes
        self.videos_window = ctk.CTkFrame(self.videos_canvas, width=880, height=600)
        self.videos_window.pack_propagate(False)
        
        # Videos list (initially empty)
        self.videos_labels = {}
        
        # Start harvesting frame
        self.harvest_frame = ctk.CTkFrame(youtube_frame)
        self.harvest_frame.pack(fill="x", pady=10)
        
        self.start_btn = ctk.CTkButton(self.harvest_frame, text="Start Harvesting", 
                                        command=self._start_harvesting, height=50, 
                                        state="disabled")
        self.start_btn.pack(fill="x", padx=10, pady=5)
        
        # Group by playlist checkbox
        col_frame2 = ctk.CTkFrame(youtube_frame)
        col_frame2.pack(fill="x", pady=10)
        
        self.group_var = ctk.BooleanVar(value=self.group_by_playlist)
        self.group_checkbox = ctk.CTkCheckBox(col_frame2, text="Group by Playlist", 
                                              variable=self.group_var)
        self.group_checkbox.pack(side="left")
    
    def _create_local_tab(self):
        """Create the Local Import tab content."""
        local_frame = ctk.CTkFrame(self.tabview.tab("local"))
        local_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Title
        ctk.CTkLabel(local_frame, text="Local Import", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        ctk.CTkLabel(local_frame, text="Upload local subtitle files (.vtt, .srt, .txt) with their metadata JSON files.", 
                     text_color="gray").pack()
        
        # File upload button
        upload_btn = ctk.CTkButton(local_frame, text="Choose Subtitle Files", 
                                   command=self._select_local_files, height=50)
        upload_btn.pack(fill="x", padx=10, pady=10)
        
        # Selected files list
        self.local_files_frame = ctk.CTkFrame(local_frame)
        self.local_files_frame.pack(fill="both", expand=True, pady=10)
        self.local_files_canvas = ctk.CTkCanvas(self.local_files_frame)
        self.local_files_canvas.pack(fill="both", expand=True)
        
        # Local scrollbar
        local_scrollbar = ctk.CTkScrollbar(self.local_files_frame, orientation="vertical")
        local_scrollbar.pack(side="right", fill="y")
        self.local_files_canvas.configure(yscrollcommand=local_scrollbar.set)
        local_scrollbar.configure(command=self.local_files_canvas.yview)
        
        # Local files window
        self.local_files_window = ctk.CTkFrame(self.local_files_canvas, width=880, height=600)
        self.local_files_window.pack_propagate(False)
        
        # Process button
        self.process_local_btn = ctk.CTkButton(local_frame, text="Process Local Files", 
                                               command=self._process_local_files, height=50)
        self.process_local_btn.pack(fill="x", padx=10, pady=10)
        
        # Merge strategy section
        ctk.CTkLabel(local_frame, text="Merge Strategy:", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        
        col_local = ctk.CTkFrame(local_frame)
        col_local.pack(fill="x", padx=10, pady=5)
        
        self.merge_var = ctk.StringVar(value=self.merge_strategy)
        merge_options = ["No Merge", "One File", "Medium Chunks (~50k chars)", "Large Chunks (~200k chars)"]
        self.merge_combo = ctk.CTkComboBox(col_local, values=merge_options, variable=self.merge_var, width=300)
        self.merge_combo.pack(side="left", padx=(0, 10))
        
        ctk.CTkLabel(col_local, text="One text file per video / Single combined file / Batch files").pack(side="left")
    
    def _update_ui(self, status_text: str, percentage: float = 1.0):
        """Thread-safe UI update method."""
        def update():
            with self.ui_lock:
                self.progress_bar.set(min(1.0, percentage))
                self.progress_label.configure(text=status_text)
                # FIXED: Removed self.processed_files = [] to prevent race condition data loss
                # Clear log
                self.log_text.configure(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.configure(state="disabled")
        
        self.after(0, update)
    
    def _add_log(self, message: str):
        """Add a message to the log (thread-safe)."""
        with self.ui_lock:
            self.log_entries.insert(0, message)
            if len(self.log_entries) > 100:
                self.log_entries = self.log_entries[:100]
            # Show last 50 entries
            log_text = '\n'.join(self.log_entries[-50:])
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("1.0", log_text)
            self.log_text.configure(state="disabled")
    
    def _fetch_list(self):
        """Fetch video list from YouTube."""
        if not self.url_entry.get().strip():
            self._add_log("⚠️ Please enter at least one YouTube URL.")
            return

        # Pull current selections from UI controls before launching worker thread.
        self.browser = self.browser_combo.get()
        self.player_client = self.player_combo.get()
        
        urls = [url.strip() for url in self.url_entry.get().strip().split('\n') 
                if url.strip() and 'youtube.com' in url]
        
        self._add_log(f"Fetching list for {len(urls)} URL(s)...")
        
        def fetch():
            try:
                video_list = fetch_video_list(
                    urls,
                    browser=self.browser,
                    player_client=self.player_client,
                    progress_callback=lambda st, pg: self._update_ui(st, pg),
                    error_callback=lambda msg: self._add_log(f"❌ {msg}")
                )
                self.fetched_videos = video_list
                self._add_log(f"✓ Fetched {len(video_list)} video(s)")
                
                if video_list:
                    self._add_log(f"✓ Found {len(video_list)} video(s)!")
                    
                    # Update videos frame
                    self._populate_videos_frame(video_list)
                    
                    # Enable start button
                    self.start_btn.configure(state="normal")
                    
                    # Clear excluded videos
                    self.excluded_videos = []
            except Exception as e:
                self._add_log(f"❌ Error: {str(e)}")
                self._add_log(f"Error fetching videos: {str(e)}")
        
        # Run in separate thread
        threading.Thread(target=fetch, daemon=True).start()
    
    def _populate_videos_frame(self, video_list):
        """Populate the videos frame with checkboxes."""
        # Clear existing
        for label in self.videos_labels.values():
            label.destroy()
        self.videos_labels.clear()
        
        # Calculate scrollbar height
        canvas_width = self.videos_canvas.winfo_reqwidth()
        frame_width = self.videos_window.winfo_width()
        canvas_height = self.videos_canvas.winfo_reqheight()
        frame_height = self.videos_window.winfo_height()
        
        # Adjust window size based on number of videos
        required_height = len(video_list) * 50 + 100
        new_height = min(required_height, frame_height * 2)
        self.videos_window.update_idletasks()
        
        # Pack videos
        for idx, video in enumerate(video_list):
            checkbox = ctk.CTkCheckBox(self.videos_window, text=video['title'], 
                                       variable=ctk.StringVar(value=True))
            checkbox.grid(row=idx, column=0, padx=10, pady=5, sticky="w")
            self.videos_labels[video['title']] = checkbox
    
    def _start_harvesting(self):
        """Start harvesting subtitles from selected videos."""
        selected = [video for video, checkbox in self.videos_labels.items() 
                    if checkbox.get() == 1]
        
        if not selected:
            self._add_log("⚠️ Please select at least one video to harvest.")
            return

        # Pull current selections from UI controls before launching worker thread.
        self.browser = self.browser_combo.get()
        self.player_client = self.player_combo.get()
        self.group_by_playlist = self.group_var.get()
        
        self.start_btn.configure(state="disabled")
        self._add_log(f"🌱 Starting to harvest subtitles from {len(selected)} video(s)...")
        
        def harvest():
            try:
                st_session_id = str(self.session_id)
                selected_videos = selected
                total_videos = len(selected_videos)
                processed_files = []
                
                for i, video in enumerate(selected_videos):
                    url = video.get('url', '')
                    progress = (i / total_videos) * 0.9
                    
                    # Update progress
                    self._update_ui(f"Processing: {video['title'][:50]}", 
                                   progress - self.progress)
                    self._add_log(f"Processing: {video['title'][:50]}")
                    
                    try:
                        result = download_video_subtitles(
                            url,
                            session_id=st_session_id,
                            browser=self.browser,
                            player_client=self.player_client,
                            group_by_playlist=self.group_by_playlist,
                            progress_callback=lambda st, pg: self._update_ui(st, pg),
                            error_callback=lambda msg: self._add_log(f"❌ {msg}")
                        )
                        
                        if result:
                            processed_files.extend(result)
                            self._add_log(f"✓ Downloaded: {video['title'][:50]}...")
                        else:
                            self._add_log(f"⚠️ No subtitles found for: {video['title'][:50]}")
                    except Exception as e:
                        self._add_log(f"❌ Error processing {video['title'][:50]}: {str(e)}")
                
                # Update final progress
                self._update_ui("Processing complete", 0.1)
                self._update_ui("Ready", 1.0)
                
                self.processed_files = processed_files
                self._add_log(f"✓ Harvesting complete! Processed {len(processed_files)} video(s).")
                
            except Exception as e:
                self._add_log(f"❌ Harvesting error: {str(e)}")
                self._add_log(str(e))
        
        # Run in separate thread
        threading.Thread(target=harvest, daemon=True).start()
    
    def _select_local_files(self):
        """Open file dialog to select local subtitle files."""
        filetypes = [
            ("Subtitle files", "*.vtt *.srt"),
            ("Metadata JSON", "*.json"),
            ("All files", "*.*")
        ]
        
        filenames = filedialog.askopenfilenames(
            title="Choose subtitle files",
            filetypes=filetypes
        )
        
        if filenames:
            # Convert tuple to list for process_local_files() compatibility
            self.local_files = list(filenames)
            self._add_log(f"✓ Selected {len(self.local_files)} file(s)")
            
            # Display in local files frame
            self._populate_local_files_frame(filenames)
    
    def _populate_local_files_frame(self, filenames):
        """Populate the local files frame with file names."""
        # Clear existing
        for label in self.local_files_labels.values() if hasattr(self, 'local_files_labels') else []:
            label.destroy()
        if hasattr(self, 'local_files_labels'):
            self.local_files_labels.clear()
        
        # Adjust window size
        required_height = len(filenames) * 30 + 50
        self.local_files_window.update_idletasks()
        
        # Pack files
        for idx, filename in enumerate(filenames):
            text = ctk.CTkLabel(self.local_files_window, text=str(filename))
            text.grid(row=idx, column=0, padx=10, pady=5, sticky="w")
            self.local_files_labels[str(filename)] = text
    
    def _process_local_files(self):
        """Process selected local files."""
        if not hasattr(self, 'local_files') or not self.local_files:
            self._add_log("⚠️ Please select files first.")
            return
        
        self._add_log(f"Processing {len(self.local_files)} local file(s)...")
        
        def process():
            try:
                processed = process_local_files(
                    self.local_files,
                    session_id=str(self.session_id),
                    progress_callback=lambda st, pg: self._update_ui(st, pg),
                    error_callback=lambda msg: self._add_log(f"❌ {msg}")
                )
                
                self._update_ui(f"✓ Processed {len(processed)} file(s)", 1.0)
                self._add_log(f"✓ Local files processed! Total: {len(processed)} file(s)")
                
                # Add to processed files
                self.processed_files.extend(processed)
                
            except Exception as e:
                self._add_log(f"❌ Error: {str(e)}")
        
        # Run in separate thread
        threading.Thread(target=process, daemon=True).start()
    
    def _update_merge_strategy(self):
        """Update merge strategy from combo box."""
        self.merge_strategy = self.merge_combo.get()
    
    def _download(self):
        """Create and download ZIP of processed files."""
        if not self.processed_files:
            self._add_log("⚠️ No processed files to download.")
            return
        
        self._add_log("Creating ZIP archive...")
        
        def download():
            try:
                # Merge files
                session_dir = pathlib.Path(tempfile.gettempdir()) / "youscriber" / self.session_id
                merged_files = merge_files(
                    self.processed_files,
                    self.merge_strategy,
                    session_dir
                )
                
                # Create ZIP
                output_zip = f"youscriber_output_{self.session_id}.zip"
                output_path = pathlib.Path(tempfile.gettempdir()) / output_zip
                
                zip_files(merged_files, output_path)
                self._add_log(f"✓ Created ZIP: {output_zip}")
                
                # Show download in temp directory
                import webbrowser
                temp_path = pathlib.Path(tempfile.gettempdir())
                self._add_log(f"ZIP saved to: {temp_path}")
                
                # Open temp directory
                self._add_log(f"Opening temp directory...")
                webbrowser.open(f"file://{temp_path.resolve()}")
                
            except Exception as e:
                self._add_log(f"❌ Error creating ZIP: {str(e)}")
        
        # Run in separate thread
        threading.Thread(target=download, daemon=True).start()


def main():
    """Main entry point."""
    app = YouScriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
