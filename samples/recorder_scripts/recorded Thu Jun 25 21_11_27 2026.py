# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"||Window"):
	click(u"||Custom->||Button->||Image#[||Custom->||Button->||Image,4]%(20.00,-16.00)")
	click(u"||Custom#[1,3]%(80.61,-86.32)")
	click(u"创建一个新的气象对象||Window%(75.12,11.35)")
	click(u"创建一个新的气象对象||Window%(70.89,26.91)")
	click(u"创建一个新的气象对象||Window%(84.72,41.69)")
	click(u"创建一个新的气象对象||Window->||Window->MTD.Wpf.Controls.Enumerations.MTDLocalizedEnumValue||ListItem->私有||Text%(-90.61,51.61)")
	click(u"创建一个新的气象对象||Window%(-48.13,61.74)")
	click(u"创建一个新的气象对象||Window%(43.58,60.16)")
	click(u"创建一个新的气象对象||Window%(65.53,86.54)")
	click(u"||Custom#[1,3]%(-40.73,49.66)")
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->1831-20230402-20240524-tim.txt||ListItem->名称||Edit%(56.59,-50.00)")
	click(u"打开||Window->打开(O)||SplitButton%(-13.16,45.45)")
	click(u"导入时间序列文件||Window%(-54.40,-81.34)")
	click(u"导入时间序列文件||Window->||Window->MTD.Wpf.Controls.Enumerations.MTDLocalizedEnumValue||ListItem->Tabulation||Text%(-22.54,-38.71)")
	click(u"导入时间序列文件||Window%(-72.20,7.25)")
	click(u"导入时间序列文件||Window%(-72.20,20.37)")
	click(u"导入时间序列文件||Window%(-25.96,-58.65)")
	click(u"导入时间序列文件||Window->||Window->DateTime||ListItem->日期时间||Text%(10.32,33.33)")
	click(u"导入时间序列文件||Window%(9.71,-59.88)")
	click(u"导入时间序列文件||Window->||Window->WindSpeed||ListItem->平均风速||Text%(-63.47,-12.90)")
	click(u"导入时间序列文件||Window%(41.08,-59.60)")
	click(u"导入时间序列文件||Window->||Window->Direction||ListItem->风向||Text%(-70.89,-38.71)")
	click(u"导入时间序列文件||Window%(-62.83,-51.95)")
	click(u"导入时间序列文件||Window%(0.26,-52.08)")
	click(u"导入时间序列文件||Window%(33.78,-53.59)")
	click(u"导入时间序列文件||Window%(78.38,93.64)")
	click(u"||Custom#[1,3]%(-70.52,93.98)")
	click(u"||Custom#[1,3]%(-97.58,-96.58)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(-44.12,-20.59)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
