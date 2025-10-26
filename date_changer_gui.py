#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
date_changer_gui.py
-------------------------------------------------
macOS Tkinter GUI to mass-update file timestamps (not just photos).
- Match by EXIF date (DateTimeOriginal/CreateDate) OR by File Modified date.
- Set File Modified to a target date (preserving time-of-day), optional.
- Set File Created = File Modified, optional.
- Works for ANY extensions you specify, or ALL files with "*".
- Dry-run preview.
- Presets for common workflows.
- Tooltips on key controls.
- Can help install Homebrew, python-tk (Tkinter), and ExifTool when missing.

Author: ChatGPT
"""
import os
import sys
import subprocess
import shlex
import time
import threading
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from queue import Queue, Empty

# ----------------- Utilities -----------------

def enrich_path():
    default_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    changed = False
    for p in reversed(default_paths):
        if p not in parts:
            parts.insert(0, p)
            changed = True
    if changed:
        os.environ["PATH"] = os.pathsep.join(parts)

def which(cmd):
    for p in os.environ.get("PATH","").split(os.pathsep):
        full = os.path.join(p, cmd)
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    return None

def run(cmd, check=False, capture=False):
    if isinstance(cmd, str):
        cmd_list = shlex.split(cmd)
    else:
        cmd_list = cmd
    if capture:
        return subprocess.run(cmd_list, check=check, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    else:
        return subprocess.run(cmd_list, check=check)

def prompt_cli_yesno(question):
    try:
        ans = input(f"{question} [y/N]: ").strip().lower()
        return ans in ("y","yes")
    except EOFError:
        return False

# ----------------- Preflight installers -----------------

def ensure_homebrew():
    enrich_path()
    if which("brew"):
        return True
    print("Homebrew not found on PATH.")
    if not prompt_cli_yesno("Install Homebrew now?"):
        print("Homebrew is required for auto-install. You can install manually from https://brew.sh/")
        return False
    cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    print("Running Homebrew installer...")
    run(cmd)
    brew_bin = "/opt/homebrew/bin/brew"
    if os.path.exists(brew_bin):
        try:
            zprof = os.path.expanduser("~/.zprofile")
            with open(zprof, "a") as f:
                f.write('\n# Added by date_changer_gui\n')
                f.write('eval "$(/opt/homebrew/bin/brew shellenv)"\n')
        except Exception:
            pass
        os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH","")
    enrich_path()
    return which("brew") is not None

def ensure_python_tk():
    enrich_path()
    try:
        import tkinter  # noqa
        return True
    except Exception:
        print("Tkinter not available in this Python.")
        if not ensure_homebrew():
            return False
        if not prompt_cli_yesno("Install Tk via Homebrew (brew install python-tk)?"):
            print("Cannot proceed without Tkinter. Install it and re-run.")
            return False
        print("Installing python-tk via Homebrew...")
        run(["brew","install","python-tk"])
        time.sleep(1)
        try:
            import tkinter  # noqa
            return True
        except Exception as e:
            print("Tkinter still not importable in this interpreter.\n"
                  "Tip: The Homebrew Tk works best with Homebrew Python. "
                  "Alternatively, install Python from python.org which includes Tk.\n"
                  f"Details: {e}")
            return False

def ensure_exiftool():
    enrich_path()
    if which("exiftool"):
        return True
    if not ensure_homebrew():
        return False
    print("Installing exiftool via Homebrew...")
    run(["brew","install","exiftool"])
    return which("exiftool") is not None

# ----------------- Tooltips -----------------
class Tooltip:
    """Lightweight tooltip for Tk widgets."""
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self.on_enter)
        widget.bind("<Leave>", self.on_leave)
        widget.bind("<ButtonPress>", self.on_leave)

    def on_enter(self, _e):
        self.schedule()

    def on_leave(self, _e=None):
        self.unschedule()
        self.hide()

    def schedule(self):
        self.unschedule()
        self.after_id = self.widget.after(self.delay, self.show)

    def unschedule(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def show(self):
        if self.tip or not self.text:
            return
        try:
            x, y, _, _ = self.widget.bbox("insert")
        except Exception:
            x = y = 0
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify="left",
                         background="#ffffe0", relief="solid", borderwidth=1,
                         font=("TkDefaultFont", 9), padx=6, pady=3)
        label.pack(ipadx=1)

    def hide(self):
        if self.tip:
            self.tip.destroy()
            self.tip = None

# ----------------- GUI -----------------

def launch_gui():
    APP_TITLE = "Date Changer (GUI) — Any File Type"
    DEFAULT_OLD_DATE = "2025:10:25"
    DEFAULT_NEW_DATE = "2025:10:25"

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(APP_TITLE)
            self.geometry("1000x700")
            enrich_path()

            self.queue = Queue()

            # Vars
            self.dir_var = tk.StringVar(value="/Volumes/Untitled/DCIM/102_FUJI")
            self.old_var = tk.StringVar(value=DEFAULT_OLD_DATE)
            self.new_var = tk.StringVar(value=DEFAULT_NEW_DATE)
            self.dry_run = tk.BooleanVar(value=False)
            self.match_mode = tk.StringVar(value="modified")  # "exif" or "modified"
            self.set_created = tk.BooleanVar(value=True)      # Set Date Created = Modified
            self.set_modified = tk.BooleanVar(value=False)    # Set Date Modified to Target Date
            self.exts_var = tk.StringVar(value="jpg,jpeg,heic")  # comma-separated; "*" = all
            self.preset_var = tk.StringVar(value="(choose a preset)")

            self.create_widgets()
            self.after(120, self.drain_log_queue)

        # --- Logging helpers (queue-based) ---
        def ui_log(self, s: str):
            """Enqueue a log line for the UI thread to render."""
            self.queue.put(s)

        def drain_log_queue(self):
            try:
                while True:
                    line = self.queue.get_nowait()
                    self.txt.insert("end", line + "\n")
                    self.txt.see("end")
            except Empty:
                pass
            self.after(150, self.drain_log_queue)

        def create_widgets(self):
            pad = 8
            frm = ttk.Frame(self); frm.pack(fill="both", expand=True, padx=pad, pady=pad)

            # Row 0: directory
            lbl_dir = ttk.Label(frm, text="Source Folder:"); lbl_dir.grid(row=0, column=0, sticky="w")
            ent = ttk.Entry(frm, textvariable=self.dir_var, width=84)
            ent.grid(row=0, column=1, sticky="we", padx=(pad, pad))
            btn_browse = ttk.Button(frm, text="Browse…", command=self.browse_dir)
            btn_browse.grid(row=0, column=2, sticky="we")
            frm.grid_columnconfigure(1, weight=1)
            Tooltip(lbl_dir, "Folder to scan recursively for matching files.")

            # Row 1: dates
            lbl_match = ttk.Label(frm, text="Match Date:"); lbl_match.grid(row=1, column=0, sticky="w")
            ent_match = ttk.Entry(frm, textvariable=self.old_var, width=18)
            ent_match.grid(row=1, column=1, sticky="w")
            lbl_target = ttk.Label(frm, text="→ Target Date:"); lbl_target.grid(row=1, column=1, sticky="e", padx=(0, 300))
            ent_target = ttk.Entry(frm, textvariable=self.new_var, width=18)
            ent_target.grid(row=1, column=2, sticky="w")
            Tooltip(lbl_match, "Date to match.\nFormat: YYYY:MM:DD (or YYYY-MM-DD).")
            Tooltip(lbl_target, "Date to write into 'Date Modified' (if enabled).\nFormat: YYYY:MM:DD (or YYYY-MM-DD).")

            # Row 2: options
            opt = ttk.Frame(frm); opt.grid(row=2, column=0, columnspan=3, sticky="w", pady=(pad, 0))
            ttk.Label(opt, text="Match by:").pack(side="left")
            r1 = ttk.Radiobutton(opt, text="EXIF (DateTimeOriginal/CreateDate)", variable=self.match_mode, value="exif")
            r2 = ttk.Radiobutton(opt, text="File Modified date (Finder)", variable=self.match_mode, value="modified")
            r1.pack(side="left", padx=(8,0)); r2.pack(side="left", padx=(8,0))
            chk_dry = ttk.Checkbutton(opt, text="Dry Run (preview only)", variable=self.dry_run)
            chk_dry.pack(side="left", padx=(16,0))
            Tooltip(r1, "Use image metadata to match.\nBest for photos/videos with EXIF.\nRequires ExifTool.")
            Tooltip(r2, "Use filesystem 'Date Modified' to match.\nWorks for any file type.")
            Tooltip(chk_dry, "Preview only; no writes will be performed.")

            # Row 3: actions
            opt2 = ttk.Frame(frm); opt2.grid(row=3, column=0, columnspan=3, sticky="w", pady=(pad, 0))
            chk_mod = ttk.Checkbutton(opt2, text="Set 'Date Modified' to Target Date (keep time-of-day)", variable=self.set_modified)
            chk_cre = ttk.Checkbutton(opt2, text="Also set Finder 'Date Created' = Modified", variable=self.set_created)
            chk_mod.pack(side="left"); chk_cre.pack(side="left", padx=(16,0))
            Tooltip(chk_mod, "Change only the DATE part of 'Date Modified'.\nTime-of-day is preserved per file.")
            Tooltip(chk_cre, "After modifying (or reading) 'Date Modified', copy it to 'Date Created'.")

            # Row 4: extensions
            ext_row = ttk.Frame(frm); ext_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(pad, 0))
            lbl_ext = ttk.Label(ext_row, text="Extensions (comma-separated, no dots; use * for ALL files):")
            lbl_ext.pack(side="left")
            ent_ext = ttk.Entry(ext_row, textvariable=self.exts_var, width=35); ent_ext.pack(side="left", padx=(8,0))
            Tooltip(lbl_ext, "File types to include.\nExamples: 'jpg,jpeg,heic' or 'pdf,docx' or '*' for all files.")

            # Row 5: presets
            pre_row = ttk.Frame(frm); pre_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(pad, 0))
            ttk.Label(pre_row, text="Presets:").pack(side="left")
            preset = ttk.Combobox(pre_row, textvariable=self.preset_var, width=46, state="readonly",
                                  values=[
                                      "(choose a preset)",
                                      "All files • match by Modified • Created = Modified",
                                      "Photos • match by EXIF • set Modified to Target • Created = Modified",
                                      "Photos • match by EXIF • Created = Modified (no change to Modified)",
                                      "All files • match by Modified • set Modified to Target",
                                      "Preview only (no writes)"
                                  ])
            preset.pack(side="left", padx=(8,0))
            ttk.Button(pre_row, text="Apply Preset", command=self.apply_preset).pack(side="left", padx=(8,0))
            Tooltip(preset, "Select a common workflow, then click 'Apply Preset'.")

            # Row 6: buttons
            btn = ttk.Frame(frm); btn.grid(row=6, column=0, columnspan=3, sticky="w", pady=(pad, pad))
            ttk.Button(btn, text="Run", command=self.on_run).pack(side="left")
            ttk.Button(btn, text="Clear Log", command=self.clear_log).pack(side="left", padx=(10,0))
            ttk.Button(btn, text="Check Tools", command=self.check_tools).pack(side="left", padx=(10,0))
            ttk.Button(btn, text="Install ExifTool (brew)", command=self.install_exiftool).pack(side="left", padx=(10,0))
            ttk.Button(btn, text="Install Tk (python-tk)", command=self.install_tk).pack(side="left", padx=(10,0))

            # Row 7: log
            ttk.Label(frm, text="Log:").grid(row=7, column=0, sticky="w")
            self.txt = tk.Text(frm, height=20, wrap="none")
            self.txt.grid(row=8, column=0, columnspan=3, sticky="nsew")
            frm.grid_rowconfigure(8, weight=1)
            ysb = ttk.Scrollbar(frm, orient="vertical", command=self.txt.yview)
            self.txt.configure(yscrollcommand=ysb.set)
            ysb.grid(row=8, column=3, sticky="ns")

        # --- Presets ---
        def apply_preset(self):
            p = self.preset_var.get()
            if p == "All files • match by Modified • Created = Modified":
                self.match_mode.set("modified")
                self.set_created.set(True)
                self.set_modified.set(False)
                self.exts_var.set("*")
                self.dry_run.set(False)
            elif p == "Photos • match by EXIF • set Modified to Target • Created = Modified":
                self.match_mode.set("exif")
                self.set_modified.set(True)
                self.set_created.set(True)
                self.exts_var.set("jpg,jpeg,heic")
                self.dry_run.set(False)
            elif p == "Photos • match by EXIF • Created = Modified (no change to Modified)":
                self.match_mode.set("exif")
                self.set_modified.set(False)
                self.set_created.set(True)
                self.exts_var.set("jpg,jpeg,heic")
                self.dry_run.set(False)
            elif p == "All files • match by Modified • set Modified to Target":
                self.match_mode.set("modified")
                self.set_modified.set(True)
                self.set_created.set(False)
                self.exts_var.set("*")
                self.dry_run.set(False)
            elif p == "Preview only (no writes)":
                self.dry_run.set(True)
            # else "(choose a preset)" -> no-op

        # --- Basic UI helpers ---
        def clear_log(self):
            self.txt.delete("1.0", "end")

        def browse_dir(self):
            d = filedialog.askdirectory(initialdir=self.dir_var.get() or os.path.expanduser("~"))
            if d:
                self.dir_var.set(d)

        def check_tools(self):
            enrich_path()
            self.ui_log(f"brew: {which('brew') or 'NOT FOUND'}")
            self.ui_log(f"exiftool: {which('exiftool') or 'NOT FOUND'}")
            self.ui_log(f"SetFile: {which('SetFile') or 'NOT FOUND'}")
            try:
                import tkinter as tk  # noqa
                self.ui_log("tkinter: OK")
            except Exception as e:
                self.ui_log(f"tkinter: NOT AVAILABLE - {e}")

        def install_exiftool(self):
            b = which("brew")
            if not b:
                self.ui_log("Homebrew not found. Install from https://brew.sh/")
                return
            self.ui_log("Installing exiftool via Homebrew...")
            threading.Thread(target=lambda: self._run_and_log([b,"install","exiftool"]), daemon=True).start()

        def install_tk(self):
            b = which("brew")
            if not b:
                self.ui_log("Homebrew not found. Install from https://brew.sh/")
                return
            self.ui_log("Installing python-tk via Homebrew...")
            threading.Thread(target=lambda: self._run_and_log([b,"install","python-tk"]), daemon=True).start()

        def _run_and_log(self, cmd):
            self.ui_log(" ".join(shlex.quote(c) for c in cmd))
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    self.ui_log(line.rstrip("\n"))
                rc = proc.wait()
                self.ui_log(f"[exit code {rc}]")
            except Exception as e:
                self.ui_log(f"ERROR: {e}")

        def parse_extensions(self):
            raw = self.exts_var.get().strip()
            if not raw or raw == "*":
                return None  # None = all files
            parts = [p.strip().lower().lstrip(".") for p in raw.split(",") if p.strip()]
            return [p for p in parts if p]

        def on_run(self):
            source_dir = self.dir_var.get().strip()
            old_date = self.old_var.get().strip()
            new_date = self.new_var.get().strip()
            match_mode = self.match_mode.get()
            dry_run = self.dry_run.get()
            set_created = self.set_created.get()
            set_modified = self.set_modified.get()
            exts = self.parse_extensions()

            if not os.path.isdir(source_dir):
                messagebox.showerror("Invalid Folder", "Please choose a valid source folder.")
                return

            def normalize_exif(s):
                # Accept YYYY:MM:DD or YYYY-MM-DD -> YYYY:MM:DD
                if ":" in s:
                    try:
                        datetime.datetime.strptime(s, "%Y:%m:%d"); return s
                    except Exception: return None
                else:
                    try:
                        dt = datetime.datetime.strptime(s, "%Y-%m-%d"); return dt.strftime("%Y:%m:%d")
                    except Exception: return None

            old_norm = normalize_exif(old_date)
            new_norm = normalize_exif(new_date) if new_date else None
            if not old_norm:
                messagebox.showerror("Invalid Dates", "Match Date must be YYYY:MM:DD (or YYYY-MM-DD).")
                return
            if set_modified and not new_norm:
                messagebox.showerror("Missing Target Date", "Enter a Target Date to set 'Date Modified'.")
                return

            self.clear_log()
            self.ui_log(f"Folder: {source_dir}")
            self.ui_log(f"Match by: {match_mode.upper()}")
            self.ui_log(f"Match Date: {old_norm}  | Target Date: {new_norm or '(none)'}")
            self.ui_log(f"Extensions: {self.exts_var.get()}")
            self.ui_log(f"Set Date Modified to Target: {set_modified}")
            self.ui_log(f"Also set Date Created = Modified: {set_created}")
            self.ui_log(f"Dry run: {dry_run}")
            self.ui_log("-------------------------------------------------------------")

            threading.Thread(target=self.run_job, args=(source_dir, old_norm, new_norm, match_mode, exts, set_modified, set_created, dry_run), daemon=True).start()

        def run_job(self, source_dir, old_norm, new_norm, match_mode, exts, set_modified, set_created, dry_run):
            enrich_path()
            et = which("exiftool")
            if not et:
                self.ui_log("ExifTool not found. Attempting to install via Homebrew...")
                if ensure_homebrew():
                    run(["brew","install","exiftool"])
                    et = which("exiftool")
            if not et:
                self.ui_log("ERROR: exiftool not available.")
                return

            # Build exiftool base with extensions
            base = [et, "-fast2", "-r"]
            if exts:
                for e in exts:
                    base += ["-ext", e]
            if not dry_run:
                base.append("-overwrite_original")

            # Filter
            if match_mode == "exif":
                exif_if = f"$DateTimeOriginal=~/^{old_norm}/ or $CreateDate=~/^{old_norm}/"
            else:
                exif_if = f"$FileModifyDate=~/^{old_norm}/"

            # Assignments
            assigns = []
            if set_modified and new_norm:
                if match_mode == "exif":
                    assigns.append(f"-FileModifyDate<${{DateTimeOriginal;$_=~s/^{old_norm}/{new_norm}/}}")
                else:
                    assigns.append(f"-FileModifyDate<${{FileModifyDate;$_=~s/^{old_norm}/{new_norm}/}}")
            if set_created:
                assigns.append("-FileCreateDate<FileModifyDate")

            if dry_run:
                # Preview output
                if set_modified and new_norm:
                    if match_mode == "exif":
                        p = f"$FileName | $DateTimeOriginal -> {new_norm} " + "${DateTimeOriginal;$_=~s/^\\d{4}:\\d{2}:\\d{2} //}"
                    else:
                        p = f"$FileName | $FileModifyDate -> {new_norm} " + "${FileModifyDate;$_=~s/^\\d{4}:\\d{2}:\\d{2} //}"
                else:
                    p = "$FileName | Created will become Modified | $FileCreateDate -> $FileModifyDate"
                preview_cmd = base + ["-if", exif_if, "-p", p, source_dir]
                self.ui_log("Preview (no writes):")
                self.ui_log(" ".join(shlex.quote(c) for c in preview_cmd))
                proc = subprocess.Popen(preview_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    self.ui_log(line.rstrip("\n"))
                rc = proc.wait()
                self.ui_log(f"[exit code {rc}]")
            else:
                cmd = base + ["-if", exif_if] + assigns + [source_dir]
                self.ui_log("Running exiftool writes...")
                self.ui_log(" ".join(shlex.quote(c) for c in cmd))
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in proc.stdout:
                    self.ui_log(line.rstrip("\n"))
                rc = proc.wait()
                self.ui_log(f"[exit code {rc}]")

    app = App()
    app.mainloop()

def main():
    enrich_path()
    if not ensure_python_tk():
        print("Exiting because Tkinter is not available.")
        sys.exit(1)
    launch_gui()

if __name__ == "__main__":
    main()
