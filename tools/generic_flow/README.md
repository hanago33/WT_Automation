# 通用化自动化副本（去 WT/MUP 绑定）

本目录是**通用软件自动化**的副本，从项目根目录的
`wt_flow_locator.py` / `wt_flow_executor.py` / `WT_AUT_recorded.py`
**复制并改造**而来，**不修改原项目任何文件**。供其他软件做自动化适配。

## 目录内容
- `generic_flow_locator.py` / `generic_flow_executor.py` — 去 WT 化的定位/执行核心（alias 引用）
- `generic_automation.py` — 通用化主程序（原 `WT_AUT_recorded.py` 副本，启动前窗口预检已去 WT 写死）
- `generic_launcher.py` — 极简 GUI 总控台（原 `WT_Launcher.py` 的 7347 行 GUI 改为最小控件 + 通用化提权）
- `run_example.py` — 程序化调用示例
- `make_generic_copy.py` / `make_generic_automation.py` / `package_generic_release.py` — 生成/打包脚本（不进发布包）
- 本文件

## 与原版的关键区别（窗口检测层）
原版在多处写死 WT/MUP(Meteodyn / MUPSmartClient)：
- `WT_AUT_recorded.py`：`DEFAULT_GM_EXE`、`MAIN_WINDOW_TITLE_RE("Meteodyn Universe")`、
  `("MUPSmartClient",)` 主窗关键词、`_preflight_check_main_window` 预检；
- `WT_Launcher.py`：启动期按 `smartclient/meteodyn` 检测高权限目标进程并自动提权。

本副本改为由 `config_generic_target_app(exe=, title_re=, class_keywords=)` 注入目标软件，
未配置时**纯靠进程名关键词识别主窗**，不再有任何 WT 写死。

## 使用方如何运行

### 方式一（推荐）：GUI 总控台
```
python tools/generic_flow/generic_launcher.py
```
在界面填写「目标软件 exe 路径」（可选填窗口标题正则），点「运行自动化」。
（exe 文件名会自动作为进程名关键词，驱动窗口识别与预检。）

### 方式二：程序化调用
```python
import sys
sys.path.insert(0, r"路径/to/generic_flow")
sys.path.insert(0, r"路径/to/项目根")   # 含 wt_* 通用基础设施
import generic_automation

generic_automation.config_generic_target_app(
    exe=r"C:\MyApp\app.exe",
    title_re=None,                 # 留空：纯靠进程名识别主窗
    class_keywords=["app"],
)
generic_automation.FLOW_DEFINITION_FILE = r"路径/flow_definition.json"
generic_automation.run_automation(pre_raise=True)
```

## 重要提醒
- **控件库必须重采**：`control_maps/` 里的 frameworkId/automationId 是 WT 专属，换软件
  必须用 `build_control_map_library.py` 针对目标软件重新采集，否则控件定位必然失败。
- **依赖环境**：目标机需已部署完整运行版（含 wt_* 通用模块与 pywinauto 等依赖），
  本副本只带去 WT 化的代码，复用目标机已有的基础设施。
- **权限**：若目标软件以管理员运行，请同样以管理员启动本副本（或开启自动提权），
  否则 UIPI 隔离会导致读不到控件内容树。
- executor 的「投影历史校验」等业务功能依赖可选注入的 `mup_user_config`，
  通用化场景下不注入即可，不影响定位与执行。

## 重新生成副本（原项目更新后）
```
python tools/generic_flow/make_generic_copy.py
python tools/generic_flow/make_generic_automation.py
python tools/generic_flow/package_generic_release.py
```
