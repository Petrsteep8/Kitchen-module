allowed_predecessors_ready_to_eat = ["neutral", "sanitized", "clean_prep", "packaging_dispatch"]
allowed_predecessors_clean_prep = ["neutral", "sanitized", "clean_zone"]
allowed_predecessors_packaging_dispatch= ["neutral", "sanitized", "clean_prep", "ready_to_eat"]
allowed_predecessors_clean_zone = ["neutral", "sanitized", "clean_prep", "clean_zone"]
forbidden_predecessors = ["raw_animal", "raw_plant_unwashed", "waste", "dirty_dishes_containers", "cleaning_inventory",
                          "nonfood_contact", "dirty_zone", "contaminated"]


class CompatibilityMatrix:
    def __init__(self):
        pass

    def process_context_transition(self, target_context: str, source_context: str):
        if target_context == "ready_to_eat":
            self._process_ready_to_eat_context_transition(source_context=source_context)
        if target_context == "clean_prep":
            self._process_clean_prep_context_transition(source_context=source_context)
        if target_context == "packaging_dispatch":
            self._process_packaging_dispatch_context_transition(source_context=source_context)
        if target_context == "clean_zone":
            self._process_clean_zone_context_transition(source_context=source_context)

    def _process_ready_to_eat_context_transition(self, source_context: str):
        if source_context not in allowed_predecessors_ready_to_eat and source_context in forbidden_predecessors:
            print("VIOLATION")

    def _process_clean_prep_context_transition(self, source_context: str):
        if source_context not in allowed_predecessors_clean_prep and source_context in forbidden_predecessors:
            print("VIOLATION")

    def _process_packaging_dispatch_context_transition(self, source_context: str):
        if source_context not in allowed_predecessors_packaging_dispatch and source_context in forbidden_predecessors:
            print("VIOLATION")

    def _process_clean_zone_context_transition(self, source_context: str):
        if source_context not in allowed_predecessors_clean_zone and source_context in forbidden_predecessors:
            print("VIOLATION")