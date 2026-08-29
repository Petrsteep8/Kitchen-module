import time
import uuid
from typing import Dict, Any, List, Tuple
from shapely.geometry import Point, Polygon


class ZoneStatusLogic:
    def __init__(self, config: Dict[str, Any], zones_config: Dict[str, Any]):
        """
        config: config.yaml
        zones_config: загруженный zones.yaml (описание зон)
        """
        self.windows = {
            'IN_PREP_ZONE': config.get('T_zone_prep_confirm_sec', 2.0),
            'IN_WORKSTATION': config.get('T_zone_workstation_confirm_sec', 2.0),
            'IN_REST_ZONE': config.get('T_zone_rest_confirm_sec', 3.0),
            'OUT_OF_WORKPLACE': config.get('T_zone_out_confirm_sec', 5.0),
            'BETWEEN_ZONES': config.get('T_zone_between_confirm_sec', 1.0),
            'OBS_UNCERTAIN': config.get('T_zone_obs_uncertain_sec', 2.0),
        }
        self.heartbeat_interval = config.get('T_zone_status_heartbeat_sec', 30.0)
        self.hysteresis_px = config.get('zone_boundary_hysteresis_px', 15.0)

        self.polygons = {}
        for z in zones_config.get('zones', []):
            poly = Polygon(z['polygon'])
            self.polygons[(z['camera_id'], z['zone_id'])] = {'poly': poly, 'type': z['zone_type']}
        
        self.states: Dict[tuple, Dict[str, Any]] = {}
        self.candidates: Dict[tuple, Dict[str, Any]] = {}

    def _build_normalized_event(self, camera_id: str, track_id: str, event_type: str, payload: Dict) -> Dict:
        return {
            "event_id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "event_type": event_type,
            "camera_id": camera_id,
            "entity_id": track_id,
            "entity_type": "employee",
            "confidence": 1.0,
            "payload": payload,
            "time_source": "server_ntp"
        }

    def _determine_current_zone(self, camera_id: str, track_id: str, pt: Point) -> Tuple[Optional[str], Optional[str], str]:
        """
        Определяет зону и статус с учетом гистерезиса (п. 14)
        pt - опорная точка (середина нижней грани bbox)
        """
        state_key = (camera_id, track_id)
        current_zone_id = self.states.get(state_key, {}).get('zone_id')
        
        if current_zone_id:
            poly_data = self.polygons.get((camera_id, current_zone_id))
            if poly_data:
                if not poly_data['poly'].contains(pt):
                    distance_to_boundary = poly_data['poly'].distance(pt)
                    if distance_to_boundary <= self.hysteresis_px:
                        status = self._get_status_from_zone_type(poly_data['type'])
                        return current_zone_id, poly_data['type'], status

        matched_zones = []
        for (cam, z_id), data in self.polygons.items():
            if cam == camera_id and data['poly'].contains(pt):
                matched_zones.append((z_id, data))
        
        if not matched_zones:
            return None, None, "OUT_OF_WORKPLACE"

        matched_zones.sort(key=lambda x: x[1]['poly'].area)
        best_zone_id, best_data = matched_zones[0]

        return best_zone_id, best_data['type'], self._get_status_from_zone_type(best_data['type'])

    def _get_status_from_zone_type(self, zone_type: str) -> str:
        mapping = {
            'prep_zone': 'IN_PREP_ZONE',
            'clean_zone': 'IN_WORKSTATION',
            'rest_zone': 'IN_REST_ZONE',
            'neutral_zone': 'BETWEEN_ZONES'
        }
        return mapping.get(zone_type, 'IN_WORKSTATION')

    def process_location(self, camera_id: str, track_id: str, bottom_center_x: float, bottom_center_y: float, tracking_lost: bool) -> List[Dict]:
        events = []
        state_key = (camera_id, track_id)
        current_time = time.time()


        if tracking_lost:
            target_status = "OBS_UNCERTAIN"
            target_zone_id, target_zone_type = None, None
        else:
            pt = Point(bottom_center_x, bottom_center_y)
            target_zone_id, target_zone_type, target_status = self._determine_current_zone(camera_id, track_id, pt)

        if state_key not in self.states:
            self.states[state_key] = {
                'status': target_status, 'zone_id': target_zone_id, 'zone_type': target_zone_type, 
                'ts': current_time, 'last_hb': current_time
            }
            return events

        current_state = self.states[state_key]

        if current_time - current_state['last_hb'] >= self.heartbeat_interval:
            current_state['last_hb'] = current_time
            # здесь вызывается логика отправки Heartbeat агрегатору


        if current_state['status'] == target_status and current_state['zone_id'] == target_zone_id:
            self.candidates.pop(state_key, None)
            return events

        if state_key not in self.candidates or self.candidates[state_key]['status'] != target_status or self.candidates[state_key]['zone_id'] != target_zone_id:
            self.candidates[state_key] = {
                'status': target_status, 'zone_id': target_zone_id, 'zone_type': target_zone_type, 'start_time': current_time
            }
            return events

        duration = current_time - self.candidates[state_key]['start_time']

        if duration >= self.windows.get(target_status, 2.0):
            

            if current_state['zone_id']:
                exit_payload = {
                    "zone_id": current_state['zone_id'],
                    "zone_type": current_state['zone_type'],
                    "outside_duration_sec": 0.0,
                    "confirmed": True
                }
                events.append(self._build_normalized_event(camera_id, track_id, "ZONE_EXIT", exit_payload))


            if target_zone_id and target_status not in ["OUT_OF_WORKPLACE", "OBS_UNCERTAIN"]:
                enter_payload = {
                    "zone_id": target_zone_id,
                    "zone_type": target_zone_type,
                    "dwell_sec": duration
                }
                events.append(self._build_normalized_event(camera_id, track_id, "ZONE_ENTER", enter_payload))


            self.states[state_key] = {
                'status': target_status,
                'zone_id': target_zone_id,
                'zone_type': target_zone_type,
                'ts': current_time,
                'last_hb': current_time
            }
            del self.candidates[state_key]

        return events