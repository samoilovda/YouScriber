"""
YouScriber - CustomTkinter Desktop Interface

This module provides the native desktop UI for the YouTube subtitle ETL tool
using customtkinter. It imports all business logic from the shared core.py module.

Run with: python desktop/app_gui.py
"""

import customtkinter as ctk
import threading
import pathlib
import tempfile
import time
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
    OperationCancelled,
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
        self.session_id = f"gui_{self._generate_session_id()}"
        self.session_dir = Path(tempfile.gettempdir()) / "youscriber" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        # State tracking
        self.yt_urls = []
        self.fetched_videos = []
        self.excluded_videos = []
        self.processed_files = []
        self.video_items = []
        self.local_files = []
        self.local_files_labels = {}
        self.browser = "None"
        self.player_client = "android_vr"
        self.group_by_playlist = True
        self.merge_strategy = "No Merge"
        self.log_entries = []
        self.progress = 0.0
        
        # Thread safety lock for UI updates
        self.ui_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.worker_lock = threading.Lock()
        self.active_workers = {}
        self.stop_event = threading.Event()
        self.shutting_down = False
        
        # Create UI
        self._create_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close_requested)
    
    def _generate_session_id(self):
        """Generate a unique session ID."""
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

        # Cancel button
        self.cancel_btn = ctk.CTkButton(
            self.status_frame,
            text="Cancel Task",
            command=self._cancel_active_tasks,
            width=120,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=5, pady=10)
        
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

    def _run_on_ui_thread(self, func, *args):
        """Execute callable on Tk main thread."""
        if threading.current_thread() is threading.main_thread():
            func(*args)
        else:
            self.after(0, lambda: func(*args))

    def _prune_workers_locked(self):
        """Remove completed workers from the active worker map."""
        stale = [name for name, worker in self.active_workers.items() if not worker.is_alive()]
        for name in stale:
            self.active_workers.pop(name, None)

    def _has_active_workers(self):
        """Return True if any worker is currently running."""
        with self.worker_lock:
            self._prune_workers_locked()
            return bool(self.active_workers)

    def _set_busy_controls(self, busy: bool):
        """Enable/disable primary controls while work is running."""
        if busy:
            self.fetch_btn.configure(state="disabled")
            self.start_btn.configure(state="disabled")
            self.process_local_btn.configure(state="disabled")
            self.cancel_btn.configure(state="normal")
            return

        self.fetch_btn.configure(state="normal")
        self.process_local_btn.configure(state="normal")
        self.start_btn.configure(state="normal" if self.video_items else "disabled")
        if not self._has_active_workers():
            self.cancel_btn.configure(state="disabled")

    def _start_worker(self, worker_name: str, target) -> bool:
        """Start a named non-daemon worker if no other worker is active."""
        active_names = []
        worker = None
        with self.worker_lock:
            self._prune_workers_locked()
            if self.shutting_down:
                return False
            if self.active_workers:
                active_names = sorted(self.active_workers.keys())
            else:
                self.stop_event.clear()
                worker = threading.Thread(
                    target=self._worker_entry,
                    args=(worker_name, target),
                    name=f"youscriber-{worker_name}",
                    daemon=False,
                )
                self.active_workers[worker_name] = worker

        if active_names:
            self._add_log(f"⚠️ Task already running: {', '.join(active_names)}")
            return False

        self._set_busy_controls(True)
        worker.start()
        return True

    def _worker_entry(self, worker_name: str, target):
        """Run worker target and always release worker bookkeeping."""
        try:
            target(self.stop_event)
        except OperationCancelled as exc:
            self._run_on_ui_thread(self._add_log, f"⚠️ {str(exc)}")
        except Exception as exc:
            self._run_on_ui_thread(self._add_log, f"❌ Worker '{worker_name}' failed: {str(exc)}")
        finally:
            with self.worker_lock:
                self.active_workers.pop(worker_name, None)
                has_workers = bool(self.active_workers)
            if not has_workers:
                self.stop_event.clear()
                if not self.shutting_down:
                    self._run_on_ui_thread(self._set_busy_controls, False)

    def _cancel_active_tasks(self):
        """Request cancellation for active worker(s)."""
        with self.worker_lock:
            self._prune_workers_locked()
            active_names = sorted(self.active_workers.keys())
        if not active_names:
            self._add_log("ℹ️ No active background tasks.")
            self.cancel_btn.configure(state="disabled")
            return

        self.stop_event.set()
        self._add_log(f"🛑 Cancellation requested for: {', '.join(active_names)}")

    def _join_active_workers(self, timeout_seconds: float = 20.0) -> bool:
        """Wait for active workers to finish."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            with self.worker_lock:
                self._prune_workers_locked()
                workers = list(self.active_workers.values())
            if not workers:
                return True
            for worker in workers:
                remaining = max(0.0, deadline - time.time())
                if remaining <= 0:
                    break
                worker.join(timeout=min(0.3, remaining))
        return not self._has_active_workers()

    def _on_close_requested(self):
        """Gracefully shut down active workers before closing UI."""
        if self.shutting_down:
            return

        self.shutting_down = True
        self._set_busy_controls(True)
        self.cancel_btn.configure(state="disabled")
        self.stop_event.set()
        self._add_log("Shutting down. Waiting for active tasks to stop...")

        def close_after_join():
            joined = self._join_active_workers(timeout_seconds=20.0)
            if not joined:
                self._run_on_ui_thread(
                    self._add_log,
                    "⚠️ Timed out waiting for workers. Close was cancelled; try again after tasks stop.",
                )
                self.shutting_down = False
                self._run_on_ui_thread(self._set_busy_controls, self._has_active_workers())
                return
            self._run_on_ui_thread(self.destroy)

        threading.Thread(target=close_after_join, daemon=True).start()
    
    def _update_ui(self, status_text: str, percentage: float = 1.0):
        """Thread-safe UI update method."""
        def update():
            with self.ui_lock:
                self.progress = min(1.0, max(0.0, percentage))
                self.progress_bar.set(self.progress)
                self.progress_label.configure(text=status_text)
        
        self._run_on_ui_thread(update)
    
    def _add_log(self, message: str):
        """Add a message to the log (thread-safe)."""
        def add():
            with self.ui_lock:
                self.log_entries.insert(0, message)
                if len(self.log_entries) > 100:
                    self.log_entries = self.log_entries[:100]
                log_text = '\n'.join(self.log_entries[:50])
                self.log_text.configure(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.insert("1.0", log_text)
                self.log_text.configure(state="disabled")

        self._run_on_ui_thread(add)
    
    def _fetch_list(self):
        """Fetch video list from YouTube."""
        if self._has_active_workers():
            self._add_log("⚠️ Wait for current task to finish or cancel it first.")
            return

        if not self.url_entry.get().strip():
            self._add_log("⚠️ Please enter at least one YouTube URL.")
            return

        # Pull current selections from UI controls before launching worker thread.
        browser = self.browser_combo.get()
        player_client = self.player_combo.get()
        
        urls = [url.strip() for url in self.url_entry.get().strip().split('\n') 
                if url.strip() and 'youtube.com' in url]
        
        self._add_log(f"Fetching list for {len(urls)} URL(s)...")
        
        def fetch(stop_event):
            try:
                video_list = fetch_video_list(
                    urls,
                    browser=browser,
                    player_client=player_client,
                    progress_callback=lambda st, pg: self._update_ui(st, pg),
                    error_callback=lambda msg: self._add_log(f"❌ {msg}"),
                    cancel_event=stop_event,
                )
                if stop_event.is_set():
                    self._run_on_ui_thread(self._on_fetch_cancelled)
                    return
                self._run_on_ui_thread(self._on_fetch_complete, video_list)
            except Exception as e:
                self._run_on_ui_thread(self._add_log, f"❌ Error: {str(e)}")
                self._run_on_ui_thread(self._add_log, f"Error fetching videos: {str(e)}")
        
        self._start_worker("fetch", fetch)

    def _on_fetch_complete(self, video_list):
        """Handle fetch completion on main thread."""
        self.fetched_videos = video_list
        self._populate_videos_frame(video_list)
        self._add_log(f"✓ Fetched {len(video_list)} video(s)")

        if video_list:
            self._add_log(f"✓ Found {len(video_list)} video(s)!")
            self.start_btn.configure(state="normal")
            self.excluded_videos = []
        else:
            self.start_btn.configure(state="disabled")

    def _on_fetch_cancelled(self):
        """Handle cancelled fetch."""
        self._add_log("⚠️ Fetch cancelled.")
        self._update_ui("Fetch cancelled", self.progress)
    
    def _populate_videos_frame(self, video_list):
        """Populate the videos frame with checkboxes."""
        if threading.current_thread() is not threading.main_thread():
            self._run_on_ui_thread(self._populate_videos_frame, video_list)
            return

        # Clear existing
        for item in self.video_items:
            item["checkbox"].destroy()
        self.video_items = []
        
        # Pack videos
        for idx, video in enumerate(video_list):
            selected_var = ctk.BooleanVar(value=True)
            checkbox = ctk.CTkCheckBox(
                self.videos_window,
                text=video['title'],
                variable=selected_var,
            )
            checkbox.grid(row=idx, column=0, padx=10, pady=5, sticky="w")
            self.video_items.append(
                {"video": video, "selected": selected_var, "checkbox": checkbox}
            )
    
    def _start_harvesting(self):
        """Start harvesting subtitles from selected videos."""
        if self._has_active_workers():
            self._add_log("⚠️ Wait for current task to finish or cancel it first.")
            return

        selected = [item["video"] for item in self.video_items if item["selected"].get()]
        
        if not selected:
            self._add_log("⚠️ Please select at least one video to harvest.")
            return

        # Pull current selections from UI controls before launching worker thread.
        browser = self.browser_combo.get()
        player_client = self.player_combo.get()
        group_by_playlist = self.group_var.get()
        
        self._add_log(f"🌱 Starting to harvest subtitles from {len(selected)} video(s)...")
        
        def harvest(stop_event):
            try:
                st_session_id = self.session_id
                selected_videos = list(selected)
                total_videos = max(1, len(selected_videos))
                processed_files = []
                
                for i, video in enumerate(selected_videos):
                    if stop_event.is_set():
                        self._run_on_ui_thread(self._on_harvest_cancelled, processed_files)
                        return

                    url = video.get('url', '')
                    progress = (i / total_videos) * 0.9
                    
                    # Update progress
                    self._update_ui(f"Processing: {video['title'][:50]}", 
                                   progress)
                    self._add_log(f"Processing: {video['title'][:50]}")
                    
                    try:
                        result = download_video_subtitles(
                            url,
                            session_id=st_session_id,
                            browser=browser,
                            player_client=player_client,
                            group_by_playlist=group_by_playlist,
                            progress_callback=lambda st, pg: self._update_ui(st, pg),
                            error_callback=lambda msg: self._add_log(f"❌ {msg}"),
                            cancel_event=stop_event,
                        )
                        
                        if result:
                            processed_files.extend(result)
                            self._add_log(f"✓ Downloaded: {video['title'][:50]}...")
                        else:
                            self._add_log(f"⚠️ No subtitles found for: {video['title'][:50]}")
                    except OperationCancelled:
                        self._run_on_ui_thread(self._on_harvest_cancelled, processed_files)
                        return
                    except Exception as e:
                        self._add_log(f"❌ Error processing {video['title'][:50]}: {str(e)}")
                
                self._run_on_ui_thread(self._on_harvest_complete, processed_files)
                
            except Exception as e:
                self._run_on_ui_thread(self._on_harvest_failed, str(e))

        self._start_worker("harvest", harvest)

    def _on_harvest_complete(self, processed_files):
        """Finalize harvest state on main thread."""
        self._update_ui("Ready", 1.0)
        with self.state_lock:
            self.processed_files = list(processed_files)
        self._add_log(f"✓ Harvesting complete! Processed {len(processed_files)} video(s).")

    def _on_harvest_failed(self, error_message: str):
        """Handle harvest failure on main thread."""
        self._add_log(f"❌ Harvesting error: {error_message}")

    def _on_harvest_cancelled(self, processed_files):
        """Handle harvest cancellation on main thread."""
        with self.state_lock:
            self.processed_files = list(processed_files)
        self._add_log(f"⚠️ Harvest cancelled. Kept {len(processed_files)} processed file(s).")
        self._update_ui("Harvest cancelled", self.progress)
    
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
        if self._has_active_workers():
            self._add_log("⚠️ Wait for current task to finish or cancel it first.")
            return

        if not hasattr(self, 'local_files') or not self.local_files:
            self._add_log("⚠️ Please select files first.")
            return
        
        local_files = list(self.local_files)
        self._add_log(f"Processing {len(local_files)} local file(s)...")
        
        def process(stop_event):
            try:
                processed = process_local_files(
                    local_files,
                    session_id=self.session_id,
                    progress_callback=lambda st, pg: self._update_ui(st, pg),
                    error_callback=lambda msg: self._add_log(f"❌ {msg}"),
                    cancel_event=stop_event,
                )
                if stop_event.is_set():
                    self._run_on_ui_thread(self._on_local_process_cancelled, processed)
                    return
                self._run_on_ui_thread(self._on_local_process_complete, processed)
            except Exception as e:
                self._run_on_ui_thread(self._on_local_process_failed, str(e))

        self._start_worker("local-process", process)

    def _on_local_process_complete(self, processed):
        """Finalize local file processing on main thread."""
        self._update_ui(f"✓ Processed {len(processed)} file(s)", 1.0)
        with self.state_lock:
            self.processed_files.extend(processed)
            total = len(self.processed_files)
        self._add_log(f"✓ Local files processed! Total: {total} file(s)")

    def _on_local_process_failed(self, error_message: str):
        """Handle local file processing failure on main thread."""
        self._add_log(f"❌ Error: {error_message}")

    def _on_local_process_cancelled(self, processed):
        """Handle cancelled local-file processing."""
        with self.state_lock:
            self.processed_files.extend(processed)
            total = len(self.processed_files)
        self._add_log(f"⚠️ Local processing cancelled. Total retained: {total} file(s).")
        self._update_ui("Local processing cancelled", self.progress)
    
    def _update_merge_strategy(self):
        """Update merge strategy from combo box."""
        self.merge_strategy = self.merge_combo.get()
    
    def _download(self):
        """Create and download ZIP of processed files."""
        if self._has_active_workers():
            self._add_log("⚠️ Wait for current task to finish or cancel it first.")
            return

        with self.state_lock:
            files_to_export = list(self.processed_files)

        if not files_to_export:
            self._add_log("⚠️ No processed files to download.")
            return
        
        merge_strategy = self.merge_combo.get()
        session_dir = self.session_dir
        session_id = self.session_id
        self._add_log("Creating ZIP archive...")
        
        def download(stop_event):
            try:
                if stop_event.is_set():
                    raise OperationCancelled("Download cancelled before merge started.")
                # Merge files
                merged_files = merge_files(
                    files_to_export,
                    merge_strategy,
                    session_dir
                )
                if stop_event.is_set():
                    raise OperationCancelled("Download cancelled before ZIP creation.")
                
                # Create ZIP
                output_zip = f"youscriber_output_{session_id}.zip"
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

        self._start_worker("download", download)


def main():
    """Main entry point."""
    app = YouScriberApp()
    app.mainloop()


if __name__ == "__main__":
    main()
