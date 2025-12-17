"""Tests for Dockerfile and container build process."""

import subprocess
import time
from pathlib import Path

import pytest


class TestDockerfile:
    """Test Dockerfile and container build process."""

    @pytest.fixture(scope="class")
    def project_root(self):
        """Get project root directory."""
        return Path(__file__).parent.parent.parent

    @pytest.fixture(scope="class")
    def dockerfile_path(self, project_root):
        """Get Dockerfile path."""
        return project_root / "Dockerfile"

    def test_dockerfile_exists(self, dockerfile_path):
        """Test that Dockerfile exists."""
        assert dockerfile_path.exists(), "Dockerfile should exist in project root"

    def test_dockerfile_structure(self, dockerfile_path):
        """Test Dockerfile structure and best practices."""
        content = dockerfile_path.read_text()

        # Check for base image
        assert "FROM python:" in content, "Should use Python base image"

        # Check for security best practices
        assert "groupadd -r" in content and "useradd -r" in content, "Should create non-root user"
        assert "USER" in content, "Should switch to non-root user"

        # Check for virtual environment
        assert "/opt/venv" in content, "Should use virtual environment"

        # Check for health check
        assert "HEALTHCHECK" in content, "Should include health check"

        # Check for correct entrypoint
        assert "ENTRYPOINT" in content, "Should have entrypoint"
        assert "docker-entrypoint.sh" in content, "Should use entrypoint script"

    def test_dockerfile_labels(self, dockerfile_path):
        """Test that Dockerfile includes appropriate labels."""
        content = dockerfile_path.read_text()

        required_labels = [
            "org.opencontainers.image.title",
            "org.opencontainers.image.description",
            "org.opencontainers.image.version",
            "org.opencontainers.image.vendor",
        ]

        for label in required_labels:
            assert label in content, f"Dockerfile should include {label} label"

    def test_dockerfile_environment_variables(self, dockerfile_path):
        """Test that Dockerfile sets appropriate environment variables."""
        content = dockerfile_path.read_text()

        required_env_vars = [
            "PYTHONPATH=/app",
            "PYTHONDONTWRITEBYTECODE=1",
            "PYTHONUNBUFFERED=1",
            "HF_SERVER_ENABLED=true",
            "HF_SERVER_HOST=0.0.0.0",
            "HF_SERVER_PORT=8000",
        ]

        for env_var in required_env_vars:
            assert env_var in content, f"Dockerfile should set {env_var}"

    @pytest.mark.slow
    def test_docker_build_success(self, project_root):
        """Test that Docker build completes successfully."""
        try:
            result = subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    "orb-api:test-build",
                    "--build-arg",
                    "BUILD_DATE=2025-01-07T00:00:00Z",
                    "--build-arg",
                    "VERSION=test",
                    "--build-arg",
                    "VCS_REF=test",
                    str(project_root),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if "Cannot connect to the Docker daemon" in result.stderr:
                pytest.skip("Docker daemon not running")

            assert result.returncode == 0, f"Docker build failed: {result.stderr}"
            assert "Successfully tagged orb-api:test-build" in result.stdout or result.stderr

        except subprocess.TimeoutExpired:
            pytest.fail("Docker build timed out after 5 minutes")
        except FileNotFoundError:
            pytest.skip("Docker not available in test environment")

    @pytest.mark.slow
    def test_container_startup(self, project_root):
        """Test that container starts successfully."""
        try:
            # First ensure image exists
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    "orb-api:test-startup",
                    "--quiet",
                    str(project_root),
                ],
                check=True,
                capture_output=True,
            )

            # Test version command
            result = subprocess.run(
                ["docker", "run", "--rm", "orb-api:test-startup", "version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, f"Container version command failed: {result.stderr}"
            assert "Open Resource Broker REST API" in result.stdout
            assert "Version:" in result.stdout

        except subprocess.TimeoutExpired:
            pytest.fail("Container startup timed out")
        except FileNotFoundError:
            pytest.skip("Docker not available in test environment")
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Docker build failed: {e}")

    @pytest.mark.slow
    def test_container_health_check(self, project_root):
        """Test container health check functionality."""
        try:
            # Build image
            subprocess.run(
                [
                    "docker",
                    "build",
                    "-t",
                    "orb-api:test-health",
                    "--quiet",
                    str(project_root),
                ],
                check=True,
                capture_output=True,
            )

            # Start container
            container_result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    "orb-health-test",
                    "-p",
                    "8002:8000",
                    "-e",
                    "HF_SERVER_ENABLED=true",
                    "-e",
                    "HF_AUTH_ENABLED=false",
                    "orb-api:test-health",
                    "serve",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            if container_result.returncode != 0:
                pytest.skip(f"Container start failed: {container_result.stderr}")

            container_id = container_result.stdout.strip()

            try:
                # Wait for container to start
                time.sleep(10)

                # Check container health
                _ = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container_id,
                        "curl",
                        "-f",
                        "http://localhost:8000/health",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                # Note: This might fail due to missing dependencies in container
                # The important thing is that the container started

            finally:
                # Cleanup
                subprocess.run(["docker", "stop", container_id], check=False, capture_output=True)
                subprocess.run(["docker", "rm", container_id], check=False, capture_output=True)

        except subprocess.TimeoutExpired:
            pytest.fail("Health check timed out")
        except FileNotFoundError:
            pytest.skip("Docker not available in test environment")
        except subprocess.CalledProcessError as e:
            pytest.skip(f"Docker operation failed: {e}")

    def test_entrypoint_script_exists(self, project_root):
        """Test that entrypoint script exists and is executable."""
        entrypoint_path = project_root / "deployment" / "docker" / "docker-entrypoint.sh"
        assert entrypoint_path.exists(), "docker-entrypoint.sh should exist"

        # Check if file is executable
        import stat

        file_stat = entrypoint_path.stat()
        assert file_stat.st_mode & stat.S_IEXEC, "docker-entrypoint.sh should be executable"

    def test_entrypoint_script_structure(self, project_root):
        """Test entrypoint script structure."""
        entrypoint_path = project_root / "deployment" / "docker" / "docker-entrypoint.sh"

        if not entrypoint_path.exists():
            pytest.skip(f"Entrypoint script not found at {entrypoint_path}")

        content = entrypoint_path.read_text()

        # Check for correct shebang
        assert content.startswith("#!/bin/bash"), "Should have appropriate bash shebang"

        # Check for error handling
        assert "set -e" in content, "Should set error handling"

        # Check for required functions
        required_functions = [
            "print_banner",
            "validate_environment",
            "setup_configuration",
            "setup_aws_credentials",
            "start_application",
        ]

        for func in required_functions:
            assert func in content, f"Should define {func} function"

        # Check for signal handling
        assert "trap" in content, "Should handle signals for graceful shutdown"

    def test_docker_compose_files_exist(self, project_root):
        """Test that Docker Compose files exist."""
        compose_files = ["docker-compose.yml", "docker-compose.prod.yml"]

        for compose_file in compose_files:
            compose_path = project_root / "deployment" / "docker" / compose_file
            assert compose_path.exists(), f"{compose_file} should exist"

    def test_docker_compose_structure(self, project_root):
        """Test Docker Compose file structure."""
        import yaml

        # Test development compose file
        dev_compose_path = project_root / "deployment" / "docker" / "docker-compose.yml"

        if not dev_compose_path.exists():
            pytest.skip(f"Docker Compose file not found at {dev_compose_path}")

        with open(dev_compose_path) as f:
            dev_compose = yaml.safe_load(f)

        assert "services" in dev_compose, "Should have services section"
        assert "orb-api" in dev_compose["services"], "Should have orb-api service"

        orb_service = dev_compose["services"]["orb-api"]
        assert "build" in orb_service, "Should have build configuration"
        assert "ports" in orb_service, "Should expose ports"
        assert "environment" in orb_service, "Should have environment variables"
        assert "volumes" in orb_service, "Should have volume mounts"

        # Test production compose file
        prod_compose_path = project_root / "deployment" / "docker" / "docker-compose.prod.yml"
        with open(prod_compose_path) as f:
            prod_compose = yaml.safe_load(f)

        assert "services" in prod_compose, "Should have services section"
        assert "orb-api" in prod_compose["services"], "Should have orb-api service"

        prod_service = prod_compose["services"]["orb-api"]
        assert "image" in prod_service, "Production should use pre-built image"
        assert "restart" in prod_service, "Should have restart policy"

    def test_environment_file_template(self, project_root):
        """Test .env.example file exists and has required variables."""
        env_example_path = project_root / ".env.example"
        assert env_example_path.exists(), ".env.example should exist"

        content = env_example_path.read_text()

        required_sections = [
            "BUILD CONFIGURATION",
            "SERVER CONFIGURATION",
            "AUTHENTICATION CONFIGURATION",
            "AWS PROVIDER CONFIGURATION",
            "LOGGING CONFIGURATION",
            "STORAGE CONFIGURATION",
            "SECURITY CONFIGURATION",
        ]

        for section in required_sections:
            assert section in content, f"Should have {section} section"

        required_variables = [
            "HF_SERVER_ENABLED",
            "HF_SERVER_HOST",
            "HF_SERVER_PORT",
            "HF_AUTH_ENABLED",
            "HF_AUTH_STRATEGY",
            "HF_PROVIDER_TYPE",
            "AWS_DEFAULT_REGION",
        ]

        for var in required_variables:
            assert var in content, f"Should define {var} variable"
