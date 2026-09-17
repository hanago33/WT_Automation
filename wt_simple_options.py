# encoding: utf-8
"""Simple 模式「项目计算参数」下拉选项的纯逻辑层。

把「下拉选项列表」的规范化 / 新增 / 删除 / 重命名 / 排序 / 恢复默认规则从
`WT_Launcher` 的 GUI 代码里抽出来，使其不依赖 Tk，可直接单测。

设计约定
--------
1. 选项值统一 `strip()` 后保存，空串一律丢弃；
2. 列表保持插入顺序，去重以**首次出现的位置**为准；
3. 每次操作返回 ``(新列表, 结果码)``，调用方据结果码决定提示文案，
   不抛异常、不弹窗，保证纯函数可测；
4. 结果码集中为 ``RESULT_*`` 常量，调用方不要硬编码字符串；
5. **选项列表是用户唯一权威来源**：首次使用（launcher_state 无该键）才播种
   ``OPTION_KINDS[*]["defaults"]``；一旦存在显式列表（含空列表）即完全尊重，
   不会因为「删空了」而被默认值复活。恢复默认请走 ``reset_options``。
"""

from __future__ import annotations

# ── 结果码 ──────────────────────────────────────────────────────────────
RESULT_ADDED = "added"
RESULT_DUPLICATE = "duplicate"
RESULT_EMPTY = "empty"
RESULT_TOO_LONG = "too_long"
RESULT_REMOVED = "removed"
RESULT_NOT_FOUND = "not_found"
RESULT_RENAMED = "renamed"
RESULT_MOVED = "moved"
RESULT_BOUNDARY = "boundary"
RESULT_RESET = "reset"

#: 单个选项的最大长度（防止误粘整段文本进下拉框）
MAX_OPTION_LENGTH = 64

# ── 默认种子（首次使用时的「固定几个」，可在管理对话框里恢复）──────────
#: Cp 版本：取自仓库既有实际取值（发送综合计算参数矩阵）
DEFAULT_CP_VERSION_OPTIONS = ("Cp0.429", "Cp0.425", "Cp0.405")
#: 风机类型/型号：取自仓库既有实际机型（功率曲线文件名 / 参数矩阵）
DEFAULT_TURBINE_TYPE_OPTIONS = ("WT6250D220", "WT7150D200", "WT10000D230")

#: 下拉选项字段的元数据。GUI 只遍历本表，新增可选字段时改这里即可。
OPTION_KINDS = {
    "cpVersion": {
        "title": "Cp 版本",
        "attr": "cp_version_options",        # LauncherApp 上的列表属性名
        "state_key": "cpVersionOptions",     # launcher_state.json 里的键名
        "defaults": DEFAULT_CP_VERSION_OPTIONS,
        "prompt": "输入新的 Cp 版本号（如 Cp0.429）：",
        "example": "Cp0.429",
    },
    "turbineType": {
        "title": "风机类型/型号",
        "attr": "turbine_type_options",
        "state_key": "turbineTypeOptions",
        "defaults": DEFAULT_TURBINE_TYPE_OPTIONS,
        "prompt": "输入新的风机类型/型号（如 WT6250D220）：",
        "example": "WT6250D220",
    },
}

#: 支持「新建 / 删除」的下拉字段键集合，供 GUI 判定分支
OPTION_KIND_KEYS = tuple(OPTION_KINDS.keys())


def clean_value(value) -> str:
    """选项值标准化：转字符串 + 去首尾空白。"""
    return str(value if value is not None else "").strip()


def validate_value(value):
    """校验单个候选选项。

    :return: ``(清洗后的值, None)`` 表示通过；``(None, 结果码)`` 表示不通过。
    """
    cleaned = clean_value(value)
    if not cleaned:
        return None, RESULT_EMPTY
    if len(cleaned) > MAX_OPTION_LENGTH:
        return None, RESULT_TOO_LONG
    return cleaned, None


def _as_sequence(raw):
    """把入参规整成待处理序列。

    字符串必须当成**单个值**：直接 ``list("Cp0.429")`` 会被逐字符拆成
    ``['C','p','0','.','4','2','9']``，是个很隐蔽的坑。
    """
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)):
        return [raw]
    try:
        return list(raw)
    except TypeError:
        return [raw]


def normalize_options(raw, defaults=()):
    """把任意来源的列表规范化：strip → 去空 → 去重（保序）→ 补齐默认项。

    字符串入参按「单个选项」处理，不会被逐字符拆开。
    """
    result = []
    for item in _as_sequence(raw) + _as_sequence(defaults):
        cleaned = clean_value(item)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def load_options(raw, defaults=()):
    """从 launcher_state 读到的原始值解析出选项列表。

    - ``raw`` 是 list（哪怕是空列表）→ **完全尊重**，不再补默认项；
    - ``raw`` 不是 list（键缺失 / 类型异常）→ 视为首次使用，播种 ``defaults``。
      （这里刻意不把裸字符串当单个选项：持久化状态出现字符串说明已损坏，
      回落到内置默认比塞进一个可疑值更安全。）
    """
    if isinstance(raw, list):
        return normalize_options(raw)
    return normalize_options((), defaults)


def add_option(options, value):
    """新增一个选项。重复值不报错，返回 ``RESULT_DUPLICATE`` 供调用方提示。"""
    options = list(options or ())
    cleaned, err = validate_value(value)
    if err:
        return options, err
    if cleaned in options:
        return options, RESULT_DUPLICATE
    return options + [cleaned], RESULT_ADDED


def remove_option(options, value):
    """删除单个选项。"""
    options = list(options or ())
    cleaned = clean_value(value)
    if not cleaned:
        return options, RESULT_EMPTY
    if cleaned not in options:
        return options, RESULT_NOT_FOUND
    return [item for item in options if item != cleaned], RESULT_REMOVED


def remove_options(options, values):
    """批量删除（管理对话框多选删除用）。"""
    options = list(options or ())
    targets = set()
    for item in values or ():
        cleaned = clean_value(item)
        if cleaned:
            targets.add(cleaned)
    if not targets:
        return options, RESULT_EMPTY
    kept = [item for item in options if item not in targets]
    if len(kept) == len(options):
        return options, RESULT_NOT_FOUND
    return kept, RESULT_REMOVED


def rename_option(options, old_value, new_value):
    """重命名选项（保持原位置）。"""
    options = list(options or ())
    old_cleaned = clean_value(old_value)
    new_cleaned, err = validate_value(new_value)
    if err:
        return options, err
    if old_cleaned not in options:
        return options, RESULT_NOT_FOUND
    if old_cleaned == new_cleaned:
        return options, RESULT_RENAMED
    if new_cleaned in options:
        return options, RESULT_DUPLICATE
    return [new_cleaned if item == old_cleaned else item for item in options], RESULT_RENAMED


def move_option(options, value, delta):
    """把选项上移/下移 ``delta`` 位（``delta`` 为负=上移）。"""
    options = list(options or ())
    cleaned = clean_value(value)
    if cleaned not in options:
        return options, RESULT_NOT_FOUND
    index = options.index(cleaned)
    target = index + int(delta)
    if target < 0 or target >= len(options):
        return options, RESULT_BOUNDARY
    options.pop(index)
    options.insert(target, cleaned)
    return options, RESULT_MOVED


def reset_options(defaults=()):
    """恢复为默认种子列表。"""
    return normalize_options((), defaults)


def describe_result(kind, status, value="", count=1):
    """把结果码翻成给用户看的一句话（GUI 与测试共用，保证文案一致）。"""
    meta = OPTION_KINDS.get(kind) or {}
    title = meta.get("title", "下拉选项")
    value = clean_value(value)
    if status == RESULT_ADDED:
        return "已新增「{}」选项：{}".format(title, value)
    if status == RESULT_DUPLICATE:
        return "「{}」已存在于选项中，已直接选中".format(value)
    if status == RESULT_EMPTY:
        return "选项内容不能为空"
    if status == RESULT_TOO_LONG:
        return "选项过长（上限 {} 个字符）".format(MAX_OPTION_LENGTH)
    if status == RESULT_REMOVED:
        if count > 1:
            return "已删除 {} 个「{}」选项".format(count, title)
        return "已删除「{}」选项：{}".format(title, value)
    if status == RESULT_NOT_FOUND:
        return "「{}」不在当前选项中".format(value)
    if status == RESULT_RENAMED:
        return "已重命名为：{}".format(value)
    if status == RESULT_MOVED:
        return "已调整选项顺序"
    if status == RESULT_BOUNDARY:
        return "已在列表边界，无法继续移动"
    if status == RESULT_RESET:
        return "「{}」选项已恢复为内置默认".format(title)
    return "「{}」选项已更新".format(title)


#: 结果码 → 状态栏语义色（idle / success / warning / error）
RESULT_STATUS_KIND = {
    RESULT_ADDED: "success",
    RESULT_REMOVED: "success",
    RESULT_RENAMED: "success",
    RESULT_MOVED: "idle",
    RESULT_RESET: "success",
    RESULT_DUPLICATE: "warning",
    RESULT_EMPTY: "warning",
    RESULT_TOO_LONG: "warning",
    RESULT_NOT_FOUND: "warning",
    RESULT_BOUNDARY: "warning",
}


def status_kind_for(status):
    """结果码对应的状态栏语义色。"""
    return RESULT_STATUS_KIND.get(status, "idle")
