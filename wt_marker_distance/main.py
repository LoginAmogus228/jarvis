"""WT Marker Distance — оверлей расстояния до метки отряда в War Thunder.

По жёлтой метке «Указать цель» (маркер отряда) на экране определяет её позицию,
пересчитывает расстояние от игрока и рисует его поверх игры (клик-сквозь оверлей).

Горячие клавиши:
  F8  — показать/скрыть оверлей
  F9  — зафиксировать текущую метку как цель (re-center)
  F10 — сбросить цель
  Ctrl+C — выход

Запуск:  python main.py [--debug] [--nomouse]
"""
import argparse
import math
import sys
import threading
import time

from config import load_config, save_config
from detector import ScreenCapture, find_marker, MarkerTracker
from overlay import create_overlay

# ---- опциональные модули ----
try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception:
    pyautogui = None

try:
    from pynput.keyboard import Key, Listener as KListener
except Exception:
    Key = KListener = None

try:
    from pynput.mouse import Listener as MListener
except Exception:
    MListener = None


def screen_size():
    if pyautogui:
        w, h = pyautogui.size()
        return w, h
    return 1920, 1080


class App:
    def __init__(self, cfg, use_mouse=True, debug=False):
        self.cfg = cfg
        self.debug = debug
        self.running = True
        self.paused = False
        self.frame_n = 0
        self.lock = threading.Lock()
        self.last_detections = []   # для --debug окна

        self.tracker = MarkerTracker(ttl=cfg["marker_ttl"])
        self.capture = None
        self.overlay = None
        self._target = None         # зафиксированная цель (x, y) или None

        self.SW, self.SH = screen_size()
        cfg["screen_w"], cfg["screen_h"] = self.SW, self.SH

        self._start_overlay()
        self._start_capture()
        self._start_listeners(use_mouse)

    # ---------- подсистемы ----------
    def _start_overlay(self):
        try:
            self.overlay = create_overlay(
                font_size=self.cfg["font_size"], opacity=self.cfg["overlay_opacity"])
        except Exception as e:
            print(f"[!] не удалось создать оверлей: {e}")
            self.overlay = None

    def _start_capture(self):
        try:
            self.capture = ScreenCapture(monitor=1)
        except Exception as e:
            print(f"[!] захват экрана недоступен: {e}")
            self.capture = None

    def _start_listeners(self, use_mouse):
        if KListener is not None:
            hotkeys = {
                self.cfg["hotkey_toggle"]: "toggle",
                self.cfg["hotkey_recenter"]: "recenter",
                self.cfg["hotkey_reset"]: "reset",
            }

            def on_key(key):
                name = getattr(key, "name", str(key)).upper()
                action = hotkeys.get(name)
                if action == "toggle":
                    self.paused = not self.paused
                    if self.overlay:
                        self.overlay.set_visible(not self.paused)
                    print(f"[i] оверлей: {'вкл' if not self.paused else 'пауза'}")
                elif action == "recenter":
                    self.recenter()
                elif action == "reset":
                    with self.lock:
                        self._target = None
                        self.tracker.reset()
                    print("[i] цель сброшена")
                elif getattr(key, "name", "") == "esc":
                    self.running = False
            threading.Thread(
                target=lambda: KListener(on_release=on_key).start(),
                daemon=True).start()
        else:
            print("[!] pynput не установлен — горячие клавиши выключены (pip install pynput)")

        if use_mouse and MListener is not None and pyautogui is not None:
            def on_move(x, y):
                self.last_mouse = (x, y)
            self.last_mouse = (self.SW // 2, self.SH // 2)
            threading.Thread(
                target=lambda: MListener(on_move=on_move).start(),
                daemon=True).start()

    # ---------- публичное API калибратора ----------
    def recenter(self):
        """Текущая позиция трекера становится фиксированной целью."""
        with self.lock:
            if self.tracker.is_valid():
                self._target = tuple(int(v) for v in self.tracker.pos)
                print(f"[i] цель зафиксирована: {self._target}")
            else:
                print("[i] метка не найдена — сначала поймайте маркер")

    # ---------- математика расстояния ----------
    def compute_distance(self, mx, my):
        cx, cy = self.SW / 2.0, self.SH / 2.0
        dx, dy = mx - cx, my - cy
        r_px = math.hypot(dx, dy)
        base = r_px * self.cfg["m_per_px"]
        if self.cfg.get("zoom_scale", True):
            fov = math.radians(self.cfg["fov_deg"])
            ang = math.atan2(r_px, (self.SW / 2.0) / math.tan(fov / 2.0))
            base *= max(0.35, math.cos(ang))
        az = (math.degrees(math.atan2(dx, -dy))) % 360.0
        return base, az

    # ---------- главный цикл ----------
    def run(self):
        period = 1.0 / 30.0
        while self.running:
            t0 = time.time()
            try:
                self.tick()
            except Exception as e:
                if self.debug:
                    import traceback; traceback.print_exc()
            dt = time.time() - t0
            time.sleep(max(0.0, period - dt))
        self.shutdown()

    def tick(self):
        if self.capture is None or self.paused:
            return
        frame = self.capture.grab()
        self.frame_n += 1
        det = None
        if self.frame_n % max(1, int(self.cfg["detect_every"])) == 0:
            det = find_marker(frame, self.cfg, debug_draw=self.debug)
            with self.lock:
                self.tracker.update(None if det is None else det)
                if self.debug and det and "frame" in det:
                    self.last_detections.append(det["frame"])
                    self.last_detections = self.last_detections[-1:]

        with self.lock:
            tgt = self._target if self._target else (
                tuple(int(v) for v in self.tracker.pos) if self.tracker.is_valid() else None)

        if self.overlay:
            if tgt is not None:
                dist, az = self.compute_distance(*tgt)
                self.overlay.render(marker_pos=tgt, distance_m=dist, azimuth_deg=az)
            else:
                self.overlay.render(status="метка не найдена — поставьте маркер «Указать цель»")

    def shutdown(self):
        self.running = False
        if self.overlay:
            self.overlay.destroy()
        if self.capture:
            self.capture.close()


def main():
    ap = argparse.ArgumentParser(description="WT Marker Distance overlay")
    ap.add_argument("--debug", action="store_true", help="окно предпросмотра детекции")
    ap.add_argument("--nomouse", action="store_true", help="не перехватывать курсор")
    args = ap.parse_args()

    cfg = load_config()
    app = App(cfg, use_mouse=not args.nomouse, debug=args.debug)
    print("[i] запущено. F8 — пауза, F9 — зафиксировать метку, F10 — сброс, Esc — выход")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        save_config(cfg)


if __name__ == "__main__":
    sys.exit(main())
