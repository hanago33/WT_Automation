# encoding: utf-8

from pywinauto_recorder.player import *


with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane->||Pane"):
		double_click(u"Global Mapper 22 已固定||Button")
		send_keys("{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL}""{VK_CONTROL down}o""{VK_CONTROL up}")

with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"):
	with UIPath(u"打开||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->20230709 共和测量地形图.dwg||ListItem->名称||Edit")
	click(u"打开||Window->打开(O)||Button")
	with UIPath(u"Loading DWG File... (100%)||Window"):
		click(u"未知投影||Window->确定||Button")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"投影:||ComboBox")
		mouse_wheel(12.0)
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->投影:||ComboBox->投影:||List"):
		drag_and_drop(u"垂直滚动条||ScrollBar->向下翻页||Button", u"垂直滚动条||ScrollBar->向下翻页||Button")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		drag_and_drop(u"投影:||ComboBox->投影:||List->Gauss Krueger (3 degree zones)||ListItem", u"投影:||ComboBox->投影:||List->Gauss Krueger (3 degree zones)||ListItem")
		click(u"带号:||ComboBox")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->带号:||ComboBox->带号:||List"):
		drag_and_drop(u"垂直滚动条||ScrollBar->位置||Thumb", u"垂直滚动条||ScrollBar->位置||Thumb#[Zone 37 (109.5E - 112.5E)||ListItem,0]")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"带号:||ComboBox->带号:||List->Zone 40 (118.5E - 121.5E)||ListItem")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->基准:||ComboBox"):
		click(u"打开||Button#[0,0]")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->基准:||ComboBox->基准:||List"):
		drag_and_drop(u"垂直滚动条||ScrollBar->向下翻页||Button", u"垂直滚动条||ScrollBar->向下翻页||Button")
	with UIPath(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window"):
		click(u"基准:||ComboBox->基准:||List->WGS84||ListItem")
	drag_and_drop(u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->确定||Button", u"Loading DWG File... (100%)||Window->选择投影用于 20230709 共和测量地形图.dwg||Window->确定||Button")
	click(u"||Pane->菜单栏||Pane->图层(Y)||MenuItem")
	click(u"图层(Y)||Menu->||ToolBar->基于属性值拆分为独立图层...||MenuItem")
	click(u"选择要拆分的属性||Window->确定||Button")
	with UIPath(u"控制中心 (11 层)||Pane->||Tree->当前工作区||TreeItem"):
		click(u"->20230709 共和测量地形图.dwg||TreeItem#[20230709 共和测量地形图.dwg||TreeItem,0]")
	click(u"控制中心 (11 层)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg||TreeItem#[控制中心 (11 层)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg||TreeItem,0]")
	click(u"控制中心 (11 层, 11 已选)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg||TreeItem->20230709 共和测量地形图.dwg - DGX [290 Features]||TreeItem")
	with UIPath(u"控制中心 (11 层, 1 已选)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg||TreeItem"):
		click(u"20230709 共和测量地形图.dwg - DGX [290 Features]||TreeItem")
	right_click(u"控制中心 (11 层, 1 已选)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg||TreeItem->20230709 共和测量地形图.dwg - DGX [290 Features]||TreeItem")
	click(u"||Menu->||ToolBar->选择 - 使用数字化工具选择所选图层中的所有要素||MenuItem")
	right_click(u"||Pane#[控制中心 (11 层)||Pane->||Tree,0]")
	with UIPath(u"||Menu"):
		click(u"||ToolBar")
	click(u"||Menu->||ToolBar")
	click(u"高级要素创建选项||Menu->||ToolBar->为 选定/加载 的要素创建覆盖区 (凹形体)||MenuItem")
	with UIPath(u"Concave Hull Options||Window"):
		click(u"平滑||Edit")
	click(u"Concave Hull Options||Window->确定||Button")
	with UIPath(u"控制中心 (12 层, 1 已选)||Pane->||Tree->当前工作区||TreeItem"):
		click(u"20230709 共和测量地形图.dwg - DGX Coverage Areas [1 Features]||TreeItem")
	right_click(u"控制中心 (12 层, 1 已选)||Pane->||Tree->当前工作区||TreeItem->20230709 共和测量地形图.dwg - DGX Coverage Areas [1 Features]||TreeItem")
	click(u"||Menu->||ToolBar->选择 - 使用数字化工具选择所选图层中的所有要素||MenuItem")
	click(u"||Pane->菜单栏||Pane->分析(A)||MenuItem")
	click(u"分析(A)||Menu->||ToolBar->从 3D 矢量/Lidar 数据创建高程网格(D)...||MenuItem")
	click(u"选择图层||Window->确定||Button")
	with UIPath(u"网格创建选项||Window"):
		click(u"手动指定要使用的网格间距||RadioButton")
		click(u"X-轴:||Edit")
		send_keys("{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}""{VK_CONTROL down}a""{VK_CONTROL up}""{delete}5")
		click(u"Y 轴:||Edit")
		send_keys("{VK_CONTROL down}""{a down}""{VK_CONTROL up}""{a up}""{delete}5")
		click(u"||Tab->网格边界||TabItem")
		click(u"裁剪到选定的区要素||RadioButton")
	click(u"网格创建选项||Window->确定||Button")
	click(u"控制中心 (13 层)||Pane->||Tree->当前工作区||TreeItem->Generated Grid 1||TreeItem")
	right_click(u"控制中心 (13 层, 1 已选)||Pane->||Tree->当前工作区||TreeItem->Generated Grid 1||TreeItem")
	click(u"||Menu")
	click(u"图层||Menu->||ToolBar->导出 - 将图层导出到新文件...||MenuItem")
	click(u"选择图层||Window->确定||Button")
	with UIPath(u"选择导出格式||Window->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||ComboBox"):
		click(u"打开||Button")
	with UIPath(u"选择导出格式||Window->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||ComboBox->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||List"):
		drag_and_drop(u"垂直滚动条||ScrollBar->位置||Thumb", u"垂直滚动条||ScrollBar->位置||Thumb")
	with UIPath(u"选择导出格式||Window"):
		click(u"选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||ComboBox->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||List->GeoTIFF||ListItem")
	click(u"选择导出格式||Window->确定||Button")
	click(u"提示||Window->确定||Button")
	click(u"GeoTIFF 导出选项||Window->确定||Button")
	with UIPath(u"另存为||Window->||Pane"):
		click(u"控制项宿主||Pane->导航窗格||Tree->桌面||TreeItem->快速访问开始 - 桌面 (已固定)||TreeItem")
	with UIPath(u"另存为||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List"):
		drag_and_drop(u"垂直||ScrollBar->位置||Thumb", u"")
	with UIPath(u"另存为||Window"):
		click(u"||Pane->Shell 文件夹视图||Pane->项目视图||List->测试||ListItem->名称||Edit")
		click(u"打开(O)||Button")
		click(u"||Pane->文件名:||ComboBox->文件名:||Edit")
	click(u"另存为||Window->保存(S)||Button")
	with UIPath(u"||Pane->文件||Pane"):
		click(u"保存工作区||Button")
	click(u"||Pane->文件||Pane->保存工作区||Button")
	with UIPath(u"另存为||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List"):
		drag_and_drop(u"垂直||ScrollBar->位置||Thumb", u"")
	with UIPath(u"另存为||Window->||Pane->Shell 文件夹视图||Pane->项目视图||List->测试||ListItem"):
		click(u"名称||Edit")
	with UIPath(u"另存为||Window->||Pane"):
		double_click(u"Shell 文件夹视图||Pane->项目视图||List->测试||ListItem->名称||Edit")
	with UIPath(u"另存为||Window"):
		click(u"||Pane->文件名:||ComboBox")
		click(u"保存(S)||Button")

with UIPath(u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版 (测试1.gmw)||Window"):
	click(u"||Pane#[0,0]")

with UIPath(u"任务栏||Pane"):
	with UIPath(u"||Pane"):
		click(u"显示隐藏的图标||Button")

with UIPath(u"系统托盘溢出窗口。||Pane"):
	with UIPath(u"||Pane->Pywinauto recorder||Button"):
		right_click(u"||Image")

with UIPath(u"上下文||Menu"):
	click(u"Stop recording\t\tCTRL+ALT+R||MenuItem")
