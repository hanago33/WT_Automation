# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"||Window"):
	click(u"||Custom#[1,2]%(-67.16,-79.34)")
	mouse_wheel(39.0)
	click(u"||Custom#[1,2]%(-73.42,-60.33)")
	send_keys(" NOMAPPING""{ENTER}"" M""{ENTER}")
	drag_and_drop(u"||Custom#[1,2]%(-38.55,-18.88)", u"||Custom#[1,2]%(-38.47,-19.02)")
	click(u"||Custom#[1,2]%(-45.50,-14.91)")
	click(u"||Custom#[1,2]%(-41.75,10.94)")
	drag_and_drop(u"||Custom#[1,2]%(-10.95,-69.49)", u"||Custom#[1,2]%(-38.78,-64.71)")
	click(u"||Custom#[1,2]%(-25.41,-46.51)")
	click(u"||Custom#[1,2]%(3.52,-40.77)")
	with UIPath(u"||Window->Cp0.429||ListItem"):
		click(u"Cp0.429||Text%(-60.55,6.45)")
	drag_and_drop(u"||Window->Cp0.429||ListItem->Cp0.429||Text%(-60.55,6.45)", u"||Window->Cp0.429||ListItem->Cp0.429||Text%(-60.55,6.45)")
	click(u"||Custom#[1,2]%(58.72,-34.61)")
	drag_and_drop(u"||Custom#[1,2]%(-32.06,-14.77)", u"||Custom#[1,2]%(-30.49,8.62)")
	click(u"||Custom#[1,2]%(-37.14,2.46)")
	click(u"||Window->MTD.WRAAnalysis.Wpf.ViewModels.MTDWRAAnalysisComputationPointViewModel||ListItem->M1 - 90m||Text%(-71.70,-12.90)")
	click(u"||Custom#[1,2]%(-41.36,7.93)")
	drag_and_drop(u"||Custom#[1,2]%(-17.36,-65.25)", u"||Custom#[1,2]%(-31.90,-61.97)")
	click(u"||Custom#[1,2]%(-22.36,-42.13)")
	click(u"||Custom#[1,2]%(-48.87,32.97)")
	click(u"||Custom#[1,2]%(-78.19,33.52)")
	send_keys("1")
	click(u"||Custom#[1,2]%(-35.73,60.74)")
	click(u"||Custom#[1,2]%(-98.44,-89.33)")
	mouse_wheel(-17.0)
	click(u"||Custom#[1,2]%(-40.34,-9.85)")
	with UIPath(u"||Window->M1||ListItem"):
		click(u"M1||Text%(-43.60,-6.45)")
		mouse_wheel(-3.0)
	click(u"||Custom#[1,2]%(-50.43,2.87)")
	send_keys("3""30")
	mouse_wheel(-10.0)
	click(u"||Custom#[1,2]%(-37.53,64.98)")
	with UIPath(u"||Window->LOW||ListItem"):
		drag_and_drop(u"低||Text%(-54.10,-25.81)", u"低||Text%(-54.10,-25.81)")
		drag_and_drop(u"低||Text%(-54.10,-25.81)", u"低||Text%(-54.10,-25.81)")
		mouse_wheel(-3.0)
	click(u"||Custom#[1,2]%(-72.95,95.62)")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button%(-51.85,2.44)")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	right_click(u"Pywinauto recorder||Button%(-38.24,-29.41)")

with UIPath(u"上下文||Menu"):
	menu_click(u"Stop recording\t\tCTRL+ALT+R")
