# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	click(u"||Pane->文件||Pane->配置||Button")
	with UIPath(u"配置 - 投影||Window"):
		click(u"||Tree->投影||TreeItem")
		click(u"从文件加载...||Button")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	with UIPath(u"||Pane->Pywinauto recorder||Button"):
		right_click(u"||Image")

with UIPath(u"上下文||Menu"):
	click(u"Stop recording\t\tCTRL+ALT+R||MenuItem")
