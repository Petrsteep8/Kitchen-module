#!/usr/bin/env python3
# tools/annotate_zones.py — разметка зон и калибровка камеры -> zones.yaml (§14.1/§14.3)
"""
Инструмент онбординга точки: грузим опорный кадр камеры -> кликами рисуем полигоны зон ->
кликами ставим опорные точки калибровки (пиксель↔метры) -> экспорт в zones.yaml по схеме §14.1.

Запускать ТАМ, ГДЕ ЕСТЬ ДИСПЛЕЙ (matplotlib GUI). Чистые функции (build_zones_yaml,
compute_homography) вынесены отдельно и тестируются без дисплея.

Пример:
  python tools/annotate_zones.py --image frames/kitchen1.jpg --camera Kitchen_1 \\
      --site demo_site_01 --out zones.yaml
"""
import argparse

import numpy as np
import yaml


# --------- ЧИСТЫЕ ФУНКЦИИ (тестируемы без GUI) ---------
def compute_homography(pixel_pts, world_pts):
    """Гомография пиксель->метр по >=4 соответствиям. Возвращает (H 3x3 list, reproj_err_px).

    reproj_err — средняя ошибка обратной проекции в пикселях (контроль качества калибровки, §14.2).
    """
    import cv2
    src = np.array(pixel_pts, dtype=np.float64)
    dst = np.array(world_pts, dtype=np.float64)
    if len(src) < 4:
        raise ValueError("нужно >=4 опорные точки")
    H, _ = cv2.findHomography(src, dst, method=0)
    # обратная проекция метров в пиксели для оценки ошибки
    Hinv = np.linalg.inv(H)
    proj = cv2.perspectiveTransform(dst.reshape(-1, 1, 2), Hinv).reshape(-1, 2)
    err = float(np.mean(np.linalg.norm(proj - src, axis=1)))
    return H.tolist(), err


def build_zones_yaml(site_id, markup_version, zones, calibration=None):
    """Собрать dict в схеме §14.1. zones: список dict(zone_id,zone_type,status_role,camera_id,polygon,priority,valid_from)."""
    doc = {"site_id": site_id, "markup_version": markup_version, "zones": zones}
    if calibration:
        doc["calibration"] = calibration
    return doc


def dump_zones_yaml(doc, path):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)


# --------- ИНТЕРАКТИВ (нужен дисплей) ---------
def _capture_polygon(ax, title):
    import matplotlib.pyplot as plt
    ax.set_title(f"{title}\nЛКМ — вершины, ПКМ/Enter — закрыть полигон")
    plt.draw()
    pts = plt.ginput(n=-1, timeout=0)  # ЛКМ добавляет, Enter завершает
    return [[float(x), float(y)] for x, y in pts]


def annotate(image_path, camera_id, site_id, out_path, markup_version):
    import matplotlib.pyplot as plt
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        raise SystemExit(f"Не открыть кадр: {image_path}")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    zones = []
    while True:
        fig, ax = plt.subplots(); ax.imshow(rgb)
        poly = _capture_polygon(ax, f"Зона #{len(zones)+1} (камера {camera_id})")
        plt.close(fig)
        if len(poly) < 3:
            break
        zone_id = input("zone_id: ").strip()
        zone_type = input("zone_type (clean_zone/dirty_zone/prep_zone/wash_zone/waste_zone/neutral_zone/...): ").strip()
        status_role = input("status_role (prep/workstation/rest/out/between, пусто — нет): ").strip() or None
        priority = int(input("priority [10]: ").strip() or "10")
        z = {"zone_id": zone_id, "zone_type": zone_type, "camera_id": camera_id,
             "priority": priority, "valid_from": markup_version, "polygon": poly}
        if status_role:
            z["status_role"] = status_role
        zones.append(z)
        if input("Ещё зона? [y/N]: ").strip().lower() != "y":
            break

    calibration = None
    if input("Калибровать камеру (пиксель->метр)? [y/N]: ").strip().lower() == "y":
        fig, ax = plt.subplots(); ax.imshow(rgb)
        ax.set_title("Кликни >=4 опорные точки на полу"); plt.draw()
        px = plt.ginput(n=-1, timeout=0); plt.close(fig)
        world = []
        for i, _ in enumerate(px):
            xy = input(f"Точка {i+1}: координаты в метрах 'X Y': ").split()
            world.append([float(xy[0]), float(xy[1])])
        H, err = compute_homography([[x, y] for x, y in px], world)
        print(f"Ошибка репроекции: {err:.2f} px")
        calibration = {camera_id: {"homography": H, "hysteresis_m": 0.15}}

    doc = build_zones_yaml(site_id, markup_version, zones, calibration)
    dump_zones_yaml(doc, out_path)
    print(f"Сохранено: {out_path} ({len(zones)} зон)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="опорный кадр камеры")
    ap.add_argument("--camera", required=True, help="camera_id")
    ap.add_argument("--site", required=True, help="site_id")
    ap.add_argument("--out", default="zones.yaml")
    ap.add_argument("--markup-version", default="zones-1.0.0")
    args = ap.parse_args()
    annotate(args.image, args.camera, args.site, args.out, args.markup_version)


if __name__ == "__main__":
    main()
