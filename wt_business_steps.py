# encoding: utf-8

import os
import re
import time

from pywinauto_recorder.player import UIPath, click, send_keys


_LOG_STEP = lambda message: None
_ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW = lambda timeout_seconds=10: None
_CONFIGURE_PROJECTION = lambda: None
_CLICK_UNKNOWN_PROJECTION_IF_PRESENT = lambda timeout_seconds=10: None
_TYPE_PATH_INTO_OPEN_DIALOG = lambda file_path, step_id="", control_id="": None
_TRY_CLICK_LAYER_TREE_EXPAND_ICON = lambda: None
_RIGHT_CLICK_FLOW_TREE_ITEM = lambda step_id, control_id, fallback_title_re: False
_RIGHT_CLICK_TREE_ITEM_BY_TITLE_RE = lambda title_re: None
_CLICK_CONTEXT_MENU_WITH_FALLBACK = lambda step_id, control_id, fallback_uipath, timeout_seconds=4: False
_CLICK_FLOW_CONTROL = lambda *args, **kwargs: False
_GET_STEP_PARAM = lambda context, step_id, key, default="": default
_GET_FLOW_REF_PARAM = lambda context, key, default="": default
_RUN_UI_TARS = lambda prompt, step_name="": None
_HANDLE_DWG_PROJECTION_SELECTION = lambda: None
_GET_GM_EXE = lambda: ""
_GET_SOURCE_FILE_PATH = lambda: ""
_GET_OUTPUT_DIR = lambda: ""
_GET_PROJECTION_FILE_PATH = lambda: ""
_GET_MAIN_WINDOW_UIPATH = lambda: ""


def configure_wt_business_steps(
    log_step=None,
    activate_and_maximize_main_window=None,
    configure_projection=None,
    click_unknown_projection_if_present=None,
    type_path_into_open_dialog=None,
    try_click_layer_tree_expand_icon=None,
    right_click_flow_tree_item=None,
    right_click_tree_item_by_title_re=None,
    click_context_menu_with_fallback=None,
    click_flow_control=None,
    get_step_param=None,
    get_flow_ref_param=None,
    run_ui_tars=None,
    handle_dwg_projection_selection=None,
    get_gm_exe=None,
    get_source_file_path=None,
    get_output_dir=None,
    get_projection_file_path=None,
    get_main_window_uipath=None,
):
    global _LOG_STEP, _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW, _CONFIGURE_PROJECTION
    global _CLICK_UNKNOWN_PROJECTION_IF_PRESENT, _TYPE_PATH_INTO_OPEN_DIALOG
    global _TRY_CLICK_LAYER_TREE_EXPAND_ICON, _RIGHT_CLICK_FLOW_TREE_ITEM
    global _RIGHT_CLICK_TREE_ITEM_BY_TITLE_RE, _CLICK_CONTEXT_MENU_WITH_FALLBACK
    global _CLICK_FLOW_CONTROL, _GET_STEP_PARAM, _GET_FLOW_REF_PARAM
    global _RUN_UI_TARS, _HANDLE_DWG_PROJECTION_SELECTION, _GET_GM_EXE
    global _GET_SOURCE_FILE_PATH, _GET_OUTPUT_DIR, _GET_PROJECTION_FILE_PATH
    global _GET_MAIN_WINDOW_UIPATH

    if callable(log_step):
        _LOG_STEP = log_step
    if callable(activate_and_maximize_main_window):
        _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW = activate_and_maximize_main_window
    if callable(configure_projection):
        _CONFIGURE_PROJECTION = configure_projection
    if callable(click_unknown_projection_if_present):
        _CLICK_UNKNOWN_PROJECTION_IF_PRESENT = click_unknown_projection_if_present
    if callable(type_path_into_open_dialog):
        _TYPE_PATH_INTO_OPEN_DIALOG = type_path_into_open_dialog
    if callable(try_click_layer_tree_expand_icon):
        _TRY_CLICK_LAYER_TREE_EXPAND_ICON = try_click_layer_tree_expand_icon
    if callable(right_click_flow_tree_item):
        _RIGHT_CLICK_FLOW_TREE_ITEM = right_click_flow_tree_item
    if callable(right_click_tree_item_by_title_re):
        _RIGHT_CLICK_TREE_ITEM_BY_TITLE_RE = right_click_tree_item_by_title_re
    if callable(click_context_menu_with_fallback):
        _CLICK_CONTEXT_MENU_WITH_FALLBACK = click_context_menu_with_fallback
    if callable(click_flow_control):
        _CLICK_FLOW_CONTROL = click_flow_control
    if callable(get_step_param):
        _GET_STEP_PARAM = get_step_param
    if callable(get_flow_ref_param):
        _GET_FLOW_REF_PARAM = get_flow_ref_param
    if callable(run_ui_tars):
        _RUN_UI_TARS = run_ui_tars
    if callable(handle_dwg_projection_selection):
        _HANDLE_DWG_PROJECTION_SELECTION = handle_dwg_projection_selection
    if callable(get_gm_exe):
        _GET_GM_EXE = get_gm_exe
    if callable(get_source_file_path):
        _GET_SOURCE_FILE_PATH = get_source_file_path
    if callable(get_output_dir):
        _GET_OUTPUT_DIR = get_output_dir
    if callable(get_projection_file_path):
        _GET_PROJECTION_FILE_PATH = get_projection_file_path
    if callable(get_main_window_uipath):
        _GET_MAIN_WINDOW_UIPATH = get_main_window_uipath


def _ensure_output_dir_exists():
    output_dir = _GET_OUTPUT_DIR()
    if not os.path.isdir(output_dir):
        raise NotADirectoryError(f"Output dir not found: {output_dir}")


def step_launch_gm(_context):
    gm_exe = _GET_GM_EXE()
    if not os.path.exists(gm_exe):
        raise FileNotFoundError(f"目标软件可执行文件不存在: {gm_exe}")
    _LOG_STEP("启动目标软件")
    os.startfile(gm_exe)
    _LOG_STEP("等待软件启动...")
    time.sleep(12)
    _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW()


def step_configure_projection(_context):
    projection_file_path = _GET_PROJECTION_FILE_PATH()
    if not os.path.exists(projection_file_path):
        raise FileNotFoundError(f"Projection file not found: {projection_file_path}")
    _CONFIGURE_PROJECTION()


def step_open_source_dwg(_context):
    source_file_path = _GET_SOURCE_FILE_PATH()
    if not os.path.exists(source_file_path):
        raise FileNotFoundError(f"Source file not found: {source_file_path}")
    _LOG_STEP("打开文件对话框")
    with UIPath(_GET_MAIN_WINDOW_UIPATH()):
        send_keys("^o")
    time.sleep(2)
    _TYPE_PATH_INTO_OPEN_DIALOG(source_file_path, step_id="open_source_dwg", control_id="open_dialog_filename")


def step_close_unknown_projection(_context):
    _LOG_STEP("关闭未知投影提示")
    _CLICK_UNKNOWN_PROJECTION_IF_PRESENT(timeout_seconds=10)


def step_dwg_projection_confirm(_context):
    _HANDLE_DWG_PROJECTION_SELECTION()


def step_split_layers(_context):
    _LOG_STEP("基于属性值拆分为独立图层")
    with UIPath(_GET_MAIN_WINDOW_UIPATH()):
        click(u"||Pane->菜单栏||Pane->图层(Y)||MenuItem")
        time.sleep(0.5)
        click(u"图层(Y)||Menu->||ToolBar->基于属性值拆分为独立图层...||MenuItem")
        time.sleep(1)
        click(u"选择要拆分的属性||Window->确定||Button")
        time.sleep(2)


def step_select_dgx_layer(context):
    source_file_path = _GET_SOURCE_FILE_PATH()
    source_basename = context.get("source_basename", "") or os.path.basename(source_file_path)
    dgx_layer_re = re.escape(source_basename) + r" - DGX \[\d+ Features\]"
    context["dgx_layer_re"] = dgx_layer_re
    _LOG_STEP("选择DGX图层")
    _TRY_CLICK_LAYER_TREE_EXPAND_ICON()
    _RIGHT_CLICK_FLOW_TREE_ITEM("select_dgx_layer", "dgx_layer_item", dgx_layer_re)
    time.sleep(1)
    _CLICK_CONTEXT_MENU_WITH_FALLBACK(
        "select_dgx_layer",
        "dgx_select_all_menu",
        u"||Menu->||ToolBar->选择 - 使用数字化工具选择所选图层中的所有要素||MenuItem",
    )
    time.sleep(2)


def step_create_coverage(context):
    dgx_layer_re = context.get("dgx_layer_re")
    if not dgx_layer_re:
        source_file_path = _GET_SOURCE_FILE_PATH()
        source_basename = context.get("source_basename", "") or os.path.basename(source_file_path)
        dgx_layer_re = re.escape(source_basename) + r" - DGX \[\d+ Features\]"
        context["dgx_layer_re"] = dgx_layer_re
    smooth_value = str(_GET_STEP_PARAM(context, "create_coverage", "smoothValue", "10")).strip() or "10"
    _LOG_STEP("创建覆盖区")
    if not _CLICK_FLOW_CONTROL(
        "create_coverage",
        "coverage_context_pane",
        timeout_seconds=4,
        window_title_hint="__all__",
        click_kind="right",
    ):
        _RIGHT_CLICK_TREE_ITEM_BY_TITLE_RE(dgx_layer_re)
    time.sleep(1)
    _CLICK_CONTEXT_MENU_WITH_FALLBACK(
        "create_coverage",
        "advanced_feature_options_menu",
        u"高级要素创建选项||MenuItem",
    )
    time.sleep(0.6)
    _CLICK_CONTEXT_MENU_WITH_FALLBACK(
        "create_coverage",
        "create_coverage_menu",
        u"为 选定/加载 的要素创建覆盖区 (凹形体)||MenuItem",
    )
    time.sleep(1)
    _RUN_UI_TARS(
        "在“Concave Hull Options”窗口中："
        f"1. 点击“平滑”输入框，先全选里面的内容，然后输入 {smooth_value}；"
        "2. 最后点击“确定”按钮。",
        step_name="覆盖区平滑设置",
    )
    _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW()
    time.sleep(2)


def step_select_coverage_layer(context):
    source_file_path = _GET_SOURCE_FILE_PATH()
    source_basename = context.get("source_basename", "") or os.path.basename(source_file_path)
    coverage_layer_re = re.escape(source_basename) + r" - DGX Coverage Areas \[\d+ Features\]"
    context["coverage_layer_re"] = coverage_layer_re
    _LOG_STEP("选择覆盖区图层")
    _RIGHT_CLICK_FLOW_TREE_ITEM("select_coverage_layer", "coverage_layer_item", coverage_layer_re)
    time.sleep(1)
    _CLICK_CONTEXT_MENU_WITH_FALLBACK(
        "select_coverage_layer",
        "coverage_select_all_menu",
        u"||Menu->||ToolBar->选择 - 使用数字化工具选择所选图层中的所有要素||MenuItem",
    )
    time.sleep(2)


def step_create_grid(_context):
    context = _context if isinstance(_context, dict) else {}
    grid_x = str(_GET_STEP_PARAM(context, "create_grid", "xSpacing", "5")).strip() or "5"
    grid_y = str(_GET_STEP_PARAM(context, "create_grid", "ySpacing", "5")).strip() or "5"
    clip_option = str(_GET_STEP_PARAM(context, "create_grid", "clipOptionLabel", "裁剪到选定的区要素")).strip() or "裁剪到选定的区要素"
    _LOG_STEP("创建高程网格")
    with UIPath(_GET_MAIN_WINDOW_UIPATH()):
        click(u"||Pane->菜单栏||Pane->分析(A)||MenuItem")
        time.sleep(0.5)
        click(u"分析(A)||Menu->||ToolBar->从 3D 矢量/Lidar 数据创建高程网格(D)...||MenuItem")
        time.sleep(1)
        click(u"选择图层||Window->确定||Button")
        time.sleep(1)
    _RUN_UI_TARS(
        "在“网格创建选项”窗口中："
        "1. 先点击并选择“手动指定要使用的网格间距”选项；"
        f"2. 点击“X-轴”输入框，全选里面内容后输入 {grid_x}；"
        f"3. 点击“Y轴”输入框，全选里面内容后输入 {grid_y}；"
        "4. 点击“网格边界”选项卡；"
        f"5. 点击并选择“{clip_option}”选项；"
        "6. 最后点击“确定”按钮。",
        step_name="网格创建",
    )
    _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW()
    time.sleep(2)


def step_grid_options_ai_fill(_context):
    context = _context if isinstance(_context, dict) else {}
    grid_x = str(_GET_FLOW_REF_PARAM(context, "xSpacing", "5")).strip() or "5"
    grid_y = str(_GET_FLOW_REF_PARAM(context, "ySpacing", "5")).strip() or "5"
    clip_option = str(_GET_FLOW_REF_PARAM(context, "clipOptionLabel", "裁剪到选定的区要素")).strip() or "裁剪到选定的区要素"
    _RUN_UI_TARS(
        "在“网格创建选项”窗口中："
        "1. 先点击并选择“手动指定要使用的网格间距”选项；"
        f"2. 点击“X-轴”输入框，全选里面内容后输入 {grid_x}；"
        f"3. 点击“Y轴”输入框，全选里面内容后输入 {grid_y}；"
        "4. 点击“网格边界”选项卡；"
        f"5. 点击并选择“{clip_option}”选项；"
        "6. 最后点击“确定”按钮。",
        step_name="网格创建参数设置",
    )
    _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW()
    time.sleep(0.5)


def step_export_geotiff(_context):
    _ensure_output_dir_exists()
    context = _context if isinstance(_context, dict) else {}
    default_output_dir = _GET_OUTPUT_DIR()
    output_dir = str(_GET_STEP_PARAM(context, "export_geotiff", "outputDir", default_output_dir)).strip() or default_output_dir
    _LOG_STEP("导出网格")
    _RIGHT_CLICK_TREE_ITEM_BY_TITLE_RE(r"Generated Grid 1")
    time.sleep(1)
    click(u"图层||Menu->||ToolBar->导出 - 将图层导出到新文件...||MenuItem")
    time.sleep(1)
    click(u"选择图层||Window->确定||Button")
    time.sleep(1)

    _LOG_STEP("选择GeoTIFF格式")
    with UIPath(
        u"选择导出格式||Window->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||ComboBox"
    ):
        click(u"打开||Button")
        time.sleep(0.5)
    with UIPath(
        u"选择导出格式||Window->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||ComboBox->选择要将加载的数据导出到的格式。有关可用格式的信息，请参阅 https://www.bluemarblegeo.com/products/global-mapper-formats.php.||List"
    ):
        send_keys("GeoTIFF{ENTER}")
        time.sleep(0.5)
    click(u"选择导出格式||Window->确定||Button")
    time.sleep(1)
    try:
        click(u"提示||Window->确定||Button")
        time.sleep(0.5)
    except Exception:
        pass
    click(u"GeoTIFF 导出选项||Window->确定||Button")
    time.sleep(1)

    _LOG_STEP("保存文件")
    time.sleep(2)
    send_keys("%d")
    time.sleep(0.5)
    send_keys("^a")
    time.sleep(0.3)
    send_keys(output_dir)
    time.sleep(0.5)
    send_keys("{ENTER}")
    time.sleep(2)
    send_keys("%s")
    time.sleep(3)


def get_step_registry():
    return [
        ("launch_gm", step_launch_gm),
        ("configure_projection", step_configure_projection),
        ("open_source_dwg", step_open_source_dwg),
        ("close_unknown_projection", step_close_unknown_projection),
        ("dwg_projection_confirm", step_dwg_projection_confirm),
        ("split_layers", step_split_layers),
        ("select_dgx_layer", step_select_dgx_layer),
        ("create_coverage", step_create_coverage),
        ("select_coverage_layer", step_select_coverage_layer),
        ("create_grid", step_create_grid),
        ("grid_options_ai_fill", step_grid_options_ai_fill),
        ("export_geotiff", step_export_geotiff),
    ]


def get_step_registry_map():
    return {step_id: func for step_id, func in get_step_registry()}
