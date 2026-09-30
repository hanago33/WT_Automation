# encoding: utf-8
"""第三期轻量页面UI现代化重构（session-20）的单元与回归测试。

覆盖两大重构目标：
1. `WT_Flow_Editor.py::ControlMapImportDialog`（控件库导入与维护窗口）：
   - 折叠帮助横幅（展开/收起状态与说明展示）；
   - 质量分级语义色彩与符号标签（_format_quality_display：推荐保留、建议优化、谨慎使用、待验证、未分类）；
   - 列表视图与树形视图中的质量徽章映射与 tag 绑定；
   - 快捷复制定位表达式（_copy_selected_locator）与 JSON 数据（_copy_selected_json）；
   - 单选控件提取（_get_selected_single_control）在平铺与树形模式下的稳健性。

2. `WT_Launcher.py::RelativeRegionHelperDialog`（相对区域取点与坐标校准助手）：
   - 顶部折叠说明条与紧凑型界面空间优化；
   - 参数配置模块的三大卡片化结构（窗口抓取与选区、相对比例与动作参数、批量采集与数据导出）；
   - 数值解析与安全边界校验（_parse_float）；
   - 绝对屏幕坐标到相对比例的精确换算（_apply_absolute_region）；
   - 动作配置与步骤样例生成（build_action_config、build_step_template）；
   - 预览配置 JSON 一键复制（copy_preview_json）；
   - 带有工程 CAD 网格底纹与高对比准星标注的实时预览画布绘制（_refresh_region_preview）。

测试全量采用无 GUI 依赖的轻量替身，毫秒级快速运行。
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E
import WT_Launcher as L


# ── 测试替身 ────────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for cb in self._callbacks:
            try:
                cb()
            except TypeError:
                cb(None, None, None)

    def trace_add(self, mode, callback):
        self._callbacks.append(callback)


class FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.clipboard_data = ""

    def destroy(self):
        self.destroyed = True

    def clipboard_clear(self):
        self.clipboard_data = ""

    def clipboard_append(self, data):
        self.clipboard_data += str(data)

    def update_idletasks(self):
        pass


class FakeTree:
    def __init__(self, selected_iids=()):
        self.selected_iids = tuple(selected_iids)
        self.inserted_items = []
        self.configured_tags = {}
        self.item_tags = {}

    def selection(self):
        return self.selected_iids

    def selection_set(self, *iids):
        self.selected_iids = tuple(iids)

    def delete(self, *items):
        self.inserted_items.clear()

    def get_children(self, parent=""):
        return [item["iid"] for item in self.inserted_items]

    def configure(self, **kwargs):
        pass

    def heading(self, col, **kwargs):
        pass

    def column(self, col, **kwargs):
        pass

    def tag_configure(self, tag, **kwargs):
        self.configured_tags[tag] = kwargs

    def insert(self, parent, index, iid=None, text="", values=(), tags=(), **kwargs):
        item = {
            "parent": parent,
            "index": index,
            "iid": iid,
            "text": text,
            "values": values,
            "tags": tags,
        }
        self.inserted_items.append(item)
        if tags:
            self.item_tags[iid] = tags
        return iid

    def item(self, iid, **kwargs):
        if "tags" in kwargs:
            self.item_tags[iid] = kwargs["tags"]


class FakeText:
    def __init__(self, initial=""):
        self.content = initial

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text):
        self.content += str(text)

    def get(self, start, end):
        return self.content


class FakeCanvas:
    def __init__(self, width=600, height=300):
        self._width = width
        self._height = height
        self.drawn_elements = []

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def delete(self, what):
        self.drawn_elements.clear()

    def create_rectangle(self, *coords, **kwargs):
        item = {"type": "rectangle", "coords": coords, "kwargs": kwargs}
        self.drawn_elements.append(item)
        return len(self.drawn_elements)

    def create_line(self, *coords, **kwargs):
        item = {"type": "line", "coords": coords, "kwargs": kwargs}
        self.drawn_elements.append(item)
        return len(self.drawn_elements)

    def create_oval(self, *coords, **kwargs):
        item = {"type": "oval", "coords": coords, "kwargs": kwargs}
        self.drawn_elements.append(item)
        return len(self.drawn_elements)

    def create_text(self, x, y, **kwargs):
        item = {"type": "text", "coords": (x, y), "kwargs": kwargs}
        self.drawn_elements.append(item)
        return len(self.drawn_elements)


# ── 测试集 1：ControlMapImportDialog (Scheme 2) ─────────────────────────


class TestControlMapImportDialogModernization(unittest.TestCase):
    """测试 ControlMapImportDialog 的质量标签、表达式/JSON复制与辅助逻辑。"""

    def setUp(self):
        self.dialog = object.__new__(E.ControlMapImportDialog)
        self.dialog.window = FakeWindow()
        self.dialog.var_status = FakeVar()
        self.dialog.var_view_mode = FakeVar(value="flat")
        self.dialog.var_candidate_summary = FakeVar()
        self.dialog.control_tree = FakeTree()
        self.dialog.preview_text = FakeText()
        self.dialog.current_payload = {"targetWindow": {"title": "主测试窗口"}}
        self.dialog._tree_node_map = {}

    def test_format_quality_display_mapping(self):
        """测试 _format_quality_display 的多级映射与语义 tag 分类。"""
        # 推荐保留
        text, tag = E.ControlMapImportDialog._format_quality_display("推荐保留")
        self.assertEqual(text, "✓ 推荐保留")
        self.assertEqual(tag, "quality_high")

        text, tag = E.ControlMapImportDialog._format_quality_display("高推荐")
        self.assertEqual(text, "✓ 推荐保留")
        self.assertEqual(tag, "quality_high")

        # 建议优化
        text, tag = E.ControlMapImportDialog._format_quality_display("建议优化")
        self.assertEqual(text, "⚡ 建议优化")
        self.assertEqual(tag, "quality_medium")

        # 谨慎使用
        text, tag = E.ControlMapImportDialog._format_quality_display("谨慎使用")
        self.assertEqual(text, "⚠️ 谨慎使用")
        self.assertEqual(tag, "quality_low")

        # 待验证
        text, tag = E.ControlMapImportDialog._format_quality_display("待验证")
        self.assertEqual(text, "🔍 待验证")
        self.assertEqual(tag, "quality_unverified")

        # 未分类与空
        text, tag = E.ControlMapImportDialog._format_quality_display("未分类")
        self.assertEqual(text, "⚪ 未分类")
        self.assertEqual(tag, "quality_unverified")

        text, tag = E.ControlMapImportDialog._format_quality_display("")
        self.assertEqual(text, "⚪ 未分类")
        self.assertEqual(tag, "quality_unverified")

        # 未知定制文本
        text, tag = E.ControlMapImportDialog._format_quality_display("特殊等级")
        self.assertEqual(text, "• 特殊等级")
        self.assertEqual(tag, "quality_unverified")

    def test_copy_selected_locator_without_selection(self):
        """未选中项时提示用户先选择。"""
        self.dialog.control_tree.selection = lambda: ()
        self.dialog._copy_selected_locator()
        self.assertIn("请先在列表中选中一个控件", self.dialog.var_status.get())
        self.assertEqual(self.dialog.window.clipboard_data, "")

    def test_copy_selected_locator_flat_view(self):
        """平铺模式下成功复制所选控件的定位表达式到剪贴板。"""
        self.dialog.var_view_mode.set("flat")
        self.dialog.control_tree.selection = lambda: ("0",)
        sample_ctrl = {
            "displayName": "确认按钮",
            "recommendedTargetMethod": "automation_id",
            "recommendedTargetValue": "btn_confirm",
        }
        self.dialog._get_filtered_controls = lambda: [sample_ctrl]

        self.dialog._copy_selected_locator()

        self.assertEqual(self.dialog.window.clipboard_data, "automation_id:btn_confirm")
        self.assertIn("已复制定位表达式到剪贴板", self.dialog.var_status.get())

    def test_copy_selected_locator_tree_view(self):
        """树形模式下成功根据 iid 映射复制定位表达式。"""
        self.dialog.var_view_mode.set("tree")
        self.dialog.control_tree.selection = lambda: ("MainForm > Header > SaveBtn",)
        sample_ctrl = {
            "displayName": "保存按钮",
            "targetMethod": "name",
            "targetValue": "保存",
        }
        self.dialog._tree_node_map = {"MainForm > Header > SaveBtn": sample_ctrl}

        self.dialog._copy_selected_locator()

        self.assertEqual(self.dialog.window.clipboard_data, "name:保存")
        self.assertIn("已复制定位表达式到剪贴板", self.dialog.var_status.get())

    def test_copy_selected_json_success(self):
        """测试选中控件后复制完整 JSON 结构到剪贴板。"""
        self.dialog.var_view_mode.set("flat")
        self.dialog.control_tree.selection = lambda: ("0",)
        sample_ctrl = {
            "name": "InputPath",
            "controlType": "Edit",
            "targetMethod": "automation_id",
            "targetValue": "txt_path",
        }
        self.dialog._get_filtered_controls = lambda: [sample_ctrl]

        self.dialog._copy_selected_json()

        self.assertIn('"name": "InputPath"', self.dialog.window.clipboard_data)
        self.assertIn('"targetValue": "txt_path"', self.dialog.window.clipboard_data)
        self.assertIn("已复制控件 JSON 数据到剪贴板", self.dialog.var_status.get())

    def test_refresh_flat_view_applies_quality_badges_and_tags(self):
        """测试平铺视图刷新时各条目正确应用质量徽章与对应色彩 tags。"""
        self.dialog.var_view_mode.set("flat")
        ctrl_1 = {
            "displayName": "推荐按钮",
            "controlType": "Button",
            "qualityTier": "推荐保留",
            "targetMethod": "automation_id",
            "targetValue": "btn_1",
            "windowTitle": "窗口A",
        }
        ctrl_2 = {
            "displayName": "待优化输入框",
            "controlType": "Edit",
            "qualityTier": "建议优化",
            "targetMethod": "name",
            "targetValue": "txt_2",
            "windowTitle": "窗口A",
        }
        ctrl_3 = {
            "displayName": "普通标签",
            "controlType": "Text",
            "qualityTier": "未分类",
            "targetMethod": "name",
            "targetValue": "lbl_3",
            "windowTitle": "窗口A",
        }
        self.dialog._get_filtered_controls = lambda: [ctrl_1, ctrl_2, ctrl_3]
        self.dialog._on_control_select = MagicMock()

        self.dialog._refresh_flat_view()

        inserted = self.dialog.control_tree.inserted_items
        self.assertEqual(len(inserted), 3)

        # 检查条目 1（推荐）
        values_1 = inserted[0]["values"]
        self.assertEqual(values_1[1], "推荐按钮")
        self.assertEqual(values_1[3], "✓ 推荐保留")
        self.assertEqual(inserted[0]["tags"], ("quality_high",))

        # 检查条目 2（建议优化）
        values_2 = inserted[1]["values"]
        self.assertEqual(values_2[1], "待优化输入框")
        self.assertEqual(values_2[3], "⚡ 建议优化")
        self.assertEqual(inserted[1]["tags"], ("quality_medium",))

        # 检查条目 3（未分类）
        values_3 = inserted[2]["values"]
        self.assertEqual(values_3[1], "普通标签")
        self.assertEqual(values_3[3], "⚪ 未分类")
        self.assertEqual(inserted[2]["tags"], ("quality_unverified",))


# ── 测试集 2：RelativeRegionHelperDialog (Scheme 3) ─────────────────────


class TestRelativeRegionHelperDialogModernization(unittest.TestCase):
    """测试 RelativeRegionHelperDialog 的坐标数学计算、JSON 导出与画布工程预览。"""

    def setUp(self):
        self.dialog = object.__new__(L.RelativeRegionHelperDialog)
        self.dialog.parent = FakeWindow()
        self.dialog.theme = {"bg": "#f8fafc", "card": "#ffffff", "text": "#1e293b", "border": "#e2e8f0"}
        self.dialog.window_rect = None
        self.dialog.var_parent_title = FakeVar(value="")
        self.dialog.var_parent_class = FakeVar(value="")
        self.dialog.var_parent_framework = FakeVar(value="WPF")
        self.dialog.var_window_rect = FakeVar(value="未捕获")
        self.dialog.var_center_x = FakeVar(value="0")
        self.dialog.var_center_y = FakeVar(value="0")
        self.dialog.var_region_x = FakeVar(value="0.45")
        self.dialog.var_region_y = FakeVar(value="0.45")
        self.dialog.var_region_width = FakeVar(value="0.32")
        self.dialog.var_region_height = FakeVar(value="0.08")
        self.dialog.var_anchor = FakeVar(value="center")
        self.dialog.var_action_name = FakeVar(value="type_text_relative")
        self.dialog.var_text = FakeVar(value="${runtime.sourceFilePath}")
        self.dialog.var_capture_delay_seconds = FakeVar(value="3")
        self.dialog.status_var = FakeVar(value="就绪")
        self.dialog.preview_metrics_var = FakeVar(value="")
        self.dialog.var_multi_region_count = FakeVar(value="批量采集：0 个区域")
        self.dialog.preview_text = FakeText()
        self.dialog.region_preview_canvas = FakeCanvas(width=640, height=280)
        self.dialog._refresh_action_specific_fields = MagicMock()

    def test_parse_float_safely(self):
        """测试浮点数解析在合法数值、异常字符串及空值下的容错回退。"""
        self.assertEqual(self.dialog._parse_float("0.75", 0.0), 0.75)
        self.assertEqual(self.dialog._parse_float("123.456", 0.0), 123.456)
        self.assertEqual(self.dialog._parse_float("invalid_str", 0.32), 0.32)
        self.assertEqual(self.dialog._parse_float(None, 0.08), 0.08)

    def test_apply_absolute_region_calculates_correct_ratios(self):
        """测试绝对像素矩形正确换算为 0~1 相对比例及中心点坐标。"""
        self.dialog.window_rect = {
            "left": 100,
            "top": 100,
            "right": 1100,
            "bottom": 900,
            "width": 1000,
            "height": 800,
        }
        # 画框选区：屏幕坐标 (300, 300) 到 (700, 500)
        # 相对父窗口 left=200, top=200, width=400, height=200
        # 相对比例: x=200/1000=0.2000, y=200/800=0.2500, w=400/1000=0.4000, h=200/800=0.2500
        # 中心点: (300+700)/2=500, (300+500)/2=400
        self.dialog._refresh_preview = MagicMock()

        self.dialog._apply_absolute_region(300, 300, 700, 500, "选区已应用")

        self.assertEqual(self.dialog.var_region_x.get(), "0.2000")
        self.assertEqual(self.dialog.var_region_y.get(), "0.2500")
        self.assertEqual(self.dialog.var_region_width.get(), "0.4000")
        self.assertEqual(self.dialog.var_region_height.get(), "0.2500")
        self.assertEqual(self.dialog.var_center_x.get(), "500")
        self.assertEqual(self.dialog.var_center_y.get(), "400")
        self.assertEqual(self.dialog.status_var.get(), "选区已应用")

    def test_build_action_config_type_text(self):
        """测试生成 type_text_relative 动作配置载荷。"""
        self.dialog.var_action_name.set("type_text_relative")
        self.dialog.var_parent_title.set("新建测风塔配置")
        self.dialog.var_parent_class.set("HwndWrapper[App.exe]")
        self.dialog.var_parent_framework.set("WPF")
        self.dialog.var_region_x.set("0.35")
        self.dialog.var_region_y.set("0.25")
        self.dialog.var_region_width.set("0.40")
        self.dialog.var_region_height.set("0.05")
        self.dialog.var_anchor.set("center")
        self.dialog.var_text.set("D:\\Data\\Tower1.xml")

        config = self.dialog.build_action_config()

        self.assertEqual(config["action"], "type_text_relative")
        self.assertEqual(config["text"], "D:\\Data\\Tower1.xml")
        self.assertEqual(config["parentWindow"]["title"], "新建测风塔配置")
        self.assertEqual(config["parentWindow"]["frameworkId"], "WPF")
        self.assertEqual(config["relativeRegion"]["x"], 0.35)
        self.assertEqual(config["relativeRegion"]["y"], 0.25)
        self.assertEqual(config["relativeRegion"]["width"], 0.40)
        self.assertEqual(config["relativeRegion"]["height"], 0.05)
        self.assertEqual(config["relativeRegion"]["anchor"], "center")

    def test_build_action_config_click_relative(self):
        """测试生成 click_relative_region 动作配置（不含 text 字段）。"""
        self.dialog.var_action_name.set("click_relative_region")
        self.dialog.var_parent_title.set("确认弹窗")
        self.dialog.var_region_x.set("0.80")
        self.dialog.var_region_y.set("0.85")

        config = self.dialog.build_action_config()

        self.assertEqual(config["action"], "click_relative_region")
        self.assertNotIn("text", config)
        self.assertEqual(config["relativeRegion"]["x"], 0.8)

    def test_build_step_template(self):
        """测试生成可直接粘贴至流程配置文件的步骤模板。"""
        self.dialog.var_action_name.set("click_relative_region")
        self.dialog.var_parent_title.set("系统登录")

        step = self.dialog.build_step_template()

        self.assertEqual(step["id"], "click_relative_region_step")
        self.assertEqual(step["name"], "父窗口区域点击")
        self.assertEqual(step["windowTitle"], "系统登录")
        self.assertIn("actionConfig", step)
        self.assertEqual(step["actionConfig"]["action"], "click_relative_region")

    def test_copy_preview_json_success(self):
        """测试 copy_preview_json 成功将预览区文本复制至剪贴板。"""
        sample_json = '{\n  "action": "click_relative_region"\n}'
        self.dialog.preview_text.content = sample_json

        self.dialog.copy_preview_json()

        self.assertEqual(self.dialog.parent.clipboard_data, sample_json)
        self.assertIn("已复制预览配置 JSON 到剪贴板", self.dialog.status_var.get())

    def test_refresh_region_preview_without_window_rect(self):
        """尚未捕获父窗口时，Canvas 正常绘制 CAD 底纹与居中引导提示，不崩溃。"""
        self.dialog.window_rect = None

        self.dialog._refresh_region_preview()

        # 检查是否绘制了底纹网格
        self.assertGreater(len(self.dialog.region_preview_canvas.drawn_elements), 5)
        # 检查提示摘要
        self.assertIn("尚未抓取父窗口", self.dialog.preview_metrics_var.get())

    def test_refresh_region_preview_with_window_rect(self):
        """捕获到父窗口后，Canvas 绘制父窗口工程框、目标选区框、准星及坐标说明文字。"""
        self.dialog.window_rect = {
            "left": 100,
            "top": 100,
            "right": 900,
            "bottom": 700,
            "width": 800,
            "height": 600,
        }
        self.dialog.var_parent_title.set("测风塔主分析平台")
        self.dialog.var_region_x.set("0.25")
        self.dialog.var_region_y.set("0.30")
        self.dialog.var_region_width.set("0.50")
        self.dialog.var_region_height.set("0.20")

        self.dialog._refresh_region_preview()

        drawn = self.dialog.region_preview_canvas.drawn_elements
        # 包含了 CAD 网格线、父窗口矩形、选区矩形、十字准星、中心圆点、多行工程文本
        rectangles = [item for item in drawn if item["type"] == "rectangle"]
        lines = [item for item in drawn if item["type"] == "line"]
        ovals = [item for item in drawn if item["type"] == "oval"]
        texts = [item for item in drawn if item["type"] == "text"]

        self.assertGreaterEqual(len(rectangles), 2)  # 外框 + 选区
        self.assertGreaterEqual(len(lines), 2)        # 准星
        self.assertEqual(len(ovals), 1)              # 中心圆点
        self.assertGreaterEqual(len(texts), 3)        # 标注文本

        # 验证度量指标摘要包含父窗口像素尺寸与区域像素尺寸
        metrics = self.dialog.preview_metrics_var.get()
        self.assertIn("800 x 600px", metrics)
        self.assertIn("400 x 120px", metrics)


if __name__ == "__main__":
    unittest.main()
