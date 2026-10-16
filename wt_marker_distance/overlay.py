"""Прозрачный полноэкранный оверлей (Windows, ctypes + GDI).

Свой лёгкий рендер на 2-канальном layered окне (SRCDC + DstAlpha), поэтому:
 - не нужен Pillow/Tkinter;
 - окно кликабельно-прозрачное (клик сквозь), topmost;
 - рисует текст расстояния и «прицельную рамку» вокруг метки.
"""
import ctypes
import math
import sys

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
else:
    # заглушки, чтобы модуль можно было импортировать для тестов на Linux/macOS
    class _Dummy:
        def __getattr__(self, name):
            raise NotImplementedError("WinAPI доступен только на Windows")
    wt = None
    user32 = gdi32 = _Dummy()

# ---- константы WinAPI ----
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0
LWA_COLORKEY = 1
LWA_ALPHA = 2
SRCCOPY = 0x00CC0020
DSTBLEND = 7
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
BI_RGB = 0
CSQ_VTTC = 0x0003
DT_TOP = 0x00000000
DT_CENTER = 0x00000001
DT_CALCRECT = 0x0400
DT_NOCLIP = 0x0100


if IS_WINDOWS:
    class _BLENDFUNCTION(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]


    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
        ]


    class _POINTFX(ctypes.Structure):
        _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float)]

else:
    _BLENDFUNCTION = _BITMAPINFOHEADER = _POINTFX = object

class Overlay:
    def __init__(self, width=None, height=None, font_size=44, opacity=0.85):
        if not self.is_supported():
            raise RuntimeError("Overlay доступен только на Windows")
        self.width = width or user32.GetSystemMetrics(0)   # SM_CXSCREEN
        self.height = height or user32.GetSystemMetrics(1)
        self.font_size = font_size
        self.opacity = opacity
        self.visible = True

        # координаты окна = весь виртуальный экран (монитор 0)
        self.x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        self.y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        self.vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        self.vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

        self._hwnd = None
        self._setup()

    @staticmethod
    def is_supported():
        return IS_WINDOWS

    # ---------------- инициализация ----------------
    def _setup(self):
        W, H = self.vw, self.vh
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        self.hbm = gdi32.CreateCompatibleBitmap(self.hdc_screen, W, H)
        gdi32.SelectObject(self.hdc_mem, self.hbm)

        self.hfont = gdi32.CreateFontW(
            -self.font_size, 0, 0, 0, 700,  # FW_BOLD
            0, 0, 0, CSQ_VTTC, 0, 0, 0, 0, "Segoe UI")

        wc = wt.WNDCLASSEX()
        hinst = user32.GetModuleHandleW(None)

        @ctypes.WINFUNCTYPE(wt.LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        def _wndproc(h, msg, wp, lp):
            if msg == 0x0002:  # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(h, msg, wp, lp)
        self._wndproc = _wndproc

        wc.cbSize = ctypes.sizeof(wc)
        wc.lpfnWndProc = _wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "WTMarkerOverlay"
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = user32.GetLastError()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise OSError(f"RegisterClassExW failed: {err}")

        hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW,
            atom, "WT Marker Distance", WS_POPUP,
            self.x, self.y, W, H, 0, 0, hinst, 0)
        if not hwnd:
            raise OSError("CreateWindowExW failed")
        self._hwnd = hwnd
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        self._apply_layered()

    def _apply_layered(self):
        bf = _BLENDFUNCTION(AC_SRC_OVER, 0, int(self.opacity * 255), AC_SRC_ALPHA)
        pt_src = _POINTFX(0, 0)
        pt_dst = _POINTFX(self.x, self.y)
        size = wt.SIZE(self.vw, self.vh)
        ok = ctypes.windll.msimg32.UpdateLayeredWindow(
            self._hwnd, self.hdc_screen, ctypes.byref(pt_dst),
            ctypes.byref(size), self.hdc_mem, ctypes.byref(pt_src),
            0, ctypes.byref(bf), LWA_ALPHA)
        if not ok:
            raise OSError(f"UpdateLayeredWindow failed: {user32.GetLastError()}")

    # ---------------- отрисовка ----------------
    def _clear(self):
        rect = wt.RECT(0, 0, self.vw, self.vh)
        brush = gdi32.GetStockObject(5)  # NULL_BRUSH
        old = gdi32.SelectObject(self.hdc_mem, brush)
        gdi32.FrameRect(self.hdc_mem, ctypes.byref(rect), brush)
        gdi32.SelectObject(self.hdc_mem, old)
        pen = gdi32.CreatePen(0, 1, 0x000000)  # PS_NULL
        oldpen = gdi32.SelectObject(self.hdc_mem, pen)
        gdi32.Rectangle(self.hdc_mem, 0, 0, self.vw, self.vh)
        gdi32.SelectObject(self.hdc_mem, oldpen)
        gdi32.DeleteObject(pen)
        gdi32.DeleteObject(brush)

    def _text(self, x, y, s, color_bgr, bold=True, align="left"):
        """Текст с чёрной обводкой для читаемости поверх игры."""
        rc = wt.RECT(x, y, x, y)
        flags = DT_NOCLIP | (DT_CENTER if align == "center" else 0)
        gdi32.SetBkMode(self.hdc_mem, 1)  # TRANSPARENT
        gdi32.SelectObject(self.hdc_mem, self.hfont)
        # тень/обводка
        for dx, dy in ((-2, -2), (-2, 2), (2, -2), (2, 2), (0, 3), (3, 0)):
            r2 = wt.RECT(rc.left + dx, rc.top + dy, rc.right + dx, rc.bottom + dy)
            gdi32.DrawTextW(self.hdc_mem, s, -1, ctypes.byref(r2), flags)
        oldcol = gdi32.SetTextColor(self.hdc_mem, color_bgr & 0xFFFFFF)
        gdi32.DrawTextW(self.hdc_mem, s, -1, ctypes.byref(rc), flags)
        gdi32.SetTextColor(self.hdc_mem, oldcol)

    def _rect_outline(self, x, y, w, h, color_bgr, thickness=3):
        pen = gdi32.CreatePen(0, thickness, color_bgr & 0xFFFFFF)  # PS_SOLID
        old = gdi32.SelectObject(self.hdc_mem, pen)
        null_brush = gdi32.GetStockObject(5)
        ob = gdi32.SelectObject(self.hdc_mem, null_brush)
        gdi32.Rectangle(self.hdc_mem, x, y, x + w, y + h)
        gdi32.SelectObject(self.hdc_mem, ob)
        gdi32.SelectObject(self.hdc_mem, old)
        gdi32.DeleteObject(pen)
        gdi32.DeleteObject(null_brush)

    def render(self, marker_pos=None, distance_m=None, azimuth_deg=None, status=""):
        """marker_pos — координаты метки в ЭКРАННЫХ пикселях (вирт. экран)."""
        if not self.visible:
            return
        self._clear()
        ox, oy = -self.x, -self.y  # перевод экранных коорд. в коорд. окна

        if marker_pos is not None and distance_m is not None:
            mx, my = marker_pos
            col = 0x00E1FF  # жёлтый BGR
            # рамка-«захват» вокруг метки
            box = 74
            self._rect_outline(mx - ox - box // 2, my - oy - box // 2, box, box, col, 3)
            # линия от центра экрана к метке
            cx, cy = self.vw // 2 - ox, self.vh // 2 - oy
            ang = math.atan2(my - oy - cy, mx - ox - cx)
            for t in range(14, min(int(math.hypot(mx - cx, my - cy)), 260), 22):
                px = cx + math.cos(ang) * t
                py = cy + math.sin(ang) * t
                self._rect_outline(int(px) - 1, int(py) - 1, 2, 2, 0xAAAAAA, 2)
            txt = f"{distance_m:.0f} м"
            if azimuth_deg is not None:
                txt += f"   азимут {azimuth_deg:.0f}\u00b0"
            self._text(mx - ox - 120, my - oy - 110, txt, 0x00E1FF)
        elif status:
            self._text(self.vw // 2 - 300, 140, status, 0x9E9E9E, align="center")

        self._apply_layered()

    # ---------------- управление ----------------
    def set_visible(self, v):
        self.visible = v
        if not v:
            self._clear()
            self._apply_layered()

    def destroy(self):
        try:
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)
            gdi32.DeleteObject(self.hbm)
            gdi32.DeleteDC(self.hdc_mem)
            gdi32.DeleteDC(self.hdc_screen)
            gdi32.DeleteObject(self.hfont)
        except Exception:
            pass


class DummyOverlay:
    """Заглушка для разработки вне Windows (печатает состояние в консоль)."""
    visible = True

    def __init__(self, *a, **k):
        print("[overlay] заглушка (не Windows)")

    def render(self, marker_pos=None, distance_m=None, azimuth_deg=None, status=""):
        if distance_m is not None:
            print(f"[overlay] цель {tuple(map(int, marker_pos))}: {distance_m:.0f} м")
        elif status:
            print(f"[overlay] {status}")

    def set_visible(self, v):
        self.visible = v

    def destroy(self):
        pass


def create_overlay(*args, **kwargs):
    if Overlay.is_supported():
        return Overlay(*args, **kwargs)
    return DummyOverlay(*args, **kwargs)
