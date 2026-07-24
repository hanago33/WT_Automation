# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"||Window"):
	click(u"||Custom->||Button->||Image#[0,3]%(-48.00,-28.00)")
	click(u"||Custom#[1,2]%(-46.68,-87.96)")
	click(u"||Custom#[1,2]%(-36.59,-86.59)")
	with UIPath(u"打开||Window"):
		click(u"文件名(N):||ComboBox->文件名(N):||Edit%(-78.10,-23.08)")
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->TEST1_Project_43.tif||ListItem%(-38.46,-1.72)")
	click(u"打开||Window->打开(O)||Button%(-31.58,18.18)")
	click(u"||Custom#[1,2]%(-37.14,-67.72)")
	click(u"||Window->MTD.Wpf.Controls.Enumerations.MTDLocalizedEnumValue||ListItem->私有||Text%(-32.75,25.81)")
	click(u"||Custom#[1,2]%(-48.08,-59.51)")
	click(u"||Window->Orography||ListItem->地形||Text%(-69.01,19.35)")
	click(u"||Custom#[1,2]%(-88.51,-40.49)")
	send_keys("cgcs""{ENTER}")
	click(u"||Custom#[1,2]%(-62.16,-18.19)")
	click(u"||Custom#[1,2]%(-39.95,51.57)")
	send_keys("{VK_CONTROL down}""{VK_MENU down}""{r down}""{VK_CONTROL up}""{r up}""{VK_MENU up}""{VK_CONTROL down}""{VK_MENU down}""{r down}""{VK_CONTROL up}""{r up}""{VK_MENU up}")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(-25.93,-12.20)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(-52.94,-32.35)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
