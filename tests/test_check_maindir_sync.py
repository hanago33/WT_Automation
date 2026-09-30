# encoding: utf-8
"""tools/check_maindir_sync.py 测试（主目录 ↔ 主干同步盘点）。

覆盖：
- classify 三分类（纯函数）；
- format_report 对「本地变体 / 主目录独有」的告警渲染；
- 临时双仓库端到端：一致 / 漂移（M+A+D）/ 本地变体三类场景。
"""
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "tools"))

import check_maindir_sync as C  # noqa: E402


# ── 纯函数：三分类 ────────────────────────────────────────────────────────────

def test_classify_splits_three_buckets():
    maindir = {"same.py": "1", "changed.py": "2", "only_main.py": "3"}
    trunk = {"same.py": "1", "changed.py": "9", "only_trunk.py": "4"}
    modified, added, deleted = C.classify(maindir, trunk)
    assert modified == ["changed.py"]
    assert added == ["only_trunk.py"]
    assert deleted == ["only_main.py"]


def test_classify_identical_trees_is_empty():
    tree = {"a.py": "1", "b.py": "2"}
    assert C.classify(tree, dict(tree)) == ([], [], [])


# ── 报告渲染 ─────────────────────────────────────────────────────────────────

def _result(modified=(), added=(), deleted=(), historical=(), local_only=()):
    return {
        "trunk_count": 10, "maindir_count": 8,
        "modified": list(modified), "added": list(added), "deleted": list(deleted),
        "historical": list(historical), "local_only": list(local_only),
    }


def test_report_flags_local_variant_and_maindir_only():
    report = C.format_report(
        _result(modified=["m.py"], added=["a.py"], deleted=["d.py"],
                historical=[], local_only=["m.py"]))
    assert "本地变体" in report and "m.py" in report
    assert "主目录独有" in report and "d.py" in report


def test_report_quiet_when_in_sync():
    report = C.format_report(_result())
    assert "!!" not in report                      # 无告警块（计数行为 0 不算告警）
    assert "M 需覆盖: 0" in report
    assert "A 需新增: 0" in report
    assert "D 主目录独有: 0" in report


# ── 端到端：临时双仓库 ────────────────────────────────────────────────────────

pytestmark_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def _git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "core.autocrlf", "false")


def _write(path, rel, text):
    full = os.path.join(path, rel)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _commit(repo, message="c"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytestmark_git
def test_inspect_reports_no_drift_when_aligned(tmp_path, monkeypatch):
    trunk = tmp_path / "trunk"
    maindir = tmp_path / "maindir"
    for repo in (trunk, maindir):
        os.makedirs(repo)
        _init_repo(str(repo))
        _write(str(repo), "a.py", "x = 1\n")
        _commit(str(repo))

    monkeypatch.chdir(trunk)
    result = C.inspect(str(trunk), str(maindir), "HEAD")
    assert (result["modified"], result["added"], result["deleted"]) == ([], [], [])
    assert C.main(["--maindir", str(maindir), "--ref", "HEAD"]) == 0


@pytestmark_git
def test_inspect_classifies_history_version_as_zero_loss(tmp_path, monkeypatch):
    """主目录落后一个提交（即主目录版本是主干历史 blob）→ M 项归入 historical。"""
    trunk = tmp_path / "trunk"
    maindir = tmp_path / "maindir"
    os.makedirs(trunk)
    os.makedirs(maindir)
    _init_repo(str(trunk))
    _init_repo(str(maindir))

    for repo in (trunk, maindir):           # v1：两边一致（同一份字节 → 同一 blob）
        _write(str(repo), "app.py", "v = 1\n")
        _write(str(repo), "keep.py", "k = 1\n")
        _commit(str(repo))
    _write(str(trunk), "app.py", "v = 2\n")  # v2：主干先行
    _write(str(trunk), "new.py", "n = 1\n")
    _commit(str(trunk), "v2")
    _write(str(maindir), "local_only.py", "l = 1\n")   # 主目录独有
    _commit(str(maindir), "maindir 独有文件")

    monkeypatch.chdir(trunk)
    result = C.inspect(str(trunk), str(maindir), "HEAD")
    assert result["modified"] == ["app.py"]
    assert result["added"] == ["new.py"]
    assert result["deleted"] == ["local_only.py"]
    assert result["historical"] == ["app.py"]
    assert result["local_only"] == []
    assert C.main(["--maindir", str(maindir), "--ref", "HEAD"]) == 1


@pytestmark_git
def test_main_run_from_subdirectory_anchors_to_repo_root(tmp_path, monkeypatch, capsys):
    """审计 P1 回归：从子目录运行 main() 必须按仓库根整树盘点。

    基线缺陷：trunk_tree 取自 cwd（子目录）→ 只见子目录子集，且路径丢失
    sub/ 前缀，仍结论"可安全同步"。修复后 toplevel 锚定，A 项带 sub/ 前缀。
    """
    trunk = tmp_path / "trunk"
    maindir = tmp_path / "maindir"
    for repo in (trunk, maindir):
        os.makedirs(repo)
        _init_repo(str(repo))
    _write(str(trunk), "a.py", "x = 1\n")
    _write(str(trunk), "sub/b.py", "y = 1\n")
    _write(str(trunk), "sub/c.py", "z = 1\n")
    _commit(str(trunk))
    _write(str(maindir), "a.py", "x = 1\n")
    _commit(str(maindir))

    monkeypatch.chdir(trunk / "sub")  # _write 时已创建
    assert C.main(["--maindir", str(maindir), "--ref", "HEAD"]) == 1
    out = capsys.readouterr().out
    assert "sub/b.py" in out and "sub/c.py" in out   # 修复前是丢前缀的 b.py/c.py
    assert "结论：主目录与主干已一致" not in out


@pytestmark_git
def test_inspect_flags_local_variant(tmp_path, monkeypatch):
    """主目录改过但主干从未提交过该内容 → 必须报「本地变体」。"""
    trunk = tmp_path / "trunk"
    maindir = tmp_path / "maindir"
    for repo in (trunk, maindir):
        os.makedirs(repo)
        _init_repo(str(repo))
        _write(str(repo), "app.py", "v = 1\n")
        _commit(str(repo))
    _write(str(maindir), "app.py", "v = 99 # 主目录本地改的\n")
    _commit(str(maindir), "local edit")

    monkeypatch.chdir(trunk)
    result = C.inspect(str(trunk), str(maindir), "HEAD")
    assert result["modified"] == ["app.py"]
    assert result["local_only"] == ["app.py"]
    assert result["historical"] == []
