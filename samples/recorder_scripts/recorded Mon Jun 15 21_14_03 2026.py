# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane->||Pane"):
		double_click(u"Global Mapper 22 已固定||Button")

with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	with UIPath(u"||TitleBar"):
		click(u"最大化||Button")
		send_keys("{VK_CONTROL down}o""{VK_CONTROL up}")
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->2026.6.10机位.kmz||ListItem->名称||Edit")
		click(u"打开(O)||Button")
		click(u"打开(O)||Button")
