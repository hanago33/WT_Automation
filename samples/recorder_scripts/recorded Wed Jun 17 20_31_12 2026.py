# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	click(u"||Pane->文件||Pane->配置||Button%(-43.14,-7.84)")
	with UIPath(u"配置 - 常规||Window->||Tree"):
		click(u"常规||TreeItem%(4.26,-48.48)")
	click(u"配置 - 常规||Window->||Tree->投影||TreeItem%(29.79,-36.36)")
	with UIPath(u"配置 - 投影||Window"):
		drag_and_drop(u"从文件加载...||Button%(-31.75,55.32)", u"从文件加载...||Button%(-26.46,55.32)")
	with UIPath(u"配置 - 投影||Window->打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit%(-72.16,-64.29)")
		double_click(u"文件名(N):||Edit%(-72.16,-64.29)")
		send_keys("{VK_CONTROL down}")
		send_keys("{v down}""{VK_CONTROL up}""{v up}")
	with UIPath(u"配置 - 投影||Window"):
		double_click(u"打开||Window->打开(O)||Button%(-46.05,-22.73)")
		click(u"应用||Button%(-2.63,-27.45)")
		click(u"确定||Button%(25.00,-7.84)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		double_click(u"显示隐藏的图标||Button")
		double_click(u"显示隐藏的图标||Button%(-22.22,21.95)")
		double_click(u"显示隐藏的图标||Button")
		click(u"显示隐藏的图标||Button%(18.52,-34.15)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(41.18,11.76)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
