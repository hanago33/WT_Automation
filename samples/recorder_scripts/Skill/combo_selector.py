# encoding: utf-8
"""
通用下拉选择 Skill 模块
用于处理 WT 自动化中的下拉框选择（包含滚动操作）
"""
import time
from pywinauto_recorder.player import *


def select_from_combo(combo_path, item_text, scroll_times=-3):
    """
    从下拉框中选择指定项（自动滚动查找）
    
    :param combo_path: 下拉框的 UIPath 字符串
    :param item_text: 要选择的项的文本
    :param scroll_times: 滚动次数，负数向下滚，正数向上滚，默认 -3
    """
    with UIPath(combo_path):
        click(u"打开||Button")
        time.sleep(0.5)
        
        # 尝试滚动查找
        for i in range(abs(scroll_times)):
            mouse_wheel(scroll_times / abs(scroll_times) * 1.5)
            time.sleep(0.3)
            
        # 尝试点击目标项
        click(u"{}||ListItem".format(item_text))


def scroll_and_select_from_combo(combo_path, item_text, direction="down", steps=5):
    """
    更精细的滚动选择控制
    
    :param combo_path: 下拉框的 UIPath 字符串
    :param item_text: 要选择的项的文本
    :param direction: 滚动方向，"down" 或 "up"
    :param steps: 滚动次数
    """
    with UIPath(combo_path):
        click(u"打开||Button")
        time.sleep(0.5)
        
        scroll_value = -1.5 if direction == "down" else 1.5
        
        for i in range(steps):
            mouse_wheel(scroll_value)
            time.sleep(0.3)
            try:
                click(u"{}||ListItem".format(item_text))
                return
            except Exception:
                continue
                
        # 最后再尝试一次
        click(u"{}||ListItem".format(item_text))
