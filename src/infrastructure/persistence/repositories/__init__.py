"""Single repository implementations using storage strategy composition."""

from .machine_repository import MachineRepositoryImpl as MachineRepository, MachineSerializer
from .request_repository import RequestRepositoryImpl as RequestRepository, RequestSerializer
from .template_repository import TemplateRepositoryImpl as TemplateRepository, TemplateSerializer

__all__: list[str] = [
    "MachineRepository",
    "MachineSerializer",
    "RequestRepository",
    "RequestSerializer",
    "TemplateRepository",
    "TemplateSerializer",
]
