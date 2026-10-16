"""Конфигурация и персистентное хранилище настроек (JSON)."""
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    # --- зона поиска метки на экране (пиксели) ---
    # По умолчанию — верхняя половина экрана: туда летят маркеры «Указать цель».
    # Задайте свои координаты после калибровки миникарты/маркеров.
    "roi": {"x1": 0, "y1": 0, "x2": None, "y2": None},  # None = до края экрана

    # --- детектор жёлтого маркера (HSV) ---
    "hsv_h_min": 20, "hsv_h_max": 38,   # OpenCV H: 0..179
    "hsv_s_min": 150, "hsv_v_min": 150,
    "min_area": 60,        # минимальная площадь blob в пикселях
    "max_area": 8000,      # максимальная площадь blob
    "detect_every": 3,     # детектировать каждый N-й кадр (~FPS/3)
    "marker_ttl": 2.0,     # сек., сколько помнить последнюю найденную метку

    # --- расстояние ---
    "m_per_px": 2.5,       # базовое масштабирование: метров на 1 пиксель экрана
    "fov_deg": 55.0,       # горизонтальный FOV камеры (для зума по краям экрана)
    "screen_w": 1920,
    "screen_h": 1080,
    "zoom_scale": True,    # учитывать перспективу (края экрана «дальше»)

    # --- оверлей ---
    "overlay_opacity": 0.85,
    "font_size": 44,
    "hotkey_toggle": "F8",       # пауза/вкл оверлея
    "hotkey_recenter": "F9",     # принять текущую метку как новую цель
    "hotkey_reset": "F10",       # сбросить текущую цель
}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass  # битый конфиг -> дефолты
    return cfg


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)
