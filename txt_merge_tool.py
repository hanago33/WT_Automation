# encoding: utf-8
"""
txt_merge_tool.py —— 多个 txt 文档合并工具

功能：
  - 选择多个 txt 文件 / 文件夹，按列表顺序合并成一个 txt 文档
  - 合并方式：直接接在末尾 / 新起一行再接
  - 可选：文件名作为章节标题、自定义分隔符、去重空行、按文件名排序
  - 输出编码可选：UTF-8 / GBK
  - 支持拖拽添加文件（需安装 tkinterdnd2，未安装时自动降级为按钮添加）
  - 保持每个文件的原始内容与格式不变（含编码、换行符、空行等）

用法：
  1. 双击运行，或命令行执行：python txt_merge_tool.py
  2. 点击“添加文件/添加文件夹”选择文件，或用“拖拽”添加
  3. 用“上移/下移/排序”调整合并顺序
  4. 按需勾选章节标题、分隔符、去空行等选项
  5. 点击“合并”选择输出文件路径，完成合并
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

# 常见文本编码，按优先级尝试解码
_ENCODINGS = ["utf-8", "gbk", "gb18030", "utf-16", "big5", "latin-1"]

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


def read_text_file(path):
    """读取文本文件内容，自动探测编码，返回 (内容, 编码)。"""
    with open(path, "rb") as f:
        raw = f.read()
    # 优先尝试 utf-8-sig（兼容带 BOM 的 utf-8）
    for enc in ["utf-8-sig"] + _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底：latin-1 永不失败，保证不丢字节
    return raw.decode("latin-1"), "latin-1"


def merge_txt_files(
    file_paths,
    output_path,
    newline_between=False,
    add_headers=False,
    separator="",
    remove_empty_lines=False,
    output_encoding="utf-8-sig",
):
    """按顺序合并多个 txt 文件到输出文件，保持内容原样。

    参数：
      newline_between    : True 时在每个文件之间插入一个换行（新起一行再接）
      add_headers        : True 时在每个文件内容前插入一行文件名作为章节标题
      separator          : 非空时在每个文件之间插入该自定义分隔行
      remove_empty_lines : True 时删除所有空行
      output_encoding    : 输出编码（utf-8-sig / gbk 等）
    """
    parts = []
    for i, path in enumerate(file_paths):
        content, _ = read_text_file(path)

        # 文件之间的衔接（换行 / 分隔符）
        if i > 0:
            if separator:
                parts.append(separator + "\n")
            elif newline_between:
                parts.append("\n")

        # 章节标题
        if add_headers:
            parts.append(os.path.basename(path) + "\n")

        parts.append(content)

    merged = "".join(parts)

    # 去重空行
    if remove_empty_lines:
        merged = "\n".join(line for line in merged.split("\n") if line.strip() != "")

    with open(output_path, "w", encoding=output_encoding, newline="") as f:
        f.write(merged)
    return len(file_paths), len(merged)


class OleDropTarget:
    """基于 pywin32 的 OLE 文件拖拽目标（IDropTarget 实现）。"""

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

    # ---- IDropTarget 接口 ----
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
            # 从数据对象中提取文件列表
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


class TxtMergeApp:
    def __init__(self, root):
        self.root = root
        root.title("TXT 合并工具")
        root.geometry("620x560")
        root.minsize(520, 420)

        self.files = []  # 待合并文件列表

        # 顶部说明
        tip = tk.Label(
            root,
            text="按列表顺序合并多个文本文件，内容格式不变。可拖拽文件到下方列表。",
            anchor="w",
            justify="left",
            fg="#555555",
        )
        tip.pack(fill="x", padx=10, pady=(10, 4))

        # 文件列表
        frame = tk.Frame(root)
        frame.pack(fill="both", expand=True, padx=10, pady=4)

        self.listbox = tk.Listbox(frame, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        # 操作按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", padx=10, pady=6)

        tk.Button(btn_frame, text="添加文件", command=self.add_files).pack(side="left", padx=2)
        tk.Button(btn_frame, text="添加文件夹", command=self.add_folder).pack(side="left", padx=2)
        tk.Button(btn_frame, text="移除选中", command=self.remove_selected).pack(side="left", padx=2)
        tk.Button(btn_frame, text="清空列表", command=self.clear_list).pack(side="left", padx=2)
        tk.Button(btn_frame, text="上移", command=lambda: self.move(-1)).pack(side="left", padx=2)
        tk.Button(btn_frame, text="下移", command=lambda: self.move(1)).pack(side="left", padx=2)
        tk.Button(btn_frame, text="按文件名排序", command=self.sort_by_name).pack(side="left", padx=2)

        # 合并方式选项
        opt_frame = tk.Frame(root)
        opt_frame.pack(fill="x", padx=10, pady=2)
        tk.Label(opt_frame, text="文件之间衔接方式：").pack(side="left")
        self.join_var = tk.StringVar(value="direct")
        tk.Radiobutton(
            opt_frame, text="直接接在末尾", variable=self.join_var, value="direct"
        ).pack(side="left", padx=4)
        tk.Radiobutton(
            opt_frame, text="新起一行再接", variable=self.join_var, value="newline"
        ).pack(side="left", padx=4)

        # 高级选项
        adv_frame = tk.Frame(root)
        adv_frame.pack(fill="x", padx=10, pady=2)

        self.header_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            adv_frame, text="文件名作为章节标题", variable=self.header_var
        ).pack(side="left", padx=4)

        self.empty_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            adv_frame, text="去重空行", variable=self.empty_var
        ).pack(side="left", padx=4)

        # 分隔符
        sep_frame = tk.Frame(root)
        sep_frame.pack(fill="x", padx=10, pady=2)
        tk.Label(sep_frame, text="自定义分隔符（留空则不插入）：").pack(side="left")
        self.sep_var = tk.StringVar(value="")
        tk.Entry(sep_frame, textvariable=self.sep_var, width=24).pack(side="left", padx=4)

        # 输出编码
        enc_frame = tk.Frame(root)
        enc_frame.pack(fill="x", padx=10, pady=2)
        tk.Label(enc_frame, text="输出编码：").pack(side="left")
        self.enc_var = tk.StringVar(value="utf-8-sig")
        for enc, label in [
            ("utf-8-sig", "UTF-8(带BOM)"),
            ("utf-8", "UTF-8"),
            ("gbk", "GBK"),
        ]:
            tk.Radiobutton(
                enc_frame, text=label, variable=self.enc_var, value=enc
            ).pack(side="left", padx=4)

        # 合并按钮
        merge_frame = tk.Frame(root)
        merge_frame.pack(fill="x", padx=10, pady=(6, 10))
        tk.Button(
            merge_frame, text="合并为单个 txt", command=self.merge, bg="#4CAF50", fg="white"
        ).pack(side="left", padx=2)

        self.status = tk.Label(root, text="", anchor="w", fg="#333333")
        self.status.pack(fill="x", padx=10, pady=(0, 8))

        # 拖拽支持
        self._ole_drop = None
        if _HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind("<<Drop>>", self.on_drop)
        elif _HAS_OLE_DND:
            self._ole_drop = OleDropTarget(self.root.winfo_id(), self._append_paths)
            if not self._ole_drop.register():
                self._ole_drop = None
                self.status.config(text="提示：拖拽功能初始化失败，可用按钮添加文件")
        else:
            self.status.config(text="提示：拖拽功能不可用，可用按钮添加文件")

    # ---------- 文件操作 ----------
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
        # 扫描文件夹内所有 .txt 文件（不递归子目录）
        paths = [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if name.lower().endswith(".txt")
        ]
        if not paths:
            messagebox.showinfo("提示", "该文件夹内没有找到 .txt 文件。")
            return
        self._append_paths(paths)

    def on_drop(self, event):
        # 拖拽可能带花括号包裹的路径
        raw = event.data
        paths = self.root.tk.splitlist(raw)
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

    # ---------- 合并 ----------
    def merge(self):
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
        try:
            count, total_chars = merge_txt_files(
                self.files,
                output,
                newline_between=self.join_var.get() == "newline",
                add_headers=self.header_var.get(),
                separator=self.sep_var.get().strip(),
                remove_empty_lines=self.empty_var.get(),
                output_encoding=self.enc_var.get(),
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("合并失败", f"发生错误：\n{e}")
            return
        messagebox.showinfo(
            "完成",
            f"已合并 {count} 个文件，共 {total_chars} 个字符。\n输出文件：\n{output}",
        )
        self.status.config(text=f"合并完成：{output}")


def main():
    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = TxtMergeApp(root)

    # 使用 OLE 拖拽时需要初始化 COM 并处理其消息
    if _HAS_OLE_DND and app._ole_drop is not None:
        pythoncom.CoInitialize()
        try:
            root.mainloop()
        finally:
            app._ole_drop.unregister()
            pythoncom.CoUninitialize()
    else:
        root.mainloop()


if __name__ == "__main__":
    main()
