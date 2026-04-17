import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import uuid
import core
import pathlib

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouScriber - YouTube Knowledge Harvester")
        self.geometry("900x700")
        
        self.session_id = str(uuid.uuid4())
        self.video_list = []
        self.list_type = "videos"
        self.local_files = []
        self.processed_files = []
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        self.tabview.add("YouTube Import")
        self.tabview.add("Local Import")
        self.tabview.add("Export / Merge")
        
        self._setup_youtube_tab()
        self._setup_local_tab()
        self._setup_export_tab()

    def _mac_clipboard(self, event, action):
        try:
            if action == "<<Paste>>":
                text = self.clipboard_get()
                event.widget.insert("insert", text)
            elif action == "<<Copy>>":
                if event.widget.tag_ranges("sel"):
                    text = event.widget.get("sel.first", "sel.last")
                    self.clipboard_clear()
                    self.clipboard_append(text)
            elif action == "<<Cut>>":
                if event.widget.tag_ranges("sel"):
                    text = event.widget.get("sel.first", "sel.last")
                    self.clipboard_clear()
                    self.clipboard_append(text)
                    event.widget.delete("sel.first", "sel.last")
            elif action == "<<SelectAll>>":
                event.widget.tag_add("sel", "1.0", "end")
        except Exception:
            pass
        return "break"

    # --------------- YOUTUBE IMPORT TAB ---------------
    def _setup_youtube_tab(self):
        tab = self.tabview.tab("YouTube Import")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        
        # 1. Inputs
        self.url_textbox = ctk.CTkTextbox(tab, height=80, undo=True)
        self.url_textbox.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        
        # Apply strict macOS bindings to BOTH the CTk wrapper and internal tkinter Text
        for w in [self.url_textbox, self.url_textbox._textbox]:
            w.bind("<Command-v>", lambda e: self._mac_clipboard(e, "<<Paste>>"))
            w.bind("<Command-c>", lambda e: self._mac_clipboard(e, "<<Copy>>"))
            w.bind("<Command-x>", lambda e: self._mac_clipboard(e, "<<Cut>>"))
            w.bind("<Command-a>", lambda e: self._mac_clipboard(e, "<<SelectAll>>"))
        
        placeholder = "Enter YouTube Playlist/Video URLs (one per line)"
        self.url_textbox.insert("0.0", placeholder)
        
        def on_focus_in(event):
            if self.url_textbox.get("0.0", "end").strip() == placeholder:
                self.url_textbox.delete("0.0", "end")
        
        def on_focus_out(event):
            if not self.url_textbox.get("0.0", "end").strip():
                self.url_textbox.insert("0.0", placeholder)
                
        self.url_textbox.bind("<FocusIn>", on_focus_in)
        self.url_textbox.bind("<FocusOut>", on_focus_out)
        
        # Browser selection
        browser_frame = ctk.CTkFrame(tab)
        browser_frame.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(browser_frame, text="Browser Cookies (optional):").pack(side="left", padx=10)
        
        self.browser_var = ctk.StringVar(value="None")
        browsers = ["None", "chrome", "firefox", "safari", "edge", "opera", "vivaldi"]
        self.browser_menu = ctk.CTkOptionMenu(browser_frame, values=browsers, variable=self.browser_var)
        self.browser_menu.pack(side="left", padx=10)
        
        self.cookies_btn = ctk.CTkButton(browser_frame, text="Load cookies.txt", command=self.load_cookies_file, width=120, fg_color="gray")
        self.cookies_btn.pack(side="left", padx=5)
        
        self.fetch_btn = ctk.CTkButton(browser_frame, text="Fetch Video List", command=self.start_fetch)
        self.fetch_btn.pack(side="right", padx=10)
        
        self.fetch_channel_btn = ctk.CTkButton(browser_frame, text="Fetch Channel Playlists", command=self.start_fetch_channel, fg_color="purple")
        self.fetch_channel_btn.pack(side="right", padx=(10, 0))

        # 2. Scrollable Frame for videos
        self.video_frame = ctk.CTkScrollableFrame(tab, label_text="Videos to Download")
        self.video_frame.grid(row=2, column=0, padx=10, pady=10, sticky="nsew")
        self.video_checkboxes = []

        # 3. Harvest Button & Progress
        bottom_frame = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_frame.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="ew")
        
        self.group_playlist_var = ctk.BooleanVar(value=True)
        self.group_chk = ctk.CTkCheckBox(bottom_frame, text="Group by Playlist", variable=self.group_playlist_var)
        self.group_chk.pack(side="left", pady=10)
        
        self.harvest_btn = ctk.CTkButton(bottom_frame, text="Start Harvesting", command=self.start_harvest, state="disabled", fg_color="green")
        self.harvest_btn.pack(side="right", pady=10)
        
        self.status_lbl = ctk.CTkLabel(tab, text="Ready")
        self.status_lbl.grid(row=4, column=0, padx=10, sticky="w")
        
        self.progress_bar = ctk.CTkProgressBar(tab)
        self.progress_bar.grid(row=5, column=0, padx=10, pady=(5, 10), sticky="ew")
        self.progress_bar.set(0)

    # --------------- LOCAL IMPORT TAB ---------------
    def _setup_local_tab(self):
        tab = self.tabview.tab("Local Import")
        
        self.select_files_btn = ctk.CTkButton(tab, text="Select Local .vtt / .srt / .json files", command=self.select_local_files)
        self.select_files_btn.pack(pady=20)
        
        self.local_files_lbl = ctk.CTkLabel(tab, text="No files selected.")
        self.local_files_lbl.pack(pady=10)
        
        self.process_local_btn = ctk.CTkButton(tab, text="Process Local Files", command=self.start_local_process, state="disabled")
        self.process_local_btn.pack(pady=20)
        
        self.local_status_lbl = ctk.CTkLabel(tab, text="Ready")
        self.local_status_lbl.pack(pady=10)
        
        self.local_progress_bar = ctk.CTkProgressBar(tab)
        self.local_progress_bar.pack(fill="x", padx=20, pady=10)
        self.local_progress_bar.set(0)

    # --------------- EXPORT TAB ---------------
    def _setup_export_tab(self):
        tab = self.tabview.tab("Export / Merge")
        
        self.export_status = ctk.CTkLabel(tab, text="No files processed yet.")
        self.export_status.pack(pady=20)
        
        strat_frame = ctk.CTkFrame(tab)
        strat_frame.pack(pady=10)
        ctk.CTkLabel(strat_frame, text="Merge Strategy:").pack(side="left", padx=10)
        
        self.merge_strategy_var = ctk.StringVar(value="No Merge")
        strats = ["No Merge", "One File", "Medium Chunks (~50k chars)", "Large Chunks (~200k chars)"]
        self.merge_menu = ctk.CTkOptionMenu(strat_frame, values=strats, variable=self.merge_strategy_var)
        self.merge_menu.pack(side="left", padx=10)
        
        self.merge_btn = ctk.CTkButton(tab, text="Apply Merge Strategy", command=self.apply_merge, state="disabled")
        self.merge_btn.pack(pady=20)

    # --------------- CALLBACKS & THREADING ---------------
    def update_status(self, text, tab="youtube"):
        if tab == "youtube":
            self.status_lbl.configure(text=text)
        elif tab == "local":
            self.local_status_lbl.configure(text=text)
            
    def update_progress(self, value, text, tab="youtube"):
        self.update_status(text, tab)
        if tab == "youtube":
            self.progress_bar.set(value)
        elif tab == "local":
            self.local_progress_bar.set(value)
            
    def show_error(self, text):
        print(f"ERROR: {text}")
        self.after(0, messagebox.showerror, "Error", text)

    def load_cookies_file(self):
        filepath = filedialog.askopenfilename(title="Select cookies.txt", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if filepath:
            current_values = self.browser_menu._values  # In ctk, cget("values") doesn't always work properly, _values is safer or we construct
            if filepath not in current_values:
                new_values = [filepath] + list(current_values)
                self.browser_menu.configure(values=new_values)
            self.browser_var.set(filepath)

    # --------------- YOUTUBE LOGIC ---------------
    def start_fetch(self):
        self.list_type = "videos"
        urls_text = self.url_textbox.get("0.0", "end").strip()
        if not urls_text or urls_text == "Enter YouTube Playlist/Video URLs (one per line)":
            messagebox.showwarning("Warning", "Please enter at least one URL.")
            return

        urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
        browser = self.browser_var.get()
        
        self.fetch_btn.configure(state="disabled")
        self.fetch_channel_btn.configure(state="disabled")
        self.status_lbl.configure(text="Starting fetch...")
        self.progress_bar.set(0)
        
        for cb in self.video_checkboxes:
            cb.destroy()
        self.video_checkboxes.clear()
        self.harvest_btn.configure(state="disabled")

        threading.Thread(target=self._fetch_thread, args=(urls, browser), daemon=True).start()

    def start_fetch_channel(self):
        self.list_type = "playlists"
        urls_text = self.url_textbox.get("0.0", "end").strip()
        if not urls_text or urls_text == "Enter YouTube Playlist/Video URLs (one per line)":
            messagebox.showwarning("Warning", "Please enter at least one URL.")
            return

        urls = []
        for u in urls_text.splitlines():
            u = u.strip()
            if u:
                if "/playlists" not in u:
                    u = u.rstrip('/') + "/playlists"
                urls.append(u)

        browser = self.browser_var.get()
        
        self.fetch_btn.configure(state="disabled")
        self.fetch_channel_btn.configure(state="disabled")
        self.status_lbl.configure(text="Fetching Channel Playlists...")
        self.progress_bar.set(0)
        
        for cb in self.video_checkboxes:
            cb.destroy()
        self.video_checkboxes.clear()
        self.harvest_btn.configure(state="disabled")

        threading.Thread(target=self._fetch_thread, args=(urls, browser), daemon=True).start()

    def _fetch_thread(self, urls, browser):
        def status_cb(msg): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)
        
        videos = core.fetch_playlist_info(urls, browser, status_callback=status_cb, error_callback=err_cb)
        self.after(0, self._on_fetch_complete, videos)

    def _on_fetch_complete(self, videos):
        self.video_list = videos
        if videos:
            t_str = "playlists" if self.list_type == "playlists" else "videos"
            self.status_lbl.configure(text=f"Found {len(videos)} {t_str}.")
            for i, v in enumerate(videos):
                var = ctk.BooleanVar(value=True)
                title = v.get("title", "Unknown")
                pl = v.get("playlist_title", "")
                text = f"{title}" + (f" [{pl}]" if pl else "")
                
                cb = ctk.CTkCheckBox(self.video_frame, text=text, variable=var)
                cb.grid(row=i, column=0, padx=10, pady=5, sticky="w")
                v['_selected_var'] = var
                self.video_checkboxes.append(cb)
            
            self.harvest_btn.configure(state="normal")
        else:
            self.status_lbl.configure(text="No items found.")
            
        self.fetch_btn.configure(state="normal")
        self.fetch_channel_btn.configure(state="normal")

    def start_harvest(self):
        selected_items = [v for v in self.video_list if v.get('_selected_var') and v['_selected_var'].get()]
        if not selected_items:
            t_str = "playlists" if self.list_type == "playlists" else "videos"
            messagebox.showwarning("Warning", f"No {t_str} selected.")
            return

        browser = self.browser_var.get()
        group = self.group_playlist_var.get()
        
        self.harvest_btn.configure(state="disabled")
        self.fetch_btn.configure(state="disabled")
        self.fetch_channel_btn.configure(state="disabled")
        self.progress_bar.set(0)

        if self.list_type == "playlists":
            threading.Thread(target=self._expand_and_harvest_thread, args=(selected_items, group, browser), daemon=True).start()
        else:
            threading.Thread(target=self._harvest_thread, args=(selected_items, group, browser), daemon=True).start()

    def _expand_and_harvest_thread(self, playlists, group, browser):
        def stat_cb(msg): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)
        def prog_cb(pct, msg): self.after(0, self.update_progress, pct, msg, "youtube")
        
        stat_cb("Expanding selected playlists into individual videos...")
        playlist_urls = [p.get('url') for p in playlists if p.get('url')]
        
        expanded_videos = core.fetch_playlist_info(playlist_urls, browser, status_callback=stat_cb, error_callback=err_cb)
        
        if not expanded_videos:
            err_cb("Failed to expand any videos from the selected playlists.")
            self.after(0, lambda: self.harvest_btn.configure(state="normal"))
            self.after(0, lambda: self.fetch_btn.configure(state="normal"))
            self.after(0, lambda: self.fetch_channel_btn.configure(state="normal"))
            return
            
        stat_cb(f"Successfully expanded into {len(expanded_videos)} videos.")
        files = core.download_videos(expanded_videos, group, self.session_id, browser, prog_cb, stat_cb, err_cb)
        self.after(0, self._on_harvest_complete, files)

    def _harvest_thread(self, videos, group, browser):
        def prog_cb(pct, msg): self.after(0, self.update_progress, pct, msg, "youtube")
        def stat_cb(msg): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)
        
        files = core.download_videos(videos, group, self.session_id, browser, prog_cb, stat_cb, err_cb)
        self.after(0, self._on_harvest_complete, files)

    def _on_harvest_complete(self, files):
        self.harvest_btn.configure(state="normal")
        self.fetch_btn.configure(state="normal")
        self.fetch_channel_btn.configure(state="normal")
        self.processed_files.extend(files)
        self.update_export_tab()
        messagebox.showinfo("Done", f"Harvested {len(files)} files successfully.")

    # --------------- LOCAL LOGIC ---------------
    def select_local_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Subtitle/JSON", "*.vtt *.srt *.json *.txt"), ("All Files", "*.*")])
        if files:
            self.local_files = list(files)
            self.local_files_lbl.configure(text=f"{len(self.local_files)} files selected.")
            self.process_local_btn.configure(state="normal")

    def start_local_process(self):
        if not self.local_files: return
        self.process_local_btn.configure(state="disabled")
        self.local_progress_bar.set(0)
        
        threading.Thread(target=self._local_thread, args=(self.local_files,), daemon=True).start()

    def _local_thread(self, files):
        def prog_cb(pct, msg): self.after(0, self.update_progress, pct, msg, "local")
        def stat_cb(msg): self.after(0, self.update_status, msg, "local")
        def err_cb(msg): self.after(0, self.show_error, msg)
        
        res = core.process_local_files(files, self.session_id, prog_cb, stat_cb, err_cb)
        self.after(0, self._on_local_complete, res)

    def _on_local_complete(self, files):
        self.process_local_btn.configure(state="normal")
        self.processed_files.extend(files)
        self.update_export_tab()
        messagebox.showinfo("Done", f"Processed {len(files)} local files.")

    # --------------- EXPORT LOGIC ---------------
    def update_export_tab(self):
        if self.processed_files:
            self.export_status.configure(text=f"{len(self.processed_files)} files currently in session.")
            self.merge_btn.configure(state="normal")

    def apply_merge(self):
        strat = self.merge_strategy_var.get()
        if not self.processed_files: return
        
        valid_files = [pathlib.Path(p) for p in self.processed_files if pathlib.Path(p).exists()]
        if not valid_files:
            messagebox.showerror("Error", "Processed files not found on disk.")
            return

        session_dir = core.ensure_session_dir(self.session_id)
        final_files = core.merge_files(valid_files, strat, session_dir)
        
        out_folder = str(final_files[0].parent if final_files else session_dir)
        messagebox.showinfo("Merge Complete", f"Files successfully merged/prepared in:\n{out_folder}")
        
        try:
            core.subprocess.run(["open", out_folder])
        except:
            pass

if __name__ == "__main__":
    app = App()
    app.mainloop()
