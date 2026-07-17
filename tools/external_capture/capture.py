# encoding: utf-8
"""外部控件采集统一入口 —— uia-peek / axe-windows 适配器的命令行 dispatcher。

这两个适配器是 WT_Automation 现有 pywinauto 采集（build_control_map_library.py）
的【可选补充】，互不影响。本模块只做参数转发，实际逻辑在各 client。

用法::
    python tools/external_capture/capture.py uiapeek --focused
    python tools/external_capture/capture.py uiapeek --x 250 --y 300
    python tools/external_capture/capture.py uiapeek --record 10
    python tools/external_capture/capture.py axewindows --pid 1234
    python tools/external_capture/capture.py axewindows --pid 1234 --bridge
    python tools/external_capture/capture.py axewindows --find-cli

详见 README.md。
"""
import os
import sys


def _dispatch(argv):
    if not argv:
        _print_help()
        return 2
    source = argv[0].lower()
    rest = argv[1:]

    if source in ("uiapeek", "uia-peek", "peek"):
        from . import uiapeek_client as mod
    elif source in ("axewindows", "axe-windows", "axe"):
        from . import axewindows_client as mod
    elif source in ("-h", "--help", "help"):
        _print_help()
        return 0
    else:
        print("未知来源: {}（应为 uiapeek 或 axewindows）".format(source))
        _print_help()
        return 2

    sys.argv = ["capture.py"] + rest
    return mod._main()


def _print_help():
    print(__doc__)


def main():
    sys.exit(_dispatch(sys.argv[1:]))


if __name__ == "__main__":
    # 支持 python tools/external_capture/capture.py ...
    _repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    main()
