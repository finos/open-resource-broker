"""JSON persistence package."""

from infrastructure.persistence.json.template import JSONTemplateRepositoryImpl
from infrastructure.persistence.json.unit_of_work import JSONUnitOfWork

__all__: list[str] = ["JSONTemplateRepositoryImpl", "JSONUnitOfWork"]

# Backward compatibility alias
JSONTemplateRepository = JSONTemplateRepositoryImpl
