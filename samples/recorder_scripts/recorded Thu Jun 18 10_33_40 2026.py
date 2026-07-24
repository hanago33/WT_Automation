# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	click(u"||Pane->文件||Pane->配置||Button%(-62.75,-7.84)")
	with UIPath(u"配置 - 常规||Window->||Tree"):
		double_click(u"常规||TreeItem%(-12.77,-24.24)")
	double_click(u"配置 - 常规||Window->||Tree->投影||TreeItem%(4.26,-36.36)")
	click(u"配置 - 投影||Window%(1.27,-74.38)")
	with UIPath(u"配置 - 投影||Window"):
		click(u"打开||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List%(-86.76,-85.23)")
		click(u"打开||Window%(71.83,88.70)")
		drag_and_drop(u"应用||Button%(3.95,-23.53)", u"应用||Button%(3.95,-23.53)")
		click(u"应用||Button%(-10.53,-19.61)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(-59.26,26.83)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(58.82,5.88)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
