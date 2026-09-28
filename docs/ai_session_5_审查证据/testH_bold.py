# encoding: utf-8
"""Test H: 加粗（P3 style 提交的核心交付）是否被真正验证？

test_log_lifecycle.LogTagStyleTests 用替身 _RecordingTextWidget 断言：
    self.assertIn("bold", str(widget.font("error")))
这里 widget.font() 返回的是传给 tag_configure 的**入参**，不是 Tk 的真实状态。
关键问题：真实 tk.Text 的 tag_cget(tag, "font") 会返回什么？

若真实 Tk 返回的是字体对象（如 "font5" / TkFont 名）而非含 "bold" 的字符串，
那么「用替身断言入参」与「真实控件上的效果」之间存在鸿沟 ——
测试通过不代表界面上真的加粗了。

本实验创建真实 Tk 控件验证。
"""
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import tkinter as tk

import wt_logging

root = tk.Tk()
root.withdraw()
text = tk.Text(root, font=("Consolas", 9))

wt_logging.configure_log_tags(text, dark=True, font_family="Consolas", font_size=9)

print("=== 真实 tk.Text 上的 tag 状态 ===")
for tag in ("debug", "info", "warning", "error", "success", "system"):
    fg = text.tag_cget(tag, "foreground")
    ft = text.tag_cget(tag, "font")
    print("  %-8s foreground=%-9s font=%r" % (tag, fg, ft))

err_font = text.tag_cget("error", "font")
print()
print("error 的 font 原始值 = %r" % (err_font,))
print("字符串里含 'bold' 吗？ -> %s" % ("bold" in str(err_font)))

# Tk 返回的可能是字体名，需要用 font actual 解析真实属性
try:
    import tkinter.font as tkfont
    actual = tkfont.Font(root=root, name=str(err_font)).actual()
    print("解析该字体名后的实际属性 = %r" % (actual,))
    print("  weight = %r" % (actual.get("weight"),))
    print("  真的加粗了吗？ -> %s" % (str(actual.get("weight")).lower() == "bold"))
except Exception as exc:
    print("解析字体名失败: %r" % (exc,))

# 对照：入参（替身断言看到的）
print()
print("=== 替身断言看到的入参 ===")
print("  configure_log_tags 传给 tag_configure 的 font = %r"
      % (("Consolas", 9, "bold"),))

root.destroy()
print("RESULT=H_OK")
