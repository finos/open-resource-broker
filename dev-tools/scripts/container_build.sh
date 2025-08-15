#!/bin/bash
set -e

# Docker build script for Open Host Factory Plugin REST API
# Supports multi-architecture builds and tagging

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

# Configuration
IMAGE_NAME="${IMAGE_NAME:-open-hostfactory-plugin}"  # Will be overridden by Makefile with $(CONTAINER_IMAGE)
REGISTRY="${REGISTRY:-}"
VERSION="${VERSION:-$(git describe --tags --always --dirty 2>/dev/null || echo 'latest')}"
BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
VCS_REF=$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')

# Python version support (from Makefile environment variables)
PYTHON_VERSION="${PYTHON_VERSION:-$(make -s print-DEFAULT_PYTHON_VERSION 2>/dev/null || echo '3.13')}"  # Dynamic from Makefile with fallback
MULTI_PYTHON="${MULTI_PYTHON:-false}"     # Flag for multi-Python builds

# Build arguments
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PUSH="${PUSH:-false}"
CACHE="${CACHE:-true}"

# Print build information
print_build_info() {
    log_info "Docker Build Configuration"
    echo "  Image Name: ${IMAGE_NAME}"
    echo "  Registry: ${REGISTRY:-<none>}"
    echo "  Version: ${VERSION}"
    echo "  Python Version: ${PYTHON_VERSION}"
    echo "  Multi-Python Build: ${MULTI_PYTHON}"
    echo "  Build Date: ${BUILD_DATE}"
    echo "  VCS Ref: ${VCS_REF}"
    echo "  Platforms: ${PLATFORMS}"
    echo "  Push: ${PUSH}"
    echo "  Cache: ${CACHE}"
    echo ""
}

# Check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi

    # Check Docker Buildx
    if ! docker buildx version &> /dev/null; then
        log_error "Docker Buildx is not available"
        exit 1
    fi

    # Check if we're in the right directory
    if [[ ! -f "Dockerfile" ]]; then
        log_error "Dockerfile not found. Please run from project root."
        exit 1
    fi

    log_info "Prerequisites check passed"
}

# Setup buildx builder
setup_builder() {
    log_info "Setting up Docker Buildx builder..."

    # Create builder if it doesn't exist
    if ! docker buildx inspect ohfp-builder &> /dev/null; then
        log_info "Creating new buildx builder: ohfp-builder"
        docker buildx create --name ohfp-builder --driver docker-container --bootstrap
    fi

    # Use the builder
    docker buildx use ohfp-builder

    log_info "Buildx builder ready"
}

# Build the image
build_image() {
    log_info "Building Docker image..."

    # Build wheel first if it doesn't exist
    if ! ls dist/*.whl 1> /dev/null 2>&1; then
        log_info "Building wheel package..."
        make build || {
            log_error "Failed to build wheel package"
            exit 1
        }
    else
        log_info "Using existing wheel package"
    fi

    # Get values from Makefile if not provided
    local MAKEFILE_DEFAULT_PYTHON_VERSION="${PYTHON_VERSION:-$(make -s print-DEFAULT_PYTHON_VERSION 2>/dev/null || echo '3.13')}"
    local MAKEFILE_PACKAGE_SHORT="${PACKAGE_NAME_SHORT:-$(make -s print-PACKAGE_NAME_SHORT 2>/dev/null || echo 'ohfp')}"

    # Prepare tags with Python version support
    local tags=()
    local version_tag="${VERSION}"

    # Add Python version to tag if specified
    if [[ -n "${MAKEFILE_DEFAULT_PYTHON_VERSION}" && "${MULTI_PYTHON}" == "true" ]]; then
        version_tag="${VERSION}-python${MAKEFILE_DEFAULT_PYTHON_VERSION}"
    fi

    if [[ -n "${REGISTRY}" ]]; then
        tags+=("-t" "${REGISTRY}/${IMAGE_NAME}:${version_tag}")
        # Only add latest tag if not multi-Python build
        if [[ "${MULTI_PYTHON}" != "true" ]]; then
            tags+=("-t" "${REGISTRY}/${IMAGE_NAME}:latest")
        fi
    else
        tags+=("-t" "${IMAGE_NAME}:${version_tag}")
        # Only add latest tag if not multi-Python build
        if [[ "${MULTI_PYTHON}" != "true" ]]; then
            tags+=("-t" "${IMAGE_NAME}:latest")
        fi
    fi

    # Prepare build arguments
    local build_args=(
        "--build-arg" "BUILD_DATE=${BUILD_DATE}"
        "--build-arg" "VERSION=${VERSION}"
        "--build-arg" "VCS_REF=${VCS_REF}"
        "--build-arg" "PYTHON_VERSION=${PYTHON_VERSION}"
        "--build-arg" "PACKAGE_NAME_SHORT=${MAKEFILE_PACKAGE_SHORT}"
        "--build-arg" "BUILDKIT_DOCKERFILE_CHECK=skip=SecretsUsedInArgOrEnv"
    )

    # Prepare cache arguments
    local cache_args=()
    if [[ "${CACHE}" == "true" ]]; then
        cache_args+=(
            "--cache-from" "type=gha"
            "--cache-to" "type=gha,mode=max"
        )
    fi

    # Prepare platform arguments
    local platform_args=()
    if [[ "${PUSH}" == "true" ]]; then
        # Multi-platform for registry pushes
        if [[ -n "${PLATFORMS}" ]]; then
            platform_args+=("--platform" "${PLATFORMS}")
        fi
    else
        # Single platform for local builds (so we can use --load)
        local_platform=$(docker info --format '{{.Architecture}}' 2>/dev/null || echo 'amd64')
        platform_args+=("--platform" "linux/${local_platform}")
        log_info "Using single platform linux/${local_platform} for local build"
    fi

    # Prepare push arguments
    local push_args=()
    if [[ "${PUSH}" == "true" ]]; then
        push_args+=("--push")
    else
        # Always use --load for local builds (single platform)
        push_args+=("--load")
    fi

    # Build command
    local build_cmd=(
        docker buildx build
        "${tags[@]}"
        "${build_args[@]}"
        "${cache_args[@]}"
        "${platform_args[@]}"
        "${push_args[@]}"
        .
    )

    log_info "Executing: ${build_cmd[*]}"
    "${build_cmd[@]}"

    log_info "Docker image build completed"
}

# Test the built image
test_image() {
    if [[ "${PUSH}" == "true" ]]; then
        log_info "Skipping image test (image was pushed)"
        return
    fi

    log_info "Testing built image..."

    # Use the same tagging logic as build_image function
    local MAKEFILE_DEFAULT_PYTHON_VERSION="${PYTHON_VERSION:-$(make -s print-DEFAULT_PYTHON_VERSION 2>/dev/null || echo '3.13')}"
    local version_tag="${VERSION}"
    if [[ -n "${MAKEFILE_DEFAULT_PYTHON_VERSION}" && "${MULTI_PYTHON}" == "true" ]]; then
        version_tag="${VERSION}-python${MAKEFILE_DEFAULT_PYTHON_VERSION}"
    fi

    local test_image
    if [[ -n "${REGISTRY}" ]]; then
        test_image="${REGISTRY}/${IMAGE_NAME}:${version_tag}"
    else
        test_image="${IMAGE_NAME}:${version_tag}"
    fi

    # Test image can start
    log_info "Testing image startup..."
    if docker run --rm "${test_image}" version; then
        log_info "Image startup test passed"
    else
        log_error "Image startup test failed"
        exit 1
    fi

    # Test health check
    log_info "Testing health check..."
    local container_id
    container_id=$(docker run -d -p 8001:8000 "${test_image}")

    # Wait for container to start
    sleep 10

    # Check health
    if curl -f http://localhost:8001/health; then
        log_info "Health check test passed"
    else
        log_error "Health check test failed"
        docker logs "${container_id}"
        docker stop "${container_id}"
        exit 1
    fi

    # Cleanup
    docker stop "${container_id}"

    log_info "Image testing completed successfully"
}

# Print usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -h, --help          Show this help message"
    echo "  -v, --version VER   Set version tag (default: git describe)"
    echo "  -r, --registry REG  Set registry prefix"
    echo "  -p, --push          Push image to registry"
    echo "  --no-cache          Disable build cache"
    echo "  --platforms PLAT    Set target platforms (default: linux/amd64,linux/arm64)"
    echo ""
    echo "Environment Variables:"
    echo "  VERSION             Image version tag"
    echo "  REGISTRY            Registry prefix"
    echo "  PLATFORMS           Target platforms"
    echo "  PUSH                Push to registry (true/false)"
    echo "  CACHE               Use build cache (true/false)"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Build local image"
    echo "  $0 --push --registry myregistry.com  # Build and push to registry"
    echo "  $0 --version 1.2.3 --no-cache       # Build specific version without cache"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            exit 0
            ;;
        -v|--version)
            VERSION="$2"
            shift 2
            ;;
        -r|--registry)
            REGISTRY="$2"
            shift 2
            ;;
        -p|--push)
            PUSH="true"
            shift
            ;;
        --no-cache)
            CACHE="false"
            shift
            ;;
        --platforms)
            PLATFORMS="$2"
            shift 2
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    print_build_info
    check_prerequisites
    setup_builder
    build_image
    test_image

    log_info "Build process completed successfully!"

    if [[ -n "${REGISTRY}" ]]; then
        log_info "Image: ${REGISTRY}/${IMAGE_NAME}:${VERSION}"
    else
        log_info "Image: ${IMAGE_NAME}:${VERSION}"
    fi
}

# Execute main function
main "$@"
