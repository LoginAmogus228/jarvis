"""Захват экрана (mss) и детект жёлтой метки-маркера War Thunder (HSV + контуры).

Важно: mss возвращает каналы как BGRA, а OpenCV — BGR. Конвертация учтена ниже.
"""
import time

import cv2
import numpy as np

try:
    import mss  # pip install mss
except ImportError:
    mss = None


class ScreenCapture:
    """Потоковый захват области экрана через mss."""

    def __init__(self, monitor=1):
        if mss is None:
            raise RuntimeError("Модуль 'mss' не установлен. Выполните: pip install mss")
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[monitor]

    def grab(self):
        """Возвращает ndarray BGR (HxWx3)."""
        shot = self.sct.grab(self.monitor)
        frame = np.asarray(shot)          # BGRA
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    def close(self):
        try:
            self.sct.close()
        except Exception:
            pass


def find_marker(frame_bgr, cfg, debug_draw=False):
    """Ищет самый крупный жёлтый маркер в ROI.

    Возвращает dict:
      center      - (x, y) центр маркера в координатах ЭКРАНА
      area        - площадь пикселей
      bbox        - (x, y, w, h) в координатах экрана
      roi_offset  - (rx, ry) смещение ROI (для отладки)
      frame       - кадр с обводками (если debug_draw=True)
    либо None, если ничего не найдено.
    """
    h, w = frame_bgr.shape[:2]
    rx1 = int(cfg["roi"]["x1"] or 0)
    ry1 = int(cfg["roi"]["y1"] or 0)
    rx2 = int(cfg["roi"]["x2"]) if cfg["roi"].get("x2") else w
    ry2 = int(cfg["roi"]["y2"]) if cfg["roi"].get("y2") else h
    rx1, ry1 = max(0, rx1), max(0, ry1)
    rx2, ry2 = min(w, rx2), min(h, ry2)
    if rx2 <= rx1 or ry2 <= ry1:
        return None

    roi = frame_bgr[ry1:ry2, rx1:rx2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    mask = cv2.inRange(
        hsv,
        (int(cfg["hsv_h_min"]), int(cfg["hsv_s_min"]), int(cfg["hsv_v_min"])),
        (int(cfg["hsv_h_max"]), 255, 255),
    )
    # чистим шум и склеиваем разорванные части маркера
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < cfg["min_area"] or a > cfg["max_area"]:
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        aspect = float(cw) / max(ch, 1)
        if not (0.2 <= aspect <= 5.0):   # маркер примерно квадратный/каплевидный
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        if a > best_area:
            best_area = a
            best = (cx, cy, x, y, cw, ch)

    result = None
    if best is not None:
        cx, cy, x, y, cw, ch = best
        result = {
            "center": (cx + rx1, cy + ry1),
            "area": int(best_area),
            "bbox": (x + rx1, y + ry1, cw, ch),
            "roi_offset": (rx1, ry1),
        }

    if debug_draw:
        dbg = frame_bgr.copy()
        cv2.rectangle(dbg, (rx1, ry1), (rx2, ry2), (255, 80, 0), 2)
        for c in contours:
            a = cv2.contourArea(c)
            if cfg["min_area"] <= a <= cfg["max_area"]:
                x, y, cw, ch = cv2.boundingRect(c)
                cv2.rectangle(dbg, (x + rx1, y + ry1), (x + rx1 + cw, y + ry1 + ch), (0, 255, 255), 2)
        if result:
            cv2.circle(dbg, result["center"], 12, (0, 255, 0), 2)
        result_dbg = dict(result) if result else {}
        result_dbg["frame"] = dbg
        return result_dbg
    return result


class MarkerTracker:
    """Сглаживание позиции метки между кадрами детекции."""

    def __init__(self, alpha=0.45, ttl=2.0):
        self.alpha = alpha
        self.ttl = ttl
        self.pos = None      # сглаженная позиция (x, y)
        self.raw = None      # последний raw-детект
        self.last_seen = 0.0

    def update(self, detection):
        now = time.time()
        if detection is not None:
            self.raw = detection["center"]
            self.last_seen = now
            if self.pos is None:
                self.pos = self.raw
            else:
                px, py = self.pos
                nx, ny = self.raw
                self.pos = (px + self.alpha * (nx - px),
                            py + self.alpha * (ny - py))
        return self.is_valid(now)

    def is_valid(self, now=None):
        now = now or time.time()
        return self.pos is not None and (now - self.last_seen) <= self.ttl

    def reset(self):
        self.pos = None
        self.raw = None
        self.last_seen = 0.0
