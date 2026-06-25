# kitchen_vision/pattern_analyzer.py — распознавание паттернов движения объектов
"""
MotionPatternAnalyzer — классифицирует движение трека как linear / vertical / circular.

Назначение: модуль, который не доделал прошлый ML-разработчик. Встаёт в точку инъекции
`analyser` в action_detector.py (поле self.__pattern_analyser, вызов на стр. ~222
`analyze_motion_patterns(...)`), результат используется в __check_hand_item_interaction (~543):
  knife + (linear|vertical) -> cut;  pot/pan + circular -> mix;  food + linear / spoon + (linear|circular) -> serve.

Контракт (по фактическим вызовам action_detector.py:222 и run_multi_camera.py:121):
  ВХОД : movement_vectors = { uid: последовательность кортежей (cx, cy, vx, vy) }
         uid = "{class}_{track_id}";  cx,cy — центр bbox;  vx,vy — скорость (px/сек).
  ВЫХОД: { uid: pattern }, pattern in {"linear","vertical","circular"}.
         Для треков, где паттерн не определён (мало точек / стоит на месте / неоднозначно),
         ключ НЕ кладём — потребитель читает через .get(uid, "").

Метод эвристический (геометрия траектории), модель не нужна:
  * считаем длину пути и прямолинейность (net_disp / path_len);
  * вращение по направлениям шагов (суммарный |поворот| и его ЗНАКОВУЮ согласованность);
  * circular = путь сильно непрямой И есть устойчивое одностороннее вращение;
  * иначе прямолинейно: vertical, если вертикальная ось доминирует, иначе linear.
Пороги вынесены в конструктор (настраиваются на пилоте, как и прочие П1-параметры).
"""
import logging
import math

logger = logging.getLogger("pattern_analyzer")


class MotionPatternAnalyzer:
    def __init__(self, min_samples=5, min_path_px=15.0, min_step_px=1.5,
                 spread_circular=0.15, min_turn_circular=math.pi * 0.6, vertical_ratio=1.5):
        self.min_samples = min_samples            # минимум точек траектории для решения
        self.min_path_px = min_path_px            # суммарный путь меньше -> дрожание, паттерна нет
        self.min_step_px = min_step_px            # шаги короче игнорируем при оценке направления
        self.spread_circular = spread_circular    # λ2/λ1 ковариации точек: ~0 = линия, ~1 = плоская кривая
        self.min_turn_circular = min_turn_circular  # суммарный |поворот| направлений для circular (~180°+)
        self.vertical_ratio = vertical_ratio      # во сколько раз верт. ось должна доминировать для vertical

    def analyze_motion_patterns(self, movement_vectors):
        """{uid: [(cx,cy,vx,vy),...]} -> {uid: pattern}. Неопределённые uid опускаются."""
        out = {}
        if not movement_vectors:
            return out
        for uid, samples in movement_vectors.items():
            try:
                pattern = self.classify(list(samples))
                if pattern:
                    out[uid] = pattern
            except Exception:
                logger.debug("classify упал для %s", uid, exc_info=True)
        return out

    @staticmethod
    def _wrap(angle):
        """Угол -> [-pi, pi]."""
        return (angle + math.pi) % (2 * math.pi) - math.pi

    def classify(self, samples):
        """Один трек: список (cx,cy,vx,vy) -> 'linear'|'vertical'|'circular'|None."""
        if not samples or len(samples) < self.min_samples:
            return None

        pts = [(float(s[0]), float(s[1])) for s in samples]
        steps = [(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts))]

        path_len = sum(math.hypot(dx, dy) for dx, dy in steps)
        if path_len < self.min_path_px:
            return None  # объект практически на месте — паттерна нет

        # доминирование оси (для vertical vs linear)
        tot_dx = sum(abs(dx) for dx, _ in steps)
        tot_dy = sum(abs(dy) for _, dy in steps)

        # суммарный |поворот| направлений значимых шагов (плавная кривая -> большой)
        dirs = [math.atan2(dy, dx) for dx, dy in steps if math.hypot(dx, dy) >= self.min_step_px]
        abs_turn = sum(abs(self._wrap(dirs[i] - dirs[i - 1])) for i in range(1, len(dirs)))

        # 2D-разброс точек: отличает плоскую кривую от движения по прямой / туда-обратно.
        # (у прямой и у «вверх-вниз» все точки лежат на линии -> λ2/λ1 ≈ 0; у круга ≈ 1.)
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts) / n
        syy = sum((p[1] - my) ** 2 for p in pts) / n
        sxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / n
        tr = sxx + syy
        root = math.sqrt(max(0.0, (tr / 2) ** 2 - (sxx * syy - sxy * sxy)))
        l1, l2 = tr / 2 + root, tr / 2 - root
        spread_ratio = (l2 / l1) if l1 > 1e-9 else 0.0

        # circular: траектория реально плоская (2D) И направление накрутило заметный угол.
        # 2D-гейт отсекает «рубку» вверх-вниз: развороты на 180° дают большой |поворот|, но spread≈0.
        if spread_ratio >= self.spread_circular and abs_turn >= self.min_turn_circular:
            return "circular"

        # иначе прямолинейно: vertical, если вертикальная ось доминирует (рубка), иначе linear
        if tot_dy >= self.vertical_ratio * max(tot_dx, 1e-9):
            return "vertical"
        return "linear"
