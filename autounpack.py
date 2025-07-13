import configparser
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import pystray
from PIL import Image, ImageDraw, ImageFont
from qbittorrent import Client
# Watchdog is no longer needed
# from watchdog.events import FileSystemEventHandler
# from watchdog.observers import Observer

from style import Style

# --- Platform Specific Imports ---
IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import win32com.client


# --- Configuration ---
CONFIG_FILE = "config.ini"
EXTRACTION_LOG_FILE = "extractions.log"
SUPPORTED_ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"}
PART_REGEX = re.compile(r"(.+?)\.(part\d{1,3}|[rs]\d{2}|z\d{2}|\d{3})$", re.IGNORECASE)


class Unpacker:
    """Handles the logic for finding and extracting archives."""

    def __init__(self, seven_zip_path, delete_on_success, logger, gui_queue, create_subfolder):
        self.seven_zip_path = seven_zip_path
        self.delete_on_success = delete_on_success
        self.logger = logger
        self.gui_queue = gui_queue
        self.create_subfolder = create_subfolder

    def unpack_archives(self, path):
        """Finds and unpacks archives at the given path."""
        all_archives = []
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS or PART_REGEX.match(path.name):
                all_archives.append(path)
        elif path.is_dir():
            for root, _, files in os.walk(path):
                for file in files:
                    file_path = Path(root) / file
                    if file_path.suffix.lower() in SUPPORTED_ARCHIVE_EXTENSIONS or PART_REGEX.match(file_path.name):
                        all_archives.append(file_path)

        if not all_archives:
            self.logger.info(f"No supported archives found in '{path}'.")
            return

        archive_sets = defaultdict(list)
        for archive in all_archives:
            # For filenames like "archive.part1.rar", the stem is "archive.part1"
            name_to_check = archive.stem
            match = PART_REGEX.match(name_to_check)
            if not match and archive.suffix.lower() == '.rar':
                # For ".rar, .r00, .r01" sets, the base name is the same for all.
                # The first file is ".rar", subsequent are ".rXX"
                match = PART_REGEX.match(archive.name)

            base_name = match.group(1) if match else archive.stem
            archive_sets[base_name].append(archive)

        for base_name, file_list in archive_sets.items():
            # For RAR sets, the primary file is the one with the .rar extension
            # or the lowest numbered part if .rar is not present.
            rar_files = [f for f in file_list if f.suffix.lower() == '.rar']
            if rar_files:
                primary_file = rar_files[0]
            else:
                primary_file = sorted(file_list)[0]

            self.logger.info(f"Found archive set '{base_name}' with {len(file_list)} parts. Starting with '{primary_file.name}'.")
            self.extract_archive(primary_file, file_list, path.name)

    def extract_archive(self, archive_path, all_parts, torrent_name):
        base_name = torrent_name
        
        if self.create_subfolder:
            output_dir = archive_path.parent / base_name
            counter = 1
            # Create a unique directory name if the original one exists
            while output_dir.exists():
                output_dir = archive_path.parent / f"{base_name} ({counter})"
                counter += 1
            
            extraction_name = output_dir.name
            # The directory is guaranteed not to exist at this point, so we create it.
            output_dir.mkdir()
            self.logger.info(f"Created extraction directory: '{output_dir}'")
        else:
            output_dir = archive_path.parent
            extraction_name = base_name
            self.logger.info(f"Extracting archive to root folder: '{output_dir}'")

        command = [self.seven_zip_path, "x", str(archive_path), f"-o{output_dir}", "-y"]
        try:
            self.gui_queue.put(('status', f"Extracting: {archive_path.name}"))
            self.gui_queue.put(('progress', 'start'))
            self.logger.info(f"Extracting '{archive_path.name}'...")
            
            # Use CREATE_NO_WINDOW on Windows to prevent any console window from appearing
            if IS_WINDOWS:
                subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore', creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            
            self.logger.info(f"Successfully extracted '{archive_path.name}' to '{output_dir}'")
            self._log_extraction_event('SUCCESS', extraction_name, str(output_dir))


            if self.delete_on_success:
                self.logger.info(f"Deleting {len(all_parts)} archive part(s)...")
                for part in all_parts:
                    try:
                        os.remove(part)
                        self.logger.info(f"Deleted '{part.name}'")
                    except OSError as e:
                        self.logger.error(f"Failed to delete '{part.name}': {e}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to extract '{archive_path.name}'.")
            if e.stderr:
                self.logger.error(f"7-Zip Output:\n{e.stderr}")
            self._log_extraction_event('FAILURE', extraction_name, str(output_dir))
        except Exception as e:
            self.logger.error(f"An unexpected error occurred during extraction: {e}")
            self._log_extraction_event('FAILURE', extraction_name, str(output_dir))
        finally:
            self.gui_queue.put(('progress', 'stop'))
            self.gui_queue.put(('status', "Monitoring..."))

    def _log_extraction_event(self, status, name, path):
        """Logs an extraction event to the history file and GUI queue."""
        with open(EXTRACTION_LOG_FILE, 'a') as f:
            f.write(f"{status}:{name}:{path}\n")
        
        if status == 'SUCCESS':
            self.gui_queue.put(('extraction_success', (name, path)))
        elif status == 'FAILURE':
            self.gui_queue.put(('extraction_failure', (name, path)))


class QueueHandler(logging.Handler):
    """Class to send logging records to a queue."""
    def __init__(self, gui_queue):
        super().__init__()
        self.gui_queue = gui_queue

    def emit(self, record):
        self.gui_queue.put(('log', self.format(record)))


class UnpackMonitorThread(threading.Thread):
    """The main worker thread for monitoring and unpacking."""
    def __init__(self, config, logger, processed_torrents, gui_queue):
        super().__init__()
        self.config = config
        self.logger = logger
        self.daemon = True
        self._stop_event = threading.Event()
        self.processed_torrents = processed_torrents
        self.gui_queue = gui_queue

    def run(self):
        qbt_config = self.config["qBittorrent"]
        qbt_url = f"http://{qbt_config.get('host')}:{qbt_config.get('port')}/"
        qbt_client = Client(qbt_url)

        try:
            qbt_client.login(username=qbt_config.get("username"), password=qbt_config.get("password"))
            self.logger.info("Successfully connected to qBittorrent.")
            self.gui_queue.put(('status', "Monitoring..."))
        except Exception as e:
            self.logger.error(f"Could not connect to qBittorrent: {e}")
            self.gui_queue.put(('status', "Error: Connection Failed"))
            return

        folder_config = self.config["Folders"]
        monitor_path_str = folder_config.get("monitor_path")
        monitor_path = Path(monitor_path_str)
        seven_zip_path = folder_config.get("seven_zip_path")
        delete_on_success = self.config.getboolean("General", "delete_on_success", fallback=False)
        create_subfolder = self.config.getboolean("General", "create_subfolder", fallback=True)

        unpacker = Unpacker(seven_zip_path, delete_on_success, self.logger, self.gui_queue, create_subfolder)
        
        self.logger.info(f"Starting real-time monitoring of: {monitor_path_str}")
        self.logger.info("Polling qBittorrent for completed torrents every 15 seconds...")

        while not self._stop_event.is_set():
            try:
                torrents_to_process = []
                for torrent in qbt_client.torrents():
                    # Check if torrent is complete, not already processed, and in the monitored path
                    if torrent["progress"] == 1 and torrent['hash'] not in self.processed_torrents:
                        content_path = Path(torrent["content_path"])
                        # Ensure we only process torrents inside the monitored path
                        if str(content_path.resolve()).startswith(str(monitor_path.resolve())):
                            torrents_to_process.append(torrent)
                
                if torrents_to_process:
                    self.logger.info(f"Found {len(torrents_to_process)} new completed torrent(s).")
                    for i, torrent in enumerate(torrents_to_process):
                        content_path = Path(torrent["content_path"])
                        self.gui_queue.put(('status', f"Processing ({i+1}/{len(torrents_to_process)}): {torrent['name']}"))
                        self.processed_torrents.add(torrent['hash'])
                        
                        try:
                            # Using pause_torrents which is the correct v2 API method name
                            qbt_client.torrents_pause(torrent_hashes=[torrent['hash']])
                            self.logger.info(f"Paused torrent: {torrent['name']}")
                        except Exception as e:
                            self.logger.error(f"Failed to pause torrent '{torrent['name']}': {e}. Proceeding anyway.")
                        
                        unpacker.unpack_archives(content_path)
                    
                    self.gui_queue.put(('status', "Monitoring...")) # Reset status after processing batch

            except Exception as e:
                self.logger.error(f"An error occurred during polling: {e}", exc_info=True)
                self.gui_queue.put(('status', "Error during polling. Retrying..."))
                # Wait longer after an error to avoid spamming logs
                self._stop_event.wait(60) 
                continue

            # Wait for 15 seconds before the next poll.
            # wait() is used instead of sleep() to make stopping more responsive.
            self._stop_event.wait(15)

        self.logger.info("Monitoring stopped.")

    def stop(self):
        self._stop_event.set()

# The ArchiveEventHandler is no longer needed with the polling method.
# class ArchiveEventHandler(FileSystemEventHandler):
#     ...

class MainApp(tk.Tk):
    """The main GUI application class."""
    def __init__(self):
        super().__init__()
        self.title("AutoUnpack")
        
        self.style = Style()

        self.minsize(650, 450)

        self.config = configparser.ConfigParser()
        self.monitor_thread = None
        self.processed_torrents = set()
        self.extraction_history = []
        self.log_window = None
        self.log_text_widget = None
        self._save_job = None
        self.tray_icon = None

        self.gui_queue = queue.Queue()
        self.queue_handler = QueueHandler(self.gui_queue)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        self.queue_handler.setFormatter(formatter)
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        self.logger.addHandler(self.queue_handler)
        
        self._create_widgets()
        self.load_config()
        self._load_extraction_history()
        self._create_icon_file_if_needed()
        self._setup_window_icon()
        self._setup_system_tray()

        if self.start_on_launch.get():
            self.after(500, self.start_monitoring)

        self.after(100, self._poll_gui_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_closing)
        self.bind("<Unmap>", self._on_minimize)

    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill="both", expand=True)

        settings_frame = ttk.LabelFrame(main_frame, text="Settings", padding=(10, 10, 10, 5))
        settings_frame.pack(fill="x")

        qbit_frame = ttk.LabelFrame(settings_frame, text="qBittorrent", padding=(10, 5))
        qbit_frame.grid(row=0, column=0, padx=(0, 5), pady=5, sticky="nsew")

        self.qbt_host_var = tk.StringVar()
        self.qbt_host_var.trace_add("write", self._schedule_save)
        ttk.Label(qbit_frame, text="Host:").grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.qbt_host = tk.Entry(qbit_frame, textvariable=self.qbt_host_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.qbt_host.grid(row=0, column=1, sticky="ew", pady=(0, 2))

        self.qbt_port_var = tk.StringVar()
        self.qbt_port_var.trace_add("write", self._schedule_save)
        ttk.Label(qbit_frame, text="Port:").grid(row=1, column=0, sticky="w", pady=(0, 2))
        self.qbt_port = tk.Entry(qbit_frame, textvariable=self.qbt_port_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.qbt_port.grid(row=1, column=1, sticky="ew", pady=(0, 2))

        self.qbt_user_var = tk.StringVar()
        self.qbt_user_var.trace_add("write", self._schedule_save)
        ttk.Label(qbit_frame, text="Username:").grid(row=2, column=0, sticky="w", pady=(0, 2))
        self.qbt_user = tk.Entry(qbit_frame, textvariable=self.qbt_user_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.qbt_user.grid(row=2, column=1, sticky="ew", pady=(0, 2))

        self.qbt_pass_var = tk.StringVar()
        self.qbt_pass_var.trace_add("write", self._schedule_save)
        ttk.Label(qbit_frame, text="Password:").grid(row=3, column=0, sticky="w", pady=(0, 2))
        self.qbt_pass = tk.Entry(qbit_frame, show="*", textvariable=self.qbt_pass_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.qbt_pass.grid(row=3, column=1, sticky="ew")

        folder_frame = ttk.LabelFrame(settings_frame, text="Folders", padding=(10, 5))
        folder_frame.grid(row=0, column=1, padx=(5, 0), pady=5, sticky="nsew")
        folder_frame.grid_columnconfigure(0, weight=1)

        ttk.Label(folder_frame, text="Monitor Folder:").grid(row=0, column=0, sticky="w", columnspan=2)
        self.monitor_path_var = tk.StringVar()
        self.monitor_path_var.trace_add("write", self._schedule_save)
        self.monitor_path = tk.Entry(folder_frame, width=30, textvariable=self.monitor_path_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.monitor_path.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ttk.Button(folder_frame, text="Browse...", command=self._browse_monitor_folder).grid(row=1, column=1, padx=(5,0), pady=(2, 0))

        ttk.Label(folder_frame, text="7-Zip Path (7z.exe):").grid(row=2, column=0, sticky="w", columnspan=2, pady=(5,0))
        self.seven_zip_path_var = tk.StringVar()
        self.seven_zip_path_var.trace_add("write", self._schedule_save)
        self.seven_zip_path = tk.Entry(folder_frame, width=30, textvariable=self.seven_zip_path_var, background=self.style.COLOR_MEDIUM_GRAY, foreground=self.style.COLOR_WHITE, insertbackground=self.style.COLOR_WHITE, borderwidth=2, relief="flat")
        self.seven_zip_path.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        ttk.Button(folder_frame, text="Browse...", command=self._browse_7zip).grid(row=3, column=1, padx=(5,0), pady=(2, 0))

        options_frame = ttk.Frame(settings_frame)
        options_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(5,0))

        self.delete_on_success = tk.BooleanVar()
        self.delete_on_success.trace_add("write", self._schedule_save)
        ttk.Checkbutton(options_frame, text="Delete archives after successful extraction", variable=self.delete_on_success).pack(side="left", anchor="w")
        
        self.start_on_launch = tk.BooleanVar()
        self.start_on_launch.trace_add("write", self._schedule_save)
        ttk.Checkbutton(options_frame, text="Start monitoring on launch", variable=self.start_on_launch).pack(side="left", anchor="w", padx=10)

        self.create_subfolder = tk.BooleanVar()
        self.create_subfolder.trace_add("write", self._schedule_save)
        ttk.Checkbutton(options_frame, text="Create subfolder for each extraction", variable=self.create_subfolder).pack(side="left", anchor="w")

        self.run_on_startup = tk.BooleanVar()
        if IS_WINDOWS:
            self.run_on_startup.trace_add("write", self._update_startup_setting)
            ttk.Checkbutton(options_frame, text="Run on Windows startup", variable=self.run_on_startup).pack(side="left", anchor="w", padx=10)
        
        settings_frame.grid_columnconfigure(1, weight=1)

        status_frame = ttk.LabelFrame(main_frame, text="Status", padding=(10, 5))
        status_frame.pack(pady=10, fill="x")
        self.status_label = ttk.Label(status_frame, text="Idle", anchor="w")
        self.status_label.pack(fill="x")
        self.progress_bar = ttk.Progressbar(status_frame, mode='indeterminate')
        self.progress_bar.pack(fill='x', pady=5)
        
        history_frame = ttk.LabelFrame(main_frame, text="Extraction History", padding=(10, 5))
        history_frame.pack(pady=(0, 10), fill="both", expand=True)
        
        history_list_frame = ttk.Frame(history_frame)
        history_list_frame.pack(fill="both", expand=True, pady=5)
        
        scrollbar = ttk.Scrollbar(history_list_frame, orient="vertical")
        
        self.history_listbox = tk.Listbox(history_list_frame, 
            yscrollcommand=scrollbar.set,
            background=self.style.listbox_bg,
            foreground=self.style.listbox_fg,
            selectbackground=self.style.listbox_select_bg,
            selectforeground=self.style.listbox_select_fg,
            borderwidth=0,
            highlightthickness=0,
            activestyle="none"
        )
        self.history_listbox.pack(side="left", fill="both", expand=True)
        self.history_listbox.bind("<<ListboxSelect>>", self._on_history_select)
        scrollbar.config(command=self.history_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        
        history_buttons_frame = ttk.Frame(history_frame)
        history_buttons_frame.pack(fill="x")
        
        self.open_folder_button = ttk.Button(history_buttons_frame, text="Open Folder", command=self._open_extraction_folder, state="disabled")
        self.open_folder_button.pack(side="left", pady=(0, 5))
        
        ttk.Button(history_buttons_frame, text="Clear History", command=self._clear_extraction_history).pack(side="right", pady=(0, 5))
        ttk.Button(history_buttons_frame, text="Clear & Delete All Data", command=self._delete_all_data, style="Danger.TButton").pack(side="right", padx=5, pady=(0, 5))


        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill="x")
        self.start_button = ttk.Button(controls_frame, text="Save & Start", command=self.start_monitoring)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls_frame, text="Stop", command=self.stop_monitoring, state="disabled")
        self.stop_button.pack(side="left", padx=5)
        # The manual scan button is removed as the new polling mechanism makes it redundant.
        self.logs_button = ttk.Button(controls_frame, text="View Logs", command=self.show_logs)
        self.logs_button.pack(side="right")

    def show_logs(self):
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return
            
        self.log_window = tk.Toplevel(self)
        self.log_window.title("Activity Log")
        self.log_window.geometry("800x500")
        self.log_window.configure(background=self.style.COLOR_DARK_GRAY)
        
        log_frame = ttk.Frame(self.log_window, padding="10")
        log_frame.pack(fill="both", expand=True)
        
        self.log_text_widget = scrolledtext.ScrolledText(log_frame, 
            state="disabled", 
            wrap=tk.WORD,
            background=self.style.COLOR_MEDIUM_GRAY,
            foreground=self.style.COLOR_LIGHT_GRAY
        )
        self.log_text_widget.pack(fill="both", expand=True)

        self.log_window.protocol("WM_DELETE_WINDOW", self.hide_logs)

    def hide_logs(self):
        if self.log_window:
            self.log_window.withdraw()

    def _schedule_save(self, *args):
        """Schedules a save operation, debouncing rapid changes."""
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(1000, self.save_config) # 1 second delay

    def _browse_monitor_folder(self):
        path = filedialog.askdirectory(title="Select Folder to Monitor")
        if path:
            self.monitor_path_var.set(path)
            self.save_config()

    def _browse_7zip(self):
        path = filedialog.askopenfilename(title="Select 7z.exe", filetypes=(("Executable", "*.exe"), ("All files", "*.*")))
        if path:
            self.seven_zip_path_var.set(path)
            self.save_config()

    def _update_startup_setting(self, *args):
        if not IS_WINDOWS:
            return
        
        try:
            startup_path = self._get_startup_folder()
            shortcut_path = os.path.join(startup_path, "AutoUnpack.lnk")

            if self.run_on_startup.get():
                self.logger.info("Adding application to Windows startup.")
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(shortcut_path)
                
                # Use pythonw.exe to run without a console window
                python_exe_path = sys.executable.replace("python.exe", "pythonw.exe")
                script_path = os.path.abspath(__file__)
                
                shortcut.Targetpath = python_exe_path
                shortcut.Arguments = f'"{script_path}"'
                shortcut.WorkingDirectory = os.path.dirname(script_path)
                shortcut.IconLocation = python_exe_path
                shortcut.save()
            else:
                self.logger.info("Removing application from Windows startup.")
                if os.path.exists(shortcut_path):
                    os.remove(shortcut_path)

        except Exception as e:
            self.logger.error(f"Failed to update startup setting: {e}", exc_info=True)
            messagebox.showerror("Startup Error", f"Failed to update Windows startup setting:\n{e}")

    def _get_startup_folder(self):
        if IS_WINDOWS:
            shell = win32com.client.Dispatch("WScript.Shell")
            return shell.SpecialFolders("Startup")
        return None

    def _on_history_select(self, event=None):
        """Enables or disables the 'Open Folder' button based on selection."""
        if self.history_listbox.curselection():
            self.open_folder_button.config(state="normal")
        else:
            self.open_folder_button.config(state="disabled")

    def _open_extraction_folder(self):
        """Opens the folder for the selected extraction history item."""
        selected_indices = self.history_listbox.curselection()
        if not selected_indices:
            return
        
        selected_index = selected_indices[0]
        try:
            _, _, path_str = self.extraction_history[selected_index]
            path = Path(path_str)
            if path.exists():
                os.startfile(path) # For Windows
            else:
                messagebox.showerror("Error", f"Folder not found:\n{path}")
        except IndexError:
            self.logger.error("History selection index is out of range.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{e}")

    def _load_extraction_history(self):
        self.extraction_history = []
        if Path(EXTRACTION_LOG_FILE).exists():
            with open(EXTRACTION_LOG_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split(':', 2)
                    if len(parts) < 3: continue
                    
                    status, name, path = parts
                    self.extraction_history.append((status, name, path))

                    self.history_listbox.insert(tk.END, name)
                    idx = self.history_listbox.size() - 1
                    if status == 'SUCCESS':
                        self.history_listbox.itemconfig(idx, {'bg': self.style.success_color, 'fg': self.style.COLOR_DARK_GRAY})
                    elif status == 'FAILURE':
                        self.history_listbox.itemconfig(idx, {'bg': self.style.error_color, 'fg': self.style.COLOR_DARK_GRAY})

    def _clear_extraction_history(self):
        if messagebox.askyesno("Clear History", "Are you sure you want to permanently delete the extraction history log?\n\n(This will not delete any extracted files.)"):
            self.history_listbox.delete(0, tk.END)
            self.extraction_history = []
            if Path(EXTRACTION_LOG_FILE).exists():
                os.remove(EXTRACTION_LOG_FILE)
            self.logger.info("Extraction history log cleared.")
            self._on_history_select()

    def _delete_all_data(self):
        """Deletes the history log AND all extracted folders after confirmation."""
        import tkinter.simpledialog
        
        warning_msg = (
            "This is a destructive action that cannot be undone.\n\n"
            "It will permanently delete:\n"
            "  1. All entries from the Extraction History.\n"
            "  2. All corresponding extracted folders and their contents from your disk.\n\n"
            "To confirm, please type 'DELETE!' in the box below."
        )

        response = tkinter.simpledialog.askstring("Confirm Deletion", warning_msg, parent=self)

        if response != "DELETE!":
            messagebox.showinfo("Cancelled", "Deletion cancelled. No files were changed.")
            return

        self.logger.info("Starting deletion of all extracted data...")
        
        deleted_count = 0
        failed_count = 0
        
        # Make a copy for safe iteration while modifying the original
        history_to_delete = list(self.extraction_history)

        for _, _, path_str in history_to_delete:
            try:
                path = Path(path_str)
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path)
                    self.logger.info(f"Deleted folder: {path}")
                    deleted_count += 1
                elif path.exists():
                    # Handle cases where the path might be a file (less likely)
                    os.remove(path)
                    self.logger.info(f"Deleted file: {path}")
                    deleted_count += 1
            except Exception as e:
                self.logger.error(f"Failed to delete '{path_str}': {e}")
                failed_count += 1
        
        # Clear the in-app history and log file
        self.history_listbox.delete(0, tk.END)
        self.extraction_history = []
        if Path(EXTRACTION_LOG_FILE).exists():
            os.remove(EXTRACTION_LOG_FILE)
        
        self.logger.info("All data deletion process complete.")
        
        summary_msg = f"Deletion complete.\n\nSuccessfully deleted: {deleted_count} items.\nFailed to delete: {failed_count} items."
        if failed_count > 0:
            summary_msg += "\n\nCheck the logs for more details on failures."
        messagebox.showinfo("Deletion Complete", summary_msg)
        self._on_history_select()

    def load_config(self):
        if not Path(CONFIG_FILE).exists():
            self.logger.info("No config file found. Please enter your settings.")
            return

        self.config.read(CONFIG_FILE)
        if 'qBittorrent' in self.config:
            qbt = self.config['qBittorrent']
            self.qbt_host_var.set(qbt.get('host', 'localhost'))
            self.qbt_port_var.set(qbt.get('port', '8080'))
            self.qbt_user_var.set(qbt.get('username', ''))
            self.qbt_pass_var.set(qbt.get('password', ''))

        if 'Folders' in self.config:
            folders = self.config['Folders']
            self.monitor_path_var.set(folders.get('monitor_path', ''))
            self.seven_zip_path_var.set(folders.get('seven_zip_path', ''))

        if 'General' in self.config:
            general_config = self.config['General']
            self.delete_on_success.set(general_config.getboolean('delete_on_success', fallback=False))
            self.start_on_launch.set(general_config.getboolean('start_on_launch', fallback=False))
            self.create_subfolder.set(general_config.getboolean('create_subfolder', fallback=True))
            if IS_WINDOWS:
                self.run_on_startup.set(general_config.getboolean('run_on_startup', fallback=False))
            
        self.logger.info("Configuration loaded.")

    def save_config(self):
        self.config['qBittorrent'] = {'host': self.qbt_host_var.get(), 'port': self.qbt_port_var.get(), 'username': self.qbt_user_var.get(), 'password': self.qbt_pass_var.get()}
        self.config['Folders'] = {'monitor_path': self.monitor_path_var.get(), 'seven_zip_path': self.seven_zip_path_var.get()}
        self.config['General'] = {
            'delete_on_success': str(self.delete_on_success.get()),
            'start_on_launch': str(self.start_on_launch.get()),
            'create_subfolder': str(self.create_subfolder.get()),
            'run_on_startup': str(self.run_on_startup.get())
            }
        with open(CONFIG_FILE, 'w') as configfile:
            self.config.write(configfile)
        self.logger.info("Configuration saved.")
        return True

    def start_monitoring(self):
        if not all([self.qbt_host_var.get(), self.qbt_port_var.get(), self.monitor_path_var.get(), self.seven_zip_path_var.get()]):
            messagebox.showerror("Error", "Please fill in all required fields.")
            return

        if not self.save_config():
            return

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        # self.scan_button.config(state="disabled") # No longer needed
        self.monitor_thread = UnpackMonitorThread(self.config, self.logger, self.processed_torrents, self.gui_queue)
        self.monitor_thread.start()

    def stop_monitoring(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.stop()
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        # self.scan_button.config(state="normal") # No longer needed
        self.status_label.config(text="Idle")

    # The manual_scan and _run_manual_scan methods are no longer needed.
    
    def _poll_gui_queue(self):
        while True:
            try:
                message_type, data = self.gui_queue.get(block=False)
            except queue.Empty:
                break
            else:
                if message_type == 'log':
                    if self.log_window and self.log_window.winfo_exists() and self.log_text_widget:
                        self.log_text_widget.config(state="normal")
                        self.log_text_widget.insert(tk.END, data + '\n')
                        self.log_text_widget.config(state="disabled")
                        self.log_text_widget.see(tk.END)
                elif message_type == 'status':
                    self.status_label.config(text=data)
                elif message_type == 'progress':
                    if data == 'start':
                        self.progress_bar.start(10)
                    elif data == 'stop':
                        self.progress_bar.stop()
                elif message_type == 'extraction_success':
                    name, path = data
                    self.extraction_history.append(('SUCCESS', name, path))
                    self.history_listbox.insert(tk.END, name)
                    self.history_listbox.itemconfig(self.history_listbox.size() - 1, {'bg': self.style.success_color, 'fg': self.style.COLOR_DARK_GRAY})
                elif message_type == 'extraction_failure':
                    name, path = data
                    self.extraction_history.append(('FAILURE', name, path))
                    self.history_listbox.insert(tk.END, name)
                    self.history_listbox.itemconfig(self.history_listbox.size() - 1, {'bg': self.style.error_color, 'fg': self.style.COLOR_DARK_GRAY})
                        
        self.after(100, self._poll_gui_queue)

    def _create_icon_file_if_needed(self):
        """Generates a permanent icon.ico file if one doesn't already exist."""
        icon_path = "icon.ico"
        if not os.path.exists(icon_path):
            self.logger.info("Generating permanent icon file (icon.ico)...")
            try:
                width = 64
                height = 64
                background_color = (45, 45, 45)
                text_color = (0, 191, 255)
                image = Image.new('RGB', (width, height), background_color)
                draw = ImageDraw.Draw(image)
                try:
                    font = ImageFont.truetype("arialbd.ttf", 42)
                except IOError:
                    font = ImageFont.load_default()
                text = "AE"
                try:
                    text_bbox = draw.textbbox((0, 0), text, font=font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                    position = ((width - text_width) / 2, (height - text_height) / 2 - 5)
                    draw.text(position, text, font=font, fill=text_color)
                except AttributeError:
                    text_width, text_height = draw.textsize(text, font)
                    position = ((width - text_width) / 2, (height - text_height) / 2)
                    draw.text(position, text, font=font, fill=text_color)
                
                # Save as a multi-resolution ICO file
                image.save(icon_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            except Exception as e:
                self.logger.error(f"Failed to generate icon.ico: {e}", exc_info=True)

    def _setup_window_icon(self):
        """Creates and sets the main window icon from the permanent icon file."""
        try:
            icon_path = "icon.ico"
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception as e:
            self.logger.warning(f"Could not create or set window icon: {e}")

    def _setup_system_tray(self):
        """Sets up the system tray icon and its thread."""
        try:
            image = Image.open("icon.ico")
            menu = (
                pystray.MenuItem('Show', self._show_window, default=True),
                pystray.MenuItem('Quit', self._quit_application)
            )
            self.tray_icon = pystray.Icon("AutoUnpack", image, "AutoUnpack", menu)
            
            # Run the icon in a separate thread
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self.logger.error(f"Failed to create system tray icon: {e}", exc_info=True)

    def _show_window(self):
        """Shows the main application window."""
        self.deiconify()
        self.lift()

    def _quit_application(self):
        """Stops all processes and quits the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.stop()
            self.monitor_thread.join(timeout=2) # Wait for thread to finish
        
        self.destroy()

    def _on_minimize(self, event=None):
        """Handles the window being minimized."""
        # When the window state is 'iconic' (minimized), hide it.
        if self.state() == 'iconic':
            self.withdraw()

    def _on_closing(self):
        # Hide the window to the system tray instead of closing it
        self.iconify()
        self.withdraw()
        self.tray_icon.notify("AutoUnpack is still running in the background.", "AutoUnpack")


if __name__ == "__main__":
    app = MainApp()
    app.mainloop() 