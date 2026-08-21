try:
    from kitchen_vision.sanitary_checker.fsm.employee_context_fsm import EmployeeContextFSM
    from kitchen_vision.sanitary_checker.fsm.hands_context_fsm import HandsContextFSM
    from kitchen_vision.sanitary_checker.fsm.tool_context_fsm import ToolContextFSM
    from kitchen_vision.sanitary_checker.fsm.container_context_fsm import ContainerContextFSM
    from kitchen_vision.sanitary_checker.fsm.surface_context_fsm import SurfaceContextFSM
    from kitchen_vision.sanitary_checker.fsm.zone_context_fsm import ZoneFSM
    from kitchen_vision.sanitary_checker.fsm.observation_fsm import ObservationFSM
    from kitchen_vision.sanitary_checker.fsm.action_termination_fsm import ActionTerminationFSM
except ImportError as e:
    pass

class FSMHub:
    def __init__(self):
        self._employee_context_fsm = EmployeeContextFSM() or None
        self._hands_context_fsm = HandsContextFSM() or None
        self._tool_context_fsm = ToolContextFSM() or None
        self._container_context_fsm = ContainerContextFSM() or None
        self._surface_context_fsm = SurfaceContextFSM() or None
        self._zone_context_fsm = ZoneFSM() or None
        self._observation_fsm = ObservationFSM() or None
        self._action_termination_fsm = ActionTerminationFSM() or None

    @property
    def employee_context(self):
        return self._employee_context_fsm
    @property
    def hands_context(self):
        return self._hands_context_fsm
    @property
    def tool_context(self):
        return self._tool_context_fsm
    @property
    def container_context(self):
        return self._container_context_fsm
    @property
    def surface_context(self):
        return self._surface_context_fsm
    @property
    def zone_context(self):
        return self._zone_context_fsm
    @property
    def observation_fsm(self):
        return self._observation_fsm
    @property
    def action_termination_fsm(self):
        return self._action_termination_fsm
