# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"||Window"):
	click(u"||Custom->||Button->||Image#[||Custom->||Button->||Image,4]%(8.00,-32.00)")
	click(u"||Custom#[1,3]%(80.06,-87.69)")
	click(u"创建一个新的气象对象||Window%(48.46,10.82)")
	send_keys("Test""{ENTER}")
	click(u"创建一个新的气象对象||Window%(49.59,22.69)")
	click(u"创建一个新的气象对象||Window%(84.07,39.31)")
	click(u"创建一个新的气象对象||Window->||Window->MTD.Wpf.Controls.Enumerations.MTDLocalizedEnumValue||ListItem->私有||Text")
	click(u"创建一个新的气象对象||Window%(-24.39,62.01)")
	send_keys("120")
	click(u"创建一个新的气象对象||Window%(36.91,60.69)")
	send_keys("35")
	click(u"创建一个新的气象对象||Window%(66.02,86.81)")
	click(u"||Custom#[1,3]%(-39.95,49.38)")
	with UIPath(u"打开||Window->文件名(N):||ComboBox"):
		click(u"文件名(N):||Edit%(-39.75,-38.46)")
		mouse_wheel(-3.0)
	click(u"打开||Window%(-22.38,40.33)")
	double_click(u"打开||Window%(1.41,-33.89)")
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List%(-28.21,-44.91)")
	click(u"打开||Window->打开(O)||SplitButton%(-64.47,-50.00)")
	click(u"导入时间序列文件||Window%(-54.32,-78.33)")
	click(u"导入时间序列文件||Window->||Window->MTD.Wpf.Controls.Enumerations.MTDLocalizedEnumValue||ListItem->Tabulation||Text%(-31.79,38.71)")
	click(u"导入时间序列文件||Window%(-61.19,-72.18)")
	click(u"导入时间序列文件||Window%(-59.99,-65.35)")
	click(u"导入时间序列文件||Window%(-59.13,-52.36)")
	click(u"导入时间序列文件||Window%(-96.26,-39.37)")
	click(u"导入时间序列文件||Window%(-54.23,-32.95)")
	click(u"导入时间序列文件||Window%(-58.79,-34.45)")
	click(u"导入时间序列文件||Window%(-54.66,-24.88)")
	mouse_wheel(-19.0)
	click(u"导入时间序列文件||Window->||Window->yyyy/MM/dd HH:mm||ListItem->yyyy/MM/dd HH:mm||Text%(11.59,100.00)")
	click(u"导入时间序列文件||Window%(-24.84,-60.70)")
	click(u"导入时间序列文件||Window%(-31.11,-50.44)")
	click(u"导入时间序列文件||Window%(-80.28,5.74)")
	click(u"导入时间序列文件||Window%(-26.04,-60.42)")
	click(u"导入时间序列文件||Window->||Window->DateTime||ListItem->日期时间||Text%(1.29,-33.33)")
	click(u"导入时间序列文件||Window%(8.85,-60.97)")
	click(u"导入时间序列文件||Window->||Window->WindSpeed||ListItem->平均风速||Text%(-73.07,-58.06)")
	click(u"导入时间序列文件||Window%(5.16,-54.82)")
	click(u"导入时间序列文件||Window%(39.36,-59.06)")
	click(u"导入时间序列文件||Window->||Window->Direction||ListItem->风向||Text%(-51.87,-51.61)")
	click(u"导入时间序列文件||Window%(24.75,-54.00)")
	click(u"导入时间序列文件||Window%(-75.55,8.89)")
	click(u"导入时间序列文件||Window%(-78.56,18.46)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(22.22,9.76)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(-55.88,-2.94)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
