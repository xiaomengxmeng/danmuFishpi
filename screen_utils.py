"""Robust multi-monitor enumeration (Windows-focused, Qt-aware).

优先用 Qt 的 QScreen（DPI 正确的逻辑坐标），再用 Win32 EnumDisplayMonitors
交叉校验。若 Qt 漏报了某块屏，用 Win32 的 RECT 换算逻辑像素兜底。
"""
import sys


def _rect_match(a: dict, b: dict, tol: int = 5) -> bool:
    """判断两个显示器矩形是否指向同一物理屏幕（允许 tol 像素误差）。"""
    return (abs(a.get("x", 0) - b.get("x", 0)) <= tol and
            abs(a.get("y", 0) - b.get("y", 0)) <= tol and
            abs(a.get("width", 0) - b.get("width", 0)) <= tol and
            abs(a.get("height", 0) - b.get("height", 0)) <= tol)


def _win32_monitors():
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore
    except Exception:
        return None

    MONITORINFOF_PRIMARY = 1

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    monitors = []

    def _callback(hMonitor, hdc, lprect, lParam):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if not user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            return 1
        dpi_x = ctypes.c_uint(96)
        dpi_y = ctypes.c_uint(96)
        try:
            shcore.GetDpiForMonitor(hMonitor, 0,
                                    ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        except Exception:
            pass
        scale = max(dpi_x.value, 1) / 96.0
        # 使用工作区 (rcWork)，与 Qt 的 availableGeometry() 保持一致，
        # 这样同一块屏幕的 Win32 与 Qt 矩形才能几何对齐，便于去重。
        r = info.rcWork
        monitors.append({
            "name": info.szDevice,
            "is_primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
            "x": int(round(r.left / scale)),
            "y": int(round(r.top / scale)),
            "width": int(round((r.right - r.left) / scale)),
            "height": int(round((r.bottom - r.top) / scale)),
        })
        return 1

    Proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR,
                              wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)
    try:
        user32.EnumDisplayMonitors(0, 0, Proc(_callback), 0)
    except Exception:
        return None
    return monitors or None


def list_monitors(app=None):
    """返回 [{name,is_primary,x,y,width,height}, ...]。

    优先以 Qt 枚举结果为准（逻辑坐标、名字更友好、DPI 处理正确），
    Win32 仅作为兜底——仅加入 Qt 明显漏报（按几何去重）的屏幕，
    避免同一台显示器被 Qt 与 Win32 各列出一次而重复。
    """
    try:
        from PyQt6.QtWidgets import QApplication
        # `app` 应为 QApplication 实例。若调用方误传了窗口对象等，则回退到真正的实例。
        if not isinstance(app, QApplication):
            app = QApplication.instance()
    except Exception:
        app = None

    qt = []
    if app is not None:
        try:
            qt = app.screens()
        except Exception:
            qt = []

    win = _win32_monitors()

    # 1) 先收集 Qt 的 screens（已正确处理 DPI 缩放），作为主数据源。
    result = []
    for s in qt:
        g = s.availableGeometry()
        prim = (app.primaryScreen() == s) if app else False
        result.append({
            "name": s.name() or "unknown",
            "is_primary": prim,
            "x": g.x(), "y": g.y(),
            "width": g.width(), "height": g.height(),
        })

    # 2) Win32 兜底：只加入 Qt 明显漏掉的屏幕（按几何去重）。
    if win:
        for m in win:
            if not any(_rect_match(m, r) for r in result):
                result.append(m)

    # 3) 完全拿不到 Qt 屏幕时，退回 Win32 结果。
    if not result and win:
        result = win

    return result


def target_geometry(app, index):
    """index<0 或无效 -> 主屏；否则返回对应屏的 QRect。

    不再因"只枚举到 1 块屏"就强制回退主屏——只要 index 落在枚举结果范围内就
    返回对应屏，从而正确处理"实际有多屏但某次枚举漏报"的场景。
    """
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QRect
    a = app or QApplication.instance()
    if a is None:
        return QRect(0, 0, 800, 600)
    if index is None or index < 0:
        return a.primaryScreen().availableGeometry()
    monitors = list_monitors(a)
    if 0 <= index < len(monitors):
        m = monitors[index]
        return QRect(m["x"], m["y"], m["width"], m["height"])
    return a.primaryScreen().availableGeometry()


def baseline_sizes(rect) -> dict:
    """根据一块屏的「逻辑分辨率」推导字号/边距/卡片宽度的基准像素。

    rect 为 QRect（availableGeometry）。返回的基准与「系数 1.0」相乘即该屏下的
    默认视觉大小，从而实现「自动适配目标屏」——切到更小的屏字号/卡片自动变小。

    字号使用 setPixelSize（绝对像素），因此基准直接以像素为单位。历史上基准
    按磅值设计（1pt = 96/72 px），此处乘 96/72 换算为像素，保持视觉不变。

    公式（以主屏为锚，使系数 1.0 ≈ 主屏现状视觉）：
      - 字号基准     = round(屏高 / 30)   # 1080p:36px  1440p:48px  4K:72px
      - 顶部边距基准 = round(屏高 / 50)   # 1080p:21px（与原默认 21 对齐）
      - 卡片宽度基准 = round(屏宽 / 8)    # 1920:240px  2560:320px
      - 浮动字号基准 = round(屏高 / 81)   # 1080p:13px
    """
    if rect is None:
        return {
            "font_size": 32,
            "top_margin": 21,
            "floating_card_width": 240,
            "floating_font_size": 21,
        }
    try:
        h = max(1, int(rect.height()))
        w = max(1, int(rect.width()))
    except Exception:
        return {
            "font_size": 32,
            "top_margin": 21,
            "floating_card_width": 240,
            "floating_font_size": 21,
        }
    return {
        "font_size": max(8, int(round(h / 30.0))),
        "top_margin": max(0, int(round(h / 50.0))),
        "floating_card_width": max(80, int(round(w / 8.0))),
        "floating_font_size": max(8, int(round(h / 81.0))),
    }
