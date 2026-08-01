try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError as e:
    pass

class ContaminationTable:
    def __init__(self, fsm_hub: FSMHub):
        self._fsm_hub = fsm_hub

    def _get_fsm(self, source_entity: str):
        if source_entity == "tool":
            return self._fsm_hub.tool_context()
        if source_entity == "hands":
            return self._fsm_hub.hands_context()
        if source_entity == "surface":
            return self._fsm_hub.surface_context()
        if source_entity == "employee":
            return self._fsm_hub.employee_context()
        if source_entity == "zone":
            return self._fsm_hub.zone_context()
        return None

    def process_event(self, pkt: dict):
        contamination_type = pkt["payload"]["contamination_type"]
        target_entity = pkt["payload"]["target_entity"]
        target_entity_id = pkt["payload"]["target_entity_id"]

        fsm = self._get_fsm(target_entity)
        if fsm is None:
            return

        if contamination_type == "raw_food":
            self._process_raw_food_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)
        if contamination_type == "waste":
            self._process_waste_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)
        if contamination_type == "dirty_surface":
            self._process_dirty_surface_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)
        if contamination_type == "packaging":
            self._process_packaging_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)
        if contamination_type == "nonfood_contact":
            self._process_nonfood_contact_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)
        if contamination_type == "unknown":
            self._process_unknown_event(fsm=fsm, target_entity=target_entity, target_entity_id=target_entity_id)

    def _process_raw_food_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands", "tool", "surface", "container"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="contaminated")
    def _process_waste_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands", "tool", "surface", "container"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="contaminated")
    def _process_dirty_surface_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands", "tool", "container"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="contaminated")
    def _process_packaging_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="contaminated")
    def _process_nonfood_contact_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="contaminated")
    def _process_unknown_event(self, fsm, target_entity: str, target_entity_id: str):
        if target_entity in ["hands", "tool", "surface", "container"]:
            fsm.set_sanitary_state(track_id=target_entity_id, sanitary_state="unknown")