"""Single repository implementations using storage strategy composition."""

from .machine_repository import MachineRepositoryImpl as MachineRepository
from .machine_repository import MachineSerializer
from .request_repository import RequestRepositoryImpl as RequestRepository
from .request_repository import RequestSerializer
from .template_repository import TemplateRepositoryImpl as TemplateRepository
from .template_repository import TemplateSerializer

__all__: list[str] = [
    "MachineRepository",
    "MachineSerializer",
    "RequestRepository",
    "RequestSerializer",
    "TemplateRepository",
    "TemplateSerializer",
]
