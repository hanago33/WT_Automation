# encoding: utf-8

from pywinauto_recorder.player import *

send_keys("{VK_CONTROL down}o""{VK_CONTROL up}""{VK_CONTROL down}o""{VK_CONTROL up}")

with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	click(u"||Pane#[控制中心||Pane->||Tree,0]")
	send_keys("{VK_CONTROL down}""{o down}""{VK_CONTROL up}""{o up}")
	with UIPath(u"打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit")
		send_keys("{VK_CONTROL down}""{v down}""{VK_CONTROL up}""{v up}")
	with UIPath(u"打开||Window"):
		click(u"打开(O)||Button")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	drag_and_drop(u"Pywinauto recorder||Button", u"Pywinauto recorder||Button")

with UIPath(u"上下文||Menu"):
	click(u"Stop recording\t\tCTRL+ALT+R||MenuItem")
