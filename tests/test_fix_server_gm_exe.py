# encoding: utf-8
"""fix_server_gm_exe 校验/修复逻辑测试。

核心约定：现值是已存在的直接 .exe 时**不得**改动资源文件（verify-first）；
仅 .lnk / 空值 / 文件缺失才改写为 target；失败路径不改文件并以退出码 1 暴露。
"""
import sys

import fix_server_gm_exe


def _write_resource(path, gm_exe_value):
    lines = ["*** Variables ***"]
    if gm_exe_value is not None:
        # 不能用 .format：${GM_EXE} 会被 str.format 当占位符解析
        lines.append("${GM_EXE}               " + gm_exe_value)
    lines.append("${OTHER_VAR}             whatever")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_read_value_unescapes_double_backslash(tmp_path):
    resource = _write_resource(
        tmp_path / "project_config.resource",
        r"C:\\Program Files\\Meteodyn\\MeteodynUniverse\\MUPSmartClient.exe",
    )
    content = resource.read_text(encoding="utf-8")
    assert fix_server_gm_exe.read_gm_exe_value(content) == (
        r"C:\Program Files\Meteodyn\MeteodynUniverse\MUPSmartClient.exe"
    )


def test_read_value_returns_none_when_line_absent(tmp_path):
    resource = _write_resource(tmp_path / "project_config.resource", None)
    content = resource.read_text(encoding="utf-8")
    assert fix_server_gm_exe.read_gm_exe_value(content) is None


def test_verify_ok_keeps_direct_exe_unchanged(tmp_path):
    exe = tmp_path / "MUPSmartClient.exe"
    exe.write_bytes(b"")
    value = str(tmp_path / "MUPSmartClient.exe")
    resource = _write_resource(tmp_path / "project_config.resource", value.replace("\\", "\\\\"))
    before = resource.read_text(encoding="utf-8")

    changed, ok, message = fix_server_gm_exe.fix_gm_exe(
        resource_path=str(resource), target=str(exe)
    )

    assert (changed, ok) == (False, True)
    assert "无需修复" in message
    assert resource.read_text(encoding="utf-8") == before


def test_fix_rewrites_lnk_even_when_lnk_exists(tmp_path):
    lnk = tmp_path / "MUPSmartClient.lnk"
    lnk.write_bytes(b"")
    exe = tmp_path / "real" / "MUPSmartClient.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    resource = _write_resource(tmp_path / "project_config.resource", str(lnk).replace("\\", "\\\\"))

    changed, ok, message = fix_server_gm_exe.fix_gm_exe(
        resource_path=str(resource), target=str(exe)
    )

    assert (changed, ok) == (True, True)
    assert ".lnk" in message
    assert str(exe).replace("\\", "\\\\") in resource.read_text(encoding="utf-8")


def test_fix_rewrites_value_pointing_to_missing_file(tmp_path):
    exe = tmp_path / "MUPSmartClient.exe"
    exe.write_bytes(b"")
    resource = _write_resource(
        tmp_path / "project_config.resource",
        str(tmp_path / "gone" / "MUPSmartClient.exe").replace("\\", "\\\\"),
    )

    changed, ok, message = fix_server_gm_exe.fix_gm_exe(
        resource_path=str(resource), target=str(exe)
    )

    assert (changed, ok) == (True, True)
    assert str(exe).replace("\\", "\\\\") in resource.read_text(encoding="utf-8")


def test_fail_when_target_exe_missing(tmp_path):
    resource = _write_resource(
        tmp_path / "project_config.resource",
        str(tmp_path / "gone.lnk").replace("\\", "\\\\"),
    )
    before = resource.read_text(encoding="utf-8")

    changed, ok, message = fix_server_gm_exe.fix_gm_exe(
        resource_path=str(resource), target=str(tmp_path / "no_such.exe")
    )

    assert (changed, ok) == (False, False)
    assert "目标不存在" in message
    assert resource.read_text(encoding="utf-8") == before


def test_fail_when_gm_exe_line_absent(tmp_path):
    exe = tmp_path / "MUPSmartClient.exe"
    exe.write_bytes(b"")
    resource = _write_resource(tmp_path / "project_config.resource", None)
    before = resource.read_text(encoding="utf-8")

    changed, ok, message = fix_server_gm_exe.fix_gm_exe(
        resource_path=str(resource), target=str(exe)
    )

    assert (changed, ok) == (False, False)
    assert "未找到" in message
    assert resource.read_text(encoding="utf-8") == before


def test_main_exit_code_ok_for_verified_resource(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "MUPSmartClient.exe"
    exe.write_bytes(b"")
    resource = _write_resource(tmp_path / "project_config.resource", str(exe).replace("\\", "\\\\"))
    monkeypatch.setattr(fix_server_gm_exe, "RESOURCE_PATH", str(resource))
    monkeypatch.setattr(sys, "argv", ["fix_server_gm_exe.py", "--target", str(exe)])

    assert fix_server_gm_exe.main() == 0
    assert "无需修复" in capsys.readouterr().out


def test_main_exit_code_1_when_target_missing(tmp_path, monkeypatch, capsys):
    resource = _write_resource(
        tmp_path / "project_config.resource",
        str(tmp_path / "gone.lnk").replace("\\", "\\\\"),
    )
    monkeypatch.setattr(fix_server_gm_exe, "RESOURCE_PATH", str(resource))
    monkeypatch.setattr(sys, "argv", ["fix_server_gm_exe.py", "--target", str(tmp_path / "no.exe")])

    assert fix_server_gm_exe.main() == 1
    assert "目标不存在" in capsys.readouterr().out
