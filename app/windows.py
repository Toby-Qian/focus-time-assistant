"""Windows APIs: foreground app, last input, lock state, memory trim and instance wake-up."""
import ctypes as c
from ctypes import wintypes as w
from pathlib import PureWindowsPath

u = c.WinDLL("user32", use_last_error=True)
k = c.WinDLL("kernel32", use_last_error=True)
u.GetForegroundWindow.restype = w.HWND
u.GetWindowTextLengthW.argtypes = [w.HWND]
u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
u.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
u.OpenInputDesktop.restype = w.HANDLE
u.CloseDesktop.argtypes = [w.HANDLE]
u.SwitchDesktop.argtypes = [w.HANDLE]
u.AllowSetForegroundWindow.argtypes = [w.DWORD]
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
k.CloseHandle.argtypes = [w.HANDLE]
k.GetTickCount64.restype = c.c_ulonglong
k.GetCurrentProcess.restype = w.HANDLE
k.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
k.CreateEventW.restype = w.HANDLE
k.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
k.OpenEventW.restype = w.HANDLE
k.SetEvent.argtypes = [w.HANDLE]
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.WaitForSingleObject.restype = w.DWORD
k.K32EmptyWorkingSet.argtypes = [w.HANDLE]

u.IsWindowVisible.argtypes = [w.HWND]
u.IsIconic.argtypes = [w.HWND]
u.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
u.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
u.EnumChildWindows.argtypes = [w.HWND, c.c_void_p, w.LPARAM]
u.GetWindow.argtypes = [w.HWND, w.UINT]
u.GetWindow.restype = w.HWND
u.GetWindowLongW.argtypes = [w.HWND, c.c_int]
u.GetWindowLongW.restype = c.c_long
try:
    dwm = c.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
except OSError:
    dwm = None

SUFFIXES = (" - Google Chrome", " - Microsoft Edge", " — Mozilla Firefox", " - Brave", " - Chromium")
# Shell surfaces that can hold keyboard focus but are not an application being used.
SHELL_CLASSES = {"Progman": "桌面", "WorkerW": "桌面", "Shell_TrayWnd": "任务栏", "Shell_SecondaryTrayWnd": "任务栏",
                 "NotifyIconOverflowWindow": "托盘区", "TopLevelWindowForOverflowXamlIsland": "托盘区",
                 "TaskListThumbnailWnd": "任务栏预览", "MultitaskingViewFrame": "任务切换", "XamlExplorerHostIslandWindow": "任务切换",
                 "ForegroundStaging": "任务切换", "TaskSwitcherWnd": "任务切换"}
SHELL_APPS = {"startmenuexperiencehost.exe": "开始菜单", "searchhost.exe": "搜索", "searchapp.exe": "搜索",
              "shellexperiencehost.exe": "系统面板", "shellhost.exe": "系统面板", "lockapp.exe": "锁屏",
              "textinputhost.exe": "输入法面板", "screenclippinghost.exe": "截图工具栏"}
MIN_SIDE = 48  # physical pixels; smaller focused windows are helper / hidden windows


class LastInput(c.Structure):
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]


# Buffers are reused across the once-per-second samples instead of reallocated.
_input = LastInput(c.sizeof(LastInput), 0)
_pid = w.DWORD()
_path = c.create_unicode_buffer(1024)
_size = w.DWORD()
_title = c.create_unicode_buffer(512)
_names = {}
paths = {}          # program name → full .exe path, for list icons (see appicons.py)
paths_dirty = False
paths_version = 0   # bumped whenever a path is learned, so cached icons refresh
_missing = {}       # program name → monotonic time of the last failed lookup
_descriptions = {}  # .exe path → its FileDescription (or ProductName)
k.K32EnumProcesses.argtypes = [c.POINTER(w.DWORD), w.DWORD, c.POINTER(w.DWORD)]
_ver = c.WinDLL("version")
_ver.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
_ver.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID]
_ver.VerQueryValueW.argtypes = [w.LPCVOID, w.LPCWSTR, c.POINTER(w.LPVOID), c.POINTER(w.UINT)]


def _learn(name, full):
    global paths_dirty, paths_version
    if full and paths.get(name) != full:
        paths[name] = full; paths_dirty = True; paths_version += 1


def find_path(name):
    """Where a program lives: learned while sampling, else the App Paths registry, else a running
    process with that name. A failed lookup is retried at most once a minute."""
    import time, winreg, os
    if name in paths:
        return paths[name]
    now = time.monotonic()
    if now-_missing.get(name, -1e9) < 60:
        return None
    _missing[name] = now
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                value = os.path.expandvars(winreg.QueryValue(key, None) or "").strip().strip('"')
            if value and os.path.isfile(value):
                _learn(name, value); return value
        except OSError:
            pass
    _scan_processes(name)
    return paths.get(name)


def _scan_processes(target):
    """Look for a running process named `target` and remember only its path (a few milliseconds)."""
    pids = (w.DWORD*4096)(); needed = w.DWORD()
    if not k.K32EnumProcesses(pids, c.sizeof(pids), c.byref(needed)):
        return
    for pid in pids[:needed.value//c.sizeof(w.DWORD)]:
        full = _image_path(pid) if pid else ""
        if full and PureWindowsPath(full).name.lower() == target:
            _learn(target, full); return


def describe(path):
    """The program's own display name from its version resource, e.g. "Windows Terminal"."""
    if path in _descriptions:
        return _descriptions[path]
    result = ""
    try:
        size = _ver.GetFileVersionInfoSizeW(path, None)
        if size:
            data = c.create_string_buffer(size)
            if _ver.GetFileVersionInfoW(path, 0, size, data):
                pointer, length = w.LPVOID(), w.UINT()
                if _ver.VerQueryValueW(data, "\\VarFileInfo\\Translation", c.byref(pointer), c.byref(length)) and length.value >= 4:
                    words = c.cast(pointer, c.POINTER(w.WORD)); lang, page = words[0], words[1]
                    for field in ("FileDescription", "ProductName"):
                        if _ver.VerQueryValueW(data, f"\\StringFileInfo\\{lang:04x}{page:04x}\\{field}", c.byref(pointer), c.byref(length)) and length.value > 1:
                            text = c.wstring_at(pointer, length.value-1).strip()
                            if text and len(text) <= 40 and not text.lower().endswith(".exe"):
                                result = text; break
    except (OSError, ValueError):
        pass
    _descriptions[path] = result
    return result
_class = c.create_unicode_buffer(256)
_rect = w.RECT()
_cloaked = w.DWORD()
last_pid = 0  # process id of the most recent foreground window
# Why the most recent foreground window is not an app being used ("" = it is one).
last_background = ""
# True when the foreground window belonged to the skipped process and the window below it was used.
last_skipped = False


def idle_age():
    if not u.GetLastInputInfo(c.byref(_input)):
        raise c.WinError(c.get_last_error())
    return ((k.GetTickCount64() & 0xFFFFFFFF)-_input.dwTime & 0xFFFFFFFF)/1000


def locked():
    handle = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not handle:
        return True
    try:
        return not bool(u.SwitchDesktop(handle))
    finally:
        u.CloseDesktop(handle)


def _image_path(pid):
    handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        _size.value = len(_path)
        return _path.value if k.QueryFullProcessImageNameW(handle, 0, _path, c.byref(_size)) else ""
    finally:
        k.CloseHandle(handle)


def _image_name(pid):
    """Program name of a foreground process; its path is remembered for the list icon."""
    full = _image_path(pid)
    if not full:
        return ""
    name = PureWindowsPath(full).name.lower()
    _learn(name, full)
    return name


def _hosted_app(hwnd, host_pid):
    """UWP apps run inside ApplicationFrameHost; the real app owns a child window."""
    found = []

    @c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def visit(child, _):
        pid = w.DWORD(); u.GetWindowThreadProcessId(child, c.byref(pid))
        if pid.value and pid.value != host_pid:
            found.append(pid.value); return False
        return True

    u.EnumChildWindows(hwnd, visit, 0)
    return (_image_name(found[0]), found[0]) if found else ("", 0)


def window_kind(hwnd, app):
    """Return "" for a window the user is operating, otherwise a short reason.

    Background processes never own the foreground window, but the foreground can
    still sit on something that is not an application in use: the desktop, the
    taskbar, the Start menu, the task switcher, a hidden or tiny helper window
    that took focus, a minimised window, or a window cloaked on another desktop.
    """
    u.GetClassNameW(hwnd, _class, len(_class))
    shell = SHELL_CLASSES.get(_class.value) or SHELL_APPS.get(app)
    if shell:
        return shell
    if not u.IsWindowVisible(hwnd):
        return "隐藏窗口"
    if u.IsIconic(hwnd):
        return "已最小化"
    if dwm and not dwm.DwmGetWindowAttribute(hwnd, 14, c.byref(_cloaked), 4) and _cloaked.value:  # DWMWA_CLOAKED
        return "其他桌面"
    if u.GetWindowRect(hwnd, c.byref(_rect)) and (_rect.right-_rect.left < MIN_SIDE or _rect.bottom-_rect.top < MIN_SIDE):
        return "隐藏窗口"
    return ""


def _below(hwnd, skip_pid):
    """First real application window under `hwnd` in Z-order that belongs to another process."""
    pid = w.DWORD()
    for _ in range(500):
        hwnd = u.GetWindow(hwnd, 2)  # GW_HWNDNEXT: the next window down
        if not hwnd:
            return None
        u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if pid.value == skip_pid or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            continue
        u.GetClassNameW(hwnd, _class, len(_class))
        if _class.value in SHELL_CLASSES:
            return hwnd  # reached the desktop: nothing else is open
        if u.GetWindowTextLengthW(hwnd) == 0 or u.GetWindowLongW(hwnd, -20) & 0x80:  # untitled or WS_EX_TOOLWINDOW
            continue
        if dwm and not dwm.DwmGetWindowAttribute(hwnd, 14, c.byref(_cloaked), 4) and _cloaked.value:
            continue  # on another virtual desktop
        if u.GetWindowRect(hwnd, c.byref(_rect)) and (_rect.right-_rect.left < MIN_SIDE or _rect.bottom-_rect.top < MIN_SIDE):
            continue
        return hwnd
    return None


def foreground(skip_pid=None):
    """(app, title) of the foreground window. With `skip_pid`, a window of that process is
    looked through: the first application window below it is reported instead."""
    global _title, last_pid, last_background, last_skipped
    hwnd = u.GetForegroundWindow(); last_skipped = False
    if not hwnd:
        last_pid = 0; last_background = "无前台窗口"
        return "", ""
    if skip_pid:
        u.GetWindowThreadProcessId(hwnd, c.byref(_pid))
        if _pid.value == skip_pid:
            below = _below(hwnd, skip_pid)
            if below:
                hwnd = below; last_skipped = True
    length = u.GetWindowTextLengthW(hwnd)
    if length >= len(_title):
        _title = c.create_unicode_buffer(length+64)
    _title.value = ""
    u.GetWindowTextW(hwnd, _title, len(_title))
    title = _title.value
    u.GetWindowThreadProcessId(hwnd, c.byref(_pid))
    # The window handle pins the process, so (hwnd, pid) can be cached safely.
    key = (hwnd, _pid.value)
    cached = _names.get(key)
    if cached is None:
        if len(_names) > 256:
            _names.clear()
        app, pid = _image_name(_pid.value), _pid.value
        if app == "applicationframehost.exe":
            hosted, hosted_pid = _hosted_app(hwnd, _pid.value)
            if hosted:
                app, pid = hosted, hosted_pid
                _names[key] = (app, pid)  # cache only once the hosted app is known
        else:
            _names[key] = (app, pid)
    else:
        app, pid = cached
    last_pid = pid
    last_background = window_kind(hwnd, app)
    for suffix in SUFFIXES:
        if title.endswith(suffix):
            return app, title[:-len(suffix)]
    return app, title


_animations = None


def animations_enabled():
    """Windows Settings → Accessibility → Visual effects → Animation effects (read once per run)."""
    global _animations
    if _animations is None:
        value = w.BOOL(True)
        try:
            ok = u.SystemParametersInfoW(0x1042, 0, c.byref(value), 0)  # SPI_GETCLIENTAREAANIMATION
            _animations = bool(value.value) if ok else True
        except Exception:
            _animations = True
    return _animations


def trim_memory():
    """Release resident pages while hidden; Windows pages them back in on demand."""
    try:
        k.K32EmptyWorkingSet(k.GetCurrentProcess())
    except (AttributeError, OSError):
        pass


def wake_event(name, create):
    """Auto-reset named event used by a second launch to show the running window."""
    if create:
        return k.CreateEventW(None, False, False, name)
    return k.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE


def signal_wake(name):
    handle = wake_event(name, False)
    if not handle:
        return False
    try:
        u.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY: let the running window come to the front
        return bool(k.SetEvent(handle))
    finally:
        k.CloseHandle(handle)


def wait(handle, milliseconds=0xFFFFFFFF):
    return k.WaitForSingleObject(handle, milliseconds) == 0


def wait_process(pid, milliseconds):
    """Block until process `pid` has exited (used when the program restarts itself)."""
    handle = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if handle:
        try: k.WaitForSingleObject(handle, milliseconds)
        finally: k.CloseHandle(handle)


def dark_title_bar(hwnd, enabled=True):
    """Windows 10 20H1+ / 11: draw the window's title bar in dark colours."""
    if not dwm: return
    value = c.c_int(1 if enabled else 0)
    dwm.DwmSetWindowAttribute(w.HWND(hwnd), 20, c.byref(value), c.sizeof(value))  # DWMWA_USE_IMMERSIVE_DARK_MODE
