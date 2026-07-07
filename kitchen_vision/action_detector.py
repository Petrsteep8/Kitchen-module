import queue
import time
import uuid
import logging
import threading
from collections import defaultdict, deque
from typing import Dict, List, Tuple, Optional, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("action_detector")


class Camera:
    def __init__(self, name: str):
        self.name = name


class ActionDetector(threading.Thread):
    def __init__(
        self,
        cams: List[str],
        in_queue: queue.Queue,
        analyser,
        out_queue: Optional[queue.Queue] = None,
        work_zone_polygons: Optional[Dict[str, List[Tuple[float, float]]]] = None,
        detection_threshold: float = 0.5,
        action_detection_delay: float = 0.5,
        action_end_delay: float = 0.4,
        action_check_timeout: float = 30.0,
        left_confirm_time: float = 1.2,
        tracking_lost_confirm: float = 0.8,
        key_object_lost_confirm: float = 0.8,
        hand_item_distance_px: float = 50.0,
        hand_overlap_iou_threshold: float = 0.1,
    ):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.in_queue = in_queue
        self.out_queue = out_queue

        self.__cameras = [Camera(cam) for cam in cams]
        self.__action_possible_cameras = set()

        self.__pattern_analyser = analyser
        self.__detection_threshold = detection_threshold

        self.__tool_classes = {"knife", "tool"}
        self.__key_object_classes = {
            "knife", "tool", "food", "cutting_board", "container",
            "phone", "cigarette_or_smoke_object", "cup", "bottle"
        }
        self.__container_classes = {"cutting_board", "container"}
        self.__hand_classes = {"hands"}

        self.__previous_position = defaultdict(dict)
        self.__movement_vectors = defaultdict(lambda: defaultdict(lambda: deque(maxlen=20)))
        self.__last_seen_ids = defaultdict(dict)

        self.__detected_actions: Dict[str, Dict[str, Any]] = {}
        self.__last_packet_by_cam: Dict[str, Dict[str, Any]] = {}

        self.__ACTION_DETECTION_DELAY = action_detection_delay
        self.__ACTION_END_DELAY = action_end_delay
        self.__ACTION_CHECK_TIMEOUT = action_check_timeout

        self.__T_LEFT_CONFIRM = left_confirm_time
        self.__T_TRACKING_LOST_CONFIRM = tracking_lost_confirm
        self.__T_KEY_OBJECT_LOST_CONFIRM = key_object_lost_confirm

        self.__HAND_ITEM_DISTANCE_PX = hand_item_distance_px
        self.__HAND_OVERLAP_IOU = hand_overlap_iou_threshold

        self.__work_zone_polygons = work_zone_polygons or {}

        for cam in cams:
            self.__reset_camera_state(cam)

    def run(self):
        while not self.stop_event.is_set():
            try:
                pkt = self.in_queue.get(timeout=0.25)
            except queue.Empty:
                self.__check_timeouts_without_packets()
                continue

            try:
                camera_id = pkt["camera_id"]
                self.__last_packet_by_cam[camera_id] = pkt

                frame = pkt.get("frame_raw")
                if frame is None:
                    frame = pkt.get("frame")

                if self.action_possible_on_cam(camera_id):
                    cam_data = self.analyze_motion(pkt)
                    if cam_data is not None:
                        patterns, centers = cam_data
                        events = self.detect_action(patterns, centers, pkt)
                        self.__emit_events(events)
                else:
                    self.is_action_possible(pkt)

            except Exception:
                logger.exception("ActionDetector processing error")

    def __emit_events(self, events: Optional[List[Dict[str, Any]]]):
        if not events:
            return

        import json
        for event in events:
            logger.info("ACTION EVENT JSON:\n%s", json.dumps(event, ensure_ascii=False, indent=2, default=str))
            if self.out_queue is not None:
                try:
                    self.out_queue.put_nowait(event)
                except Exception:
                    try:
                        self.out_queue.put(event, timeout=0.1)
                    except Exception:
                        logger.warning("Failed to push action event to out_queue")

    def __check_timeouts_without_packets(self):
        now = time.time()
        for camera_id, state in list(self.__detected_actions.items()):
            if state.get("state") != "ACTION_ACTIVE":
                continue

            last_seen_ts = state.get("last_seen_packet_ts", 0.0)
            if now - last_seen_ts >= self.__ACTION_CHECK_TIMEOUT:
                pkt = self.__last_packet_by_cam.get(camera_id, {
                    "camera_id": camera_id,
                    "objects": [],
                    "timestamp": now,
                })
                end_event = self.__finish_action(
                    camera_id=camera_id,
                    end_reason="tracking_lost",
                    pkt=pkt
                )
                self.__emit_events([end_event])

    def is_action_possible(self, pkt: Dict) -> bool:
        camera_id = pkt["camera_id"]
        objects = self.__filter_objects(pkt.get("objects", []))
        detected = {obj["class"] for obj in objects}

        has_person = "person" in detected
        has_hand = any(obj["class"] in self.__hand_classes for obj in objects)
        has_key_obj = any(obj["class"] in self.__key_object_classes for obj in objects)

        if has_person and has_hand and has_key_obj:
            if camera_id not in self.__action_possible_cameras:
                self.__action_possible_cameras.add(camera_id)
                self.__reset_camera_state(camera_id)
            return True

        self.__clear_cam_data(camera_id)
        return False

    def analyze_motion(self, pkt: Dict):
        camera_id = pkt["camera_id"]
        current_time = time.time()
        objects = self.__filter_objects(pkt.get("objects", []))

        current_ids = set()
        center_positions = []

        for obj in objects:
            bbox = self.__parse_bbox(obj.get("bbox"))
            if not bbox:
                continue

            track_id = obj.get("id")
            cls = obj.get("class")

            if track_id is None:
                track_id = f"{cls}_fallback_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}"

            uid = f"{cls}_{track_id}"
            current_ids.add(uid)

            cx, cy = self.__calculate_bbox_center(bbox)
            center_positions.append({
                "class": cls,
                "unique_id": uid,
                "track_id": track_id,
                "position": (cx, cy),
                "bbox": bbox,
                "confidence": float(obj.get("confidence", 0.0)),
                "employee_id": obj.get("employee_id"),
                "face_score": float(obj.get("face_score", 0.0)) if obj.get("face_score") is not None else None,
            })

            if uid in self.__previous_position[camera_id]:
                px, py, pt, _ = self.__previous_position[camera_id][uid]
                dt = current_time - pt
                if dt > 0:
                    vx = (cx - px) / dt
                    vy = (cy - py) / dt
                    self.__movement_vectors[camera_id][uid].append((cx, cy, vx, vy))

            self.__previous_position[camera_id][uid] = (cx, cy, current_time, bbox)
            self.__last_seen_ids[camera_id][uid] = current_time

        for uid in list(self.__previous_position[camera_id].keys()):
            if uid not in current_ids:
                last_ts = self.__last_seen_ids[camera_id].get(uid, 0.0)
                if current_time - last_ts > 1.5:
                    self.__previous_position[camera_id].pop(uid, None)
                    self.__movement_vectors[camera_id].pop(uid, None)
                    self.__last_seen_ids[camera_id].pop(uid, None)

        patterns = self.__pattern_analyser.analyze_motion_patterns(
            dict(self.__movement_vectors[camera_id])
        )

        pkt["motion_patterns"] = patterns or {}

        for obj in pkt.get("objects", []):
            try:
                bbox = self.__parse_bbox(obj.get("bbox"))
                if not bbox:
                    obj["motion_pattern"] = None
                    continue

                track_id = obj.get("id")
                cls = obj.get("class")

                if track_id is None:
                    track_id = f"{cls}_fallback_{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}"

                uid = f"{cls}_{track_id}"
                obj["motion_pattern"] = pkt["motion_patterns"].get(uid)
            except Exception:
                obj["motion_pattern"] = None

        return patterns, center_positions

    def detect_action(self, patterns, centers, pkt) -> Optional[List[Dict[str, Any]]]:
        camera_id = pkt["camera_id"]
        state = self.__detected_actions[camera_id]
        now = time.time()

        state["last_seen_packet_ts"] = now

        action = self.__check_hand_item_interaction(patterns, centers)
        if action is not None:
            self.__update_action_observation_state(camera_id, action, centers, pkt)

        if state["state"] == "IDLE":
            if action:
                state.update({
                    "state": "ACTION_CANDIDATE",
                    "candidate_since": now,
                    "action_type": action["type"],
                    "employee_track_id": action.get("employee_track_id"),
                    "employee_id": action.get("employee_id", "undefined"),
                    "employee_confidence": float(action.get("employee_confidence", 0.0)),
                    "key_object_id": action.get("key_object_id"),
                    "key_object_class": action.get("key_object_class"),
                    "last_action_seen_ts": now,
                    "last_observed_objects": centers,
                })
            return None

        if state["state"] == "ACTION_CANDIDATE":
            if action and action["type"] == state["action_type"]:
                state["last_action_seen_ts"] = now
                state["last_observed_objects"] = centers

                if action.get("employee_track_id") is not None:
                    state["employee_track_id"] = action["employee_track_id"]
                if action.get("employee_id"):
                    state["employee_id"] = action["employee_id"]
                state["employee_confidence"] = float(action.get("employee_confidence", state.get("employee_confidence", 0.0)))
                if action.get("key_object_id") is not None:
                    state["key_object_id"] = action["key_object_id"]
                if action.get("key_object_class") is not None:
                    state["key_object_class"] = action["key_object_class"]

                if now - state["candidate_since"] >= self.__ACTION_DETECTION_DELAY:
                    action_id = str(uuid.uuid4())

                    state.update({
                        "state": "ACTION_ACTIVE",
                        "action_id": action_id,
                        "timestamp_start": now,
                        "timestamp_end": now,
                        "last_action_seen_ts": now,
                        "last_motion_seen_ts": now,
                        "left_zone_since": None,
                        "tracking_lost_since": None,
                        "key_object_lost_since": None,
                        "last_observed_objects": centers,
                    })

                    start_event = self.make_action_start_packet(camera_id, pkt)
                    return [start_event]
            else:
                self.__reset_camera_state(camera_id)
            return None

        if state["state"] == "ACTION_ACTIVE":
            if action and action["type"] == state["action_type"]:
                state["timestamp_end"] = now
                state["last_action_seen_ts"] = now
                state["last_motion_seen_ts"] = now
                state["last_observed_objects"] = centers

                if action.get("employee_track_id") is not None:
                    state["employee_track_id"] = action["employee_track_id"]
                if action.get("employee_id"):
                    state["employee_id"] = action["employee_id"]
                state["employee_confidence"] = float(action.get("employee_confidence", state.get("employee_confidence", 0.0)))
                if action.get("key_object_id") is not None:
                    state["key_object_id"] = action["key_object_id"]
                if action.get("key_object_class") is not None:
                    state["key_object_class"] = action["key_object_class"]

            end_reason = self.__choose_end_reason(camera_id, pkt)
            if end_reason is not None:
                end_event = self.__finish_action(
                    camera_id=camera_id,
                    end_reason=end_reason,
                    pkt=pkt
                )
                return [end_event]

        return None

    def __choose_end_reason(self, camera_id: str, pkt: Dict) -> Optional[str]:
        now = time.time()
        state = self.__detected_actions[camera_id]
        objects = self.__filter_objects(pkt.get("objects", []))

        if self.__is_tracking_lost(camera_id, objects, now):
            return "tracking_lost"

        if self.__is_left_zone_confirmed(camera_id, pkt, objects, now):
            return "left_zone"

        if self.__is_key_object_lost(camera_id, objects, now):
            return "key_object_lost"

        if now - state.get("last_motion_seen_ts", 0.0) >= self.__ACTION_END_DELAY:
            return "motion_stopped"

        return None

    def __is_tracking_lost(self, camera_id: str, objects: List[Dict], now: float) -> bool:
        state = self.__detected_actions[camera_id]
        employee_track_id = state.get("employee_track_id")
        if employee_track_id is None:
            person_present = any(obj.get("class") == "person" for obj in objects)
            if person_present:
                state["tracking_lost_since"] = None
                return False

            if state.get("tracking_lost_since") is None:
                state["tracking_lost_since"] = now
                return False
            return (now - state["tracking_lost_since"]) >= self.__T_TRACKING_LOST_CONFIRM

        present = any(
            obj.get("class") == "person" and str(obj.get("id")) == str(employee_track_id)
            for obj in objects
        )
        if present:
            state["tracking_lost_since"] = None
            return False

        if state.get("tracking_lost_since") is None:
            state["tracking_lost_since"] = now
            return False

        return (now - state["tracking_lost_since"]) >= self.__T_TRACKING_LOST_CONFIRM

    def __is_left_zone_confirmed(self, camera_id: str, pkt: Dict, objects: List[Dict], now: float) -> bool:
        state = self.__detected_actions[camera_id]

        if self.__is_tracking_lost(camera_id, objects, now):
            return False

        polygon = pkt.get("work_zone_polygon")
        if polygon is None:
            polygon = self.__work_zone_polygons.get(camera_id)
        if not polygon:
            state["left_zone_since"] = None
            return False

        person = self.__get_action_employee_object(camera_id, objects)
        if person is None:
            state["left_zone_since"] = None
            return False

        center = self.__calculate_bbox_center(person["bbox"])
        outside = not self.__point_in_polygon(center, polygon)

        if not outside:
            state["left_zone_since"] = None
            return False

        if state.get("left_zone_since") is None:
            state["left_zone_since"] = now
            return False

        return (now - state["left_zone_since"]) >= self.__T_LEFT_CONFIRM

    def __is_key_object_lost(self, camera_id: str, objects: List[Dict], now: float) -> bool:
        state = self.__detected_actions[camera_id]
        key_object_id = state.get("key_object_id")
        key_object_class = state.get("key_object_class")

        present = False
        if key_object_id is not None:
            present = any(
                obj.get("class") == key_object_class and str(obj.get("id")) == str(key_object_id)
                for obj in objects
            )
        elif key_object_class is not None:
            present = any(obj.get("class") == key_object_class for obj in objects)

        if present:
            state["key_object_lost_since"] = None
            return False

        if state.get("key_object_lost_since") is None:
            state["key_object_lost_since"] = now
            return False

        return (now - state["key_object_lost_since"]) >= self.__T_KEY_OBJECT_LOST_CONFIRM

    def make_action_start_packet(self, camera_id: str, pkt: Dict) -> Dict[str, Any]:
        d = self.__detected_actions[camera_id]
        return {
            "event_type": "ACTION_START",
            "action_id": d["action_id"],
            "employee_id": d.get("employee_id"),
            "camera_id": camera_id,
            "zone_id": camera_id,
            "action_type": d["action_type"],
            "timestamp_start": d["timestamp_start"],
            "hygiene": d.get("hygiene"),
            "motion_patterns": pkt.get("motion_patterns", {}),
            "objects": pkt.get("objects", []),
        }

    def make_action_end_packet(self, camera_id: str, pkt: Dict, end_reason: str) -> Dict[str, Any]:
        d = self.__detected_actions[camera_id]
        objects = self.__filter_objects(pkt.get("objects", []))

        employee_obj = self.__get_action_employee_object(camera_id, objects)
        employee_confidence = 0.0
        if employee_obj is not None:
            if employee_obj.get("face_score") is not None:
                employee_confidence = float(employee_obj["face_score"])
            else:
                employee_confidence = float(employee_obj.get("confidence", 0.0))
        else:
            employee_confidence = float(d.get("employee_confidence", 0.0))

        carrying_tool = self.__is_carrying_class_group(
            employee_obj=employee_obj,
            objects=objects,
            target_classes=self.__tool_classes
        )
        carrying_food = self.__is_carrying_class_group(
            employee_obj=employee_obj,
            objects=objects,
            target_classes={"food"}
        )

        return {
            "event_type": "ACTION_END",
            "action_id": d["action_id"],
            "employee_id": d.get("employee_id"),
            "camera_id": camera_id,
            "zone_id": camera_id,
            "action_type": d["action_type"],
            "timestamp_start": d["timestamp_start"],
            "timestamp_end": d["timestamp_end"],
            "hygiene": d.get("hygiene"),
            "end_reason": end_reason,
            "employee_confidence": max(0.0, min(1.0, employee_confidence)),
            "carrying_tool": carrying_tool,
            "carrying_food": carrying_food,
            "objects": pkt.get("objects", []),
            "motion_patterns": pkt.get("motion_patterns", {}),
        }

    def __finish_action(self, camera_id: str, end_reason: str, pkt: Dict) -> Dict[str, Any]:
        state = self.__detected_actions[camera_id]
        state["timestamp_end"] = time.time()

        end_event = self.make_action_end_packet(camera_id, pkt, end_reason)

        self.__reset_camera_state(camera_id)
        return end_event

    def __check_hand_item_interaction(self, patterns, centers):
        hands = [c for c in centers if self.__is_hand(c["class"])]
        persons = [c for c in centers if c["class"] == "person"]
        key_objects = [c for c in centers if self.__is_key_object(c["class"])]

        for hand in hands:
            employee = self.__find_owner_person_for_hand(hand, persons)
            employee_track_id = employee["track_id"] if employee else None
            employee_id = employee.get("employee_id") if employee else None
            employee_conf = self.__extract_employee_confidence(employee)

            for obj in key_objects:
                if self.__calculate_distance(hand["position"], obj["position"]) > self.__HAND_ITEM_DISTANCE_PX:
                    continue

                hp = patterns.get(hand["unique_id"], "")
                obj_class = obj["class"]

                if obj_class == "knife" and hp in {"linear", "vertical"}:
                    return {
                        "type": "cut",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class == "container" and hp == "circular":
                    return {
                        "type": "mix",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class == "food" and hp == "linear":
                    return {
                        "type": "serve",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class == "tool" and hp in {"linear", "circular"}:
                    return {
                        "type": "serve",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or None,
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class == "phone":
                    return {
                        "type": "phone_use",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class == "cigarette_or_smoke_object":
                    return {
                        "type": "smoking",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

                if obj_class in {"cup", "bottle"} and hp in {"linear", "vertical"}:
                    return {
                        "type": "drinking",
                        "employee_track_id": employee_track_id,
                        "employee_id": employee_id or "undefined",
                        "employee_confidence": employee_conf,
                        "key_object_id": obj["track_id"],
                        "key_object_class": obj_class,
                    }

        return None

    def __update_action_observation_state(self, camera_id: str, action: Dict, centers: List[Dict], pkt: Dict):
        state = self.__detected_actions[camera_id]
        state["last_observed_objects"] = centers
        state["last_seen_packet_ts"] = time.time()

        if action.get("employee_track_id") is not None:
            state["employee_track_id"] = action["employee_track_id"]
        if action.get("employee_id"):
            state["employee_id"] = action["employee_id"]
        if action.get("employee_confidence") is not None:
            state["employee_confidence"] = float(action["employee_confidence"])
        if action.get("key_object_id") is not None:
            state["key_object_id"] = action["key_object_id"]
        if action.get("key_object_class") is not None:
            state["key_object_class"] = action["key_object_class"]

    def __get_action_employee_object(self, camera_id: str, objects: List[Dict]) -> Optional[Dict]:
        state = self.__detected_actions[camera_id]
        employee_track_id = state.get("employee_track_id")

        if employee_track_id is not None:
            for obj in objects:
                if obj.get("class") == "person" and str(obj.get("id")) == str(employee_track_id):
                    return obj

        employee_id = state.get("employee_id")
        if employee_id and employee_id != "undefined":
            for obj in objects:
                if obj.get("class") == "person" and obj.get("employee_id") == employee_id:
                    return obj

        persons = [obj for obj in objects if obj.get("class") == "person"]
        if persons:
            return max(persons, key=lambda x: float(x.get("confidence", 0.0)))

        return None

    def __find_owner_person_for_hand(self, hand: Dict, persons: List[Dict]) -> Optional[Dict]:
        if not persons:
            return None

        hx, hy = hand["position"]
        best = None
        best_dist = float("inf")

        for person in persons:
            pb = person["bbox"]
            if self.__point_in_bbox((hx, hy), pb):
                return person

            px, py = person["position"]
            dist = self.__calculate_distance((hx, hy), (px, py))
            if dist < best_dist:
                best_dist = dist
                best = person

        return best

    def __extract_employee_confidence(self, employee_obj: Optional[Dict]) -> float:
        if employee_obj is None:
            return 0.0
        if employee_obj.get("face_score") is not None:
            return float(employee_obj["face_score"])
        return float(employee_obj.get("confidence", 0.0))

    def __is_carrying_class_group(
        self,
        employee_obj: Optional[Dict],
        objects: List[Dict],
        target_classes: set,
    ) -> bool:
        if employee_obj is None:
            return False

        employee_bbox = self.__parse_bbox(employee_obj.get("bbox"))
        if not employee_bbox:
            return False

        hand_boxes = []
        for obj in objects:
            if obj.get("class") in self.__hand_classes:
                bbox = self.__parse_bbox(obj.get("bbox"))
                if bbox and (
                    self.__bbox_contains_bbox(employee_bbox, bbox)
                    or self.__iou(employee_bbox, bbox) > 0.01
                ):
                    hand_boxes.append(bbox)

        if not hand_boxes:
            return False

        for obj in objects:
            if obj.get("class") not in target_classes:
                continue
            target_bbox = self.__parse_bbox(obj.get("bbox"))
            if not target_bbox:
                continue

            for hand_bbox in hand_boxes:
                if self.__iou(hand_bbox, target_bbox) > self.__HAND_OVERLAP_IOU:
                    return True
                if self.__bbox_contains_bbox(hand_bbox, target_bbox) or self.__bbox_contains_bbox(target_bbox, hand_bbox):
                    return True

        return False

    def stop(self):
        self.stop_event.set()

    def action_possible_on_cam(self, cam: str) -> bool:
        return cam in self.__action_possible_cameras

    def __reset_camera_state(self, cam: str):
        self.__detected_actions[cam] = {
            "state": "IDLE",
            "action_type": None,
            "candidate_since": None,
            "action_id": None,
            "employee_track_id": None,
            "employee_id": "undefined",
            "employee_confidence": 0.0,
            "key_object_id": None,
            "key_object_class": None,
            "timestamp_start": None,
            "timestamp_end": None,
            "last_action_seen_ts": 0.0,
            "last_motion_seen_ts": 0.0,
            "last_seen_packet_ts": 0.0,
            "left_zone_since": None,
            "tracking_lost_since": None,
            "key_object_lost_since": None,
            "last_observed_objects": [],
            "hygiene": None,
        }

    def __clear_cam_data(self, cam: str):
        self.__action_possible_cameras.discard(cam)
        self.__previous_position.pop(cam, None)
        self.__movement_vectors.pop(cam, None)
        self.__last_seen_ids.pop(cam, None)
        self.__reset_camera_state(cam)

    def __filter_objects(self, objects: List[Dict]) -> List[Dict]:
        filtered = []
        for obj in objects:
            try:
                conf = float(obj.get("confidence", 0.0))
                cls = obj.get("class")

                threshold = self.__detection_threshold
                if cls in self.__hand_classes:
                    threshold = 0.3

                if conf < threshold:
                    continue
                if not cls:
                    continue
                if not self.__parse_bbox(obj.get("bbox")):
                    continue

                filtered.append(obj)
            except Exception:
                continue
        return filtered

    def __calculate_bbox_center(self, bbox):
        x1, y1, x2, y2 = bbox
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def __calculate_distance(self, p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def __parse_bbox(self, bbox):
        try:
            if bbox is None:
                return None
            if isinstance(bbox, str):
                import json
                bbox = json.loads(bbox)
            vals = [float(x) for x in bbox]
            if len(vals) != 4:
                return None
            x1, y1, x2, y2 = vals
            if x2 <= x1 or y2 <= y1:
                return None
            return vals
        except Exception:
            return None

    def __is_hand(self, cls: str) -> bool:
        return cls in self.__hand_classes

    def __is_key_object(self, cls: str) -> bool:
        return cls in self.__key_object_classes

    def __point_in_bbox(self, pt, bbox):
        x, y = pt
        x1, y1, x2, y2 = bbox
        return x1 <= x <= x2 and y1 <= y <= y2

    def __bbox_contains_bbox(self, outer, inner):
        ox1, oy1, ox2, oy2 = outer
        ix1, iy1, ix2, iy2 = inner
        return ox1 <= ix1 and oy1 <= iy1 and ox2 >= ix2 and oy2 >= iy2

    def __iou(self, boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[1])

        inter_w = max(0.0, xB - xA)
        inter_h = max(0.0, yB - yA)
        inter = inter_w * inter_h

        area_a = max(0.0, boxA[2] - boxA[0]) * max(0.0, boxA[3] - boxA[1])
        area_b = max(0.0, boxB[2] - boxB[0]) * max(0.0, boxB[3] - boxB[1])

        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union

    def __point_in_polygon(self, point, polygon):
        x, y = point
        inside = False
        n = len(polygon)
        if n < 3:
            return False

        px1, py1 = polygon[0]
        for i in range(n + 1):
            px2, py2 = polygon[i % n]
            if y > min(py1, py2):
                if y <= max(py1, py2):
                    if x <= max(px1, px2):
                        if py1 != py2:
                            xinters = (y - py1) * (px2 - px1) / (py2 - py1 + 1e-9) + px1
                        else:
                            xinters = px1
                        if px1 == px2 or x <= xinters:
                            inside = not inside
            px1, py1 = px2, py2
        return inside