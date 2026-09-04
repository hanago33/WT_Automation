# encoding: utf-8
"""诊断：当前会话与关键进程的 SessionId。判断 wt_task_server 与 MUP 是否同会话。

如果 MUPSmartClient.exe 的 SessionId 和 wt_task_server / wt_queue_selfcheck 不在同一个
session（甚至不在当前 PowerShell 所在 session），worker 子进程 EnumWindows 看不到
MUP 主窗口，健康检查只能找到自己 session 里的 CASCADIA 160x28 小窗口。
"""
import ctypes
import ctypes.wintypes
import os
import subprocess


def session_id(pid):
    sid = ctypes.wintypes.DWORD()
    if ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        return int(sid.value)
    return None


def is_elevated(pid):
    """查询进程是否以管理员(提权)令牌运行。失败返回 None（无权限或进程已退出）。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    token = ctypes.wintypes.HANDLE()
    try:
        if not ctypes.windll.advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        elevation = ctypes.wintypes.DWORD()
        size = ctypes.wintypes.DWORD()
        ok = ctypes.windll.advapi32.GetTokenInformation(
            token, TokenElevation, ctypes.byref(elevation),
            ctypes.sizeof(elevation), ctypes.byref(size),
        )
        return bool(elevation.value) if ok else None
    finally:
        if token.value:
            ctypes.windll.kernel32.CloseHandle(token)
        ctypes.windll.kernel32.CloseHandle(handle)


def run_powershell(ps_code):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_code],
            text=True, errors="replace", creationflags=flags,
        ).strip()
    except Exception as exc:
        return "(powershell 失败: {})".format(exc)


def collect_pids(name_re, cmdline_re=None):
    """返回匹配进程名(正则) / 可选命令行(正则) 的进程 PID 列表。"""
    where = ["$_.Name -match '{}'".format(name_re)]
    if cmdline_re:
        where.append("$_.CommandLine -match '{}'".format(cmdline_re))
    ps = (
        "Get-CimInstance Win32_Process | Where-Object {{ {} }}"
        " | Select-Object -ExpandProperty ProcessId"
    ).format(" -and ".join(where))
    out = run_powershell(ps)
    pids = []
    for token in out.replace(",", " ").split():
        if token.isdigit():
            pids.append(int(token))
    return pids


def _elev_str(pid):
    value = is_elevated(pid)
    if value is True:
        return "是(管理员)"
    if value is False:
        return "否(普通)"
    return "未知/无权限"


def main():
    here_sid = session_id(os.getpid())
    print("== 当前 PowerShell session ==")
    print("python pid={} session={}".format(os.getpid(), here_sid))
    print()

    print("== 登录会话(query session) ==")
    qexe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "query.exe")
    if os.path.isfile(qexe):
        try:
            out = subprocess.check_output([qexe, "session"], text=True, errors="replace")
            print(out.strip())
        except Exception as exc:
            print("(query session 失败:", exc, ")")
    else:
        print("({} 不存在)".format(qexe))
    print()

    print("== 关键进程 SessionId(MUP / 队列服务 / Launcher) ==")
    ps = (
        "Get-CimInstance Win32_Process | Where-Object {"
        "$_.Name -match '^(MUPSmartClient|python|cmd)\\.exe$' -and ("
        "$_.CommandLine -match 'MUPSmartClient|wt_task_server|wt_queue_selfcheck|WT_Launcher|WT_AUT_recorded'"
        ")"
        "} | Select-Object ProcessId, Name, SessionId, CommandLine"
        " | Format-Table -AutoSize -Wrap | Out-String -Width 4096"
    )
    out = run_powershell(ps)
    print(out if out else "(无匹配进程)")
    print()

    print("== 判定 ==")
    print("你当前 PowerShell 在 session {}。".format(here_sid))
    print("- MUPSmartClient.exe 的 SessionId 应 == 你当前 session（{}），说明 MUP 在你眼前。".format(here_sid))
    print("- wt_task_server / wt_queue_selfcheck 的 SessionId 也应 == {}，".format(here_sid))
    print("  否则 worker 子进程 EnumWindows 看不到 MUP 主窗口 -> 健康检查只看到 CASCADIA 160x28。")
    print()
    if here_sid is not None:
        mup_sids = set(session_id(p) for p in collect_pids(r"^MUPSmartClient\.exe$"))
        svc_sids = set(session_id(p) for p in collect_pids(r"^(python|cmd)\.exe$", r"wt_task_server|wt_queue_selfcheck"))
        for label, sids in (("MUPSmartClient", mup_sids), ("队列服务(wt_task_server/wt_queue_selfcheck)", svc_sids)):
            if sids and sids != {here_sid}:
                print("  [MISMATCH] {} 不在当前 session：{} != 当前 {}".format(label, sids, here_sid))
                print("             -> 必须杀净该进程，再从当前 PowerShell 用 --server --force 重启。")

    print()
    print("== UIPI 权限一致性检查 ==")
    here_elv = is_elevated(os.getpid())
    print("当前 PowerShell 提权状态：{}".format(_elev_str(os.getpid())))
    for pid in collect_pids(r"^MUPSmartClient\.exe$"):
        print("  MUPSmartClient.exe  pid={} session={} 提权={}".format(pid, session_id(pid), _elev_str(pid)))
    svc_pids = collect_pids(r"^(python|cmd)\.exe$", r"wt_task_server|wt_queue_selfcheck")
    for pid in svc_pids:
        print("  队列服务进程        pid={} session={} 提权={}".format(pid, session_id(pid), _elev_str(pid)))
    if here_elv is not None and svc_pids:
        mup_elv = [is_elevated(p) for p in collect_pids(r"^MUPSmartClient\.exe$")]
        svc_elv = [is_elevated(p) for p in svc_pids]
        mup_elevated = any(v is True for v in mup_elv)
        svc_elevated = any(v is True for v in svc_elv)
        if mup_elevated and not svc_elevated:
            print("  [UIPI 风险] MUP 以管理员运行，而队列服务为普通权限。")
            print("              worker 的 UI 操作可能被 UIPI 拦截 -> 步骤报『未命中控件』。")
            print("              建议：统一为普通权限重启 MUP；或把服务也提权运行（两者必须一致）。")
        elif mup_elv and mup_elevated == svc_elevated:
            print("  [OK] MUP 与队列服务提权状态一致（都{}）。".format("管理员" if mup_elevated else "普通权限"))
        else:
            print("  [INFO] 无法读取全部关键进程提权状态，跳过 UIPI 判定。")
    else:
        print("  [INFO] 当前无队列服务进程运行，跳过 MUP 与服务间的 UIPI 判定。")


if __name__ == "__main__":
    main()