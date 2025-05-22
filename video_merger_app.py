import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import os
import subprocess
import threading
import json
from datetime import datetime
import queue # Added for dialog synchronization
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin # For handling relative URLs from scraping
from tkinter import scrolledtext # For displaying scraped titles
from PIL import Image, ImageTk # For video preview
import cv2 # For video preview
from difflib import get_close_matches # For fuzzy search, standard library alternative to thefuzz

class VideoMergerApp:
    # Define a more specific User-Agent
    REQUEST_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    def __init__(self, root):
        self.all_scraped_titles = [] # To store all titles fetched from URL
        self.selected_local_video_path = None # Path of the video selected for preview
        self.video_capture = None # OpenCV video capture object
        self.is_previewing = False # Flag to control video preview loop
        self.preview_frame_job = None # To store root.after job ID for preview
        self.root = root
        self.dialog_queue = queue.Queue() # Added for dialog synchronization
        self.root.title("视频合并转换工具")
        self.root.geometry("800x600")

        # --- Configuration ---
        self.history_file = os.path.join(os.path.dirname(__file__), "conversion_history.json")
        
        # Construct path to ffmpeg.exe relative to the script's directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ffmpeg_dir_path = os.path.join(script_dir, "ffmpeg", "bin", "ffmpeg.exe")
        ffmpeg_script_path = os.path.join(script_dir, "ffmpeg.exe")

        if os.path.exists(ffmpeg_dir_path) and os.access(ffmpeg_dir_path, os.X_OK):
            self.ffmpeg_path = ffmpeg_dir_path
        elif os.path.exists(ffmpeg_script_path) and os.access(ffmpeg_script_path, os.X_OK):
            self.ffmpeg_path = ffmpeg_script_path
        else:
            self.ffmpeg_path = "ffmpeg" # Fallback to system PATH

        # --- UI Elements ---
        self.setup_ui()
        self.load_history()

    def setup_ui(self):
        # --- Frames ---
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Input/Output Frame
        io_frame = ttk.LabelFrame(main_frame, text="输入与输出", padding="10")
        io_frame.pack(fill=tk.X, pady=5)

        # Controls Frame
        controls_frame = ttk.LabelFrame(main_frame, text="操作", padding="10")
        controls_frame.pack(fill=tk.X, pady=5)

        # Progress Frame
        progress_frame = ttk.LabelFrame(main_frame, text="进度", padding="10")
        progress_frame.pack(fill=tk.X, pady=5)

        # History Frame
        history_frame = ttk.LabelFrame(main_frame, text="历史记录", padding="10")
        history_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # --- Input Folder ---
        ttk.Label(io_frame, text="输入文件夹:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.input_folder_var = tk.StringVar()
        self.input_folder_entry = ttk.Entry(io_frame, textvariable=self.input_folder_var, width=60)
        self.input_folder_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        self.browse_input_button = ttk.Button(io_frame, text="浏览...", command=self.browse_input_folder)
        self.browse_input_button.grid(row=0, column=2, padx=5, pady=5)

        # --- Output Folder ---
        ttk.Label(io_frame, text="输出文件夹:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.output_folder_var = tk.StringVar()
        self.output_folder_entry = ttk.Entry(io_frame, textvariable=self.output_folder_var, width=60)
        self.output_folder_entry.grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)
        self.browse_output_button = ttk.Button(io_frame, text="浏览...", command=self.browse_output_folder)
        self.browse_output_button.grid(row=1, column=2, padx=5, pady=5)

        io_frame.columnconfigure(1, weight=1) # Make entry expand

        # --- Start Button ---
        self.start_button = ttk.Button(controls_frame, text="开始合并与转换", command=self.start_processing_thread)
        self.start_button.pack(pady=10, padx=5, side=tk.LEFT)

        # --- Batch Rename Button ---
        self.rename_button = ttk.Button(controls_frame, text="批量重命名", command=self.open_batch_rename_window)
        self.rename_button.pack(pady=10, padx=5, side=tk.LEFT)

        # --- Progress Bar ---
        self.progress_label_var = tk.StringVar()
        self.progress_label_var.set("状态: 空闲")
        ttk.Label(progress_frame, textvariable=self.progress_label_var).pack(fill=tk.X, pady=2)
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=5)

        # --- History TreeView ---
        self.history_tree = ttk.Treeview(history_frame, columns=("timestamp", "input", "output", "status"), show="headings")
        self.history_tree.heading("timestamp", text="时间")
        self.history_tree.heading("input", text="输入")
        self.history_tree.heading("output", text="输出文件")
        self.history_tree.heading("status", text="状态")

        self.history_tree.column("timestamp", width=150, anchor=tk.W)
        self.history_tree.column("input", width=250, anchor=tk.W)
        self.history_tree.column("output", width=250, anchor=tk.W)
        self.history_tree.column("status", width=100, anchor=tk.W)

        history_scrollbar = ttk.Scrollbar(history_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=history_scrollbar.set)
        history_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_tree.pack(fill=tk.BOTH, expand=True)

        clear_history_button = ttk.Button(history_frame, text="清空历史记录", command=self.clear_history)
        clear_history_button.pack(pady=5, side=tk.RIGHT)

    def browse_input_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.input_folder_var.set(folder_selected)

    def browse_output_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.output_folder_var.set(folder_selected)

    def log_history(self, input_path, output_file, status):
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "input": input_path,
            "output": output_file,
            "status": status
        }
        history = self.load_history_data()
        history.insert(0, entry) # Add to the beginning
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
        except IOError as e:
            messagebox.showerror("历史记录错误", f"无法写入历史记录文件: {e}")
        self.load_history() # Refresh treeview

    def load_history_data(self):
        if not os.path.exists(self.history_file):
            return []
        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            messagebox.showerror("历史记录错误", f"无法读取或解析历史记录文件: {e}")
            return []

    def load_history(self):
        for i in self.history_tree.get_children():
            self.history_tree.delete(i)
        history = self.load_history_data()
        for entry in history:
            self.history_tree.insert("", tk.END, values=(entry["timestamp"], entry["input"], entry["output"], entry["status"]))

    def clear_history(self):
        if messagebox.askyesno("确认", "确定要清空所有历史记录吗?"):
            try:
                if os.path.exists(self.history_file):
                    os.remove(self.history_file)
                self.load_history() # Refresh (will be empty)
                messagebox.showinfo("成功", "历史记录已清空。")
            except OSError as e:
                messagebox.showerror("错误", f"无法清空历史记录: {e}")

    def ask_user_for_overwrite(self, output_filename_display, q):
        # This method is called via root.after, so it runs in the main Tkinter thread
        response = messagebox.askquestion("文件已存在",
                                          f"输出文件 '{output_filename_display}' 已存在。\n\n"
                                          "选择操作:",
                                          icon='warning',
                                          type=messagebox.YESNOCANCEL,
                                          detail="是: 覆盖\n否: 跳过\n取消: 中止所有后续转换",
                                          parent=self.root)
        q.put(response)

    def open_batch_rename_window(self):
        rename_window = tk.Toplevel(self.root)
        rename_window.title("批量文件重命名")
        rename_window.geometry("700x500")
        rename_window.transient(self.root) # Keep window on top of main
        rename_window.grab_set() # Modal behavior

        # Store the rename_window to access its components later if needed
        self.rename_window_ref = rename_window
        self.all_scraped_titles = [] # Reset for each new window instance
        self.selected_local_video_path = None
        # Bind listbox selection events for enabling/disabling buttons
        self.local_files_listbox.bind('<<ListboxSelect>>', self._update_rename_button_state)
        self.matched_online_titles_listbox.bind('<<ListboxSelect>>', self._update_rename_button_state)

        # --- UI Elements for Batch Rename Window ---
        rename_main_frame = ttk.Frame(rename_window, padding="10")
        rename_main_frame.pack(fill=tk.BOTH, expand=True)

        # URL Scraping Frame
        scraping_frame = ttk.LabelFrame(rename_main_frame, text="从URL抓取课程名称", padding="10")
        scraping_frame.pack(fill=tk.X, pady=5)

        ttk.Label(scraping_frame, text="课程URL:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.course_url_var = tk.StringVar(value="https://www.icourse163.org/learn/CUG-1206351804?tid=1474049452#/learn/content")
        self.course_url_entry = ttk.Entry(scraping_frame, textvariable=self.course_url_var, width=60)
        self.course_url_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        self.scrape_button = ttk.Button(scraping_frame, text="抓取名称", command=self.scrape_course_titles)
        self.scrape_button.grid(row=0, column=2, padx=5, pady=5)
        scraping_frame.columnconfigure(1, weight=1)

        # Scraped Titles Frame
        scraped_titles_frame = ttk.LabelFrame(rename_main_frame, text="抓取的课程名称", padding="10")
        scraped_titles_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.scraped_titles_listbox = scrolledtext.ScrolledText(scraped_titles_frame, height=10, width=70)
        self.scraped_titles_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.scraped_titles_listbox.configure(state='disabled') # Make it read-only initially

        # Folder Selection Frame
        folder_select_frame = ttk.LabelFrame(rename_main_frame, text="选择本地视频文件夹", padding="10")
        folder_select_frame.pack(fill=tk.X, pady=5)

        ttk.Label(folder_select_frame, text="目标文件夹:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.rename_folder_var = tk.StringVar()
        self.rename_folder_entry = ttk.Entry(folder_select_frame, textvariable=self.rename_folder_var, width=50)
        self.rename_folder_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        browse_rename_folder_button = ttk.Button(folder_select_frame, text="浏览...", command=lambda: self.browse_rename_folder(self.rename_folder_var))
        browse_rename_folder_button.grid(row=0, column=2, padx=5, pady=5)
        folder_select_frame.columnconfigure(1, weight=1)

        # Renaming Rules Frame
        rules_frame = ttk.LabelFrame(rename_main_frame, text="重命名规则", padding="10")
        rules_frame.pack(fill=tk.X, pady=5)

        ttk.Label(rules_frame, text="查找:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.find_var = tk.StringVar()
        ttk.Entry(rules_frame, textvariable=self.find_var, width=20).grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(rules_frame, text="替换为:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        self.replace_var = tk.StringVar()
        ttk.Entry(rules_frame, textvariable=self.replace_var, width=20).grid(row=0, column=3, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(rules_frame, text="添加前缀:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.prefix_var = tk.StringVar()
        ttk.Entry(rules_frame, textvariable=self.prefix_var, width=20).grid(row=1, column=1, padx=5, pady=5, sticky=tk.EW)

        ttk.Label(rules_frame, text="添加后缀:").grid(row=1, column=2, padx=5, pady=5, sticky=tk.W)
        self.suffix_var = tk.StringVar()
        ttk.Entry(rules_frame, textvariable=self.suffix_var, width=20).grid(row=1, column=3, padx=5, pady=5, sticky=tk.EW)
        
        self.use_sequence_var = tk.BooleanVar()
        sequence_check = ttk.Checkbutton(rules_frame, text="使用序列号 (例如: file_001)", variable=self.use_sequence_var)
        sequence_check.grid(row=2, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)
        ttk.Label(rules_frame, text="起始编号:").grid(row=2, column=2, padx=5, pady=5, sticky=tk.W)
        self.sequence_start_var = tk.StringVar(value="1")
        ttk.Entry(rules_frame, textvariable=self.sequence_start_var, width=5).grid(row=2, column=3, padx=5, pady=5, sticky=tk.W)

        rules_frame.columnconfigure(1, weight=1)
        rules_frame.columnconfigure(3, weight=1)

        # Video Preview and Comparison Frame
        video_compare_frame = ttk.LabelFrame(rename_main_frame, text="视频预览与名称匹配", padding="10")
        video_compare_frame.pack(fill=tk.X, pady=5)

        # Left side: Local files and preview
        local_file_preview_frame = ttk.Frame(video_compare_frame)
        local_file_preview_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        ttk.Label(local_file_preview_frame, text="本地文件:").pack(anchor=tk.W)
        self.local_files_listbox = tk.Listbox(local_file_preview_frame, height=8, exportselection=False)
        self.local_files_listbox.pack(fill=tk.X, expand=True, pady=(0,5))
        self.local_files_listbox.bind('<<ListboxSelect>>', self.on_local_file_select)

        self.video_preview_canvas = tk.Canvas(local_file_preview_frame, width=320, height=180, bg="black")
        self.video_preview_canvas.pack(pady=5)
        self.play_preview_button = ttk.Button(local_file_preview_frame, text="播放15秒预览", command=self.play_video_preview, state=tk.DISABLED)
        self.play_preview_button.pack()

        # Right side: Scraped titles and matching
        scraped_title_match_frame = ttk.Frame(video_compare_frame)
        scraped_title_match_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)

        ttk.Label(scraped_title_match_frame, text="匹配课程名称 (可模糊搜索):").pack(anchor=tk.W)
        self.online_title_search_var = tk.StringVar()
        self.online_title_search_entry = ttk.Entry(scraped_title_match_frame, textvariable=self.online_title_search_var)
        self.online_title_search_entry.pack(fill=tk.X, pady=(0,5))
        self.online_title_search_entry.bind('<KeyRelease>', self.filter_scraped_titles)

        self.matched_online_titles_listbox = tk.Listbox(scraped_title_match_frame, height=8, exportselection=False)
        self.matched_online_titles_listbox.pack(fill=tk.BOTH, expand=True, pady=(0,5))

        self.rename_selected_button = ttk.Button(scraped_title_match_frame, text="使用选中课程名重命名", command=self.rename_to_selected_online_title, state=tk.DISABLED)
        self.rename_selected_button.pack()

        # File List/Preview Frame (Original Renaming Rules)
        preview_frame = ttk.LabelFrame(rename_main_frame, text="文件预览 (原始名 -> 新名) - 基于下方规则", padding="10")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.rename_preview_tree = ttk.Treeview(preview_frame, columns=("original", "new"), show="headings")
        self.rename_preview_tree.heading("original", text="原始文件名")
        self.rename_preview_tree.heading("new", text="新文件名")
        self.rename_preview_tree.column("original", width=250, anchor=tk.W)
        self.rename_preview_tree.column("new", width=250, anchor=tk.W)
        
        preview_scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=self.rename_preview_tree.yview)
        self.rename_preview_tree.configure(yscrollcommand=preview_scrollbar.set)
        preview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rename_preview_tree.pack(fill=tk.BOTH, expand=True)

        # Action Buttons Frame for Rename Window
        rename_action_frame = ttk.Frame(rename_main_frame, padding="5")
        rename_action_frame.pack(fill=tk.X, pady=5)

        preview_button = ttk.Button(rename_action_frame, text="预览更改", command=self.preview_rename_changes)
        preview_button.pack(side=tk.LEFT, padx=5)
        apply_button = ttk.Button(rename_action_frame, text="应用重命名", command=self.apply_rename_changes)
        apply_button.pack(side=tk.LEFT, padx=5)
        close_button = ttk.Button(rename_action_frame, text="关闭", command=rename_window.destroy)
        close_button.pack(side=tk.RIGHT, padx=5)

    def browse_rename_folder(self, string_var):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            string_var.set(folder_selected)
            self.preview_rename_changes() # Auto-preview when folder changes
        self._load_local_video_files(folder_selected) # Load video files for the new comparison UI

    def preview_rename_changes(self):
        # Clear previous preview
        for i in self.rename_preview_tree.get_children():
            self.rename_preview_tree.delete(i)

        folder = self.rename_folder_var.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择一个有效的文件夹。", parent=self.rename_preview_tree.winfo_toplevel())
            return

        find_str = self.find_var.get()
        replace_str = self.replace_var.get()
        prefix = self.prefix_var.get()
        suffix = self.suffix_var.get()
        use_sequence = self.use_sequence_var.get()
        try:
            sequence_start = int(self.sequence_start_var.get())
        except ValueError:
            messagebox.showerror("错误", "序列号起始编号必须是数字。", parent=self.rename_preview_tree.winfo_toplevel())
            return

        files = sorted([f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))])
        
        for i, original_filename in enumerate(files):
            base, ext = os.path.splitext(original_filename)
            new_base = base

            if find_str: # Find and Replace
                new_base = new_base.replace(find_str, replace_str)
            
            if prefix: # Add Prefix
                new_base = prefix + new_base
            
            if suffix: # Add Suffix
                new_base = new_base + suffix
            
            if use_sequence: # Add Sequence Number
                # Format sequence number, e.g., _001, _002
                # Adjust padding based on total number of files for better alignment if needed
                sequence_num_str = f"_{sequence_start + i:03d}" 
                # Decide if sequence replaces part of name or is appended
                # For now, let's append it before the extension, or replace if no other rule applied
                if new_base == base and not prefix and not suffix and not find_str: # if name is unchanged, sequence becomes the name
                    new_base = f"file{sequence_num_str}"
                else:
                    new_base += sequence_num_str

            new_filename = new_base + ext
            self.rename_preview_tree.insert("", tk.END, values=(original_filename, new_filename))

    def apply_rename_changes(self):
        folder = self.rename_folder_var.get()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("错误", "请选择一个有效的文件夹。", parent=self.rename_preview_tree.winfo_toplevel())
            return

        if not self.rename_preview_tree.get_children():
            messagebox.showwarning("提示", "没有可应用的更改。请先预览。", parent=self.rename_preview_tree.winfo_toplevel())
            return

        if not messagebox.askyesno("确认重命名", "确定要应用这些重命名操作吗？此操作无法撤销。", parent=self.rename_preview_tree.winfo_toplevel()):
            return

        renamed_count = 0
        error_count = 0
        for item_id in self.rename_preview_tree.get_children():
            original_filename, new_filename = self.rename_preview_tree.item(item_id, 'values')
            original_filepath = os.path.join(folder, original_filename)
            new_filepath = os.path.join(folder, new_filename)

            if original_filepath == new_filepath:
                continue # No change needed

            if os.path.exists(new_filepath):
                messagebox.showerror("错误", f"目标文件 '{new_filename}' 已存在。请解决冲突后重试。", parent=self.rename_preview_tree.winfo_toplevel())
                error_count += 1
                continue
            
            try:
                os.rename(original_filepath, new_filepath)
                renamed_count += 1
            except OSError as e:
                messagebox.showerror("重命名错误", f"无法重命名 '{original_filename}' 为 '{new_filename}':\n{e}", parent=self.rename_preview_tree.winfo_toplevel())
                error_count += 1
        
        if error_count > 0:
            messagebox.showwarning("部分完成", f"{renamed_count} 个文件已成功重命名，{error_count} 个文件失败。", parent=self.rename_preview_tree.winfo_toplevel())
        else:
            messagebox.showinfo("成功", f"{renamed_count} 个文件已成功重命名。", parent=self.rename_preview_tree.winfo_toplevel())
        
        self.preview_rename_changes() # Refresh preview for rule-based renaming
        self._load_local_video_files(folder) # Refresh local video files list
        # Clear selections and search for the comparison UI
        self.local_files_listbox.selection_clear(0, tk.END)
        self.matched_online_titles_listbox.delete(0, tk.END)
        self.online_title_search_var.set("")
        if hasattr(self, 'video_preview_canvas'):
            self.video_preview_canvas.delete("all")
        self.play_preview_button.config(state=tk.DISABLED)
        self.rename_selected_button.config(state=tk.DISABLED)
        self.selected_local_video_path = None

    def start_processing_thread(self):
        input_folder = self.input_folder_var.get()
        output_folder = self.output_folder_var.get()

        if not input_folder or not os.path.isdir(input_folder):
            messagebox.showerror("错误", "请输入有效的输入文件夹路径。")
            return
        if not output_folder or not os.path.isdir(output_folder):
            messagebox.showerror("错误", "请输入有效的输出文件夹路径。")
            return

        self.start_button.config(state=tk.DISABLED)
        self.progress_bar["value"] = 0
        self.progress_label_var.set("状态: 准备中...")

        # Run processing in a separate thread to keep UI responsive
        thread = threading.Thread(target=self.process_videos, args=(input_folder, output_folder))
        thread.daemon = True # Allows main program to exit even if thread is running
        thread.start()

    def _get_video_files_in_folder(self, folder_path):
        video_extensions = ['.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv'] # Add more if needed
        video_files = []
        if not folder_path or not os.path.isdir(folder_path):
            return video_files
        for f_name in sorted(os.listdir(folder_path)):
            if os.path.isfile(os.path.join(folder_path, f_name)) and \
               os.path.splitext(f_name)[1].lower() in video_extensions:
                video_files.append(f_name)
        return video_files

    def _load_local_video_files(self, folder_path):
        self.local_files_listbox.delete(0, tk.END)
        video_files = self._get_video_files_in_folder(folder_path)
        for vf in video_files:
            self.local_files_listbox.insert(tk.END, vf)
        self.play_preview_button.config(state=tk.DISABLED)
        self.rename_selected_button.config(state=tk.DISABLED)
        self.selected_local_video_path = None
        if hasattr(self, 'video_preview_canvas'):
            self.video_preview_canvas.delete("all")

    def scrape_course_titles(self):
        url = self.course_url_var.get()
        if not url:
            messagebox.showerror("错误", "请输入课程URL。", parent=self.rename_window_ref)
            return

        self.scraped_titles_listbox.configure(state='normal')
        self.scraped_titles_listbox.delete(1.0, tk.END)
        self.scraped_titles_listbox.insert(tk.END, "正在抓取，请稍候...\n")
        self.scraped_titles_listbox.configure(state='disabled')
        self.scrape_button.config(state=tk.DISABLED)
        self.root.update_idletasks()

        try:
            # Using a session for potential cookie handling or connection pooling
            session = requests.Session()
            response = session.get(url, headers=VideoMergerApp.REQUEST_HEADERS, timeout=20) # Increased timeout
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Attempt to find titles - this selector is a guess and likely needs adjustment
            # Based on manual inspection of icourse163, titles might be in divs with class 'text js-text' inside 'section-list'
            # Or, for the specific course, they are in <p class="text"> inside <div class="textCon f-fl"> within list items
            # Let's try a more specific selector based on the course structure
            # The actual titles seem to be within <div class="textCon f-fl">...<p class="text">TITLE</p>...</div>
            # and also the main chapter titles <h3 class="f-thide f-fl listTxt">CHAPTER TITLE</h3>
            # This needs to be robust.

            self.all_scraped_titles = []
            # Example: Find all chapter titles (h3 with class 'listTxt') and lesson titles (p with class 'text')
            # This is a simplified example; a real scraper would need to handle the page structure more carefully.
            # For icourse163, content is often loaded dynamically. This might only get initial static content.
            
            # Let's try to find elements that look like video titles. Common class: 'text' inside 'lessontitle'
            # Or, for the provided URL structure: items with class 'f-richEditorText'.
            # The titles are in <p class="text"> within <div class="textCon f-fl"> for each lesson item.
            # And chapter titles are in <h3 class="f-thide f-fl listTxt">.

            # First, try to get chapter titles
            chapter_elements = soup.select('div.chapter div.listInnerPos h3.listTxt')
            # Then, lesson titles within each chapter section
            lesson_elements_in_chapters = soup.select('div.chapter ul.section-list li.section div.textCon p.text')
            # Also, sometimes there are top-level lesson lists
            top_level_lesson_elements = soup.select('ul.video-list li div.textCon p.text')

            # A more general approach for the given URL structure (based on observed structure):
            # Titles seem to be in elements with class 'j-titletext' or within 'f-richEditorText'
            # Let's try to find all <p class="text"> elements within <div class="textCon"> as a primary target
            title_elements = soup.select('div.textCon p.text')
            if not title_elements:
                # Fallback to another common pattern if the above fails
                title_elements = soup.select('.lessontitle .text') # A common pattern on MOOC sites
            if not title_elements:
                 title_elements = soup.select('h3.f-thide.f-fl.listTxt') # Chapter titles

            if not title_elements:
                self.all_scraped_titles.append("未能自动提取标题，请检查URL或手动输入。")
            else:
                for el in title_elements:
                    title = el.get_text(strip=True)
                    if title:
                        self.all_scraped_titles.append(title)
            
            self.scraped_titles_listbox.configure(state='normal')
            self.scraped_titles_listbox.delete(1.0, tk.END)
            if self.all_scraped_titles:
                for title_text in self.all_scraped_titles:
                    self.scraped_titles_listbox.insert(tk.END, title_text + "\n")
            else:
                self.scraped_titles_listbox.insert(tk.END, "未找到课程标题。可能是动态加载的内容，或页面结构已更改。\n")
            self.scraped_titles_listbox.configure(state='disabled')
            self.filter_scraped_titles() # Populate the matched listbox initially

        except requests.exceptions.RequestException as e:
            messagebox.showerror("抓取错误", f"无法连接到URL或请求失败: {e}", parent=self.rename_window_ref)
            self.scraped_titles_listbox.configure(state='normal')
            self.scraped_titles_listbox.delete(1.0, tk.END)
            self.scraped_titles_listbox.insert(tk.END, f"抓取失败: {e}\n")
            self.scraped_titles_listbox.configure(state='disabled')
        except Exception as e:
            messagebox.showerror("抓取错误", f"解析内容时发生未知错误: {e}", parent=self.rename_window_ref)
            self.scraped_titles_listbox.configure(state='normal')
            self.scraped_titles_listbox.delete(1.0, tk.END)
            self.scraped_titles_listbox.insert(tk.END, f"解析错误: {e}\n")
            self.scraped_titles_listbox.configure(state='disabled')
        finally:
            self.scrape_button.config(state=tk.NORMAL)

    def on_local_file_select(self, event=None):
        if not self.local_files_listbox.curselection():
            self.play_preview_button.config(state=tk.DISABLED)
            self.selected_local_video_path = None
            if hasattr(self, 'video_preview_canvas'): self.video_preview_canvas.delete("all") # Clear preview
            return
        
        selected_index = self.local_files_listbox.curselection()[0]
        filename = self.local_files_listbox.get(selected_index)
        folder = self.rename_folder_var.get()
        self.selected_local_video_path = os.path.join(folder, filename)
        
        self.play_preview_button.config(state=tk.NORMAL)
        self._update_rename_button_state()
        # Display first frame as static preview
        self._display_first_frame_preview()

    def _display_first_frame_preview(self):
        if not self.selected_local_video_path or not os.path.exists(self.selected_local_video_path):
            return
        try:
            cap = cv2.VideoCapture(self.selected_local_video_path)
            if not cap.isOpened():
                return
            ret, frame = cap.read()
            if ret:
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # Resize frame to fit canvas
                canvas_w = self.video_preview_canvas.winfo_width()
                canvas_h = self.video_preview_canvas.winfo_height()
                if canvas_w == 1 or canvas_h == 1: # Canvas not yet rendered
                    canvas_w, canvas_h = 320, 180 # Default size
                
                img_h, img_w, _ = frame_rgb.shape
                aspect_ratio = img_w / img_h
                
                if canvas_w / aspect_ratio <= canvas_h:
                    new_w = canvas_w
                    new_h = int(canvas_w / aspect_ratio)
                else:
                    new_h = canvas_h
                    new_w = int(canvas_h * aspect_ratio)
                
                resized_frame = cv2.resize(frame_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
                img = Image.fromarray(resized_frame)
                self.preview_photo = ImageTk.PhotoImage(image=img) # Keep a reference
                self.video_preview_canvas.create_image(canvas_w//2, canvas_h//2, anchor=tk.CENTER, image=self.preview_photo)
            cap.release()
        except Exception as e:
            print(f"Error displaying first frame: {e}") # Log to console for now
            self.video_preview_canvas.delete("all") # Clear on error

    def _update_preview_frame(self):
        if not self.is_previewing or not self.video_capture or not self.video_capture.isOpened():
            self._stop_video_preview_playback()
            return

        ret, frame = self.video_capture.read()
        current_time_ms = self.video_capture.get(cv2.CAP_PROP_POS_MSEC)

        if ret and current_time_ms <= 15000: # Play for 15 seconds
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            canvas_w = self.video_preview_canvas.winfo_width()
            canvas_h = self.video_preview_canvas.winfo_height()
            if canvas_w == 1 or canvas_h == 1: canvas_w, canvas_h = 320, 180

            img_h, img_w, _ = frame_rgb.shape
            aspect_ratio = img_w / img_h
            if canvas_w / aspect_ratio <= canvas_h:
                new_w = canvas_w
                new_h = int(canvas_w / aspect_ratio)
            else:
                new_h = canvas_h
                new_w = int(canvas_h * aspect_ratio)
            
            resized_frame = cv2.resize(frame_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
            img = Image.fromarray(resized_frame)
            self.preview_photo = ImageTk.PhotoImage(image=img)
            self.video_preview_canvas.create_image(canvas_w//2, canvas_h//2, anchor=tk.CENTER, image=self.preview_photo)
            self.preview_frame_job = self.root.after(33, self._update_preview_frame) # Approx 30 FPS
        else:
            self._stop_video_preview_playback()

    def _stop_video_preview_playback(self):
        self.is_previewing = False
        if self.video_capture:
            self.video_capture.release()
            self.video_capture = None
        if self.preview_frame_job:
            self.root.after_cancel(self.preview_frame_job)
            self.preview_frame_job = None
        self.play_preview_button.config(text="播放15秒预览", state=tk.NORMAL if self.selected_local_video_path else tk.DISABLED)
        # Optionally, revert to first frame after playback finishes
        if self.selected_local_video_path:
             self._display_first_frame_preview()

    def play_video_preview(self):
        if self.is_previewing:
            self._stop_video_preview_playback()
            return

        if not self.selected_local_video_path or not os.path.exists(self.selected_local_video_path):
            messagebox.showerror("错误", "未选择有效的视频文件或文件不存在。", parent=self.rename_window_ref)
            return

        try:
            self.video_capture = cv2.VideoCapture(self.selected_local_video_path)
            if not self.video_capture.isOpened():
                messagebox.showerror("错误", "无法打开视频文件进行预览。", parent=self.rename_window_ref)
                self.video_capture = None
                return
            
            self.is_previewing = True
            self.play_preview_button.config(text="停止预览", state=tk.NORMAL)
            self.video_preview_canvas.delete("all") # Clear previous static image
            self._update_preview_frame()

        except Exception as e:
            messagebox.showerror("预览错误", f"播放视频预览时出错: {e}", parent=self.rename_window_ref)
            if self.video_capture: self.video_capture.release()
            self.video_capture = None
            self.is_previewing = False
            self.play_preview_button.config(text="播放15秒预览", state=tk.NORMAL if self.selected_local_video_path else tk.DISABLED)

    def filter_scraped_titles(self, event=None): # event=None for initial call
        search_term = self.online_title_search_var.get().lower()
        self.matched_online_titles_listbox.delete(0, tk.END)
        
        if not self.all_scraped_titles:
            return

        if not search_term:
            # If search is empty, show all scraped titles
            for title in self.all_scraped_titles:
                self.matched_online_titles_listbox.insert(tk.END, title)
        else:
            # Use difflib for fuzzy matching
            # get_close_matches returns a list of best matches
            # You might want to adjust n (max number of matches) and cutoff (similarity threshold 0.0 to 1.0)
            matches = get_close_matches(search_term, self.all_scraped_titles, n=len(self.all_scraped_titles), cutoff=0.3) # Adjust cutoff as needed
            # Alternative: simple substring matching
            # matches = [title for title in self.all_scraped_titles if search_term in title.lower()]
            for match in matches:
                self.matched_online_titles_listbox.insert(tk.END, match)
        self._update_rename_button_state()

    def _update_rename_button_state(self, event=None):
        local_selected = self.local_files_listbox.curselection()
        online_selected = self.matched_online_titles_listbox.curselection()
        if local_selected and online_selected:
            self.rename_selected_button.config(state=tk.NORMAL)
        else:
            self.rename_selected_button.config(state=tk.DISABLED)

    def rename_to_selected_online_title(self):
        if not self.local_files_listbox.curselection() or not self.matched_online_titles_listbox.curselection():
            messagebox.showwarning("提示", "请同时在左侧选择一个本地文件和在右侧选择一个匹配的课程名称。", parent=self.rename_window_ref)
            return

        local_file_index = self.local_files_listbox.curselection()[0]
        original_filename = self.local_files_listbox.get(local_file_index)
        
        online_title_index = self.matched_online_titles_listbox.curselection()[0]
        selected_online_title = self.matched_online_titles_listbox.get(online_title_index)

        folder = self.rename_folder_var.get()
        original_filepath = os.path.join(folder, original_filename)
        _, ext = os.path.splitext(original_filename)

        # Sanitize the online title to be a valid filename
        # Replace invalid characters, trim whitespace, etc.
        # This is a basic sanitization, might need to be more robust
        sanitized_title = "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in selected_online_title).strip()
        if not sanitized_title: # if title becomes empty after sanitization
            sanitized_title = "renamed_video"
        new_filename = f"{sanitized_title}{ext}"
        new_filepath = os.path.join(folder, new_filename)

        if original_filepath == new_filepath:
            messagebox.showinfo("提示", "新文件名与原文件名相同，无需重命名。", parent=self.rename_window_ref)
            return

        if os.path.exists(new_filepath):
            if not messagebox.askyesno("文件已存在", f"目标文件 '{new_filename}' 已存在。是否覆盖?", parent=self.rename_window_ref):
                return

        try:
            os.rename(original_filepath, new_filepath)
            messagebox.showinfo("成功", f"文件 '{original_filename}' 已重命名为 '{new_filename}'.", parent=self.rename_window_ref)
            # Refresh the local files list and clear selections
            self._load_local_video_files(folder)
            self.matched_online_titles_listbox.selection_clear(0, tk.END)
            self._update_rename_button_state()
            # Also refresh the main rule-based preview if it's showing this file
            self.preview_rename_changes()
        except OSError as e:
            messagebox.showerror("重命名错误", f"无法重命名 '{original_filename}' 为 '{new_filename}':\n{e}", parent=self.rename_window_ref)

    def process_videos(self, input_dir, output_dir):

    def process_videos(self, input_dir, output_dir):
        subfolders_to_process = []
        for item in os.listdir(input_dir):
            item_path = os.path.join(input_dir, item)
            if os.path.isdir(item_path):
                # Check for .m3u8 file in the subfolder
                m3u8_files = [f for f in os.listdir(item_path) if f.endswith(".m3u8")]
                if m3u8_files:
                    subfolders_to_process.append((item, item_path, m3u8_files[0])) # (folder_name, full_path, m3u8_filename)
        
        if not subfolders_to_process:
            self.root.after(0, lambda: messagebox.showinfo("提示", "输入文件夹中未找到包含 .m3u8 文件的子文件夹。"))
            self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.progress_label_var.set("状态: 空闲"))
            return

        total_folders = len(subfolders_to_process)
        self.progress_bar["maximum"] = total_folders

        for i, (folder_name, folder_path, m3u8_file) in enumerate(subfolders_to_process):
            self.root.after(0, lambda: self.progress_label_var.set(f"状态: 正在处理 {folder_name} ({i+1}/{total_folders})"))
            m3u8_path = os.path.join(folder_path, m3u8_file)
            output_filename = f"{folder_name}.mp4" # This is the base name for messages and default output
            output_filepath = os.path.join(output_dir, output_filename)
            
            # --- Duplicate Output Detection ---
            if os.path.exists(output_filepath):
                # Ask user: Overwrite, Skip, or Cancel
                # Schedule the dialog in the main thread and wait for the response via the queue
                self.root.after(0, lambda name_disp=output_filename, q=self.dialog_queue: self.ask_user_for_overwrite(name_disp, q))
                
                action = self.dialog_queue.get() # Worker thread blocks here

                if action == 'yes': # Overwrite
                    self.root.after(0, lambda op=output_filepath, ip=folder_path: self.log_history(ip, op, "准备覆盖"))
                    # FFmpeg command uses -y, so it will overwrite.
                elif action == 'no': # Skip
                    self.root.after(0, lambda op=output_filepath, ip=folder_path: self.log_history(ip, op, "已跳过 (文件已存在)"))
                    self.root.after(0, lambda val=i+1: self.progress_bar.config(value=val))
                    self.root.after(0, lambda fn=folder_name: self.progress_label_var.set(f"状态: 已跳过 {fn}"))
                    continue # Skip to the next video in the loop
                elif action == 'cancel': # Cancel all or dialog closed with 'X'
                    self.root.after(0, lambda ip=folder_path: self.log_history(ip, "N/A", "用户取消操作"))
                    self.root.after(0, lambda: self.progress_label_var.set("状态: 用户取消操作"))
                    self.root.after(0, lambda: messagebox.showinfo("取消", "所有后续转换已取消。", parent=self.root))
                    self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                    self.root.after(0, lambda: self.progress_bar.config(value=0))
                    return # Exit the process_videos method
                else: # Should ideally not happen with YESNOCANCEL if 'X' is treated as 'cancel'
                      # If it somehow returns something else (e.g. None), treat as skip to be safe.
                    self.root.after(0, lambda op=output_filepath, ip=folder_path: self.log_history(ip, op, "已跳过 (对话框响应未知)"))
                    self.root.after(0, lambda val=i+1: self.progress_bar.config(value=val))
                    self.root.after(0, lambda fn=folder_name: self.progress_label_var.set(f"状态: 已跳过 {fn} (对话框响应未知)"))
                    continue # Skip to the next video
            
            # If not skipping or cancelling, or if file didn't exist, proceed.
            # The old auto-renaming logic is now removed.

            # FFmpeg command
            command = [
                self.ffmpeg_path,
                '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,pipe',
                '-i', m3u8_path,
                '-c', 'copy', # Fast remuxing, assumes TS segments are compatible with MP4
                '-bsf:a', 'aac_adtstoasc', # Necessary for some AAC streams in TS to MP4
                '-y', # Overwrite output file without asking
                output_filepath
            ]

            try:
                # For Windows, hide the console window
                startupinfo = None
                if os.name == 'nt':
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', startupinfo=startupinfo)
                stdout, stderr = process.communicate()

                if process.returncode == 0:
                    status = "成功"
                    self.root.after(0, lambda fp=output_filepath, s=status, ip=folder_path: self.log_history(ip, fp, s))
                else:
                    status = f"失败: {stderr.strip()}"
                    self.root.after(0, lambda fp=output_filepath, s=status, ip=folder_path: self.log_history(ip, fp, s))
                    self.root.after(0, lambda err=stderr.strip(), fn=folder_name: messagebox.showerror("转换错误", f"处理 {fn} 失败:\n{err}"))
            
            except FileNotFoundError:
                status = "失败: FFmpeg 未找到"
                self.root.after(0, lambda fp=output_filepath, s=status, ip=folder_path: self.log_history(ip, fp, s))
                self.root.after(0, lambda: messagebox.showerror("错误", "FFmpeg 未找到。请确保已安装 FFmpeg 并将其添加至系统 PATH，或在脚本中指定其完整路径。"))
                self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                self.root.after(0, lambda: self.progress_label_var.set("状态: FFmpeg错误"))
                return # Stop processing if ffmpeg is not found
            except Exception as e:
                status = f"失败: {str(e)}"
                self.root.after(0, lambda fp=output_filepath, s=status, ip=folder_path: self.log_history(ip, fp, s))
                self.root.after(0, lambda err=str(e), fn=folder_name: messagebox.showerror("未知错误", f"处理 {fn} 时发生未知错误:\n{err}"))

            self.root.after(0, lambda val=i+1: self.progress_bar.config(value=val))

        self.root.after(0, lambda: self.progress_label_var.set("状态: 处理完成!"))
        self.root.after(0, lambda: messagebox.showinfo("完成", "所有视频已处理完毕。"))
        self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoMergerApp(root)
    root.mainloop()