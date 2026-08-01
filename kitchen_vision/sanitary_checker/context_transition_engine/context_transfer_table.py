dirty_contexts = ["raw_animal", "raw_plant_unwashed", "waste", "nonfood_contact", "dirty_dishes_containers", "cleaning_inventory"]

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
    from kitchen_vision.sanitary_checker.context_transition_engine.context_transfer_table import CompatibilityMatrix
except ImportError as e:
    pass

class ContextTransferTable:
    def __init__(self, fsm_hub: FSMHub):
        self._fsm_hub = fsm_hub or None
        self._compatibility_matrix = CompatibilityMatrix()

    def _get_fsm(self, source_entity: str):
        if source_entity == "food":
            return None     #TODO food context fsm
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

    def _get_source_context(self, fsm, source_entity_id):
        return fsm.get_context(source_entity_id)

    def process_event(self, pkt: dict):
        source_entity = pkt["payload"]["actor_entity_type"]
        source_entity_id = pkt["payload"]["actor_entity_id"]
        target_entity = pkt["payload"]["target_entity_type"]
        target_entity_id = pkt["payload"]["target_entity_id"]
        interaction_type = pkt["payload"]["interaction_type"]

        fsm = self._get_fsm(source_entity)
        if fsm is None:
            return

        source_context = self._get_source_context(fsm=fsm, source_entity_id=source_entity_id)
        fsm = self._get_fsm(target_entity)
        if fsm is None:
            return
        target_context = self._get_source_context(fsm=fsm, source_entity_id=target_entity_id)

        if source_entity == "food":
            self._process_food_event(
                target_entity=target_entity,
                target_entity_id = target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)
        if source_entity == "tool":
            self._process_tool_event(
                target_entity=target_entity,
                target_entity_id=target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)
        if source_entity == "hands":
            self._process_hands_event(
                target_entity=target_entity,
                target_entity_id=target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)
        if source_entity == "surface":
            self._process_surface_event(
                target_entity=target_entity,
                target_entity_id=target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)
        if source_entity == "employee":
            self._process_employee_event(
                target_entity=target_entity,
                target_entity_id=target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)
        if source_entity == "zone":
            self._process_zone_event(
                target_entity=target_entity,
                target_entity_id=target_entity_id,
                interaction_type=interaction_type,
                target_context=target_context,
                source_context=source_context,
                fsm=fsm)

    def _check_compatibility(self, target_context: str, source_context: str):
        self._compatibility_matrix.process_context_transition(target_context=target_context, source_context=source_context)

    def _process_food_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "tool" and interaction_type in ["contact", "hold", "carry"] and source_context == "raw_animal":
            new_context = "raw_animal"
        if target_entity == "tool" and interaction_type in ["contact", "hold", "carry"] and source_context == "raw_plant_unwashed":
            new_context = "raw_plant_unwashed"
        if target_entity == "tool" and interaction_type == "place":
            new_context = source_context
        if target_entity == "hands" and interaction_type in ["contact", "hold", "carry"] and source_context == "raw_animal":
            new_context = "raw_animal"
        if target_entity == "hands" in ["contact", "hold", "carry"] and source_context == "raw_plant_unwashed":
            new_context = "raw_plant_unwashed"
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)

    def _process_tool_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "hands" and interaction_type in ["contact", "hold"] and source_context in dirty_contexts:
            new_context = source_context
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)

    def _process_hands_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "container" and interaction_type in ["contact", "place"]:
            new_context = source_context
        if target_entity == "surface" and interaction_type == "contact" and source_context in dirty_contexts:
            new_context = source_context
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)

    def _process_surface_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "food" and interaction_type in ["contact", "place"] and source_context == dirty_contexts:
            new_context = source_context
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)

    def _process_employee_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "tool" and interaction_type in ["hold", "carry"]:
            new_context = source_context
        if target_entity == "container" and interaction_type in ["hold", "carry"]:
            new_context = source_context
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)

    def _process_zone_event(self, target_entity: str, target_entity_id: str, interaction_type: str, target_context: str, source_context: str, fsm):
        new_context = ""
        if target_entity == "employee" and interaction_type == "dwell":
            new_context = source_context
        if target_entity in ["food", "tool", "container"] and interaction_type == "dwell":
            new_context = source_context
        fsm.set_context(track_id=target_entity_id, new_context=new_context)
        self._check_compatibility(target_context=new_context, source_context=target_context)