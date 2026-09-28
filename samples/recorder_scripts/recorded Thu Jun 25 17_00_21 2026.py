# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"||Window"):
	click(u"导入时间序列文件||Window%(-77.18,22.69)")
	click(u"导入时间序列文件||Window%(80.10,92.28)")
	click(u"||Custom#[1,3]%(-39.41,48.15)")
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->1831-20230402-20240524-TI.txt||ListItem%(-40.42,48.78)")
	click(u"打开||Window->打开(O)||SplitButton%(-42.11,36.36)")
	click(u"导入统计数据文件||Window%(96.09,-70.95)")
	click(u"导入统计数据文件||Window->||Window->TurbulenceIntensity||ListItem->湍流强度||Text")
	click(u"导入统计数据文件||Window%(86.98,-63.16)")
	drag_and_drop(u"导入统计数据文件||Window%(78.13,93.64)", u"导入统计数据文件||Window%(78.13,93.64)")
	click(u"||Custom#[1,3]%(-40.03,50.21)")
	with UIPath(u"打开||Window->||Pane->Shell 文件夹视图||Pane"):
		click(u"项目视图||List->1831-20230402-20240524-TISD.txt||ListItem->名称||Edit%(35.12,-56.25)")
	click(u"打开||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List%(14.81,76.09)")
	click(u"导入统计数据文件||Window%(95.92,-70.68)")
	drag_and_drop(u"导入统计数据文件||Window%(-71.68,-52.49)", u"导入统计数据文件||Window%(-71.68,-52.49)")
	click(u"导入统计数据文件||Window%(89.21,-62.75)")
	click(u"导入统计数据文件||Window%(80.70,93.37)")
	click(u"||Custom#[1,3]%(-36.43,48.43)")
	drag_and_drop(u"从时间序列计算||Window%(-87.55,-60.00)", u"从时间序列计算||Window%(-87.55,-60.00)")
	click(u"从时间序列计算||Window%(60.63,78.26)")
	click(u"||Custom#[1,3]%(-67.79,93.16)")
	click(u"||Custom#[1,3]%(-98.20,-94.80)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(37.04,41.46)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(23.53,23.53)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop Smart mode\t\tCTRL+ALT+S")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(-62.96,-7.32)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(-8.82,-26.47)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
