import enum

try:
    from kitchen_vision.sanitary_checker.fsm.fsm_hub import FSMHub
except ImportError as e:
    pass

class TTLLogic:
    def __init__(self, fsm_hub: FSMHub):
        self._fsm_hub = fsm_hub