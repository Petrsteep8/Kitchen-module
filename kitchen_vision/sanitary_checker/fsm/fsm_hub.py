try:
    from kitchen_vision.sanitary_checker.fsm.employee_context_fsm import EmployeeContextFSM
    from kitchen_vision.sanitary_checker.fsm.hands_context_fsm import HandsContextFSM
    from kitchen_vision.sanitary_checker.fsm.tool_context_fsm import ToolContextFSM
    from kitchen_vision.sanitary_checker.fsm.container_context_fsm import ContainerContextFSM
    from kitchen_vision.sanitary_checker.fsm.surface_context_fsm import SurfaceContextFSM
    from kitchen_vision.sanitary_checker.fsm.zone_fsm import ZoneContextFSM
    from kitchen_vision.sanitary_checker.fsm.observation_fsm import ObservationFSM
    from kitchen_vision.sanitary_checker.fsm.action_termination_fsm import ActionTerminationFSM
except ImportError as e:
    pass

class FSMHub:
    def __init__(self):
        self._employee_context_fsm = EmployeeContextFSM()
        self._hands_context_fsm = HandsContextFSM()
        self._tool_context_fsm = ToolContextFSM()
        self._container_context_fsm = ContainerContextFSM()
        self._surface_context_fsm = SurfaceContextFSM()
        self._zone_context_fsm = ZoneContextFSM()
        self._observation_fsm = ObservationFSM()
        self._action_termination_fsm = ActionTerminationFSM()

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
