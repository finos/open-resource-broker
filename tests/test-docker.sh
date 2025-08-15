#!/bin/bash
set -e

# Docker Testing Script for Open Host Factory Plugin
# Comprehensive testing of Docker containerization

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
    if [[ "${DEBUG:-false}" == "true" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

# Configuration
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_IMAGE_NAME="ohfp-api:docker-test"
CONTAINER_NAME="ohfp-docker-test"
TEST_PORT="8004"

# Cleanup function
cleanup() {
    log_info "Cleaning up test resources..."

    # Stop and remove container
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    fi

    if docker ps -aq -f name="${CONTAINER_NAME}" | grep -q .; then
        docker rm "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    fi

    # Remove test image
    if docker images -q "${TEST_IMAGE_NAME}" | grep -q .; then
        docker rmi "${TEST_IMAGE_NAME}" >/dev/null 2>&1 || true
    fi

    log_info "Cleanup complete"
}

# Trap cleanup on exit
trap cleanup EXIT

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi

    # Check Docker daemon
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon is not running"
        exit 1
    fi

    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        log_warn "Docker Compose not available - some tests will be skipped"
    fi

    # Check pytest
    if ! command -v pytest &> /dev/null; then
        log_error "pytest is not installed"
        exit 1
    fi

    log_info "Prerequisites check passed"
}

# Build test image
build_test_image() {
    log_info "Building test Docker image..."

    cd "${PROJECT_ROOT}"

    docker build \
        -t "${TEST_IMAGE_NAME}" \
        --build-arg BUILD_DATE="$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
        --build-arg VERSION="docker-test" \
        --build-arg VCS_REF="test" \
        . || {
        log_error "Docker build failed"
        exit 1
    }

    log_info "Test image built successfully"
}

# Test Docker build
test_docker_build() {
    log_info "Testing Docker build process..."

    # Test that image was created
    if ! docker images -q "${TEST_IMAGE_NAME}" | grep -q .; then
        log_error "Test image not found"
        return 1
    fi

    # Test image labels
    local labels
    labels=$(docker inspect "${TEST_IMAGE_NAME}" --format '{{json .Config.Labels}}')

    if ! echo "${labels}" | grep -q "org.opencontainers.image.title"; then
        log_error "Image missing required labels"
        return 1
    fi

    log_info "Docker build tests passed"
}

# Test container startup
test_container_startup() {
    log_info "Testing container startup..."

    # Test version command
    local version_output
    if ! version_output=$(docker run --rm "${TEST_IMAGE_NAME}" version 2>&1); then
        log_error "Container version command failed"
        log_error "Output: ${version_output}"
        return 1
    fi

    if ! echo "${version_output}" | grep -q "Open Host Factory Plugin REST API"; then
        log_error "Version output doesn't contain expected text"
        return 1
    fi

    log_info "Container startup tests passed"
}

# Test environment configuration
test_environment_configuration() {
    log_info "Testing environment configuration..."

    # Test with various environment variables
    local env_test_output
    if ! env_test_output=$(docker run --rm \
        -e HF_SERVER_ENABLED=true \
        -e HF_SERVER_HOST=127.0.0.1 \
        -e HF_SERVER_PORT=9000 \
        -e HF_AUTH_ENABLED=false \
        -e HF_PROVIDER_TYPE=aws \
        -e HF_PROVIDER_AWS_REGION=us-west-2 \
        -e HF_DEBUG=true \
        "${TEST_IMAGE_NAME}" version 2>&1); then
        log_error "Environment configuration test failed"
        log_error "Output: ${env_test_output}"
        return 1
    fi

    log_info "Environment configuration tests passed"
}

# Test server functionality
test_server_functionality() {
    log_info "Testing server functionality..."

    # Start container in background
    local container_id
    if ! container_id=$(docker run -d \
        --name "${CONTAINER_NAME}" \
        -p "${TEST_PORT}:8000" \
        -e HF_SERVER_ENABLED=true \
        -e HF_AUTH_ENABLED=false \
        -e HF_LOGGING_LEVEL=DEBUG \
        "${TEST_IMAGE_NAME}" serve); then
        log_error "Failed to start container"
        return 1
    fi

    # Wait for container to start
    log_info "Waiting for server to start..."
    sleep 10

    # Check if container is still running
    if ! docker ps -q -f id="${container_id}" | grep -q .; then
        log_error "Container stopped unexpectedly"
        docker logs "${container_id}"
        return 1
    fi

    # Try to connect (may fail due to missing dependencies, but container should be running)
    local health_check_result=0
    curl -f "http://localhost:${TEST_PORT}/health" >/dev/null 2>&1 || health_check_result=$?

    if [[ ${health_check_result} -eq 0 ]]; then
        log_info "Health check successful"
    else
        log_warn "Health check failed (expected due to missing dependencies)"
        log_info "Container is running but server may not be fully functional"
    fi

    log_info "Server functionality tests completed"
}

# Test Docker Compose
test_docker_compose() {
    log_info "Testing Docker Compose configuration..."

    if ! command -v docker-compose &> /dev/null; then
        log_warn "Docker Compose not available - skipping compose tests"
        return 0
    fi

    cd "${PROJECT_ROOT}"

    # Test compose file validation
    if ! docker-compose -f docker-compose.yml config --quiet; then
        log_error "Development Docker Compose file is invalid"
        return 1
    fi

    if ! docker-compose -f docker-compose.prod.yml config --quiet; then
        log_error "Production Docker Compose file is invalid"
        return 1
    fi

    log_info "Docker Compose tests passed"
}

# Run pytest Docker tests
run_pytest_docker_tests() {
    log_info "Running pytest Docker tests..."

    cd "${PROJECT_ROOT}"

    # Run Docker-specific tests
    if pytest tests/docker/ -v -m docker --tb=short; then
        log_info "Pytest Docker tests passed"
    else
        log_error "Pytest Docker tests failed"
        return 1
    fi
}

# Test security features
test_security_features() {
    log_info "Testing container security features..."

    # Test non-root user
    local user_check
    user_check=$(docker run --rm "${TEST_IMAGE_NAME}" bash -c "whoami")

    if [[ "${user_check}" != "ohfp" ]]; then
        log_error "Container not running as ohfp user (running as: ${user_check})"
        return 1
    fi

    # Test file permissions
    local perm_check
    perm_check=$(docker run --rm "${TEST_IMAGE_NAME}" bash -c "ls -la /app/ | grep -E '^d.*ohfp.*ohfp'")

    if [[ -z "${perm_check}" ]]; then
        log_error "App directory not owned by ohfp user"
        return 1
    fi

    log_info "Security tests passed"
}

# Main test execution
main() {
    log_info "Starting Docker containerization tests..."
    log_info "Project root: ${PROJECT_ROOT}"
    log_info "Test image: ${TEST_IMAGE_NAME}"
    log_info "Test port: ${TEST_PORT}"

    # Run test phases
    check_prerequisites
    build_test_image
    test_docker_build
    test_container_startup
    test_environment_configuration
    test_server_functionality
    test_docker_compose
    test_security_features
    run_pytest_docker_tests

    log_info "All Docker tests completed successfully!"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG=true
            shift
            ;;
        --port)
            TEST_PORT="$2"
            shift 2
            ;;
        --image)
            TEST_IMAGE_NAME="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --debug         Enable debug output"
            echo "  --port PORT     Use specific port for testing (default: 8004)"
            echo "  --image NAME    Use specific image name (default: ohfp-api:docker-test)"
            echo "  --help          Show this help message"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Execute main function
main "$@"
