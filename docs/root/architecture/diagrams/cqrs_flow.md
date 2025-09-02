# CQRS Flow Diagram

*Generated: 2025-07-12 12:07:09*

```mermaid
graph TD
    %% CQRS Flow Diagram
    %% Generated: 2025-07-12 12:07:09

    Client[Client/Interface]
    CommandBus[Command Bus]
    QueryBus[Query Bus]
    EventBus[Event Bus]
    Domain[Domain Model]
    ReadStore[Read Store]
    WriteStore[Write Store]

    %% Command Flow
    Client -->|Commands| CommandBus
    CommandBus --> CommandHandlers[Command Handlers<br/>8 handlers]
    CommandHandlers --> Domain
    Domain --> WriteStore
    Domain -->|Events| EventBus

    %% Query Flow  
    Client -->|Queries| QueryBus
    QueryBus --> QueryHandlers[Query Handlers<br/>0 handlers]
    QueryHandlers --> ReadStore

    %% Event Flow
    EventBus --> EventHandlers[Event Handlers<br/>5 handlers]
    EventHandlers --> ReadStore
    EventHandlers --> ExternalSystems[External Systems]

    %% Styling
    classDef commandStyle fill:#ffebee,stroke:#c62828,stroke-width:2px
    classDef queryStyle fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef eventStyle fill:#f1f8e9,stroke:#2e7d32,stroke-width:2px
    classDef domainStyle fill:#fce4ec,stroke:#ad1457,stroke-width:2px

    class CommandBus,CommandHandlers commandStyle
    class QueryBus,QueryHandlers queryStyle
    class EventBus,EventHandlers eventStyle
    class Domain domainStyle
```


---

*This diagram is automatically generated. Run `python scripts/generate_dependency_graphs.py` to regenerate.*
