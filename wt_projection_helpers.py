# encoding: utf-8

import os
import subprocess
import time
from datetime import datetime

import pyautogui
from PIL import Image
from pywinauto import Desktop
from pywinauto_recorder.player import send_keys


class StageExecutionError(RuntimeError):
    def __init__(self, message, resume_stage):
        super().__init__(message)
        self.resume_stage = resume_stage


_LOG_STEP = lambda message: None
_CLICK_FLOW_CONTROL = lambda *args, **kwargs: False
_ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW = lambda timeout_seconds=10: None
_CONFIRM_OPEN_FILE_DIALOG = lambda timeout_seconds=5: None
_GET_PROJECTION_FILE_PATH = lambda: ""
_GET_UI_TARS_RUNNER = lambda: ""
_GET_IMAGE_TEMPLATE_DIR = lambda: ""
_GET_LAYER_TREE_TEMPLATE_DIR = lambda: ""
_GET_DEBUG_SCREENSHOT_DIR = lambda: ""
_GET_IMAGE_TEMPLATES = lambda: {}


def configure_wt_projection_helpers(
    log_step=None,
    click_flow_control=None,
    activate_and_maximize_main_window=None,
    confirm_open_file_dialog=None,
    get_projection_file_path=None,
    get_ui_tars_runner=None,
    get_image_template_dir=None,
    get_layer_tree_template_dir=None,
    get_debug_screenshot_dir=None,
    get_image_templates=None,
):
    global _LOG_STEP, _CLICK_FLOW_CONTROL, _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW
    global _CONFIRM_OPEN_FILE_DIALOG, _GET_PROJECTION_FILE_PATH, _GET_UI_TARS_RUNNER
    global _GET_IMAGE_TEMPLATE_DIR, _GET_LAYER_TREE_TEMPLATE_DIR
    global _GET_DEBUG_SCREENSHOT_DIR, _GET_IMAGE_TEMPLATES

    if callable(log_step):
        _LOG_STEP = log_step
    if callable(click_flow_control):
        _CLICK_FLOW_CONTROL = click_flow_control
    if callable(activate_and_maximize_main_window):
        _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW = activate_and_maximize_main_window
    if callable(confirm_open_file_dialog):
        _CONFIRM_OPEN_FILE_DIALOG = confirm_open_file_dialog
    if callable(get_projection_file_path):
        _GET_PROJECTION_FILE_PATH = get_projection_file_path
    if callable(get_ui_tars_runner):
        _GET_UI_TARS_RUNNER = get_ui_tars_runner
    if callable(get_image_template_dir):
        _GET_IMAGE_TEMPLATE_DIR = get_image_template_dir
    if callable(get_layer_tree_template_dir):
        _GET_LAYER_TREE_TEMPLATE_DIR = get_layer_tree_template_dir
    if callable(get_debug_screenshot_dir):
        _GET_DEBUG_SCREENSHOT_DIR = get_debug_screenshot_dir
    if callable(get_image_templates):
        _GET_IMAGE_TEMPLATES = get_image_templates


def run_ui_tars(prompt, step_name="AI介入操作"):
    api_key = os.environ.get("VOLC_API_KEY") or os.environ.get("UI_TARS_API_KEY")
    if not api_key:
        raise RuntimeError("未获取到 VOLC_API_KEY（或 UI_TARS_API_KEY），无法执行 AI 介入步骤。")
    ui_tars_runner = _GET_UI_TARS_RUNNER()
    if not os.path.exists(ui_tars_runner):
        raise FileNotFoundError(f"UI-TARS Runner not found: {ui_tars_runner}")

    _LOG_STEP(f"开始{step_name}")
    ui_tars_stdout = os.path.join(os.path.dirname(__file__), f"ui_tars_{step_name}_stdout.log")
    ui_tars_stderr = os.path.join(os.path.dirname(__file__), f"ui_tars_{step_name}_stderr.log")

    for log_file in [ui_tars_stdout, ui_tars_stderr]:
        if os.path.exists(log_file):
            os.remove(log_file)

    result = subprocess.run(
        ["node", ui_tars_runner, prompt],
        cwd=os.path.dirname(__file__),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="ignore",
        text=True,
        env={
            **os.environ,
            "VOLC_API_KEY": api_key,
            "UI_TARS_VLM_BASE_URL": os.environ.get("UI_TARS_VLM_BASE_URL", ""),
            "MODEL_NAME": os.environ.get("MODEL_NAME", ""),
            "UI_TARS_REPO_ROOT": os.environ.get("UI_TARS_REPO_ROOT", ""),
            "UI_TARS_CLI_CONFIG": os.environ.get("UI_TARS_CLI_CONFIG", ""),
        },
    )

    with open(ui_tars_stdout, "w", encoding="utf-8", errors="ignore") as file_obj:
        if result.stdout:
            file_obj.write(result.stdout)
    with open(ui_tars_stderr, "w", encoding="utf-8", errors="ignore") as file_obj:
        if result.stderr:
            file_obj.write(result.stderr)

    _LOG_STEP(f"{step_name}完成，UI-TARS日志已保存到: {ui_tars_stdout}, {ui_tars_stderr}")

    if result.returncode != 0:
        raise RuntimeError(f"UI-TARS 执行失败 rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return {
        "stdoutLog": ui_tars_stdout,
        "stderrLog": ui_tars_stderr,
        "stepName": step_name,
    }


def build_projection_ai_prompt(start_stage):
    projection_file_path = _GET_PROJECTION_FILE_PATH()
    stage_instructions = {
        "config_button": [
            "点击“配置”按钮。",
            "在左侧依次点击“常规”和“投影”。",
            "点击“从文件加载...”按钮。",
            f"在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影配置窗口后，点击“应用”，再点击“确定”。",
        ],
        "general_tree_item": [
            "当前“配置”按钮已经点击成功，配置窗口应该已经打开，不要再次点击“配置”。",
            "只需从左侧“常规”开始继续，接着点击“投影”。",
            "点击“从文件加载...”按钮。",
            f"在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影配置窗口后，点击“应用”，再点击“确定”。",
        ],
        "projection_tree_item": [
            "当前已经进入配置窗口，不要再次点击“配置”。",
            "只需点击左侧“投影”。",
            "点击“从文件加载...”按钮。",
            f"在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影配置窗口后，点击“应用”，再点击“确定”。",
        ],
        "load_from_file_button": [
            "当前已经进入“投影”页面，不要再次点击“配置”“常规”或“投影”。",
            "直接点击“从文件加载...”按钮。",
            f"在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影配置窗口后，点击“应用”，再点击“确定”。",
        ],
        "file_name_input": [
            "当前“从文件加载...”按钮已经点击成功，不要再次点击“配置”“常规”“投影”或“从文件加载...”。",
            f"只需在当前打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影配置窗口后，点击“应用”，再点击“确定”。",
        ],
        "apply_button": [
            "投影文件已经加载完成，不要再次点击“配置”“常规”“投影”或“从文件加载...”，也不要重新输入投影文件路径。",
            "只需在当前投影配置窗口中点击“应用”，然后点击“确定”。",
        ],
        "ok_button": [
            "投影文件已经加载并且“应用”应该已经成功，不要再次点击“配置”“常规”“投影”或“从文件加载...”，也不要重新输入投影文件路径。",
            "只需在当前窗口中点击“确定”完成收尾。",
        ],
    }
    steps = stage_instructions.get(start_stage, stage_instructions["config_button"])
    step_text = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(steps))
    return (
        "请在目标软件中继续完成当前配置，但只能从当前进度继续，不要重复已经成功的步骤。\n"
        f"当前续跑阶段是：{start_stage}\n"
        f"{step_text}\n"
        "如果发现目标步骤已经完成，请直接继续后续步骤。"
    )


def build_dwg_projection_confirmation_prompt():
    projection_file_path = _GET_PROJECTION_FILE_PATH()
    return (
        "当前正在处理 DWG 导入后的投影/坐标系确认窗口。\n"
        f"投影文件 {projection_file_path} 已经通过脚本加载过，不要再次点击“从文件加载...”或重新输入文件路径。\n"
        "请只执行以下操作：\n"
        "1. 检查投影类型是否为“Gauss Krueger (3 degree zones)”。\n"
        "2. 检查带号是否为“Zone 40 (118.5E - 121.5E)”或等价的 Zone 40 显示。\n"
        "3. 检查基准面是否为“WGS84”。\n"
        "4. 如果三项都正确，点击“确定”继续；如果不正确，优先修正到正确值后再点击“确定”。\n"
        "5. 不要执行与投影确认无关的其他操作。"
    )


def build_dwg_projection_ai_prompt(start_stage):
    projection_file_path = _GET_PROJECTION_FILE_PATH()
    stage_instructions = {
        "load_from_file_button": [
            "当前正在 DWG 导入后的投影选择窗口。",
            "点击“从文件加载...”按钮。",
            f"在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影选择窗口后，确认投影类型为“Gauss Krueger (3 degree zones)”、带号为“Zone 40”、基准面为“WGS84”，最后点击“确定”。",
        ],
        "file_name_input": [
            "当前“从文件加载...”已经点击成功，不要重复点击。",
            f"只需在打开文件对话框中，把文件名设置为：{projection_file_path}，然后点击“打开(O)”。",
            "回到投影选择窗口后，确认投影类型为“Gauss Krueger (3 degree zones)”、带号为“Zone 40”、基准面为“WGS84”，最后点击“确定”。",
        ],
        "confirm_values": [
            f"投影文件 {projection_file_path} 已经加载，不要再次点击“从文件加载...”或重新输入文件路径。",
            "只需确认投影类型为“Gauss Krueger (3 degree zones)”、带号为“Zone 40”、基准面为“WGS84”，必要时修正，然后点击“确定”。",
        ],
    }
    steps = stage_instructions.get(start_stage, stage_instructions["load_from_file_button"])
    step_text = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(steps))
    return f"请在当前 DWG 投影选择流程中从阶段 {start_stage} 继续执行，不要重复已完成的步骤。\n{step_text}"


def get_template_path(template_key):
    template_name = _GET_IMAGE_TEMPLATES()[template_key]
    return os.path.join(_GET_IMAGE_TEMPLATE_DIR(), template_name)


def locate_template_center(template_key, timeout_seconds=8, confidence=0.8):
    template_path = get_template_path(template_key)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"缺少图片模板: {template_path}")
    return locate_template_center_by_path(template_path, timeout_seconds=timeout_seconds, confidence=confidence)


def locate_template_center_by_path(template_path, timeout_seconds=8, confidence=0.8, region=None, scales=None):
    """定位模板中心并返回 pyautogui.Point。

    在原 pyautogui 精确匹配基础上增加：
      - region(ROI)：缩小搜索范围，提速并减少误命中；
      - 多尺度兜底：原匹配失败时，用 cv2.matchTemplate 在多个缩放下重试，
        缓解高分屏 / DPI / 缩放导致的模板漂移漏命中（见 debug-relative-region-offset.md 等）。

    签名向后兼容：region/scales 均有默认值，region=None 时主路径行为与旧版一致，
    仅在精确匹配失败时才进入多尺度兜底，因此对已有流程无回归风险。
    """
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"缺少图片模板: {template_path}")

    if scales is None:
        scales = [1.0, 1.1, 0.9, 1.2, 0.8, 1.35, 0.7]

    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        # 1) 精确匹配（与旧行为一致，仅额外支持传入 ROI 缩小搜索范围）
        try:
            with Image.open(template_path) as template_image:
                try:
                    center = pyautogui.locateCenterOnScreen(template_image, confidence=confidence, region=region)
                except Exception:
                    center = pyautogui.locateCenterOnScreen(template_image, region=region)
            if center:
                return center
        except Exception as exc:
            last_error = exc

        # 2) 多尺度兜底：原匹配失败时，缩放模板以对抗 DPI/缩放漂移
        try:
            ms = _match_template_multiscale(template_path, confidence, region, scales)
            if ms is not None:
                return ms
        except Exception as exc:
            last_error = exc

        time.sleep(0.4)

    raise RuntimeError(f"模板未匹配到: path={template_path}; last_error={last_error}")


def _match_template_multiscale(template_path, confidence, region, scales):
    """使用 cv2.matchTemplate 在多个缩放下寻找模板最佳匹配。

    返回 pyautogui.Point（命中且满足置信度）或 None。region 给定时，
    匹配坐标会叠加 region 左上偏移还原为绝对屏幕坐标。
    """
    import cv2
    import numpy as np

    screenshot = pyautogui.screenshot(region=region)
    screen_gray = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2GRAY)

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise RuntimeError(f"无法读取模板图片: {template_path}")
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    sh, sw = screen_gray.shape[:2]
    th, tw = template_gray.shape[:2]

    offset_x = region[0] if region else 0
    offset_y = region[1] if region else 0

    best_score = -1.0
    best_center = None
    for scale in scales:
        w = int(round(tw * scale))
        h = int(round(th * scale))
        if w < 2 or h < 2 or w > sw or h > sh:
            continue
        resized = cv2.resize(template_gray, (w, h), interpolation=cv2.INTER_AREA)
        if resized.shape[0] > sh or resized.shape[1] > sw:
            continue
        result = cv2.matchTemplate(screen_gray, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_score:
            best_score = max_val
            best_center = (int(max_loc[0] + w / 2 + offset_x), int(max_loc[1] + h / 2 + offset_y))

    if best_center is not None and best_score >= confidence:
        return pyautogui.Point(best_center[0], best_center[1])
    return None


def capture_debug_screenshot(tag):
    try:
        debug_screenshot_dir = _GET_DEBUG_SCREENSHOT_DIR()
        os.makedirs(debug_screenshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(debug_screenshot_dir, f"{timestamp}_{tag}.png")
        pyautogui.screenshot(file_path)
        _LOG_STEP(f"已保存调试截图: {file_path}")
        return file_path
    except Exception as exc:
        _LOG_STEP(f"保存调试截图失败: {exc}")
        return None


def find_config_window(timeout_seconds=2):
    deadline = time.time() + timeout_seconds
    last_window = None
    while time.time() < deadline:
        try:
            desktop = Desktop(backend="uia")
            for win in desktop.windows():
                try:
                    title = win.window_text()
                except Exception:
                    title = ""
                if title and title.startswith("配置 - "):
                    last_window = win
                    return win
        except Exception:
            pass
        time.sleep(0.2)
    return last_window


def config_window_is_open():
    try:
        return find_config_window(timeout_seconds=0.8) is not None
    except Exception:
        return False


def log_projection_debug_context(stage):
    try:
        config_open = config_window_is_open()
    except Exception:
        config_open = False

    try:
        desktop = Desktop(backend="uia")
        window_titles = []
        for win in desktop.windows():
            try:
                title = win.window_text()
            except Exception:
                title = ""
            if title:
                window_titles.append(title)
        window_titles = window_titles[:10]
    except Exception as exc:
        window_titles = [f"窗口枚举失败: {exc}"]

    _LOG_STEP(f"[投影调试] stage={stage}, 配置窗口打开={config_open}, 可见窗口={window_titles}")


def click_template(template_key, timeout_seconds=8, confidence=0.8):
    center = locate_template_center(
        template_key,
        timeout_seconds=timeout_seconds,
        confidence=confidence,
    )
    pyautogui.moveTo(center.x, center.y, duration=0.2)
    pyautogui.click()
    time.sleep(0.5)


def try_click_layer_tree_expand_icon(timeout_seconds=4, confidence=0.8):
    template_path = os.path.join(_GET_LAYER_TREE_TEMPLATE_DIR(), "展开图标.png")
    if not os.path.exists(template_path):
        _LOG_STEP(f"图层树展开模板不存在，跳过模板点击: {template_path}")
        return False

    try:
        center = locate_template_center_by_path(
            template_path,
            timeout_seconds=timeout_seconds,
            confidence=confidence,
        )
        pyautogui.moveTo(center.x, center.y, duration=0.2)
        pyautogui.click()
        time.sleep(0.8)
        _LOG_STEP(f"已点击图层树展开图标: ({center.x}, {center.y})")
        return True
    except Exception as exc:
        _LOG_STEP(f"图层树展开图标未命中，继续沿用原有图层右键逻辑: {exc}")
        return False


def click_button_in_config_window(button_title, timeout_seconds=6, flow_control_id=None):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            if flow_control_id and _CLICK_FLOW_CONTROL(
                "configure_projection",
                flow_control_id,
                timeout_seconds=1.2,
                window_title_hint="配置 - ",
            ):
                return True
            window = find_config_window(timeout_seconds=0.8)
            if window is None:
                raise RuntimeError("未找到配置窗口")
            window.set_focus()
            button = window.child_window(title=button_title, control_type="Button")
            if button.exists(timeout=0.5):
                button.click_input()
                time.sleep(0.5)
                return True
            raise RuntimeError(f"配置窗口中未找到按钮: {button_title}")
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"控件点击失败: button={button_title}; last_error={last_error}")


def configure_projection_by_image(start_stage="config_button"):
    _LOG_STEP(f"固定流程失败，切换图片识别加载投影，起始阶段: {start_stage}")
    _LOG_STEP(f"图片模板目录: {_GET_IMAGE_TEMPLATE_DIR()}")
    for template_key, template_name in _GET_IMAGE_TEMPLATES().items():
        template_path = get_template_path(template_key)
        _LOG_STEP(
            f"模板检查: key={template_key}, file={template_name}, "
            f"exists={os.path.exists(template_path)}, path={template_path}"
        )

    stage_order = [
        "config_button",
        "general_tree_item",
        "projection_tree_item",
        "load_from_file_button",
        "file_name_input",
        "apply_button",
        "ok_button",
    ]
    start_index = stage_order.index(start_stage) if start_stage in stage_order else 0
    ai_resume_stage = start_stage
    projection_file_path = _GET_PROJECTION_FILE_PATH()

    try:
        if start_index <= stage_order.index("config_button"):
            if config_window_is_open():
                _LOG_STEP("检测到配置窗口已打开，图片识别流程跳过配置按钮点击")
            else:
                _ACTIVATE_AND_MAXIMIZE_MAIN_WINDOW(timeout_seconds=5)
                if _CLICK_FLOW_CONTROL("configure_projection", "config_button", timeout_seconds=2.5):
                    _LOG_STEP("流程链路匹配点击配置成功")
                else:
                    click_template("config_button", timeout_seconds=6)
                    _LOG_STEP("图片识别点击配置成功")
                time.sleep(1)
            ai_resume_stage = "general_tree_item"
        else:
            _LOG_STEP(f"根据录制流程失败位置，从 {start_stage} 继续图片识别，不再重复点击配置")

        if start_index <= stage_order.index("general_tree_item"):
            try:
                if _CLICK_FLOW_CONTROL("configure_projection", "general_tree_item", timeout_seconds=2.0):
                    _LOG_STEP("流程链路匹配点击常规成功")
                else:
                    click_template("general_tree_item", timeout_seconds=5)
                    _LOG_STEP("图片识别点击常规成功")
            except Exception as exc:
                _LOG_STEP(f"常规按钮未匹配，继续尝试投影按钮: {exc}")
                log_projection_debug_context("general_tree_item_not_matched")
            ai_resume_stage = "projection_tree_item"

        if start_index <= stage_order.index("projection_tree_item"):
            if _CLICK_FLOW_CONTROL("configure_projection", "projection_tree_item", timeout_seconds=2.5):
                _LOG_STEP("流程链路匹配点击投影成功")
            else:
                click_template("projection_tree_item", timeout_seconds=8)
                _LOG_STEP("图片识别点击投影成功")
            time.sleep(0.8)
            ai_resume_stage = "load_from_file_button"

        if start_index <= stage_order.index("load_from_file_button"):
            try:
                click_button_in_config_window("从文件加载...", timeout_seconds=4, flow_control_id="load_from_file_button")
                _LOG_STEP("控件点击从文件加载成功")
            except Exception as exc:
                _LOG_STEP(f"控件点击从文件加载失败，改用图片模板: {exc}")
                click_template("load_from_file_button", timeout_seconds=8)
                _LOG_STEP("图片识别点击从文件加载成功")
            time.sleep(1)
            ai_resume_stage = "file_name_input"

        if start_index <= stage_order.index("file_name_input"):
            try:
                click_template("file_name_input", timeout_seconds=5)
                _LOG_STEP("图片识别点击文件名输入框成功")
            except Exception as exc:
                _LOG_STEP(f"文件名输入框图片未匹配，改用 Alt+N: {exc}")
                send_keys("%n")
                time.sleep(0.5)

            send_keys("^a")
            time.sleep(0.2)
            send_keys(projection_file_path)
            _LOG_STEP(f"图片识别流程已输入投影文件路径: {projection_file_path}")
            time.sleep(0.3)
            _CONFIRM_OPEN_FILE_DIALOG(timeout_seconds=3)
            _LOG_STEP("图片识别流程已确认打开文件")
            time.sleep(1)
            ai_resume_stage = "apply_button"

        if start_index <= stage_order.index("apply_button"):
            log_projection_debug_context("before_apply_button")
            try:
                click_button_in_config_window("应用", timeout_seconds=4, flow_control_id="apply_button")
                _LOG_STEP("控件点击应用成功")
            except Exception as exc:
                _LOG_STEP(f"控件点击应用失败，改用图片模板: {exc}")
                click_template("apply_button", timeout_seconds=8)
                _LOG_STEP("图片识别点击应用成功")
            time.sleep(0.5)
            ai_resume_stage = "ok_button"

        if start_index <= stage_order.index("ok_button"):
            try:
                click_button_in_config_window("确定", timeout_seconds=4, flow_control_id="ok_button")
                _LOG_STEP("控件点击确定成功")
            except Exception as exc:
                _LOG_STEP(f"控件点击确定失败，改用图片模板: {exc}")
                click_template("ok_button", timeout_seconds=8)
                _LOG_STEP("图片识别点击确定成功")
            time.sleep(0.5)
    except Exception as exc:
        raise StageExecutionError(str(exc), ai_resume_stage) from exc
