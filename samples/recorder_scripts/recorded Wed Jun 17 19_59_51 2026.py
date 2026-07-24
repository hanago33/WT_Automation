# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	click(u"||Pane->文件||Pane->配置||Button%(-11.76,-11.76)")
	with UIPath(u"配置 - 常规||Window->||Tree"):
		click(u"->常规||TreeItem#[常规||TreeItem,0]%(-29.79,18.18)")
	click(u"配置 - 常规||Window->||Tree->投影||TreeItem#[配置 - 常规||Window->||Tree->投影||TreeItem,0]%(-4.26,-48.48)")
	with UIPath(u"配置 - 投影||Window"):
		click(u"||Tree->投影||TreeItem->投影||TreeItem%(-17.02,-18.18)")
		click(u"从文件加载...||Button%(-10.58,51.06)")
	with UIPath(u"配置 - 投影||Window->打开||Window"):
		click(u"文件名(N):||Text%(16.33,-35.71)")
	with UIPath(u"配置 - 投影||Window->打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit")
		send_keys("{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}v""{VK_CONTROL up}")


