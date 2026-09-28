# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->投影:||ComboBox"):
		click(u"打开||Button")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->投影:||ComboBox->投影:||List->垂直滚动条||ScrollBar"):
		drag_and_drop(u"位置||Thumb", u"位置||Thumb")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->投影:||ComboBox->投影:||List"):
		drag_and_drop(u"垂直滚动条||ScrollBar->向下翻页||Button", u"垂直滚动条||ScrollBar->位置||Thumb")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"投影:||ComboBox->投影:||List->Gauss Krueger (3 degree zones)||ListItem")
		click(u"带号:||ComboBox->打开||Button#[带号:||ComboBox,0]")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->带号:||ComboBox->带号:||List->垂直滚动条||ScrollBar"):
		drag_and_drop(u"位置||Thumb", u"位置||Thumb")
		mouse_wheel(-2.0)
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"带号:||ComboBox->带号:||List->Zone 40 (118.5E - 121.5E)||ListItem")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->基准:||ComboBox"):
		click(u"打开||Button")
		mouse_wheel(2.0)
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->基准:||ComboBox->基准:||List->垂直滚动条||ScrollBar"):
		drag_and_drop(u"向下翻页||Button", u"向下翻页||Button")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"基准:||ComboBox->基准:||List->垂直滚动条||ScrollBar->向下翻页||Button")
		click(u"确定||Button")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button")

with UIPath(u"上下文||Menu"):
	click(u"Stop recording\t\tCTRL+ALT+R||MenuItem")
