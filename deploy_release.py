#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网机一键部署（解压 + 应用）增强版。融合原 deploy_release 与发布包批量更新能力。

用法：
  1) 双击运行：图形界面（有 tkinter 时）；否则命令行交互输入。
  2) 命令行（保持原兼容）：
       deploy_release.exe <发布包.zip> [内网仓库根目录]
       deploy_release.exe --dry-run            # 只预览不执行
       deploy_release.exe --latest [根目录]    # 只覆盖最新一个包
       deploy_release.exe --all [根目录]       # 从旧到新依次覆盖全部包（默认）
    找不到 zip 时，自动在脚本目录/当前目录/发布包文件夹寻找 发布包_*.zip。
记忆：目标根目录、发布包文件夹、覆盖模式 自动保存（~/.wt_deploy_release.json），下次打开默认填入。
备份：覆盖前把将被覆盖的旧文件备份到 <目标>\.update_backup\<时间戳>\，可 --no-backup 关闭。
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import tempfile
import zipfile

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    _HAS_TK = True
except Exception:
    _HAS_TK = False

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".wt_deploy_release.json")
BACKUP_DIR_NAME = ".update_backup"

# 与旧版保持一致的跳过列表（工具自身/清单文件不覆盖）。
SKIP = {"清单.txt", "deleted.txt", "apply_release.py", "apply_release.exe",
        "make_release.py", "make_release.exe", "deploy_release.py"}


# --------------------------------------------------------------------------
# 核心逻辑（可脱离 GUI / exe 复用与测试）
# --------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def list_release_zips(release_dir):
    """返回按修改时间倒序（新->旧）的 zip 列表。"""
    items = []
    if not release_dir or not os.path.isdir(release_dir):
        return items
    for name in os.listdir(release_dir):
        if name.lower().endswith(".zip"):
            path = os.path.join(release_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = 0
            items.append({"path": path, "name": name, "mtime": mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def find_zip(hint=None, release_dir=None):
    """兼容旧版：优先 hint 指定的 zip；否则在脚本目录/当前目录/发布包文件夹找最新的 发布包_*.zip。"""
    if hint and os.path.isfile(hint):
        return hint
    cands = []
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.getcwd(), release_dir or ""):
        if base and os.path.isdir(base):
            cands.extend(glob.glob(os.path.join(base, "发布包_*.zip")))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def _safe_join(root, rel):
    root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root, rel))
    if not target.startswith(root + os.sep) and target != root:
        raise RuntimeError("发布包路径越界: %s" % rel)
    return target


def _zip_file_list(zip_path):
    with zipfile.ZipFile(zip_path, "r") as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def apply_zip(zip_path, target, backup_dir=None, log=print):
    """解压覆盖单个 zip 到目标目录（跳过 SKIP 文件）；覆盖前可选备份。返回覆盖的文件数。

    先整体解压到临时目录，再逐文件复制（跳过 SKIP），确保工具自身/清单文件不被覆盖。
    """
    tmp = tempfile.mkdtemp(prefix="wt_deploy_")
    effective = []
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp)
        for root, _dirs, files in os.walk(tmp):
            for fn in files:
                if fn in SKIP:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, tmp)
                dst = _safe_join(target, rel)
                if os.path.exists(dst) and backup_dir:
                    bak = os.path.join(backup_dir, rel)
                    os.makedirs(os.path.dirname(bak), exist_ok=True)
                    shutil.copy2(dst, bak)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(full, dst)
                effective.append(rel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    skipped = len(_zip_file_list(zip_path)) - len(effective)
    log("  [OK] %s -> %d 个文件已覆盖（跳过 %d 个工具自身文件）"
        % (os.path.basename(zip_path), len(effective), skipped))
    # 处理删除清单
    _apply_deleted_list(zip_path, target)
    return len(effective)


def _apply_deleted_list(zip_path, target):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            data = z.read("deleted.txt").decode("utf-8", errors="replace")
    except Exception:
        return 0
    removed = 0
    for line in data.splitlines():
        rel = line.strip()
        if not rel:
            continue
        p = _safe_join(target, rel)
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed += 1
            except Exception:
                pass
    if removed:
        print("  [del] 按删除清单删除 %d 个文件" % removed)


def plan_apply(zips, target, mode="all", dry_run=False, backup=True, log=print):
    """按模式执行或预览覆盖。

    mode: "latest" 只覆盖最新一个；"all" 从旧到新依次覆盖（默认）。
    返回 (成功包数, 覆盖文件总数, backup_dir)。
    """
    if not zips:
        log("没有找到发布包 zip。")
        return (0, 0, None)
    ordered = sorted(zips, key=lambda x: x["mtime"]) if mode == "all" else [zips[0]]
    backup_dir = None
    if backup and not dry_run:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(target, BACKUP_DIR_NAME, ts)
        os.makedirs(backup_dir, exist_ok=True)
        log("备份目录: %s" % backup_dir)
    total = 0
    for item in ordered:
        dt = datetime.datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        log("== %s (%s)" % (item["name"], dt))
        if dry_run:
            names = _zip_file_list(item["path"])
            log("  [预览] 将覆盖 %d 个文件（跳过 %d 个工具自身文件）"
                % (len([n for n in names if os.path.basename(n) not in SKIP]),
                   len([n for n in names if os.path.basename(n) in SKIP])))
            total += len([n for n in names if os.path.basename(n) not in SKIP])
        else:
            total += apply_zip(item["path"], target, backup_dir, log=log)
    return (len(ordered), total, backup_dir)


# --------------------------------------------------------------------------
# 图形界面
# --------------------------------------------------------------------------
class DeployApp(object):
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        root.title("WT Automation - 发布包一键部署")
        root.geometry("880x620")
        try:
            import wt_dpi
            wt_dpi.enable_process_dpi_awareness()
            wt_dpi.compute_scale(root)
        except Exception:
            pass
        self._build_ui()
        self._load_cfg_into_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)

        row = ttk.Frame(frm)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="内网仓库根目录:").pack(side="left")
        self.var_root = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_root).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…", command=self._browse_root).pack(side="right")

        row = ttk.Frame(frm)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="发布包文件夹:").pack(side="left")
        self.var_release = tk.StringVar()
        ttk.Entry(row, textvariable=self.var_release).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…", command=self._browse_release).pack(side="right")
        ttk.Button(row, text="刷新列表", command=self._refresh_list).pack(side="right", padx=2)

        row = ttk.Frame(frm)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="覆盖模式:").pack(side="left")
        self.var_mode = tk.StringVar(value="all")
        ttk.Radiobutton(row, text="从旧到新依次覆盖（全部）", value="all", variable=self.var_mode).pack(side="left")
        ttk.Radiobutton(row, text="仅覆盖最新一个", value="latest", variable=self.var_mode).pack(side="left", padx=6)
        self.var_backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="覆盖前备份", variable=self.var_backup).pack(side="left", padx=10)

        ttk.Label(frm, text="发布包列表（按时间倒序，默认选中最新的）:").pack(anchor="w", **pad)
        self.listbox = tk.Listbox(frm, selectmode="extended", height=8)
        self.listbox.pack(fill="x", padx=8)

        row = ttk.Frame(frm)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="预览覆盖", command=self._preview).pack(side="left")
        ttk.Button(row, text="执行覆盖", command=self._apply).pack(side="left", padx=6)
        ttk.Label(row, text="（路径/模式自动记忆）", foreground="#666").pack(side="left", padx=8)

        ttk.Label(frm, text="日志:").pack(anchor="w", **pad)
        self.log = tk.Text(frm, height=14, state="disabled")
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.log.tag_configure("err", foreground="#c00")
        self.log.tag_configure("ok", foreground="#060")

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        if tag:
            self.log.tag_add(tag, "end-%dc" % (len(msg) + 1), "end-1c")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _load_cfg_into_ui(self):
        if self.cfg.get("project_root"):
            self.var_root.set(self.cfg["project_root"])
        if self.cfg.get("release_dir"):
            self.var_release.set(self.cfg["release_dir"])
        else:
            root_path = self.var_root.get()
            if root_path:
                self.var_release.set(os.path.join(root_path, "release_out"))
        if self.cfg.get("mode"):
            self.var_mode.set(self.cfg["mode"])
        self._refresh_list()

    def _save_cfg(self):
        self.cfg["project_root"] = self.var_root.get().strip()
        self.cfg["release_dir"] = self.var_release.get().strip()
        self.cfg["mode"] = self.var_mode.get()
        save_config(self.cfg)

    def _browse_root(self):
        path = filedialog.askdirectory(title="选择内网仓库根目录", initialdir=self.var_root.get() or os.getcwd())
        if path:
            self.var_root.set(path)
            if not self.var_release.get():
                self.var_release.set(os.path.join(path, "release_out"))
            self._save_cfg()
            self._refresh_list()

    def _browse_release(self):
        path = filedialog.askdirectory(title="选择发布包文件夹（存放 zip）", initialdir=self.var_release.get() or os.getcwd())
        if path:
            self.var_release.set(path)
            self._save_cfg()
            self._refresh_list()

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        self._zips = list_release_zips(self.var_release.get().strip())
        if not self._zips:
            # 兼容旧行为：脚本所在目录找
            here = os.path.dirname(os.path.abspath(__file__))
            self._zips = list_release_zips(here)
        for i, item in enumerate(self._zips):
            dt = datetime.datetime.fromtimestamp(item["mtime"]).strftime("%m-%d %H:%M")
            self.listbox.insert("end", "[%s] %s" % (dt, item["name"]))
        if self._zips:
            self.listbox.selection_set(0)
            self.listbox.activate(0)

    def _selected_zips(self):
        sel = self.listbox.curselection()
        if not sel and self._zips:
            sel = (0,)
        return [self._zips[i] for i in sel]

    def _validate(self):
        root_path = self.var_root.get().strip()
        release_dir = self.var_release.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showerror("错误", "内网仓库根目录不存在或未选择。")
            return None, None
        if not release_dir or not os.path.isdir(release_dir):
            messagebox.showerror("错误", "发布包文件夹不存在或未选择。")
            return None, None
        return root_path, release_dir

    def _preview(self):
        root_path, _ = self._validate()
        if not root_path:
            return
        self._save_cfg()
        zips = self._selected_zips() if self.var_mode.get() == "latest" else self._zips
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        plan_apply(zips, root_path, mode=self.var_mode.get(), dry_run=True, log=lambda m: self._log(m))
        self._log("--- 预览结束 ---", "ok")

    def _apply(self):
        root_path, _ = self._validate()
        if not root_path:
            return
        self._save_cfg()
        zips = self._selected_zips() if self.var_mode.get() == "latest" else self._zips
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        if not messagebox.askyesno("确认", "将覆盖 %d 个发布包到:\n%s\n\n是否继续？" % (len(zips), root_path)):
            return
        try:
            plan_apply(zips, root_path, mode=self.var_mode.get(),
                       dry_run=False, backup=self.var_backup.get(), log=lambda m: self._log(m))
            self._log("--- 覆盖完成 ---", "ok")
            messagebox.showinfo("完成", "发布包覆盖完成。若改了服务端脚本，请重启队列服务使其生效。")
        except Exception as exc:
            self._log("执行失败: %s" % exc, "err")
            messagebox.showerror("失败", "覆盖失败:\n%s" % exc)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def main_cli(argv):
    parser = argparse.ArgumentParser(description="内网机一键部署（解压 + 应用）")
    parser.add_argument("zip_hint", nargs="?", help="发布包 zip 路径（省略则自动找最新）")
    parser.add_argument("target", nargs="?", help="内网仓库根目录（省略则用记忆值）")
    parser.add_argument("--latest", action="store_true", help="只覆盖最新一个包（默认从旧到新全部）")
    parser.add_argument("--all", action="store_true", help="从旧到新依次覆盖全部包（默认）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--no-backup", action="store_true", help="覆盖前不备份")
    args = parser.parse_args(argv)

    cfg = load_config()
    target = args.target or cfg.get("project_root")
    if not target or not os.path.isdir(target):
        print("错误：未指定有效的内网仓库根目录。")
        print("用法: deploy_release.exe <发布包.zip> <内网仓库根目录>   （或直接双击运行图形界面）")
        return 2

    release_dir = cfg.get("release_dir") or os.path.join(target, "release_out")
    zip_path = find_zip(args.zip_hint, release_dir=release_dir)
    if not zip_path or not os.path.isfile(zip_path):
        print("错误：找不到发布包 zip 文件。")
        print("用法: deploy_release.exe <发布包.zip> <内网仓库根目录>   （或直接双击运行图形界面）")
        return 2

    cfg["project_root"] = target
    cfg["release_dir"] = release_dir
    cfg["mode"] = "latest" if args.latest else "all"
    save_config(cfg)

    if os.path.dirname(zip_path) != release_dir and zip_path.endswith(".zip"):
        release_dir = os.path.dirname(zip_path)
    zips = [{"path": zip_path, "name": os.path.basename(zip_path),
             "mtime": os.path.getmtime(zip_path)}] if args.zip_hint else list_release_zips(release_dir)
    mode = "latest" if args.latest else "all"
    n_ok, n_files, backup_dir = plan_apply(zips, target, mode=mode,
                                           dry_run=args.dry_run, backup=not args.no_backup, log=print)
    if args.dry_run:
        print("\n(预览模式。去掉 --dry-run 执行覆盖)")
    else:
        print("\n部署完成：成功 %d 个包，共 %d 个文件" % (n_ok, n_files))
        print("  来源：%s" % os.path.basename(zip_path))
        print("  目标：%s" % target)
        if backup_dir:
            print("  备份目录：%s" % backup_dir)
    return 0


def main():
    if _HAS_TK and len(sys.argv) == 1:
        try:
            root = tk.Tk()
            DeployApp(root)
            root.mainloop()
            return 0
        except Exception as exc:
            print("图形界面启动失败（%s），切换命令行模式。" % exc)
    if len(sys.argv) == 1:
        print("[提示] 未检测到图形界面（Tkinter 不可用），进入命令行模式。")
    return main_cli(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())