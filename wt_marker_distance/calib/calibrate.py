"""Калибратор: рисует synthetic-маркер, прогоняет детектор и считает ошибку.

Использование:
    python calibrate.py                 # автотест точности детекции
    python calibrate.py --roi           # интерактивный выбор зоны поиска (нужен GUI)
Результат калибровки m_per_px сохраняется в config.json.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detector import find_marker  # noqa: E402
from config import load_config, save_config, CONFIG_PATH  # noqa: E402


def make_synthetic_frame(w=1920, h=1080, marker_xy=(760, 300), jitter=2):
    """Кадр, похожий на скриншот WT: трава/небо + жёлтый маркер-«капля»."""
    rng = np.random.default_rng(42)
    frame = np.zeros((h, w, 3), np.uint8)
    # небо градиентом
    for y in range(h // 2):
        t = y / (h / 2)
        frame[y, :] = (int(200 - 60 * t), int(150 - 40 * t), int(90 + 30 * t))
    # земля с шумом
    ground = frame[h // 2:, :]
    noise = rng.integers(-18, 18, ground.shape, dtype=np.int16)
    base = np.array([70, 110, 80], np.int16)
    ground[:] = np.clip(base + noise, 0, 255).astype(np.uint8)
    # деревья-пятна
    for _ in range(40):
        cx, cy = rng.integers(0, w), rng.integers(h // 2, h)
        r = int(rng.integers(8, 40))
        col = tuple(int(v) for v in rng.integers(40, 110, 3))
        cv2.circle(frame, (cx, cy), r, col, -1)
    # жёлтый маркер: круг + хвост капли (цвет как у «Указать цель»: ~ RGB(255,200,0))
    x, y = marker_xy
    x += int(rng.integers(-jitter, jitter + 1))
    y += int(rng.integers(-jitter, jitter + 1))
    color = (0, 200, 255)  # BGR
    cv2.circle(frame, (x, y - 10), 14, color, -1)
    pts = np.array([[x - 12, y - 4], [x + 12, y - 4], [x, y + 22]], np.int32)
    cv2.fillPoly(frame, [pts], color)
    return frame, (x, y)


def auto_test(rounds=30):
    cfg = load_config()
    ok, miss, total_err = 0, 0, 0.0
    for i in range(rounds):
        mx = int(np.random.default_rng(i).integers(200, 1700))
        my = int(np.random.default_rng(i + 1000).integers(120, 500))
        frame, actual = make_synthetic_frame(marker_xy=(mx, my))
        det = find_marker(frame, cfg)
        if det is None:
            miss += 1
            continue
        err = np.hypot(det["center"][0] - actual[0], det["center"][1] - actual[1])
        total_err += err
        if err <= 12:
            ok += 1
    avg = total_err / max(ok + miss - miss, 1)
    print(json.dumps({
        "rounds": rounds, "detected": ok + miss, "missed": miss,
        "avg_px_error": round(avg, 2),
        "verdict": "OK" if (ok + miss) == rounds and avg < 12 else "CHECK HSV PARAMS",
    }, ensure_ascii=False, indent=2))
    return (ok + miss) == rounds and avg < 12


def interactive_roi():
    """Выбор мышью прямоугольника зоны поиска прямо на скриншоте игры."""
    print("Сделайте скриншот игры (Win+Shift+S -> calib/screenshot.png), затем запустите:")
    print("    python calibrate.py --roi")
    path = os.path.join(os.path.dirname(CONFIG_PATH), "calib", "screenshot.png")
    if not os.path.exists(path):
        print(f"[!] файл не найден: {path}")
        return False
    img = cv2.imread(path)
    try:
        r = cv2.selectROI("Выделите зону метки (Enter — ок, C — отмена)", img, False)
        cv2.destroyAllWindows()
    except Exception as e:
        print(f"[!] GUI недоступен ({e}). Запустите на Windows.")
        return False
    if r == (0, 0, 0, 0):
        print("[i] отменено")
        return False
    cfg = load_config()
    cfg["roi"] = {"x1": int(r[0]), "y1": int(r[1]),
                  "x2": int(r[0] + r[2]), "y2": int(r[1] + r[3])}
    save_config(cfg)
    print(f"[+] ROI сохранён в config.json: {cfg['roi']}")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--roi", action="store_true", help="интерактивная зона поиска")
    args = ap.parse_args()
    if args.roi:
        sys.exit(0 if interactive_roi() else 1)
    sys.exit(0 if auto_test() else 1)
