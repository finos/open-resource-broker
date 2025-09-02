# Architecture Metrics Report

*Generated: 2025-07-12 12:07:07*

## Summary Statistics

| Metric | Value |
|--------|-------|
| Total Python Files | 324 |
| Total Lines of Code | 62,571 |
| Average File Size | 193 lines |

## File Size Distribution

| Category | Count | Percentage |
|----------|-------|------------|
| Small (< 100 lines) | 108 | 33.3% |
| Medium (100-300 lines) | 146 | 45.1% |
| Large (300-600 lines) | 63 | 19.4% |
| Extra Large (> 600 lines) | 7 | 2.2% |

## Layer Distribution

| Layer | Files | Lines | Avg Lines/File |
|-------|-------|-------|----------------|
| Bootstrap.py | 1 | 246 | 246 |
| Run.py | 1 | 31 | 31 |
| Interface | 6 | 1,593 | 265 |
| Config | 20 | 3,075 | 153 |
| Providers | 53 | 13,905 | 262 |
| Cli | 3 | 1,152 | 384 |
| Api | 21 | 3,088 | 147 |
| Application | 50 | 8,173 | 163 |
| Monitoring | 2 | 762 | 381 |
| Infrastructure | 122 | 24,755 | 202 |
| Domain | 45 | 5,791 | 128 |

## Large Files Analysis

The following files exceed the 600-line threshold and may benefit from refactoring:

| File | Lines | Recommendation |
|------|-------|----------------|
| `infrastructure/error/exception_handler.py` | 1060 | Consider splitting into smaller modules |
| `infrastructure/di/container.py` | 1037 | Consider splitting into smaller modules |
| `config/loader.py` | 735 | Consider splitting into smaller modules |
| `providers/base/strategy/composite_strategy.py` | 637 | Consider splitting into smaller modules |
| `providers/base/strategy/fallback_strategy.py` | 635 | Consider splitting into smaller modules |
| `infrastructure/persistence/json/template.py` | 623 | Consider splitting into smaller modules |
| `providers/aws/infrastructure/handlers/spot_fleet_handler.py` | 605 | Consider splitting into smaller modules |

## Quality Indicators

- **Single Responsibility Adherence**: 97.8%
- **Code Distribution Balance**: Unbalanced

---

*This report is automatically generated. Run `python scripts/generate_arch_docs.py --metrics` to regenerate.*
