import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
import uuid
import subprocess
import sys
import os
import core
import pathlib
import datetime
import json

# Setup appearance default
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# --------------- CONFIGURATION MANAGER ---------------
def load_config():
    config_path = pathlib.Path("config.json")
    defaults = {
        "languages": "ru-orig, ru, en-orig, en",
        "export_dir": str(pathlib.Path("output").absolute()),
        "appearance_mode": "Dark"
    }
    if not config_path.exists():
        # Ensure default output folder exists
        pathlib.Path("output").mkdir(exist_ok=True)
        return defaults
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            # merge defaults for missing keys
            for k, v in defaults.items():
                if k not in user_config:
                    user_config[k] = v
            # Ensure folders exist
            pathlib.Path(user_config["export_dir"]).mkdir(parents=True, exist_ok=True)
            return user_config
    except Exception:
        return defaults

def save_config(config):
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

class App(ctk.CTk):
    # Premium YouTube Crimson Theme Colors (Light Mode, Dark Mode)
    ACCENT_BLUE = ("#D32F2F", "#E53935")  # Softer Red
    ACCENT_BLUE_HOVER = ("#B71C1C", "#C62828")
    SUCCESS_GREEN = ("#10B981", "#10B981")
    SUCCESS_GREEN_HOVER = ("#059669", "#059669")
    DANGER_RED = ("#D32F2F", "#E53935")
    DANGER_RED_HOVER = ("#B71C1C", "#C62828")
    MUTED_GRAY = ("#E5E7EB", "#2D2D2D")
    MUTED_GRAY_HOVER = ("#D1D5DB", "#374151")
    TEXT_MUTED = ("#6B7280", "#AAAAAA")
    CARD_BG = ("#FFFFFF", "#1E1E1E")
    CARD_BORDER = ("#E5E7EB", "#2D2D2D")

    def __init__(self):
        super().__init__()
        self.title("YouScriber - YouTube Knowledge Harvester")
        self.geometry("1100x800")
        self.minsize(950, 700)
        
        # Load local configurations
        self.config = load_config()
        os.environ['YOUSCRIBER_SUB_LANGS'] = self.config["languages"]
        
        self.session_id = str(uuid.uuid4())
        self.cancel_event = threading.Event()
        threading.Thread(target=core.cleanup_old_sessions, daemon=True).start()
        self.video_list = []
        self.list_type = "videos"
        self.local_files = []
        self.processed_files = []
        
        self.local_files_widgets = []
        self.export_files_widgets = []
        
        # Configure layout: Column 0 is sidebar, Column 1 is content
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # --- Left Sidebar ---
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)  # Spacer row
        
        # Title and subtitle
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="YouScriber", 
            font=ctk.CTkFont(family="Inter", size=20, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(25, 5), sticky="w")
        
        self.subtitle_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="YouTube Harvester", 
            font=ctk.CTkFont(family="Inter", size=12), 
            text_color=self.TEXT_MUTED
        )
        self.subtitle_label.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")
        
        self.separator = ctk.CTkFrame(self.sidebar_frame, height=2, fg_color=self.CARD_BORDER)
        self.separator.grid(row=2, column=0, padx=15, pady=(0, 25), sticky="ew")
        
        # Sidebar Navigation Buttons
        self.sidebar_buttons = {}
        tabs = [
            ("youtube", "📥 YouTube Import"), 
            ("local", "📁 Local Import"), 
            ("export", "🔄 Merge & Export"),
            ("settings", "⚙️ Settings")
        ]
        for idx, (tab_id, label) in enumerate(tabs):
            btn = ctk.CTkButton(
                self.sidebar_frame, 
                text=label, 
                anchor="w", 
                fg_color="transparent", 
                text_color=("gray10", "gray90"), 
                height=40, 
                font=ctk.CTkFont(family="Inter", size=13, weight="bold"),
                command=lambda t=tab_id: self.select_tab(t)
            )
            btn.grid(row=3 + idx, column=0, padx=10, pady=5, sticky="ew")
            self.sidebar_buttons[tab_id] = btn

        # Theme switcher in sidebar
        self.theme_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="Theme Mode:", 
            font=ctk.CTkFont(family="Inter", size=11),
            text_color=self.TEXT_MUTED
        )
        self.theme_label.grid(row=8, column=0, padx=20, pady=(10, 2), sticky="w")
        
        self.theme_switch = ctk.CTkSwitch(
            self.sidebar_frame, 
            text="Dark Mode", 
            command=self.toggle_theme,
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.theme_switch.grid(row=9, column=0, padx=20, pady=(0, 25), sticky="w")

        # --- Right Content Panel ---
        self.main_content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_content_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        
        self.main_content_frame.grid_columnconfigure(0, weight=1)
        self.main_content_frame.grid_rowconfigure(0, weight=4)  # Active tab frame
        self.main_content_frame.grid_rowconfigure(1, weight=1, minsize=180)  # Log Console
        
        # Tab Frames
        self.youtube_tab_frame = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self.local_tab_frame = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self.export_tab_frame = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        self.settings_tab_frame = ctk.CTkFrame(self.main_content_frame, fg_color="transparent")
        
        # Console Log Frame
        self._setup_console_log()
        
        # Setup specific views
        self._setup_youtube_view()
        self._setup_local_view()
        self._setup_export_view()
        self._setup_settings_view()
        
        # Set dynamic theme initial value
        if self.config["appearance_mode"] == "Dark":
            self.theme_switch.select()
            ctk.set_appearance_mode("Dark")
        else:
            self.theme_switch.deselect()
            ctk.set_appearance_mode("Light")
        
        # Select default tab
        self.select_tab("youtube")
        
        self.append_log("YouScriber initialized and ready.", "INFO")

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

    def select_tab(self, name):
        # Reset all button styles
        for tab_name, btn in self.sidebar_buttons.items():
            if tab_name == name:
                btn.configure(fg_color=self.ACCENT_BLUE, hover_color=self.ACCENT_BLUE_HOVER, text_color="white")
            else:
                btn.configure(fg_color="transparent", hover_color=self.MUTED_GRAY_HOVER, text_color=("gray10", "gray90"))
                
        # Hide all tab frames
        self.youtube_tab_frame.grid_remove()
        self.local_tab_frame.grid_remove()
        self.export_tab_frame.grid_remove()
        self.settings_tab_frame.grid_remove()
        
        # Show active tab frame
        if name == "youtube":
            self.youtube_tab_frame.grid(row=0, column=0, sticky="nsew")
        elif name == "local":
            self.local_tab_frame.grid(row=0, column=0, sticky="nsew")
            self.update_local_files_list()
        elif name == "export":
            self.export_tab_frame.grid(row=0, column=0, sticky="nsew")
            self.update_export_tab()
        elif name == "settings":
            self.settings_tab_frame.grid(row=0, column=0, sticky="nsew")

    def toggle_theme(self):
        if self.theme_switch.get() == 1:
            ctk.set_appearance_mode("Dark")
            self.config["appearance_mode"] = "Dark"
        else:
            ctk.set_appearance_mode("Light")
            self.config["appearance_mode"] = "Light"
        save_config(self.config)

    # --------------- CONSOLE LOG LOGIC ---------------
    def _setup_console_log(self):
        self.console_card = ctk.CTkFrame(
            self.main_content_frame, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        self.console_card.grid(row=1, column=0, pady=(15, 0), sticky="nsew")
        
        self.console_card.grid_columnconfigure(0, weight=1)
        self.console_card.grid_rowconfigure(1, weight=1)
        
        # Console header
        console_header = ctk.CTkFrame(self.console_card, fg_color="transparent")
        console_header.grid(row=0, column=0, padx=10, pady=(5, 2), sticky="ew")
        
        console_title = ctk.CTkLabel(
            console_header, 
            text="Console Output Log", 
            font=ctk.CTkFont(family="Inter", size=12, weight="bold")
        )
        console_title.pack(side="left")
        
        clear_btn = ctk.CTkButton(
            console_header, 
            text="Clear Log", 
            width=70, 
            height=20, 
            fg_color="transparent", 
            hover_color=self.DANGER_RED_HOVER, 
            text_color=self.TEXT_MUTED,
            command=self.clear_console_log
        )
        clear_btn.pack(side="right")
        
        self.console_textbox = ctk.CTkTextbox(
            self.console_card, 
            font=ctk.CTkFont(family="Courier New" if sys.platform != "darwin" else "Monaco", size=11), 
            text_color=("gray20", "gray85"),
            fg_color=("gray95", "#151515"),
            wrap="word", 
            undo=True
        )
        self.console_textbox.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.console_textbox.configure(state="disabled")
        
        # Setup tags
        self.console_textbox.tag_config("level_info", foreground="#3B82F6" if sys.platform != "darwin" else "#5EA2FF")
        self.console_textbox.tag_config("level_success", foreground="#10B981" if sys.platform != "darwin" else "#2ECC71")
        self.console_textbox.tag_config("level_error", foreground="#EF4444")
        self.console_textbox.tag_config("level_warning", foreground="#F59E0B")

    def append_log(self, message, level="INFO"):
        self.console_textbox.configure(state="normal")
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = f"[{timestamp}] [{level}] "
        
        # Insert prefix
        start_idx = self.console_textbox.index("insert")
        self.console_textbox.insert("end", prefix)
        end_prefix_idx = self.console_textbox.index("insert")
        
        # Insert message
        self.console_textbox.insert("end", f"{message}\n")
        
        # Apply tags
        tag_name = f"level_{level.lower()}"
        self.console_textbox.tag_add(tag_name, start_idx, end_prefix_idx)
        
        self.console_textbox.configure(state="disabled")
        self.console_textbox.yview("end")

    def clear_console_log(self):
        self.console_textbox.configure(state="normal")
        self.console_textbox.delete("1.0", "end")
        self.console_textbox.configure(state="disabled")

    # --------------- YOUTUBE IMPORT VIEW ---------------
    def _setup_youtube_view(self):
        tab = self.youtube_tab_frame
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        
        # 1. Inputs Card
        input_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        input_card.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        input_card.grid_columnconfigure(0, weight=1)
        
        input_label = ctk.CTkLabel(
            input_card, 
            text="Enter YouTube Playlist/Video URLs (one per line):", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        input_label.grid(row=0, column=0, padx=15, pady=(12, 5), sticky="w")
        
        self.url_textbox = ctk.CTkTextbox(input_card, height=90, undo=True)
        self.url_textbox.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="ew")
        
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
        
        browser_frame = ctk.CTkFrame(input_card, fg_color="transparent")
        browser_frame.grid(row=2, column=0, padx=15, pady=(0, 12), sticky="ew")
        
        ctk.CTkLabel(
            browser_frame, 
            text="Cookies (optional):", 
            font=ctk.CTkFont(family="Inter", size=12)
        ).pack(side="left", padx=(0, 5))
        
        self.browser_var = ctk.StringVar(value="None")
        browsers = ["None", "chrome", "firefox", "safari", "edge", "opera", "vivaldi"]
        self.browser_menu = ctk.CTkOptionMenu(
            browser_frame, 
            values=browsers, 
            variable=self.browser_var,
            width=110,
            fg_color=self.MUTED_GRAY,
            button_color=self.MUTED_GRAY,
            button_hover_color=self.MUTED_GRAY_HOVER
        )
        self.browser_menu.pack(side="left", padx=5)
        
        self.cookies_btn = ctk.CTkButton(
            browser_frame, 
            text="📁 cookies.txt", 
            command=self.load_cookies_file, 
            width=110, 
            fg_color=self.MUTED_GRAY,
            hover_color=self.MUTED_GRAY_HOVER
        )
        self.cookies_btn.pack(side="left", padx=5)
        
        self.fetch_channel_btn = ctk.CTkButton(
            browser_frame, 
            text="📺 Channel Playlists", 
            command=self.start_fetch_channel,
            fg_color=self.ACCENT_BLUE,
            hover_color=self.ACCENT_BLUE_HOVER,
            text_color="white"
        )
        self.fetch_channel_btn.pack(side="right", padx=(5, 0))

        self.fetch_btn = ctk.CTkButton(
            browser_frame, 
            text="🔍 Fetch Videos", 
            command=self.start_fetch,
            fg_color=self.ACCENT_BLUE,
            hover_color=self.ACCENT_BLUE_HOVER,
            text_color="white"
        )
        self.fetch_btn.pack(side="right", padx=5)
        
        # 2. Videos List Card
        list_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        list_card.grid(row=1, column=0, padx=0, pady=10, sticky="nsew")
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(1, weight=1)
        
        list_header = ctk.CTkFrame(list_card, fg_color="transparent")
        list_header.grid(row=0, column=0, padx=15, pady=(10, 5), sticky="ew")
        
        list_title = ctk.CTkLabel(
            list_header, 
            text="Videos to Download", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        list_title.pack(side="left")
        
        self.select_all_btn = ctk.CTkButton(
            list_header, 
            text="Select All", 
            width=70, 
            height=24, 
            fg_color="transparent", 
            text_color=self.ACCENT_BLUE, 
            hover_color=self.MUTED_GRAY_HOVER,
            command=self.select_all_videos,
            state="disabled"
        )
        self.select_all_btn.pack(side="left", padx=(15, 5))
        
        self.deselect_all_btn = ctk.CTkButton(
            list_header, 
            text="Deselect All", 
            width=80, 
            height=24, 
            fg_color="transparent", 
            text_color=self.DANGER_RED, 
            hover_color=self.MUTED_GRAY_HOVER,
            command=self.deselect_all_videos,
            state="disabled"
        )
        self.deselect_all_btn.pack(side="left", padx=5)
        
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", self.filter_videos)
        self.search_entry = ctk.CTkEntry(
            list_header, 
            placeholder_text="🔍 Filter title...", 
            textvariable=self.search_var,
            width=180,
            height=24
        )
        self.search_entry.pack(side="right", padx=(10, 0))
        self.search_entry.configure(state="disabled")

        self.video_frame = ctk.CTkScrollableFrame(list_card, label_text="")
        self.video_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.video_checkboxes = []
        
        # 3. Harvest controls frame (Bottom)
        bottom_frame = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_frame.grid(row=2, column=0, padx=0, pady=(5, 5), sticky="ew")
        
        self.group_playlist_var = ctk.BooleanVar(value=True)
        self.group_chk = ctk.CTkCheckBox(
            bottom_frame, 
            text="Group by Playlist Folder", 
            variable=self.group_playlist_var,
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.group_chk.pack(side="left", pady=10)
        
        self.harvest_btn = ctk.CTkButton(
            bottom_frame, 
            text="🚀 Start Harvesting", 
            command=self.start_harvest, 
            state="disabled", 
            fg_color=self.SUCCESS_GREEN,
            hover_color=self.SUCCESS_GREEN_HOVER,
            text_color="white",
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.harvest_btn.pack(side="right", pady=10)
        
        self.cancel_btn = ctk.CTkButton(
            bottom_frame, 
            text="✕ Cancel", 
            command=self.cancel_operation, 
            state="disabled", 
            fg_color=self.DANGER_RED,
            hover_color=self.DANGER_RED_HOVER,
            text_color="white",
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.cancel_btn.pack(side="right", padx=10, pady=10)
        
        # 4. Status / Progress
        status_frame = ctk.CTkFrame(tab, fg_color="transparent")
        status_frame.grid(row=3, column=0, padx=0, pady=(0, 5), sticky="ew")
        status_frame.grid_columnconfigure(0, weight=1)
        
        self.status_lbl = ctk.CTkLabel(
            status_frame, 
            text="Ready", 
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.status_lbl.grid(row=0, column=0, sticky="w")
        
        self.progress_bar = ctk.CTkProgressBar(status_frame)
        self.progress_bar.grid(row=1, column=0, pady=(5, 0), sticky="ew")
        self.progress_bar.set(0)

    # --------------- LOCAL IMPORT VIEW ---------------
    def _setup_local_view(self):
        tab = self.local_tab_frame
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        
        header_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        header_card.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        
        self.select_files_btn = ctk.CTkButton(
            header_card, 
            text="📂 Select Local Files (.vtt, .srt, .json, .txt)", 
            command=self.select_local_files,
            fg_color=self.ACCENT_BLUE,
            hover_color=self.ACCENT_BLUE_HOVER,
            text_color="white",
            width=260
        )
        self.select_files_btn.pack(side="left", padx=15, pady=15)
        
        self.clear_local_btn = ctk.CTkButton(
            header_card, 
            text="✕ Clear All", 
            command=self.clear_local_files,
            fg_color=self.MUTED_GRAY,
            hover_color=self.MUTED_GRAY_HOVER,
            width=100,
            state="disabled"
        )
        self.clear_local_btn.pack(side="left", padx=5, pady=15)
        
        self.local_files_lbl = ctk.CTkLabel(
            header_card, 
            text="No files selected.", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.local_files_lbl.pack(side="right", padx=15, pady=15)
        
        files_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        files_card.grid(row=1, column=0, padx=0, pady=10, sticky="nsew")
        files_card.grid_columnconfigure(0, weight=1)
        files_card.grid_rowconfigure(0, weight=1)
        
        self.local_files_scroll = ctk.CTkScrollableFrame(files_card, label_text="")
        self.local_files_scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.local_files_scroll.grid_columnconfigure(0, weight=1)
        
        bottom_frame = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_frame.grid(row=2, column=0, padx=0, pady=(5, 5), sticky="ew")
        
        self.process_local_btn = ctk.CTkButton(
            bottom_frame, 
            text="🚀 Process Local Files", 
            command=self.start_local_process, 
            state="disabled",
            fg_color=self.SUCCESS_GREEN,
            hover_color=self.SUCCESS_GREEN_HOVER,
            text_color="white",
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.process_local_btn.pack(side="right", pady=10)
        
        status_frame = ctk.CTkFrame(tab, fg_color="transparent")
        status_frame.grid(row=3, column=0, padx=0, pady=(0, 5), sticky="ew")
        status_frame.grid_columnconfigure(0, weight=1)
        
        self.local_status_lbl = ctk.CTkLabel(
            status_frame, 
            text="Ready", 
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.local_status_lbl.grid(row=0, column=0, sticky="w")
        
        self.local_progress_bar = ctk.CTkProgressBar(status_frame)
        self.local_progress_bar.grid(row=1, column=0, pady=(5, 0), sticky="ew")
        self.local_progress_bar.set(0)

    # --------------- EXPORT / MERGE VIEW ---------------
    def _setup_export_view(self):
        tab = self.export_tab_frame
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        
        header_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        header_card.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        
        self.export_status = ctk.CTkLabel(
            header_card, 
            text="No files processed yet.", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.export_status.pack(side="left", padx=15, pady=15)
        
        strat_frame = ctk.CTkFrame(header_card, fg_color="transparent")
        strat_frame.pack(side="right", padx=15, pady=15)
        
        ctk.CTkLabel(
            strat_frame, 
            text="Merge Strategy:", 
            font=ctk.CTkFont(family="Inter", size=12)
        ).pack(side="left", padx=(0, 5))
        
        self.merge_strategy_var = ctk.StringVar(value="No Merge")
        strats = ["No Merge", "One File", "Medium Chunks (~50k chars)", "Large Chunks (~200k chars)"]
        self.merge_menu = ctk.CTkOptionMenu(
            strat_frame, 
            values=strats, 
            variable=self.merge_strategy_var,
            fg_color=self.MUTED_GRAY,
            button_color=self.MUTED_GRAY,
            button_hover_color=self.MUTED_GRAY_HOVER
        )
        self.merge_menu.pack(side="left")
        
        list_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        list_card.grid(row=1, column=0, padx=0, pady=10, sticky="nsew")
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(0, weight=1)
        
        self.export_files_scroll = ctk.CTkScrollableFrame(list_card, label_text="")
        self.export_files_scroll.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.export_files_scroll.grid_columnconfigure(0, weight=1)
        
        bottom_frame = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_frame.grid(row=2, column=0, padx=0, pady=(5, 5), sticky="ew")
        
        self.merge_btn = ctk.CTkButton(
            bottom_frame, 
            text="🔄 Apply Merge & Save", 
            command=self.apply_merge, 
            state="disabled",
            fg_color=self.SUCCESS_GREEN,
            hover_color=self.SUCCESS_GREEN_HOVER,
            text_color="white",
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.merge_btn.pack(side="right", pady=10)

    # --------------- SETTINGS VIEW ---------------
    def _setup_settings_view(self):
        tab = self.settings_tab_frame
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)  # spacer
        
        # 1. Languages Card
        lang_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        lang_card.grid(row=0, column=0, padx=0, pady=(0, 10), sticky="ew")
        lang_card.grid_columnconfigure(0, weight=1)
        
        lang_label = ctk.CTkLabel(
            lang_card, 
            text="YouTube Subtitle Preferred Languages (comma-separated):", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        lang_label.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        
        self.settings_languages_var = ctk.StringVar(value=self.config["languages"])
        self.settings_languages_entry = ctk.CTkEntry(
            lang_card, 
            textvariable=self.settings_languages_var,
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.settings_languages_entry.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="ew")
        
        # 2. Export Card
        export_card = ctk.CTkFrame(
            tab, 
            fg_color=self.CARD_BG, 
            border_color=self.CARD_BORDER, 
            border_width=1, 
            corner_radius=8
        )
        export_card.grid(row=1, column=0, padx=0, pady=10, sticky="ew")
        export_card.grid_columnconfigure(0, weight=1)
        
        export_label = ctk.CTkLabel(
            export_card, 
            text="Default Export / Merge Folder:", 
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        export_label.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        
        export_subframe = ctk.CTkFrame(export_card, fg_color="transparent")
        export_subframe.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="ew")
        export_subframe.grid_columnconfigure(0, weight=1)
        
        self.settings_export_dir_var = ctk.StringVar(value=self.config["export_dir"])
        self.settings_export_dir_entry = ctk.CTkEntry(
            export_subframe, 
            textvariable=self.settings_export_dir_var,
            state="readonly",
            font=ctk.CTkFont(family="Inter", size=12)
        )
        self.settings_export_dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        
        self.change_export_btn = ctk.CTkButton(
            export_subframe, 
            text="Browse...", 
            width=80,
            command=self.browse_export_dir,
            fg_color=self.MUTED_GRAY,
            hover_color=self.MUTED_GRAY_HOVER
        )
        self.change_export_btn.grid(row=0, column=1, sticky="e")
        
        # 3. Actions Frame (Bottom)
        actions_frame = ctk.CTkFrame(tab, fg_color="transparent")
        actions_frame.grid(row=3, column=0, padx=0, pady=10, sticky="ew")
        
        self.save_settings_btn = ctk.CTkButton(
            actions_frame, 
            text="💾 Save Settings", 
            command=self.save_settings, 
            fg_color=self.SUCCESS_GREEN,
            hover_color=self.SUCCESS_GREEN_HOVER,
            text_color="white",
            font=ctk.CTkFont(family="Inter", size=13, weight="bold")
        )
        self.save_settings_btn.pack(side="right")

    def browse_export_dir(self):
        folder = filedialog.askdirectory(initialdir=self.settings_export_dir_var.get())
        if folder:
            self.settings_export_dir_var.set(folder)

    def save_settings(self):
        self.config["languages"] = self.settings_languages_var.get().strip()
        self.config["export_dir"] = self.settings_export_dir_var.get().strip()
        save_config(self.config)
        
        # Apply runtime environment variables updates
        os.environ['YOUSCRIBER_SUB_LANGS'] = self.config["languages"]
        self.append_log("Settings saved and updated successfully.", "SUCCESS")
        messagebox.showinfo("Success", "Settings saved successfully.")

    # --------------- CALLBACKS & THREADING ---------------
    def cancel_operation(self):
        self.cancel_event.set()
        self.update_status("Cancelling...", "youtube")
        self.update_status("Cancelling...", "local")
        self.cancel_btn.configure(state="disabled")

    def update_status(self, text, tab="youtube"):
        if tab == "youtube":
            self.status_lbl.configure(text=text)
        elif tab == "local":
            self.local_status_lbl.configure(text=text)
        
        # Color coding console logs
        level = "INFO"
        text_lower = text.lower()
        if "error" in text_lower or "failed" in text_lower or "cancel" in text_lower:
            level = "ERROR"
        elif "success" in text_lower or "complete" in text_lower or "done" in text_lower or "harvested" in text_lower or "processed" in text_lower:
            level = "SUCCESS"
        elif "warning" in text_lower:
            level = "WARNING"
            
        self.append_log(f"[{tab.upper()}] {text}", level)
            
    def update_progress(self, value, text, tab="youtube"):
        self.update_status(text, tab)
        if tab == "youtube":
            self.progress_bar.set(value)
        elif tab == "local":
            self.local_progress_bar.set(value)
            
    def show_error(self, text):
        print(f"ERROR: {text}")
        self.append_log(text, "ERROR")
        self.after(0, messagebox.showerror, "Error", text)

    def load_cookies_file(self):
        filepath = filedialog.askopenfilename(title="Select cookies.txt", filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")])
        if filepath:
            current_values = self.browser_menu._values
            if filepath not in current_values:
                new_values = [filepath] + list(current_values)
                self.browser_menu.configure(values=new_values)
            self.browser_var.set(filepath)
            self.append_log(f"Loaded cookies file: {filepath}", "SUCCESS")

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
        
        self.select_all_btn.configure(state="disabled")
        self.deselect_all_btn.configure(state="disabled")
        self.search_entry.configure(state="disabled")
        self.search_var.set("")
        
        self.harvest_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_event.clear()

        # Update environment variables dynamically based on config before fetch
        os.environ['YOUSCRIBER_SUB_LANGS'] = self.config["languages"]

        self.append_log(f"Fetching video list for URLs: {urls}", "INFO")
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
        
        self.select_all_btn.configure(state="disabled")
        self.deselect_all_btn.configure(state="disabled")
        self.search_entry.configure(state="disabled")
        self.search_var.set("")
        
        self.harvest_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.cancel_event.clear()

        # Update environment variables dynamically based on config before fetch
        os.environ['YOUSCRIBER_SUB_LANGS'] = self.config["languages"]

        self.append_log(f"Fetching channel playlists for: {urls}", "INFO")
        threading.Thread(target=self._fetch_thread, args=(urls, browser), daemon=True).start()

    def _fetch_thread(self, urls, browser):
        def status_cb(msg, pct=None): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)

        try:
            videos = core.fetch_video_list(urls, browser, progress_callback=status_cb, error_callback=err_cb, cancel_event=self.cancel_event)
        except Exception as e:
            err_cb(f"Fetch failed: {e}")
            videos = []
        self.after(0, self._on_fetch_complete, videos)

    def _on_fetch_complete(self, videos):
        self.video_list = videos
        if videos:
            t_str = "playlists" if self.list_type == "playlists" else "videos"
            self.status_lbl.configure(text=f"Found {len(videos)} {t_str}.")
            self.append_log(f"Successfully loaded {len(videos)} {t_str}.", "SUCCESS")
            
            self.select_all_btn.configure(state="normal")
            self.deselect_all_btn.configure(state="normal")
            self.search_entry.configure(state="normal")
            
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
            self.append_log("No items found or expansion failed.", "WARNING")
            self.select_all_btn.configure(state="disabled")
            self.deselect_all_btn.configure(state="disabled")
            self.search_entry.configure(state="disabled")
            
        self.fetch_btn.configure(state="normal")
        self.fetch_channel_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

    def select_all_videos(self):
        count = 0
        for cb in self.video_checkboxes:
            if cb.winfo_manager() == "grid":
                cb.select()
                count += 1
        self.append_log(f"Selected all visible videos ({count}).", "INFO")

    def deselect_all_videos(self):
        count = 0
        for cb in self.video_checkboxes:
            if cb.winfo_manager() == "grid":
                cb.deselect()
                count += 1
        self.append_log(f"Deselected all visible videos ({count}).", "INFO")

    def filter_videos(self, *args):
        query = self.search_var.get().lower().strip()
        for cb, video in zip(self.video_checkboxes, self.video_list):
            title = video.get("title", "").lower()
            playlist = video.get("playlist_title", "").lower()
            if not query or query in title or query in playlist:
                cb.grid()
            else:
                cb.grid_remove()

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
        self.cancel_btn.configure(state="normal")
        self.cancel_event.clear()

        # Update environment variables dynamically based on config before harvest
        os.environ['YOUSCRIBER_SUB_LANGS'] = self.config["languages"]

        self.append_log(f"Starting subtitle extraction for {len(selected_items)} item(s)...", "INFO")
        if self.list_type == "playlists":
            threading.Thread(target=self._expand_and_harvest_thread, args=(selected_items, group, browser), daemon=True).start()
        else:
            threading.Thread(target=self._harvest_thread, args=(selected_items, group, browser), daemon=True).start()

    def _expand_and_harvest_thread(self, playlists, group, browser):
        def stat_cb(msg, pct=None): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)
        def prog_cb(msg, pct): self.after(0, self.update_progress, pct, msg, "youtube")

        try:
            stat_cb("Expanding selected playlists into individual videos...")
            playlist_urls = [p.get('url') for p in playlists if p.get('url')]

            expanded_videos = core.fetch_video_list(playlist_urls, browser, progress_callback=stat_cb, error_callback=err_cb, cancel_event=self.cancel_event)

            if not expanded_videos:
                err_cb("Failed to expand any videos from the selected playlists.")
                self.after(0, self._on_harvest_complete, [])
                return

            stat_cb(f"Successfully expanded into {len(expanded_videos)} videos.")
            files = core.download_videos(expanded_videos, group, self.session_id, browser, prog_cb, stat_cb, err_cb, self.cancel_event)
        except Exception as e:
            err_cb(f"Harvest failed: {e}")
            files = []
        self.after(0, self._on_harvest_complete, files)

    def _harvest_thread(self, videos, group, browser):
        def prog_cb(msg, pct): self.after(0, self.update_progress, pct, msg, "youtube")
        def stat_cb(msg, pct=None): self.after(0, self.update_status, msg, "youtube")
        def err_cb(msg): self.after(0, self.show_error, msg)

        try:
            files = core.download_videos(videos, group, self.session_id, browser, prog_cb, stat_cb, err_cb, self.cancel_event)
        except Exception as e:
            err_cb(f"Harvest failed: {e}")
            files = []
        self.after(0, self._on_harvest_complete, files)

    def _on_harvest_complete(self, files):
        self.harvest_btn.configure(state="normal")
        self.fetch_btn.configure(state="normal")
        self.fetch_channel_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.processed_files.extend(files)
        self.update_export_tab()
        if files:
            self.update_status(f"Harvested {len(files)} files.", "youtube")
            self.append_log(f"Successfully harvested transcripts: {files}", "SUCCESS")
            messagebox.showinfo("Done", f"Harvested {len(files)} files successfully.")
        else:
            self.update_status("No files harvested.", "youtube")
            self.append_log("Harvesting finished with no files produced.", "WARNING")
            messagebox.showwarning("Nothing harvested", "No transcripts were produced. See the status line and any error dialogs for details.")

    # --------------- LOCAL LOGIC ---------------
    def select_local_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Subtitle/JSON", "*.vtt *.srt *.json *.txt"), ("All Files", "*.*")])
        if files:
            self.local_files = list(files)
            self.local_files_lbl.configure(text=f"{len(self.local_files)} files selected.")
            self.clear_local_btn.configure(state="normal")
            self.update_local_files_list()
            self.append_log(f"Selected {len(files)} local file(s) for processing.", "INFO")

    def clear_local_files(self):
        self.local_files.clear()
        self.local_files_lbl.configure(text="No files selected.")
        self.clear_local_btn.configure(state="disabled")
        self.update_local_files_list()
        self.append_log("Cleared local files list.", "INFO")

    def remove_local_file(self, filepath):
        if filepath in self.local_files:
            self.local_files.remove(filepath)
            self.local_files_lbl.configure(text=f"{len(self.local_files)} files selected.")
            if not self.local_files:
                self.clear_local_btn.configure(state="disabled")
            self.update_local_files_list()
            self.append_log(f"Removed from list: {filepath}", "INFO")

    def update_local_files_list(self):
        for w in self.local_files_widgets:
            w.destroy()
        self.local_files_widgets.clear()
        
        if not self.local_files:
            lbl = ctk.CTkLabel(
                self.local_files_scroll, 
                text="No files selected. Click 'Select Local Files' to load transcripts.", 
                text_color=self.TEXT_MUTED,
                font=ctk.CTkFont(family="Inter", size=12)
            )
            lbl.grid(row=0, column=0, padx=15, pady=15, sticky="w")
            self.local_files_widgets.append(lbl)
            self.process_local_btn.configure(state="disabled")
            return
            
        self.process_local_btn.configure(state="normal")
        for i, filepath in enumerate(self.local_files):
            path_obj = pathlib.Path(filepath)
            size_kb = path_obj.stat().st_size / 1024 if path_obj.exists() else 0
            
            row_frame = ctk.CTkFrame(self.local_files_scroll, fg_color="transparent")
            row_frame.grid(row=i, column=0, padx=5, pady=4, sticky="ew")
            self.local_files_widgets.append(row_frame)
            
            lbl_name = ctk.CTkLabel(
                row_frame, 
                text=f"📄 {path_obj.name} ({size_kb:.1f} KB)", 
                font=ctk.CTkFont(family="Inter", size=12)
            )
            lbl_name.pack(side="left", padx=5)
            
            btn_remove = ctk.CTkButton(
                row_frame, 
                text="✕", 
                width=24, 
                height=20, 
                fg_color="transparent", 
                hover_color=self.DANGER_RED_HOVER, 
                text_color=self.TEXT_MUTED, 
                command=lambda p=filepath: self.remove_local_file(p),
                font=ctk.CTkFont(family="Inter", size=10, weight="bold")
            )
            btn_remove.pack(side="right", padx=5)

    def start_local_process(self):
        if not self.local_files: return
        self.process_local_btn.configure(state="disabled")
        self.local_progress_bar.set(0)
        self.cancel_event.clear()

        self.append_log(f"Processing {len(self.local_files)} local file(s)...", "INFO")
        threading.Thread(target=self._local_thread, args=(self.local_files,), daemon=True).start()

    def _local_thread(self, files):
        def prog_cb(msg, pct): self.after(0, self.update_progress, pct, msg, "local")
        def err_cb(msg): self.after(0, self.show_error, msg)

        try:
            res = core.process_local_files(
                files,
                self.session_id,
                progress_callback=prog_cb,
                error_callback=err_cb,
                cancel_event=self.cancel_event,
            )
        except Exception as e:
            err_cb(f"Local processing failed: {e}")
            res = []
        self.after(0, self._on_local_complete, res)

    def _on_local_complete(self, files):
        self.process_local_btn.configure(state="normal")
        self.processed_files.extend(files)
        self.update_export_tab()
        if files:
            self.append_log(f"Successfully processed local files: {files}", "SUCCESS")
            messagebox.showinfo("Done", f"Processed {len(files)} local files.")
        else:
            self.append_log("Local file processing finished with no results.", "WARNING")
            messagebox.showwarning("Nothing processed", "No local files were processed. See the status line for details.")

    # --------------- EXPORT / MERGE LOGIC ---------------
    def remove_session_file(self, filepath):
        if filepath in self.processed_files:
            self.processed_files.remove(filepath)
            self.update_export_tab()
            self.append_log(f"Removed session file: {filepath}", "INFO")

    def update_export_tab(self):
        for w in self.export_files_widgets:
            w.destroy()
        self.export_files_widgets.clear()
        
        if not self.processed_files:
            self.export_status.configure(text="No files processed in this session.")
            self.merge_btn.configure(state="disabled")
            
            lbl = ctk.CTkLabel(
                self.export_files_scroll, 
                text="No transcripts processed yet. Grab subtitles or process local files first.", 
                text_color=self.TEXT_MUTED,
                font=ctk.CTkFont(family="Inter", size=12)
            )
            lbl.grid(row=0, column=0, padx=15, pady=15, sticky="w")
            self.export_files_widgets.append(lbl)
            return
            
        self.export_status.configure(text=f"{len(self.processed_files)} files currently in session.")
        self.merge_btn.configure(state="normal")
        
        for i, filepath in enumerate(self.processed_files):
            path_obj = pathlib.Path(filepath)
            size_kb = path_obj.stat().st_size / 1024 if path_obj.exists() else 0
            
            row_frame = ctk.CTkFrame(self.export_files_scroll, fg_color="transparent")
            row_frame.grid(row=i, column=0, padx=5, pady=4, sticky="ew")
            self.export_files_widgets.append(row_frame)
            
            lbl_name = ctk.CTkLabel(
                row_frame, 
                text=f"📝 {path_obj.name} ({size_kb:.1f} KB)", 
                font=ctk.CTkFont(family="Inter", size=12)
            )
            lbl_name.pack(side="left", padx=5)
            
            btn_remove = ctk.CTkButton(
                row_frame, 
                text="✕", 
                width=24, 
                height=20, 
                fg_color="transparent", 
                hover_color=self.DANGER_RED_HOVER, 
                text_color=self.TEXT_MUTED, 
                command=lambda p=filepath: self.remove_session_file(p),
                font=ctk.CTkFont(family="Inter", size=10, weight="bold")
            )
            btn_remove.pack(side="right", padx=5)

    def apply_merge(self):
        strat = self.merge_strategy_var.get()
        if not self.processed_files: return
        
        valid_files = [pathlib.Path(p) for p in self.processed_files if pathlib.Path(p).exists()]
        if not valid_files:
            messagebox.showerror("Error", "Processed files not found on disk.")
            return

        self.append_log(f"Applying merge strategy '{strat}' to {len(valid_files)} file(s)...", "INFO")
        
        # Use user-configured export directory
        export_dir = pathlib.Path(self.config["export_dir"])
        export_dir.mkdir(parents=True, exist_ok=True)
        
        core.merge_files(valid_files, strat, export_dir)
        
        out_folder = str(export_dir)
        self.append_log(f"Merge operation completed. Output saved to: {out_folder}", "SUCCESS")
        
        messagebox.showinfo("Merge Complete", f"Files successfully merged/prepared in:\n{out_folder}")
        self._open_folder(out_folder)

    def _open_folder(self, folder: str):
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", folder])
            elif sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.run(["xdg-open", folder])
        except Exception as e:
            self.append_log(f"Failed to open output directory: {e}", "WARNING")

if __name__ == "__main__":
    app = App()
    app.mainloop()
