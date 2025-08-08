#!/bin/bash
set -e

# Docker entrypoint script for Open Host Factory Plugin REST API
# Handles configuration, environment setup, and service startup

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_debug() {
    if [[ "${HF_DEBUG:-false}" == "true" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

# Print startup banner
print_banner() {
    cat << 'EOF'
  ___                   _   _           _     _____          _                   
 / _ \ _ __   ___ _ __  | | | | ___  ___| |_  |  ___|_ _  ___| |_ ___  _ __ _   _ 
| | | | '_ \ / _ \ '_ \ | |_| |/ _ \/ __| __| | |_ / _` |/ __| __/ _ \| '__| | | |
| |_| | |_) |  __/ | | |  _  | (_) \__ \ |_  |  _| (_| | (__| || (_) | |  | |_| |
 \___/| .__/ \___|_| |_|_| |_|\___/|___/\__| |_|  \__,_|\___|\__\___/|_|   \__, |
      |_|                                                                  |___/ 
                            Plugin REST API
EOF
    echo ""
    log_info "Open Host Factory Plugin REST API"
    log_info "Version: ${VERSION:-1.0.0}"
    log_info "Build: ${BUILD_DATE:-unknown}"
    echo ""
}

# Validate environment
validate_environment() {
    log_info "Validating environment..."

    # Check Python version
    python_version=$(python --version 2>&1)
    log_debug "Python version: $python_version"

    # Check required directories
    for dir in "/app/logs" "/app/data" "/app/tmp"; do
        if [[ ! -d "$dir" ]]; then
            log_warn "Creating missing directory: $dir"
            mkdir -p "$dir"
        fi
    done

    # Check write permissions
    if [[ ! -w "/app/logs" ]]; then
        log_error "No write permission to /app/logs"
        exit 1
    fi

    if [[ ! -w "/app/data" ]]; then
        log_error "No write permission to /app/data"
        exit 1
    fi

    log_info "Environment validation complete"
}

# Setup configuration
setup_configuration() {
    log_info "Setting up configuration..."

    # Configuration file precedence:
    # 1. /app/config/docker.json (if exists)
    # 2. /app/config/production.json (if exists)  
    # 3. /app/config/default_config.json (fallback)
    # 4. Environment variables (highest precedence)

    config_file=""

    if [[ -f "/app/config/docker.json" ]]; then
        config_file="/app/config/docker.json"
        log_info "Using Docker-specific configuration: $config_file"
    elif [[ -f "/app/config/production.json" ]]; then
        config_file="/app/config/production.json"
        log_info "Using production configuration: $config_file"
    elif [[ -f "/app/config/default_config.json" ]]; then
        config_file="/app/config/default_config.json"
        log_info "Using default configuration: $config_file"
    else
        log_warn "No configuration file found, using environment variables only"
    fi

    # Export configuration file path for the application
    if [[ -n "$config_file" ]]; then
        export HF_CONFIG_FILE="$config_file"
    fi

    # Log key configuration values
    log_debug "Server enabled: ${HF_SERVER_ENABLED:-true}"
    log_debug "Server host: ${HF_SERVER_HOST:-0.0.0.0}"
    log_debug "Server port: ${HF_SERVER_PORT:-8000}"
    log_debug "Auth enabled: ${HF_AUTH_ENABLED:-false}"
    log_debug "Auth strategy: ${HF_AUTH_STRATEGY:-none}"
    log_debug "Provider type: ${HF_PROVIDER_TYPE:-aws}"

    log_info "Configuration setup complete"
}

# Setup AWS credentials (if needed)
setup_aws_credentials() {
    if [[ "${HF_PROVIDER_TYPE:-aws}" == "aws" ]] || [[ "${HF_AUTH_STRATEGY:-none}" == "iam" ]]; then
        log_info "Setting up AWS credentials..."

        # Check for AWS credentials
        if [[ -n "${AWS_ACCESS_KEY_ID}" ]] && [[ -n "${AWS_SECRET_ACCESS_KEY}" ]]; then
            log_info "Using AWS credentials from environment variables"
        elif [[ -f "/root/.aws/credentials" ]] || [[ -f "/home/ohfp/.aws/credentials" ]]; then
            log_info "Using AWS credentials from credentials file"
        elif [[ -n "${AWS_ROLE_ARN}" ]]; then
            log_info "Using AWS IAM role: ${AWS_ROLE_ARN}"
        else
            log_warn "No AWS credentials found - some features may not work"
        fi

        # Set default region if not specified
        export AWS_DEFAULT_REGION="${HF_PROVIDER_AWS_REGION:-us-east-1}"
        log_debug "AWS region: ${AWS_DEFAULT_REGION}"
    fi
}

# Wait for dependencies (if needed)
wait_for_dependencies() {
    log_info "Checking dependencies..."

    # If using external database, wait for it
    if [[ -n "${DATABASE_URL}" ]]; then
        log_info "Waiting for database connection..."
        # Add database connection check here if needed
    fi

    # If using external services, wait for them
    if [[ -n "${EXTERNAL_SERVICE_URL}" ]]; then
        log_info "Waiting for external services..."
        # Add external service checks here if needed
    fi

    log_info "Dependencies check complete"
}

# Start the application
start_application() {
    log_info "Starting Open Host Factory Plugin REST API..."

    # Build command arguments
    cmd_args=()

    # Add configuration file if available
    if [[ -n "${HF_CONFIG_FILE}" ]]; then
        cmd_args+=("--config" "${HF_CONFIG_FILE}")
    fi

    # Add logging level
    if [[ -n "${HF_LOGGING_LEVEL}" ]]; then
        cmd_args+=("--log-level" "${HF_LOGGING_LEVEL}")
    fi

    # Add system serve command
    cmd_args+=("system" "serve")

    # Add server-specific arguments
    if [[ -n "${HF_SERVER_HOST}" ]]; then
        cmd_args+=("--host" "${HF_SERVER_HOST}")
    fi

    if [[ -n "${HF_SERVER_PORT}" ]]; then
        cmd_args+=("--port" "${HF_SERVER_PORT}")
    fi

    if [[ -n "${HF_SERVER_WORKERS}" ]] && [[ "${HF_SERVER_WORKERS}" -gt 1 ]]; then
        cmd_args+=("--workers" "${HF_SERVER_WORKERS}")
    fi

    if [[ "${HF_SERVER_RELOAD:-false}" == "true" ]]; then
        cmd_args+=("--reload")
    fi

    if [[ -n "${HF_SERVER_LOG_LEVEL}" ]]; then
        cmd_args+=("--log-level" "${HF_SERVER_LOG_LEVEL}")
    fi

    log_info "Executing: python src/run.py ${cmd_args[*]}"

    # Execute the application
    exec python src/run.py "${cmd_args[@]}"
}

# Handle different commands
handle_command() {
    case "$1" in
        "serve"|"server"|"")
            # Default: start the REST API server
            setup_configuration
            setup_aws_credentials
            wait_for_dependencies
            start_application
            ;;
        "cli")
            # Run CLI commands
            shift
            log_info "Running CLI command: $*"
            exec python src/run.py "$@"
            ;;
        "bash"|"sh")
            # Start interactive shell
            log_info "Starting interactive shell"
            exec /bin/bash
            ;;
        "health"|"healthcheck")
            # Health check
            log_info "Running health check"
            curl -f "http://localhost:${HF_SERVER_PORT:-8000}/health" || exit 1
            ;;
        "version")
            # Show version
            echo "Open Host Factory Plugin REST API"
            echo "Version: ${VERSION:-1.0.0}"
            echo "Build: ${BUILD_DATE:-unknown}"
            echo "Revision: ${VCS_REF:-unknown}"
            ;;
        *)
            # Unknown command - pass through to application
            log_info "Passing command to application: $*"
            exec python src/run.py "$@"
            ;;
    esac
}

# Main execution
main() {
    print_banner
    validate_environment
    handle_command "$@"
}

# Trap signals for graceful shutdown
trap 'log_info "Received shutdown signal, stopping..."; exit 0' SIGTERM SIGINT

# Execute main function
main "$@"
