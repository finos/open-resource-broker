"""
Application Layer - Use Cases and DTOs

This application layer is organized by bounded contexts:
- base/: Shared application concepts (DTOs, commands, queries)
- template/: Template use cases and DTOs
- machine/: Machine use cases and DTOs
- request/: Request use cases and DTOs

Each context contains:
- commands.py: Command DTOs and handlers
- queries.py: Query DTOs and handlers
- dto.py: Data transfer objects
- service.py: Application service (orchestrates use cases)
"""

from .dto.base import BaseCommand, BaseDTO, BaseQuery, BaseResponse

__all__: list[str] = ["BaseDTO", "BaseCommand", "BaseQuery", "BaseResponse"]
