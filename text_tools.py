# encoding: utf-8
"""
text_tools.py —— 文本处理工具（双卡片切换）

卡片一：TXT 合并
  - 选择多个 txt 文件 / 文件夹，按列表顺序合并成一个 txt 文档
  - 合并方式：直接接在末尾 / 新起一行再接
  - 可选：文件名作为章节标题、自定义分隔符、去重空行、按文件名排序
  - 输出编码可选：UTF-8 / GBK
  - 支持拖拽添加文件（需 tkinterdnd2，未安装时自动降级）

卡片二：CSV 转换
  - 选择多个 CSV 文件 / 文件夹
  - 自动识别编码（UTF-16/UTF-8/GBK 等）与分隔符（逗号/分号/制表符）
  - 转换类型：转 XLSX / 转 TXT
  - 转 XLSX 可选合并为单个工作簿（多工作表）
  - 进度条 + 日志

用法：
  1. 双击运行，或命令行执行：python text_tools.py
  2. 在顶部切换卡片
  3. 按卡片内提示操作
"""

import os
import sys
import csv
import io
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from openpyxl import Workbook
from openpyxl.utils import get_column_letter


class BatchCancelled(Exception):
    """批量处理被取消（逐文件边界抛出，done=已完成文件数）（待修改清单 #1）。"""

    def __init__(self, done=0):
        super().__init__("cancelled after {} file(s)".format(done))
        self.done = done

# TXT 合并/读取核心统一到 wt_txt_merge_core（待修改清单 #5）：本模块与 txt_merge_tool
# 共用唯一实现，下方仅保留 text_tools 历史默认值（newline_between=False）的差异适配。
# 注意经由模块属性调用（而非 from-import 按值绑定），保证测试可以 patch 核心。
import wt_txt_merge_core
import wt_ui_state


def read_text_file(path):
    """读取文本文件内容，自动探测编码，返回 (内容, 编码)。实现统一委托核心。"""
    return wt_txt_merge_core.read_text_file(path)

# 拖拽支持：优先用 tkinterdnd2，否则用 pywin32 的 OLE 拖拽
_HAS_DND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:  # noqa: BLE001
    pass

_HAS_OLE_DND = False
try:
    import pythoncom
    from win32com.shell import shell, shellcon
    _HAS_OLE_DND = True
except Exception:  # noqa: BLE001
    pass


# =====================================================================
# 卡片一：TXT 合并
# =====================================================================

class OleDropTarget:
    """基于 pywin32 的 OLE 文件拖拽目标（IDropTarget 实现，备用方案）。"""

    _com_interfaces_ = [pythoncom.IID_IDropTarget]
    _public_methods_ = ["DragEnter", "DragOver", "DragLeave", "Drop"]

    def __init__(self, hwnd, on_files):
        self._hwnd = hwnd
        self._on_files = on_files
        self._registered = False

    def register(self):
        try:
            pythoncom.RegisterDragDrop(self._hwnd, self)
            self._registered = True
            return True
        except Exception:  # noqa: BLE001
            return False

    def unregister(self):
        try:
            if self._registered:
                pythoncom.RevokeDragDrop(self._hwnd)
                self._registered = False
        except Exception:  # noqa: BLE001
            pass

    def DragEnter(self, pDataObj, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        return 0

    def DragOver(self, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        return 0

    def DragLeave(self):
        return 0

    def Drop(self, pDataObj, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        try:
            files = []
            try:
                data = pDataObj.GetData(shellcon.CF_HDROP)
                files = shell.DragQueryFile(data)
            except Exception:  # noqa: BLE001
                pass
            if files:
                self._on_files(files)
        except Exception:  # noqa: BLE001
            pass
        return 0


def merge_txt_files(
    file_paths,
    output_path,
    newline_between=False,
    add_headers=False,
    separator="",
    remove_empty_lines=False,
    output_encoding="utf-8-sig",
    progress_callback=None,
    cancel_event=None,
):
    """按顺序合并多个 txt 文件到输出文件，保持内容原样。

    实现统一委托 wt_txt_merge_core（待修改清单 #5）；保留本模块历史默认值
    newline_between=False（txt_merge_tool 侧为 True，两个 UI 调用均显式传参，
    默认值从不参与实际行为——此处仅为兼容外部潜在调用方）。
    progress_callback / cancel_event 透传核心（待修改清单 #2）。
    """
    return wt_txt_merge_core.merge_txt_files(
        file_paths,
        output_path,
        newline_between=newline_between,
        add_headers=add_headers,
        separator=separator,
        remove_empty_lines=remove_empty_lines,
        output_encoding=output_encoding,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


class TxtMergeCard(ttk.Frame):
    """TXT 合并卡片。"""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.files = []

        tip = ttk.Label(
            self,
            text="按列表顺序合并多个文本文件，内容格式不变。可拖拽文件到下方列表。",
            foreground="#555555",
        )
        tip.pack(fill="x", pady=(0, 4))

        # 文件列表
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, pady=4)
        self.listbox = tk.Listbox(frame, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        # 操作按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=4)
        ttk.Button(btn_frame, text="添加文件", command=self.add_files).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="添加文件夹", command=self.add_folder).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="移除选中", command=self.remove_selected).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="清空列表", command=self.clear_list).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="上移", command=lambda: self.move(-1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="下移", command=lambda: self.move(1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="按文件名排序", command=self.sort_by_name).pack(side="left", padx=2)

        # 衔接方式
        opt_frame = ttk.Frame(self)
        opt_frame.pack(fill="x", pady=2)
        ttk.Label(opt_frame, text="文件之间衔接方式：").pack(side="left")
        self.join_var = tk.StringVar(value="direct")
        ttk.Radiobutton(opt_frame, text="直接接在末尾", variable=self.join_var, value="direct").pack(side="left", padx=4)
        ttk.Radiobutton(opt_frame, text="新起一行再接", variable=self.join_var, value="newline").pack(side="left", padx=4)

        # 高级选项
        adv_frame = ttk.Frame(self)
        adv_frame.pack(fill="x", pady=2)
        self.header_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv_frame, text="文件名作为章节标题", variable=self.header_var).pack(side="left", padx=4)
        self.empty_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv_frame, text="去重空行", variable=self.empty_var).pack(side="left", padx=4)

        # 分隔符
        sep_frame = ttk.Frame(self)
        sep_frame.pack(fill="x", pady=2)
        ttk.Label(sep_frame, text="自定义分隔符（留空则不插入）：").pack(side="left")
        self.sep_var = tk.StringVar(value="")
        ttk.Entry(sep_frame, textvariable=self.sep_var, width=24).pack(side="left", padx=4)

        # 输出编码
        enc_frame = ttk.Frame(self)
        enc_frame.pack(fill="x", pady=2)
        ttk.Label(enc_frame, text="输出编码：").pack(side="left")
        self.enc_var = tk.StringVar(value="utf-8-sig")
        for enc, label in [("utf-8-sig", "UTF-8(带BOM)"), ("utf-8", "UTF-8"), ("gbk", "GBK")]:
            ttk.Radiobutton(enc_frame, text=label, variable=self.enc_var, value=enc).pack(side="left", padx=4)

        # 合并按钮
        merge_frame = ttk.Frame(self)
        merge_frame.pack(fill="x", pady=(6, 4))
        self.btn_merge = ttk.Button(merge_frame, text="合并为单个 txt", command=self.merge)
        self.btn_merge.pack(side="left", padx=2)

        self.status = ttk.Label(self, text="", foreground="#333333")
        self.status.pack(fill="x", pady=(0, 4))

        # 拖拽
        self._ole_drop = None
        if _HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)
        elif _HAS_OLE_DND:
            self._ole_drop = OleDropTarget(self.winfo_toplevel().winfo_id(), self._append_paths)
            if not self._ole_drop.register():
                self._ole_drop = None
                self.status.config(text="提示：拖拽功能初始化失败，可用按钮添加文件")
        else:
            self.status.config(text="提示：拖拽功能不可用，可用按钮添加文件")

    # ---- 文件操作 ----
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择要合并的文本文件（支持 txt / wnd 等任意文本格式）",
            filetypes=[("所有文件", "*.*"), ("文本文件", "*.txt")],
        )
        self._append_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择包含文本文件的文件夹")
        if not folder:
            return
        try:
            paths = [
                os.path.join(folder, name)
                for name in sorted(os.listdir(folder))
                if name.lower().endswith(".txt")
            ]
        except OSError as exc:
            # 目录不可读/已删除时此前异常被 Tk 吞掉（"点了没反应"，审计 P2）
            messagebox.showerror("读取文件夹失败", "{}\n\n{}".format(folder, exc))
            return
        if not paths:
            messagebox.showinfo("提示", "该文件夹内没有找到 .txt 文件。")
            return
        self._append_paths(paths)

    def on_drop(self, event):
        paths = self.winfo_toplevel().tk.splitlist(event.data)
        self._append_paths(paths)

    def _append_paths(self, paths):
        for p in paths:
            if os.path.isfile(p) and p not in self.files:
                self.files.append(p)
        self.refresh_list()

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        for idx in reversed(sel):
            del self.files[idx]
        self.refresh_list()

    def clear_list(self):
        if self.files and not messagebox.askyesno(
            "确认清空", "确定清空列表中的 {} 个文件吗？".format(len(self.files))
        ):
            return
        self.files = []
        self.refresh_list()

    def move(self, direction):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.files):
            return
        self.files[idx], self.files[new_idx] = self.files[new_idx], self.files[idx]
        self.refresh_list()
        self.listbox.selection_set(new_idx)

    def sort_by_name(self):
        self.files.sort(key=lambda p: os.path.basename(p).lower())
        self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        for p in self.files:
            self.listbox.insert(tk.END, os.path.basename(p))
        self.status.config(text=f"共 {len(self.files)} 个文件")

    def merge(self):
        # 运行中再次点击 = 请求取消（逐文件边界生效，见 wt_txt_merge_core）
        thread = getattr(self, "_merge_thread", None)
        if thread is not None and thread.is_alive():
            self._merge_cancel.set()
            self.status.config(text="正在取消合并（等待当前文件完成）...")
            return

        if not self.files:
            messagebox.showwarning("提示", "请先添加要合并的 txt 文件。")
            return
        output = filedialog.asksaveasfilename(
            title="保存合并后的文件",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            initialfile="merged.txt",
        )
        if not output:
            return

        # 选项主线程快照，worker 只用局部数据（待修改清单 #1 同款纪律）
        options = dict(
            newline_between=self.join_var.get() == "newline",
            add_headers=self.header_var.get(),
            separator=self.sep_var.get().strip(),
            remove_empty_lines=self.empty_var.get(),
            output_encoding=self.enc_var.get(),
        )
        paths = list(self.files)
        total = len(paths)
        self._merge_cancel = threading.Event()
        self._merge_events = queue.Queue()
        self._merge_btn_text_backup = self.btn_merge.cget("text")
        self.btn_merge.configure(text="⨂ 取消合并")
        self.status.config(text="正在合并...")

        def _worker():
            try:
                count, total_chars = merge_txt_files(
                    paths, output, cancel_event=self._merge_cancel, **options)
            except wt_txt_merge_core.MergeCancelled as exc:
                done = exc.done
                self._merge_events.put(lambda d=done, t=total: self._merge_finish(
                    output, cancelled=True, done=d, total=t))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self._merge_events.put(lambda m=msg: self._merge_finish(output, error=m))
            else:
                self._merge_events.put(lambda c=count, tc=total_chars: self._merge_finish(
                    output, count=c, total_chars=tc))

        self._merge_thread = threading.Thread(target=_worker, daemon=True)
        self._merge_thread.start()
        # 轮询链从主线程启动（worker 不跨线程调 after）
        self.after(80, self._drain_merge_events)

    def _drain_merge_events(self):
        while True:
            try:
                fn = self._merge_events.get_nowait()
            except queue.Empty:
                break
            fn()
        thread = getattr(self, "_merge_thread", None)
        if (thread is not None and thread.is_alive()) or not self._merge_events.empty():
            self.after(80, self._drain_merge_events)

    def _merge_finish(self, output, count=0, total_chars=0, cancelled=False,
                      done=0, total=None, error=None):
        """收尾（仅主线程执行）：恢复按钮 + 三态反馈。"""
        self.btn_merge.configure(text=self._merge_btn_text_backup)
        if error is not None:
            messagebox.showerror("合并失败", f"发生错误：\n{error}")
            self.status.config(text=f"合并失败: {error}")
            return
        if cancelled:
            total = total if total else len(self.files)
            self.status.config(
                text=f"已取消（完成 {done}/{total}）——输出未写入，原文件保持原样")
            messagebox.showinfo(
                "已取消",
                f"合并已取消，完成 {done}/{total} 个文件。\n"
                f"输出未写入：目标文件保持原样。")
            return
        messagebox.showinfo("完成", f"已合并 {count} 个文件，共 {total_chars} 个字符。\n输出文件：\n{output}")
        self.status.config(text=f"合并完成：{output}")


# =====================================================================
# 卡片二：CSV 转换
# =====================================================================

def read_csv_content(path):
    """鲁棒读取 CSV 文件内容，返回 (编码, 分隔符, 字符串列表)。"""
    with open(path, "rb") as f:
        raw = f.read()
    raw_no_nul = raw.replace(b"\x00", b"")
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16-le", "utf-16-be", "latin-1", "cp1252", "big5"]
    text = None
    used_enc = "utf-8"
    for enc in encodings:
        try:
            text = raw_no_nul.decode(enc)
            used_enc = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw_no_nul.decode("utf-8", errors="replace")
        used_enc = "utf-8 (replace)"
    delim = ","
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=[",", ";", "\t", "|"])
        delim = dialect.delimiter
    except Exception:  # noqa: BLE001
        pass
    return used_enc, delim, text


def read_csv_rows(path, skip_header=False):
    """读取 CSV 内容，返回行列表（每行是单元格值列表）。

    skip_header=True 时跳过第一行（用于单工作表合并时只保留第一个文件的表头）。
    """
    _, delim, text = read_csv_content(path)
    rows = []
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    for r, row in enumerate(reader, start=1):
        if skip_header and r == 1:
            continue
        rows.append(list(row))
    return rows


def convert_csv_to_sheet(ws, path, skip_header=False):
    """把单个 CSV 内容写入给定工作表 ws，返回写入行数。"""
    rows = read_csv_rows(path, skip_header=skip_header)
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)
    max_col = ws.max_column
    for c in range(1, max_col + 1):
        letter = get_column_letter(c)
        width = 10
        for cell in ws[letter]:
            if cell.value is not None:
                width = max(width, min(len(str(cell.value)) + 2, 60))
        ws.column_dimensions[letter].width = width
    return len(rows)


def safe_sheet_name(name, used):
    """生成合法且不重复的工作表名(<=31 字符)。"""
    base = name[:31]
    candidate = base
    i = 1
    while candidate in used:
        suffix = f"_{i}"
        candidate = base[: 31 - len(suffix)] + suffix
        i += 1
    used.add(candidate)
    return candidate


def csv_to_txt(path, out_path, output_encoding="utf-8-sig"):
    """把单个 CSV 转为 TXT（保留原始文本内容，仅去除 NUL 空字节）。"""
    _, _, text = read_csv_content(path)
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write(text)
    return len(text.splitlines())


class CsvConvertCard(ttk.Frame):
    """CSV 转换卡片（转 XLSX / 转 TXT）。"""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.csv_files = []
        self.out_dir = ""

        # 文件选择
        frm = ttk.Frame(self)
        frm.pack(fill="x", pady=2)
        ttk.Label(frm, text="CSV 文件 / 文件夹：").pack(side="left")
        self.var_files = tk.StringVar(value="未选择")
        ttk.Label(frm, textvariable=self.var_files, foreground="blue").pack(side="left", padx=6)
        ttk.Button(frm, text="选择文件", command=self.pick_files).pack(side="left", padx=2)
        ttk.Button(frm, text="选择文件夹", command=self.pick_folder).pack(side="left", padx=2)

        # 输出目录
        frm2 = ttk.Frame(self)
        frm2.pack(fill="x", pady=2)
        ttk.Label(frm2, text="输出目录：").pack(side="left")
        self.var_out = tk.StringVar(value="与源文件同目录")
        ttk.Label(frm2, textvariable=self.var_out, foreground="blue").pack(side="left", padx=6)
        ttk.Button(frm2, text="选择输出目录", command=self.pick_out).pack(side="left", padx=2)

        # 转换类型
        frm3 = ttk.Frame(self)
        frm3.pack(fill="x", pady=2)
        ttk.Label(frm3, text="转换类型：").pack(side="left")
        self.conv_var = tk.StringVar(value="xlsx")
        ttk.Radiobutton(frm3, text="转 XLSX", variable=self.conv_var, value="xlsx").pack(side="left", padx=4)
        ttk.Radiobutton(frm3, text="转 TXT", variable=self.conv_var, value="txt").pack(side="left", padx=4)

        # 合并选项（仅 XLSX 有效）
        frm_merge = ttk.Frame(self)
        frm_merge.pack(fill="x", pady=2)
        ttk.Label(frm_merge, text="XLSX 输出方式：").pack(side="left")
        self.merge_var = tk.StringVar(value="single")
        self._merge_rb_list = []
        for value, label in (("single", "每个文件单独一个工作簿"), ("multi", "合并为多工作表"), ("one", "合并为单工作表")):
            rb = ttk.Radiobutton(frm_merge, text=label, variable=self.merge_var, value=value)
            rb.pack(side="left", padx=4)
            self._merge_rb_list.append(rb)

        # 单工作表合并选项（仅合并为单工作表时有效）
        self.merge_single_header = tk.BooleanVar(value=True)
        self.merge_header_chk = ttk.Checkbutton(
            self, text="合并为单工作表时仅保留第一个文件的表头", variable=self.merge_single_header
        )
        self.merge_header_chk.pack(fill="x", pady=2)

        # 输出编码（仅 TXT 有效）
        frm4 = ttk.Frame(self)
        frm4.pack(fill="x", pady=2)
        ttk.Label(frm4, text="TXT 输出编码：").pack(side="left")
        self.txt_enc_var = tk.StringVar(value="utf-8-sig")
        self._txt_enc_rb_list = []
        for enc, label in [("utf-8-sig", "UTF-8(带BOM)"), ("utf-8", "UTF-8"), ("gbk", "GBK")]:
            rb = ttk.Radiobutton(frm4, text=label, variable=self.txt_enc_var, value=enc)
            rb.pack(side="left", padx=4)
            self._txt_enc_rb_list.append(rb)

        # 开始按钮
        frm5 = ttk.Frame(self)
        frm5.pack(fill="x", pady=4)
        self.btn_run = ttk.Button(frm5, text="开始转换", command=self.run)
        self.btn_run.pack(side="left", padx=2)

        # 进度条
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=4)

        # 日志
        self.log_text = tk.Text(self, height=12, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=4)

        # 参数联动：转换类型/合并方式变化时自动禁用无关参数（审计 P2）
        self.conv_var.trace_add("write", lambda *a: self._sync_option_states())
        self.merge_var.trace_add("write", lambda *a: self._sync_option_states())
        self._sync_option_states()

    def _sync_option_states(self):
        """按当前转换类型/合并方式联动启用/禁用参数控件（审计 P2）。

        - 转 TXT：XLSX 输出方式与"仅保留第一个文件表头"禁用，TXT 编码启用；
        - 转 XLSX：TXT 编码禁用；"仅保留表头"仅"合并为单工作表"时可用。
        """
        is_xlsx = self.conv_var.get() == "xlsx"
        for rb in getattr(self, "_merge_rb_list", []):
            rb.config(state="normal" if is_xlsx else "disabled")
        state_header = "normal" if (is_xlsx and self.merge_var.get() == "one") else "disabled"
        try:
            self.merge_header_chk.config(state=state_header)
        except Exception:
            pass
        for rb in getattr(self, "_txt_enc_rb_list", []):
            rb.config(state="disabled" if is_xlsx else "normal")

    def log(self, msg):
        """日志入口：worker 线程调用时投递事件队列，主线程直接写（待修改清单 #1）。"""
        if threading.current_thread() is threading.main_thread():
            self._log_direct(msg)
        else:
            self._events.put(lambda m=msg: self._log_direct(m))

    def _log_direct(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        # 行数上限：批量转换的长日志持续膨胀会拖慢滚动与重绘（审计 P2）
        try:
            total_lines = int(self.log_text.index("end-1c").split(".")[0])
            if total_lines > 500:
                self.log_text.delete("1.0", "{}.0".format(total_lines - 500 + 1))
        except Exception:
            pass
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.update_idletasks()

    def pick_files(self):
        files = filedialog.askopenfilenames(
            title="选择 CSV 文件", filetypes=[("CSV 文件", "*.csv"), ("全部", "*.*")]
        )
        if files:
            self.csv_files = list(files)
            self.var_files.set(f"已选 {len(self.csv_files)} 个文件")

    def pick_folder(self):
        folder = filedialog.askdirectory(title="选择包含 CSV 的文件夹")
        if not folder:
            return
        try:
            self.csv_files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith(".csv")
            ]
        except OSError as exc:
            messagebox.showerror("读取文件夹失败", "{}\n\n{}".format(folder, exc))
            return
        self.var_files.set(f"文件夹: {folder} ({len(self.csv_files)} 个 CSV)")

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir = d
            self.var_out.set(d)

    def _confirm_overwrite(self, dst):
        """循环内的覆盖判定：运行前预扫描已确定策略，此处只做纯查询、零交互。

        策略语义（见 _resolve_overwrite_policy）：
          "overwrite" → 全部覆盖；"skip" → 冲突文件全部跳过；
          "ask" → 仅覆盖预扫描时逐个确认允许的文件（_overwrite_allow 集合）。
        """
        if not os.path.exists(dst):
            return True
        # 缺省 "skip"：绕过预扫描的异常路径宁可少写盘，也不能静默覆盖旧文件
        policy = getattr(self, "_overwrite_policy", "skip")
        if policy == "skip":
            return False
        if policy == "ask":
            return dst in getattr(self, "_overwrite_allow", set())
        return True

    def _collect_output_targets(self, out_dir):
        """按当前转换模式枚举全部输出目标路径（运行前预扫描用，待修改清单 #1）。"""
        conv = self.conv_var.get()
        targets = []
        if conv == "xlsx":
            merge_mode = self.merge_var.get()
            if merge_mode == "single":
                for src in self.csv_files:
                    name = os.path.splitext(os.path.basename(src))[0]
                    targets.append(os.path.join(out_dir, name + ".xlsx"))
            elif merge_mode == "multi":
                targets.append(os.path.join(out_dir, "merged.xlsx"))
            else:
                targets.append(os.path.join(out_dir, "merged_single.xlsx"))
        else:
            for src in self.csv_files:
                name = os.path.splitext(os.path.basename(src))[0]
                targets.append(os.path.join(out_dir, name + ".txt"))
        return targets

    def _ask_overwrite_policy_dialog(self, conflicts):
        """模态四选一：全部覆盖 / 全部跳过 / 逐个确认 / 取消。返回策略或 None。"""
        choice = {"value": None}
        dialog = tk.Toplevel(self)
        dialog.title("同名输出文件")
        dialog.resizable(False, False)
        tk.Label(
            dialog,
            text="检测到 {} 个同名输出文件：\n\n{}".format(
                len(conflicts), "\n".join(conflicts[:8]) + ("\n..." if len(conflicts) > 8 else "")),
            justify="left",
            wraplength=420,
        ).pack(padx=16, pady=(14, 8))

        def _set(value):
            choice["value"] = value
            dialog.destroy()

        buttons = tk.Frame(dialog)
        buttons.pack(pady=(0, 6))
        tk.Button(buttons, text="全部覆盖", width=10, command=lambda: _set("overwrite")).pack(side="left", padx=4)
        tk.Button(buttons, text="全部跳过", width=10, command=lambda: _set("skip")).pack(side="left", padx=4)
        tk.Button(buttons, text="逐个确认", width=10, command=lambda: _set("ask")).pack(side="left", padx=4)
        tk.Button(dialog, text="取消本次转换", command=lambda: _set(None)).pack(pady=(0, 12))
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        self.winfo_toplevel().wait_window(dialog)
        return choice["value"]

    def _resolve_overwrite_policy(self, conflicts):
        """预扫描冲突清单 → 一次性确定覆盖策略（主线程，待修改清单 #1）。

        交互全部留在启动前：之后 worker 循环内零对话框。
        返回 (policy, allow_set)：policy ∈ {"overwrite", "skip", "ask"}，
        "ask" 时 allow_set 为逐个确认允许覆盖的路径集合；用户取消返回 (None, set())。
        无冲突时直接 ("overwrite", set())，不弹对话框。
        """
        if not conflicts:
            return "overwrite", set()
        choice = self._ask_overwrite_policy_dialog(conflicts)
        if choice is None:
            return None, set()
        if choice == "ask":
            allow = set()
            for dst in conflicts:
                if messagebox.askyesno("覆盖确认", "{}\n\n是否覆盖该文件？".format(dst)):
                    allow.add(dst)
            return "ask", allow
        return choice, set()

    def run(self):
        # 运行中再次点击 = 请求取消（逐文件边界生效）
        thread = getattr(self, "_run_thread", None)
        if thread is not None and thread.is_alive():
            self._cancel_event.set()
            self.log("正在取消（等待当前文件完成）...")
            return

        if not self.csv_files:
            messagebox.showwarning("提示", "请先选择 CSV 文件或文件夹。")
            return
        out_dir = self.out_dir or os.path.dirname(self.csv_files[0]) or "."
        os.makedirs(out_dir, exist_ok=True)

        # ── 运行前预扫描覆盖冲突，交互全部在主线程完成（待修改清单 #1） ──
        conflicts = [p for p in self._collect_output_targets(out_dir) if os.path.exists(p)]
        policy, allow_set = self._resolve_overwrite_policy(conflicts)
        if policy is None:
            self.log("已取消：未开始转换。")
            return
        self._overwrite_policy = policy
        self._overwrite_allow = allow_set

        # 选项在主线程一次性快照，worker 不读 Tk 变量
        options = dict(
            conv=self.conv_var.get(),
            merge_mode=self.merge_var.get(),
            merge_single_header=self.merge_single_header.get(),
            txt_enc=self.txt_enc_var.get(),
        )
        files = list(self.csv_files)  # 快照：运行期增删列表不影响迭代与 total
        total = len(files)

        self._cancel_event = threading.Event()
        self._events = queue.Queue()
        self._batch_failed = 0
        self.btn_run.configure(text="⨂ 取消转换")
        self.progress["value"] = 0
        self.log("开始转换...")

        def progress(cur, total_count):
            # worker 只投递，进度条由主线程更新
            self._events.put(lambda c=cur, t=total_count: self._update_progress(c, t))

        def _worker():
            try:
                if options["conv"] == "xlsx":
                    if options["merge_mode"] == "single":
                        self._build_single_xlsx(out_dir, progress, files)
                    elif options["merge_mode"] == "multi":
                        self._build_merged_multi_sheet(out_dir, progress, files)
                    else:
                        self._build_merged_single_sheet(
                            out_dir, progress, files,
                            only_first_header=options["merge_single_header"])
                else:
                    self._build_txt(out_dir, progress, files, enc=options["txt_enc"])
            except BatchCancelled as exc:
                done = exc.done
                self._events.put(
                    lambda d=done, t=total: self._finish(cancelled=True, done=d, total=t))
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self._events.put(lambda m=msg: self._finish(error=m))
            else:
                self._events.put(lambda: self._finish(total=total))

        self._run_thread = threading.Thread(target=_worker, daemon=True)
        self._run_thread.start()
        # 轮询链从主线程启动（worker 不跨线程调 after）
        self.after(80, self._drain_events)

    def _drain_events(self):
        """主线程排空 worker 事件队列；线程存活或队列非空则续订下一轮。"""
        while True:
            try:
                fn = self._events.get_nowait()
            except queue.Empty:
                break
            fn()
        thread = getattr(self, "_run_thread", None)
        if (thread is not None and thread.is_alive()) or not self._events.empty():
            self.after(80, self._drain_events)

    def _update_progress(self, cur, total):
        self.progress["maximum"] = total
        self.progress["value"] = cur

    def _finish(self, cancelled=False, done=0, total=0, error=None):
        """收尾（仅主线程执行）：恢复按钮；三档结果反馈逻辑与原同步版一致。"""
        self.btn_run.configure(text="开始转换", state="normal")
        if error is not None:
            self.log(f"发生错误: {error}")
            messagebox.showerror("错误", error)
            return
        failed = getattr(self, "_batch_failed", 0)
        if cancelled:
            self.log(f"已取消：完成 {done}/{total}，失败 {failed}。")
            messagebox.showwarning(
                "已取消", f"已取消转换：完成 {done}/{total}，失败 {failed}。")
            return
        # 按实际结果反馈：此前无论失败多少都弹"转换完成！"，与日志矛盾（审计 P1◐）
        if failed == 0:
            self.log("全部处理完成。")
            messagebox.showinfo("完成", "转换完成！")
        elif failed < total:
            self.log(f"处理结束：成功 {total - failed}/{total}，失败 {failed}。")
            messagebox.showwarning(
                "部分失败", f"处理结束：成功 {total - failed} 个，失败 {failed} 个，详见日志。"
            )
        else:
            self.log(f"处理失败：全部 {total} 个文件均失败。")
            messagebox.showerror("转换失败", f"全部 {total} 个文件均转换失败，详见日志。")

    def _build_single_xlsx(self, out_dir, progress, files):
        total = len(files)
        for idx, src in enumerate(files, start=1):
            if self._cancel_event.is_set():
                raise BatchCancelled(done=idx - 1)  # 取消在逐文件边界（待修改清单 #1）
            try:
                name = os.path.splitext(os.path.basename(src))[0]
                dst = os.path.join(out_dir, name + ".xlsx")
                if not self._confirm_overwrite(dst):
                    self.log(f"[跳过] 已存在同名文件: {os.path.basename(dst)}")
                else:
                    wb = Workbook()
                    ws = wb.active
                    ws.title = "Sheet1"
                    rows = convert_csv_to_sheet(ws, src)
                    wb.save(dst)
                    self.log(f"[成功] {os.path.basename(src)} -> {os.path.basename(dst)} ({rows} 行)")
            except Exception as e:  # noqa: BLE001
                self._batch_failed = getattr(self, "_batch_failed", 0) + 1
                self.log(f"[失败] {os.path.basename(src)} 错误: {e}")
            progress(idx, total)

    def _build_merged_multi_sheet(self, out_dir, progress, files):
        """合并为单个工作簿，每个 CSV 一个工作表。"""
        wb = Workbook()
        wb.remove(wb.active)
        used = set()
        total = len(files)
        for idx, src in enumerate(files, start=1):
            if self._cancel_event.is_set():
                raise BatchCancelled(done=idx - 1)  # 取消在逐文件边界（待修改清单 #1）
            try:
                name = safe_sheet_name(os.path.splitext(os.path.basename(src))[0], used)
                ws = wb.create_sheet(title=name)
                rows = convert_csv_to_sheet(ws, src)
                self.log(f"[成功] {os.path.basename(src)} -> 工作表[{name}] ({rows} 行)")
            except Exception as e:  # noqa: BLE001
                self._batch_failed = getattr(self, "_batch_failed", 0) + 1
                self.log(f"[失败] {os.path.basename(src)} 错误: {e}")
            progress(idx, total)
        if wb.sheetnames:
            dst = os.path.join(out_dir, "merged.xlsx")
            if self._confirm_overwrite(dst):
                wb.save(dst)
                self.log(f"已合并保存到 {dst}")
            else:
                self.log(f"[跳过] 已存在同名文件: {dst}")
        else:
            self.log("没有可转换的文件。")

    def _build_merged_single_sheet(self, out_dir, progress, files, only_first_header=False):
        """合并为单个工作簿，所有 CSV 纵向堆叠到同一个工作表。"""
        wb = Workbook()
        ws = wb.active
        ws.title = "合并数据"
        total = len(self.csv_files)
        current_row = 1
        for idx, src in enumerate(files, start=1):
            if self._cancel_event.is_set():
                raise BatchCancelled(done=idx - 1)  # 取消在逐文件边界（待修改清单 #1）
            try:
                skip = only_first_header and idx > 1
                rows = read_csv_rows(src, skip_header=skip)
                for row in rows:
                    for c, value in enumerate(row, start=1):
                        ws.cell(row=current_row, column=c, value=value)
                    current_row += 1
                self.log(f"[成功] {os.path.basename(src)} -> 追加 {len(rows)} 行")
            except Exception as e:  # noqa: BLE001
                self._batch_failed = getattr(self, "_batch_failed", 0) + 1
                self.log(f"[失败] {os.path.basename(src)} 错误: {e}")
            progress(idx, total)
        # 自动列宽
        for c in range(1, ws.max_column + 1):
            letter = get_column_letter(c)
            width = 10
            for cell in ws[letter]:
                if cell.value is not None:
                    width = max(width, min(len(str(cell.value)) + 2, 60))
            ws.column_dimensions[letter].width = width
        if current_row > 1:
            dst = os.path.join(out_dir, "merged_single.xlsx")
            if self._confirm_overwrite(dst):
                wb.save(dst)
                self.log(f"已合并保存到 {dst}")
            else:
                self.log(f"[跳过] 已存在同名文件: {dst}")
        else:
            self.log("没有可转换的文件。")

    def _build_txt(self, out_dir, progress, files, enc="utf-8-sig"):
        total = len(files)
        for idx, src in enumerate(files, start=1):
            if self._cancel_event.is_set():
                raise BatchCancelled(done=idx - 1)  # 取消在逐文件边界（待修改清单 #1）
            try:
                name = os.path.splitext(os.path.basename(src))[0]
                dst = os.path.join(out_dir, name + ".txt")
                if self._confirm_overwrite(dst):
                    rows = csv_to_txt(src, dst, output_encoding=enc)
                    self.log(f"[成功] {os.path.basename(src)} -> {os.path.basename(dst)} ({rows} 行)")
                else:
                    self.log(f"[跳过] 已存在同名文件: {os.path.basename(dst)}")
            except Exception as e:  # noqa: BLE001
                self._batch_failed = getattr(self, "_batch_failed", 0) + 1
                self.log(f"[失败] {os.path.basename(src)} 错误: {e}")
            progress(idx, total)


# =====================================================================
# 卡片三：文本工具
# =====================================================================

def split_file_by_lines(path, out_dir, lines_per_file, output_encoding="utf-8-sig"):
    """按行数拆分文本文件为多个小文件，返回生成的文件列表。"""
    content, _ = read_text_file(path)
    all_lines = content.split("\n")
    base = os.path.splitext(os.path.basename(path))[0]
    created = []
    part = 1
    for i in range(0, len(all_lines), lines_per_file):
        chunk = all_lines[i:i + lines_per_file]
        dst = os.path.join(out_dir, f"{base}_part{part}.txt")
        with open(dst, "w", encoding=output_encoding, newline="") as f:
            f.write("\n".join(chunk))
        created.append(dst)
        part += 1
    return created


def dedupe_lines(path, out_path, output_encoding="utf-8-sig"):
    """按行去重，保留首次出现的行，返回 (原行数, 去重后行数)。"""
    content, _ = read_text_file(path)
    lines = content.split("\n")
    seen = set()
    unique = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write("\n".join(unique))
    return len(lines), len(unique)


def filter_lines(path, out_path, keyword, mode="keep", output_encoding="utf-8-sig"):
    """按关键词过滤行。mode='keep' 保留含关键词的行，mode='remove' 删除含关键词的行。"""
    content, _ = read_text_file(path)
    lines = content.split("\n")
    if mode == "keep":
        result = [line for line in lines if keyword in line]
    else:
        result = [line for line in lines if keyword not in line]
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write("\n".join(result))
    return len(lines), len(result)


def replace_keyword(path, out_path, old, new, output_encoding="utf-8-sig"):
    """全文替换关键词，返回替换次数。"""
    content, _ = read_text_file(path)
    count = content.count(old)
    content = content.replace(old, new)
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write(content)
    return count


def add_prefix_suffix(path, out_path, prefix="", suffix="", output_encoding="utf-8-sig"):
    """给每行加前缀/后缀，返回行数。"""
    content, _ = read_text_file(path)
    lines = content.split("\n")
    result = [prefix + line + suffix for line in lines]
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write("\n".join(result))
    return len(result)


def convert_case(path, out_path, mode="upper", output_encoding="utf-8-sig"):
    """大小写转换。mode: upper/lower/title。"""
    content, _ = read_text_file(path)
    if mode == "upper":
        result = content.upper()
    elif mode == "lower":
        result = content.lower()
    else:
        result = content.title()
    with open(out_path, "w", encoding=output_encoding, newline="") as f:
        f.write(result)
    return len(result.splitlines())


def convert_encoding_file(path, out_path, target_encoding="utf-8-sig"):
    """文件编码转换（自动识别源编码，转为目标编码）。"""
    content, _ = read_text_file(path)
    with open(out_path, "w", encoding=target_encoding, newline="") as f:
        f.write(content)
    return len(content.splitlines())


class TextToolsCard(ttk.Frame):
    """文本工具卡片：拆分/去重/过滤/替换/前后缀/大小写/编码转换。"""

    def __init__(self, master):
        super().__init__(master, padding=10)
        self.files = []
        self.out_dir = ""

        # 文件选择
        frm = ttk.Frame(self)
        frm.pack(fill="x", pady=2)
        ttk.Label(frm, text="文本文件：").pack(side="left")
        self.var_files = tk.StringVar(value="未选择")
        ttk.Label(frm, textvariable=self.var_files, foreground="blue").pack(side="left", padx=6)
        ttk.Button(frm, text="选择文件", command=self.pick_files).pack(side="left", padx=2)
        ttk.Button(frm, text="选择文件夹", command=self.pick_folder).pack(side="left", padx=2)

        # 输出目录
        frm2 = ttk.Frame(self)
        frm2.pack(fill="x", pady=2)
        ttk.Label(frm2, text="输出目录：").pack(side="left")
        self.var_out = tk.StringVar(value="与源文件同目录")
        ttk.Label(frm2, textvariable=self.var_out, foreground="blue").pack(side="left", padx=6)
        ttk.Button(frm2, text="选择输出目录", command=self.pick_out).pack(side="left", padx=2)

        # 操作类型
        frm3 = ttk.Frame(self)
        frm3.pack(fill="x", pady=2)
        ttk.Label(frm3, text="操作类型：").pack(side="left")
        self.op_var = tk.StringVar(value="dedupe")
        ops = [
            ("dedupe", "按行去重"),
            ("split", "按行拆分"),
            ("filter", "关键词过滤"),
            ("replace", "关键词替换"),
            ("affix", "加前后缀"),
            ("case", "大小写转换"),
            ("encoding", "编码转换"),
        ]
        for val, label in ops:
            ttk.Radiobutton(frm3, text=label, variable=self.op_var, value=val).pack(side="left", padx=3)

        # 参数区
        self.param_frame = ttk.LabelFrame(self, text="参数设置")
        self.param_frame.pack(fill="x", pady=4)

        # 拆分行数
        self.split_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.split_frame, text="每个文件行数：").pack(side="left")
        self.split_var = tk.StringVar(value="1000")
        ttk.Entry(self.split_frame, textvariable=self.split_var, width=10).pack(side="left", padx=4)

        # 过滤模式
        self.filter_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.filter_frame, text="关键词：").pack(side="left")
        self.filter_kw = tk.StringVar(value="")
        ttk.Entry(self.filter_frame, textvariable=self.filter_kw, width=20).pack(side="left", padx=4)
        self.filter_mode = tk.StringVar(value="keep")
        ttk.Radiobutton(self.filter_frame, text="保留含关键词", variable=self.filter_mode, value="keep").pack(side="left", padx=2)
        ttk.Radiobutton(self.filter_frame, text="删除含关键词", variable=self.filter_mode, value="remove").pack(side="left", padx=2)

        # 替换
        self.replace_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.replace_frame, text="查找：").pack(side="left")
        self.replace_old = tk.StringVar(value="")
        ttk.Entry(self.replace_frame, textvariable=self.replace_old, width=15).pack(side="left", padx=4)
        ttk.Label(self.replace_frame, text="替换为：").pack(side="left")
        self.replace_new = tk.StringVar(value="")
        ttk.Entry(self.replace_frame, textvariable=self.replace_new, width=15).pack(side="left", padx=4)

        # 前后缀
        self.affix_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.affix_frame, text="前缀：").pack(side="left")
        self.affix_pre = tk.StringVar(value="")
        ttk.Entry(self.affix_frame, textvariable=self.affix_pre, width=12).pack(side="left", padx=4)
        ttk.Label(self.affix_frame, text="后缀：").pack(side="left")
        self.affix_suf = tk.StringVar(value="")
        ttk.Entry(self.affix_frame, textvariable=self.affix_suf, width=12).pack(side="left", padx=4)

        # 大小写
        self.case_frame = ttk.Frame(self.param_frame)
        self.case_mode = tk.StringVar(value="upper")
        ttk.Radiobutton(self.case_frame, text="大写", variable=self.case_mode, value="upper").pack(side="left", padx=2)
        ttk.Radiobutton(self.case_frame, text="小写", variable=self.case_mode, value="lower").pack(side="left", padx=2)
        ttk.Radiobutton(self.case_frame, text="首字母大写", variable=self.case_mode, value="title").pack(side="left", padx=2)

        # 编码转换
        self.encoding_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.encoding_frame, text="目标编码：").pack(side="left")
        self.enc_target = tk.StringVar(value="utf-8-sig")
        for enc, label in [("utf-8-sig", "UTF-8(带BOM)"), ("utf-8", "UTF-8"), ("gbk", "GBK")]:
            ttk.Radiobutton(self.encoding_frame, text=label, variable=self.enc_target, value=enc).pack(side="left", padx=4)

        # 输出编码（通用）
        self.out_enc_frame = ttk.Frame(self.param_frame)
        ttk.Label(self.out_enc_frame, text="输出编码：").pack(side="left")
        self.out_enc = tk.StringVar(value="utf-8-sig")
        for enc, label in [("utf-8-sig", "UTF-8(带BOM)"), ("utf-8", "UTF-8"), ("gbk", "GBK")]:
            ttk.Radiobutton(self.out_enc_frame, text=label, variable=self.out_enc, value=enc).pack(side="left", padx=4)

        # 默认显示去重参数
        self._show_params("dedupe")

        # 开始按钮
        frm5 = ttk.Frame(self)
        frm5.pack(fill="x", pady=4)
        self.btn_run = ttk.Button(frm5, text="开始处理", command=self.run)
        self.btn_run.pack(side="left", padx=2)

        # 进度条
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=4)

        # 日志
        self.log_text = tk.Text(self, height=10, state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=4)

        # 操作类型切换时更新参数区
        self.op_var.trace_add("write", lambda *a: self._show_params(self.op_var.get()))

    def _show_params(self, op):
        """根据操作类型显示对应参数区。"""
        for frame in [self.split_frame, self.filter_frame, self.replace_frame,
                      self.affix_frame, self.case_frame, self.encoding_frame, self.out_enc_frame]:
            frame.pack_forget()
        if op == "split":
            self.split_frame.pack(fill="x", pady=2)
            self.out_enc_frame.pack(fill="x", pady=2)
        elif op == "filter":
            self.filter_frame.pack(fill="x", pady=2)
            self.out_enc_frame.pack(fill="x", pady=2)
        elif op == "replace":
            self.replace_frame.pack(fill="x", pady=2)
            self.out_enc_frame.pack(fill="x", pady=2)
        elif op == "affix":
            self.affix_frame.pack(fill="x", pady=2)
            self.out_enc_frame.pack(fill="x", pady=2)
        elif op == "case":
            self.case_frame.pack(fill="x", pady=2)
            self.out_enc_frame.pack(fill="x", pady=2)
        elif op == "encoding":
            self.encoding_frame.pack(fill="x", pady=2)
        else:  # dedupe
            self.out_enc_frame.pack(fill="x", pady=2)

    def log(self, msg):
        """日志入口：worker 线程调用时投递事件队列，主线程直接写（待修改清单 #1）。"""
        if threading.current_thread() is threading.main_thread():
            self._log_direct(msg)
        else:
            self._events.put(lambda m=msg: self._log_direct(m))

    def _log_direct(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        # 行数上限：批量处理的长日志持续膨胀会拖慢滚动与重绘（审计 P2）
        try:
            total_lines = int(self.log_text.index("end-1c").split(".")[0])
            if total_lines > 500:
                self.log_text.delete("1.0", "{}.0".format(total_lines - 500 + 1))
        except Exception:
            pass
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.update_idletasks()

    def pick_files(self):
        files = filedialog.askopenfilenames(
            title="选择文本文件", filetypes=[("所有文件", "*.*"), ("文本文件", "*.txt")]
        )
        if files:
            self.files = list(files)
            self.var_files.set(f"已选 {len(self.files)} 个文件")

    def pick_folder(self):
        folder = filedialog.askdirectory(title="选择包含文本文件的文件夹")
        if not folder:
            return
        try:
            self.files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if f.lower().endswith((".txt", ".csv", ".log"))
            ]
        except OSError as exc:
            messagebox.showerror("读取文件夹失败", "{}\n\n{}".format(folder, exc))
            return
        self.var_files.set(f"文件夹: {folder} ({len(self.files)} 个文件)")

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.out_dir = d
            self.var_out.set(d)

    def run(self):
        # 运行中再次点击 = 请求取消（逐文件边界生效）
        thread = getattr(self, "_run_thread", None)
        if thread is not None and thread.is_alive():
            self._cancel_event.set()
            self.log("正在取消（等待当前文件完成）...")
            return

        if not self.files:
            messagebox.showwarning("提示", "请先选择文本文件。")
            return
        out_dir = self.out_dir or os.path.dirname(self.files[0]) or "."
        os.makedirs(out_dir, exist_ok=True)

        # 选项在主线程一次性快照：_process_one 原本在循环内读多个 Tk 变量，
        # worker 中禁止触碰 Tk（待修改清单 #1）
        params = dict(
            split_lines=self.split_var.get(),
            filter_kw=self.filter_kw.get(),
            filter_mode=self.filter_mode.get(),
            replace_old=self.replace_old.get(),
            replace_new=self.replace_new.get(),
            affix_pre=self.affix_pre.get(),
            affix_suf=self.affix_suf.get(),
            case_mode=self.case_mode.get(),
            enc_target=self.enc_target.get(),
        )
        op = self.op_var.get()
        enc = self.out_enc.get()
        files = list(self.files)
        total = len(files)

        self._cancel_event = threading.Event()
        self._events = queue.Queue()
        self.btn_run.configure(text="⨂ 取消处理")
        self.progress["value"] = 0
        self.log("开始处理...")

        def progress(cur, total_count):
            # worker 只投递，进度条由主线程更新
            self._events.put(lambda c=cur, t=total_count: self._update_progress(c, t))

        def _worker():
            failed = 0
            try:
                for idx, src in enumerate(files, start=1):
                    if self._cancel_event.is_set():
                        raise BatchCancelled(done=idx - 1)  # 取消在逐文件边界
                    try:
                        self._process_one(src, out_dir, op, enc, params)
                    except Exception as e:  # noqa: BLE001
                        failed += 1
                        self.log(f"[失败] {os.path.basename(src)} 错误: {e}")
                    progress(idx, total)
            except BatchCancelled as exc:
                done = exc.done
                self._events.put(
                    lambda d=done, f=failed, t=total:
                    self._finish(cancelled=True, done=d, failed=f, total=t))
                return
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                self._events.put(lambda m=msg: self._finish(error=m))
                return
            self._events.put(lambda f=failed: self._finish(failed=f))

        self._run_thread = threading.Thread(target=_worker, daemon=True)
        self._run_thread.start()
        # 轮询链从主线程启动（worker 不跨线程调 after）
        self.after(80, self._drain_events)

    def _drain_events(self):
        """主线程排空 worker 事件队列；线程存活或队列非空则续订下一轮。"""
        while True:
            try:
                fn = self._events.get_nowait()
            except queue.Empty:
                break
            fn()
        thread = getattr(self, "_run_thread", None)
        if (thread is not None and thread.is_alive()) or not self._events.empty():
            self.after(80, self._drain_events)

    def _update_progress(self, cur, total):
        self.progress["maximum"] = total
        self.progress["value"] = cur

    def _finish(self, cancelled=False, done=0, failed=0, total=0, error=None):
        """收尾（仅主线程执行）：恢复按钮；三档结果反馈逻辑与原同步版一致。"""
        self.btn_run.configure(text="开始处理", state="normal")
        if error is not None:
            self.log(f"发生错误: {error}")
            messagebox.showerror("错误", error)
            return
        if cancelled:
            self.log(f"已取消：完成 {done}/{total}，失败 {failed}。")
            messagebox.showwarning(
                "已取消", f"已取消处理：完成 {done}/{total}，失败 {failed}。")
            return
        # 按实际结果反馈：此前全部失败也弹"处理完成！"，与日志矛盾（审计 P1◐）
        if failed == 0:
            self.log("全部处理完成。")
            messagebox.showinfo("完成", "处理完成！")
        elif failed < total:
            self.log(f"处理结束：成功 {total - failed}/{total}，失败 {failed}。")
            messagebox.showwarning(
                "部分失败", f"处理结束：成功 {total - failed} 个，失败 {failed} 个，详见日志。"
            )
        else:
            self.log(f"处理失败：全部 {total} 个文件均失败。")
            messagebox.showerror("处理失败", f"全部 {total} 个文件均处理失败，详见日志。")

    def _process_one(self, src, out_dir, op, enc, params=None):
        """处理单个文件。params 为 run() 在主线程快照的选项字典。

        worker 内禁止触碰 Tk 变量：params 缺失（旧式直接调用，主线程）时
        在此一次性补齐快照，函数体一律只读 params。
        """
        if params is None:
            params = dict(
                split_lines=self.split_var.get(),
                filter_kw=self.filter_kw.get(),
                filter_mode=self.filter_mode.get(),
                replace_old=self.replace_old.get(),
                replace_new=self.replace_new.get(),
                affix_pre=self.affix_pre.get(),
                affix_suf=self.affix_suf.get(),
                case_mode=self.case_mode.get(),
                enc_target=self.enc_target.get(),
            )
        base = os.path.splitext(os.path.basename(src))[0]
        if op == "dedupe":
            dst = os.path.join(out_dir, base + "_去重.txt")
            orig, uniq = dedupe_lines(src, dst, output_encoding=enc)
            self.log(f"[去重] {os.path.basename(src)}: {orig} -> {uniq} 行 -> {os.path.basename(dst)}")
        elif op == "split":
            try:
                n = int(params["split_lines"])
            except ValueError:
                raise ValueError("每个文件行数必须是整数")
            if n <= 0:
                raise ValueError("每个文件行数必须是正整数")
            created = split_file_by_lines(src, out_dir, n, output_encoding=enc)
            self.log(f"[拆分] {os.path.basename(src)} -> {len(created)} 个文件")
        elif op == "filter":
            kw = params["filter_kw"]
            if not kw:
                raise ValueError("请输入过滤关键词")
            dst = os.path.join(out_dir, base + "_过滤.txt")
            mode = params["filter_mode"]
            orig, kept = filter_lines(src, dst, kw, mode=mode, output_encoding=enc)
            self.log(f"[过滤] {os.path.basename(src)}: {orig} -> {kept} 行 -> {os.path.basename(dst)}")
        elif op == "replace":
            old = params["replace_old"]
            if not old:
                raise ValueError("请输入要查找的内容")
            dst = os.path.join(out_dir, base + "_替换.txt")
            count = replace_keyword(src, dst, old, params["replace_new"], output_encoding=enc)
            self.log(f"[替换] {os.path.basename(src)}: 替换 {count} 处 -> {os.path.basename(dst)}")
        elif op == "affix":
            dst = os.path.join(out_dir, base + "_加前后缀.txt")
            rows = add_prefix_suffix(
                src, dst, params["affix_pre"], params["affix_suf"], output_encoding=enc)
            self.log(f"[前后缀] {os.path.basename(src)}: 处理 {rows} 行 -> {os.path.basename(dst)}")
        elif op == "case":
            dst = os.path.join(out_dir, base + "_大小写.txt")
            rows = convert_case(src, dst, params["case_mode"], output_encoding=enc)
            self.log(f"[大小写] {os.path.basename(src)}: 处理 {rows} 行 -> {os.path.basename(dst)}")
        elif op == "encoding":
            dst = os.path.join(out_dir, base + "_转码.txt")
            rows = convert_encoding_file(src, dst, target_encoding=params["enc_target"])
            self.log(f"[编码转换] {os.path.basename(src)} -> {os.path.basename(dst)} ({rows} 行)")


# =====================================================================
# 主窗口
# =====================================================================

class App:
    UI_STATE_KEY = "text_tools"

    def __init__(self, root):
        self.root = root
        root.title("文本处理工具")
        root.geometry("680x620")
        root.minsize(560, 480)

        # 三卡片切换
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.txt_card = TxtMergeCard(self.notebook)
        self.csv_card = CsvConvertCard(self.notebook)
        self.tools_card = TextToolsCard(self.notebook)

        self.notebook.add(self.txt_card, text="TXT 合并")
        self.notebook.add(self.csv_card, text="CSV 转换")
        self.notebook.add(self.tools_card, text="文本工具")

    def _collect_ui_state(self):
        """窗口几何 + 各卡片选项（轻量标量；文件列表属业务数据不落盘）。"""
        state = {"geometry": wt_ui_state.capture_window_geometry(self.root)}
        state.update({
            "txt_join": self.txt_card.join_var.get(),
            "txt_headers": self.txt_card.header_var.get(),
            "txt_separator": self.txt_card.sep_var.get(),
            "txt_remove_empty": self.txt_card.empty_var.get(),
            "txt_encoding": self.txt_card.enc_var.get(),
            "csv_conv": self.csv_card.conv_var.get(),
            "csv_merge": self.csv_card.merge_var.get(),
            "csv_merged_header": self.csv_card.merge_single_header.get(),
            "csv_txt_encoding": self.csv_card.txt_enc_var.get(),
            "csv_out_dir": self.csv_card.out_dir or "",
            "tools_op": self.tools_card.op_var.get(),
            "tools_out_encoding": self.tools_card.out_enc.get(),
            "tools_split_lines": self.tools_card.split_var.get(),
            "tools_filter_kw": self.tools_card.filter_kw.get(),
            "tools_filter_mode": self.tools_card.filter_mode.get(),
            "tools_replace_old": self.tools_card.replace_old.get(),
            "tools_replace_new": self.tools_card.replace_new.get(),
            "tools_affix_pre": self.tools_card.affix_pre.get(),
            "tools_affix_suf": self.tools_card.affix_suf.get(),
            "tools_case_mode": self.tools_card.case_mode.get(),
            "tools_enc_target": self.tools_card.enc_target.get(),
            "tools_out_dir": self.tools_card.out_dir or "",
        })
        return state

    def _apply_ui_state(self, state):
        if not state:
            return

        def _set(var, key):
            if key in state:
                try:
                    var.set(state[key])
                except Exception:
                    pass

        _set(self.txt_card.join_var, "txt_join")
        _set(self.txt_card.header_var, "txt_headers")
        _set(self.txt_card.sep_var, "txt_separator")
        _set(self.txt_card.empty_var, "txt_remove_empty")
        _set(self.txt_card.enc_var, "txt_encoding")
        _set(self.csv_card.conv_var, "csv_conv")
        _set(self.csv_card.merge_var, "csv_merge")
        _set(self.csv_card.merge_single_header, "csv_merged_header")
        _set(self.csv_card.txt_enc_var, "csv_txt_encoding")
        if state.get("csv_out_dir"):
            self.csv_card.out_dir = state["csv_out_dir"]
            self.csv_card.var_out.set(state["csv_out_dir"])
        _set(self.tools_card.op_var, "tools_op")
        _set(self.tools_card.out_enc, "tools_out_encoding")
        _set(self.tools_card.split_var, "tools_split_lines")
        _set(self.tools_card.filter_kw, "tools_filter_kw")
        _set(self.tools_card.filter_mode, "tools_filter_mode")
        _set(self.tools_card.replace_old, "tools_replace_old")
        _set(self.tools_card.replace_new, "tools_replace_new")
        _set(self.tools_card.affix_pre, "tools_affix_pre")
        _set(self.tools_card.affix_suf, "tools_affix_suf")
        _set(self.tools_card.case_mode, "tools_case_mode")
        _set(self.tools_card.enc_target, "tools_enc_target")
        if state.get("tools_out_dir"):
            self.tools_card.out_dir = state["tools_out_dir"]
            self.tools_card.var_out.set(state["tools_out_dir"])
        wt_ui_state.apply_window_geometry(self.root, state.get("geometry"))

    def _on_close(self):
        try:
            wt_ui_state.save(self.UI_STATE_KEY, self._collect_ui_state())
        finally:
            self.root.destroy()


def main():
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = App(root)
    app._apply_ui_state(wt_ui_state.load(App.UI_STATE_KEY))
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
