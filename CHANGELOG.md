# Changelog

All notable changes to the Open Host Factory Plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Complete MCP (Model Context Protocol) server implementation
- AI assistant integration capabilities
- MCP tools for all CLI operations
- MCP resources for templates, requests, machines, and providers
- AI-friendly prompts for infrastructure provisioning workflows
- Comprehensive MCP test suite
- MCP integration documentation and examples
- Support for both stdio and TCP server modes
- Claude Desktop configuration examples
- Python and Node.js MCP client examples

### Changed
- Consolidated async function handlers for improved performance
- Updated README with MCP server documentation
- Improved error handling with professional status indicators
- Improved logging with structured messages
- **Architecture**: Migrated `TemplateConfigurationManager` from `@injectable` decorator to manual DI registration for better configuration control

### Fixed
- Handler signature consistency across all command handlers
- Professional code standards compliance (removed emojis)
- Template system unification and optimization

## [1.0.0] - 2024-01-15

### Added
- Initial release of Open Host Factory Plugin
- AWS provider support with EC2Fleet, SpotFleet, and Auto Scaling Groups
- Clean Architecture implementation with DDD and CQRS patterns
- REST API with OpenAPI/Swagger documentation
- CLI interface with comprehensive command support
- Configuration-driven provider system
- Template-based infrastructure provisioning
- Docker deployment support
- Comprehensive test suite
- Security scanning and SBOM generation workflows

### Infrastructure
- GitHub Actions CI/CD pipeline
- Dependabot security updates
- Pre-commit hooks with security checks
- Code quality tools (bandit, pip-audit, safety)
- Professional development standards

## [0.9.0] - 2024-01-10

### Added
- Beta release with core functionality
- AWS provider implementation
- Basic CLI interface
- Template system foundation
- Initial documentation

### Changed
- Refactored architecture for scalability
- Improved error handling
- Improved configuration system

### Fixed
- Various bug fixes and stability improvements

## [0.1.0] - 2024-01-01

### Added
- Initial project structure
- Basic provider interface
- Foundation for template system
- Development environment setup
