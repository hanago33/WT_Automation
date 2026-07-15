# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List")
	with UIPath(u"打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit")
	with UIPath(u"打开||Window"):
		click(u"文件名(N):||ComboBox->文件名(N):||Edit")
	with UIPath(u"打开||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List"):
		double_right_click(u"20230709 共和测量地形图.dwg||ListItem")

with UIPath(u"上下文||Menu"):
	click(u"复制文件地址(A)||MenuItem")

with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List")
	with UIPath(u"打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit")
		send_keys("{VK_CONTROL down}""{v down}""{VK_CONTROL up}""{v up}")
	with UIPath(u"打开||Window"):
		click(u"打开(O)||Button")
		click(u"打开(O)||Button")
		right_click(u"打开(O)||Button")

with UIPath(u"上下文||Menu"):
	click(u"Stop recording\t\tCTRL+ALT+R||MenuItem")
