# zones.py — Kitchen Module, геометрия зон и резолвер zone_id (задача ML-04)
"""
Резолвер зон: (camera_id, точка в пикселях) -> zone_id (§14).

Состояние ML-04 на сейчас: КОД ГОТОВ, КАЛИБРОВКА ОТЛОЖЕНА. Сами полигоны и гомография
заполняются ТОЛЬКО при доступе к камерам точки (онбординг §14.3, по стратегии холодного
старта — после MVP). До этого zones.yaml пустой/шаблонный -> resolve() вернёт None, и это
штатно: поле zone_id в факте опционально (§13.1).

Что закрыто этим модулем (то, что НЕ требует камер):
  * чтение zones.yaml в схеме §14.1 (zone_id, zone_type, camera_id, polygon, markup_version, valid_from);
  * принадлежность по FOOT-POINT (а не по центру bbox — фикс ложного left_zone);
  * ГИСТЕРЕЗИС границы (антидребезг при стоянии на краю зоны);
  * PRIORITY для вложенных полигонов (побеждает самый специфичный);
  * две оси (§8 контекста): zone_type (для контуров загрязнения) и status_role (для зонального статуса);
  * калибровка пиксель->метр (гомография >=4 точек ИЛИ px_per_meter) для handoff_radius_m и пр. (§14.2).
"""
import logging
import math
import os
import time

import yaml

logger = logging.getLogger("zones")

try:
    from shapely.geometry import Point, Polygon
    HAVE_SHAPELY = True
except Exception:
    HAVE_SHAPELY = False
    logger.warning("shapely не установлен -> гистерезиса не будет, point-in-polygon на ray-casting. "
                   "`pip install shapely`.")

DEFAULT_HYSTERESIS_PX = 8.0  # антидребезг по умолчанию, если нет калибровки в метрах


def _ray_cast_inside(point, polygon):
    """Точка в полигоне (ray casting) — fallback без shapely. polygon: [[x,y],...]."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


class _Zone:
    __slots__ = ("zone_id", "zone_type", "status_role", "priority", "raw_polygon", "shape")

    def __init__(self, rec):
        self.zone_id = rec["zone_id"]
        self.zone_type = rec.get("zone_type")
        self.status_role = rec.get("status_role")
        self.priority = int(rec.get("priority", 0))
        self.raw_polygon = rec.get("polygon", []) or []
        self.shape = None
        if HAVE_SHAPELY and len(self.raw_polygon) >= 3:
            try:
                self.shape = Polygon(self.raw_polygon)
            except Exception:
                self.shape = None

    def contains(self, point, hyst_px=0.0):
        if len(self.raw_polygon) < 3:
            return False
        if HAVE_SHAPELY and self.shape is not None:
            p = Point(point)
            if hyst_px > 0:
                return self.shape.buffer(hyst_px).contains(p)  # расширяем зону наружу — гистерезис
            return self.shape.contains(p)
        return _ray_cast_inside(point, self.raw_polygon)  # без гистерезиса


class _Calibration:
    """Пиксель -> метр для камеры: гомография (3x3) ИЛИ скаляр px_per_meter (§14.2)."""

    def __init__(self, rec):
        rec = rec or {}
        self.H = rec.get("homography")  # 3x3
        self.px_per_meter = rec.get("px_per_meter")
        self.hysteresis_m = rec.get("hysteresis_m")

    def _to_meters(self, p):
        if self.H is not None:
            x, y = p
            wx = self.H[2][0] * x + self.H[2][1] * y + self.H[2][2]
            mx = (self.H[0][0] * x + self.H[0][1] * y + self.H[0][2]) / (wx or 1e-9)
            my = (self.H[1][0] * x + self.H[1][1] * y + self.H[1][2]) / (wx or 1e-9)
            return (mx, my)
        return None

    def meters_between(self, p1, p2):
        m1, m2 = self._to_meters(p1), self._to_meters(p2)
        if m1 is not None and m2 is not None:
            return math.dist(m1, m2)
        if self.px_per_meter:
            return math.dist(p1, p2) / float(self.px_per_meter)
        return None  # калибровки нет

    def hysteresis_px(self):
        if self.hysteresis_m is not None and self.px_per_meter:
            return float(self.hysteresis_m) * float(self.px_per_meter)
        # с гомографией метры в пиксели локально не сводятся одним числом -> px-дефолт
        return DEFAULT_HYSTERESIS_PX


class ZoneResolver:
    def __init__(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"zones.yaml не найден: {path}")
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        self.site_id = cfg.get("site_id")
        self.markup_version = cfg.get("markup_version", "")  # входит в rules_config_version (§14.4)
        self._by_camera = {}      # camera_id -> [_Zone] (сортировка по priority убыв.)
        self._zone_meta = {}      # zone_id -> {zone_type, status_role}
        for rec in cfg.get("zones", []) or []:
            z = _Zone(rec)
            self._by_camera.setdefault(rec["camera_id"], []).append(z)
            self._zone_meta[z.zone_id] = {"zone_type": z.zone_type, "status_role": z.status_role}
        for cam in self._by_camera:
            self._by_camera[cam].sort(key=lambda z: -z.priority)  # специфичный (выше priority) первым

        self._calib = {cam: _Calibration(c) for cam, c in (cfg.get("calibration", {}) or {}).items()}
        self._last_zone = {}      # track_key -> {"zone": zone_id, "last": sec} (для гистерезиса)

        n_poly = sum(1 for cam in self._by_camera for z in self._by_camera[cam] if len(z.raw_polygon) >= 3)
        if n_poly == 0:
            logger.warning("zones.yaml без заполненных полигонов (markup_version=%r) -> resolve() = None "
                           "до калибровки. Это норма до онбординга точки (§14.3).", self.markup_version)
        else:
            logger.info("ZoneResolver: %d зон с полигонами, markup_version=%s", n_poly, self.markup_version)

    # ----- основной API -----
    def resolve(self, camera_id, point, track_key=None):
        """Вернуть zone_id для точки (foot-point) на камере, с гистерезисом по track_key, либо None."""
        zones = self._by_camera.get(camera_id, [])
        if not zones:
            return None
        hyst_px = self._calib.get(camera_id, _Calibration({})).hysteresis_px()

        # 1) гистерезис: если трек был в зоне и всё ещё в её РАСШИРЕННОЙ границе — остаёмся в ней
        if track_key is not None:
            prev = self._last_zone.get(track_key)
            if prev is not None:
                pz = next((z for z in zones if z.zone_id == prev["zone"]), None)
                if pz is not None and pz.contains(point, hyst_px=hyst_px):
                    prev["last"] = time.time()
                    return pz.zone_id

        # 2) обычное определение: среди СТРОГО содержащих точку — самый специфичный (priority)
        hit = next((z for z in zones if z.contains(point)), None)  # zones уже отсортированы по priority
        zid = hit.zone_id if hit else None
        if track_key is not None:
            if zid is not None:
                self._last_zone[track_key] = {"zone": zid, "last": time.time()}
            else:
                self._last_zone.pop(track_key, None)
        return zid

    def status_role(self, camera_id, point, track_key=None):
        """Ось зонального статуса (§15.3): rest / prep / workstation / ... — независимо от zone_type."""
        zid = self.resolve(camera_id, point, track_key=track_key)
        return self._zone_meta.get(zid, {}).get("status_role") if zid else None

    def zone_type_of(self, zone_id):
        return self._zone_meta.get(zone_id, {}).get("zone_type")

    def meters_between(self, camera_id, p1_px, p2_px):
        """Метрическое расстояние между двумя пиксельными точками (для handoff_radius_m и пр., §14.2).

        ⚠ Возвращает None, если камера не откалибрована. Правила, использующие метры, ОБЯЗАНЫ
        обрабатывать None как «дистанцию определить нельзя» (а не как 0) — иначе ложные срабатывания.
        """
        return self._calib.get(camera_id, _Calibration({})).meters_between(p1_px, p2_px)

    def gc(self, ttl_sec=10.0):
        now = time.time()
        for k in [k for k, v in self._last_zone.items() if now - v["last"] > ttl_sec]:
            self._last_zone.pop(k, None)
