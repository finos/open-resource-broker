# System Architecture Diagrams

*Last updated: 2025-07-12 12:07:09 (Auto-generated)*

This page contains automatically generated architecture diagrams that visualize the current system structure and dependencies.

## Available Diagrams

### [Layer Dependencies](diagrams/layer_dependencies.md)
Shows the dependency relationships between architectural layers (Domain, Application, Infrastructure, Interface).

### [Module Dependencies](diagrams/module_dependencies.md)  
Visualizes dependencies between key modules in the system.

### [CQRS Flow](diagrams/cqrs_flow.md)
Illustrates the Command Query Responsibility Segregation pattern implementation.

### [Compliance Overview](diagrams/compliance_overview.md)
Shows current architecture compliance metrics and recommendations.

## How to Read These Diagrams

- **Arrows** indicate dependency direction (A → B means A depends on B)
- **Colors** group related components
- **Layers** should follow Clean Architecture principles (dependencies flow inward)

## Regenerating Diagrams

To update these diagrams with the current codebase state:

```bash
python scripts/generate_dependency_graphs.py
```

---

*These diagrams are automatically generated from the current codebase.*
*They reflect the actual implementation, not just the intended design.*
