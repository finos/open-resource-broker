"""Tests for Docker Compose configurations."""

import subprocess
from pathlib import Path

import pytest
import yaml


class TestDockerCompose:
    """Test Docker Compose configurations."""

    @pytest.fixture(scope="class")
    def project_root(self):
        """Get project root directory."""
        return Path(__file__).parent.parent.parent

    def test_docker_compose_dev_file_valid(self, project_root):
        """Test development Docker Compose file is valid YAML."""
        compose_path = project_root / "docker-compose.yml"
        assert compose_path.exists(), "docker-compose.yml should exist"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        assert isinstance(compose_config, dict), "Should be valid YAML"
        assert "services" in compose_config, "Should have services section"

    def test_docker_compose_prod_file_valid(self, project_root):
        """Test production Docker Compose file is valid YAML."""
        compose_path = project_root / "docker-compose.prod.yml"
        assert compose_path.exists(), "docker-compose.prod.yml should exist"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        assert isinstance(compose_config, dict), "Should be valid YAML"
        assert "services" in compose_config, "Should have services section"

    def test_docker_compose_dev_service_configuration(self, project_root):
        """Test development Docker Compose service configuration."""
        compose_path = project_root / "docker-compose.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        # Check main service
        assert "ohfp-api" in compose_config["services"], "Should have ohfp-api service"

        ohfp_service = compose_config["services"]["ohfp-api"]

        # Check build configuration
        assert "build" in ohfp_service, "Should have build configuration"
        build_config = ohfp_service["build"]
        assert build_config["context"] == ".", "Should build from project root"
        assert build_config["dockerfile"] == "Dockerfile", "Should use Dockerfile"

        # Check port mapping
        assert "ports" in ohfp_service, "Should expose ports"

        # Check environment variables
        assert "environment" in ohfp_service, "Should have environment variables"
        env_vars = ohfp_service["environment"]

        required_env_vars = [
            "HF_SERVER_ENABLED",
            "HF_SERVER_HOST",
            "HF_SERVER_PORT",
            "HF_AUTH_ENABLED",
        ]

        for env_var in required_env_vars:
            assert env_var in env_vars, f"Should have {env_var} environment variable"

        # Check volumes
        assert "volumes" in ohfp_service, "Should have volume mounts"
        volumes = ohfp_service["volumes"]

        expected_volumes = [
            "./config:/app/config:ro",  # Configuration
            "ohfp-data:/app/data",  # Data persistence
            "ohfp-logs:/app/logs",  # Logs
        ]

        for volume in expected_volumes:
            assert volume in volumes, f"Should have {volume} volume mount"

        # Check health check
        assert "healthcheck" in ohfp_service, "Should have health check"
        healthcheck = ohfp_service["healthcheck"]
        assert "test" in healthcheck, "Should have health check test"
        assert "curl" in healthcheck["test"][1], "Should use curl for health check"

    def test_docker_compose_prod_service_configuration(self, project_root):
        """Test production Docker Compose service configuration."""
        compose_path = project_root / "docker-compose.prod.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        # Check main service
        assert "ohfp-api" in compose_config["services"], "Should have ohfp-api service"

        ohfp_service = compose_config["services"]["ohfp-api"]

        # Production should use pre-built image
        assert "image" in ohfp_service, "Should use pre-built image"
        assert "build" not in ohfp_service, "Should not build in production"

        # Check restart policy
        assert "restart" in ohfp_service, "Should have restart policy"
        assert ohfp_service["restart"] == "always", "Should always restart"

        # Check resource limits
        assert "deploy" in ohfp_service, "Should have deployment configuration"
        deploy_config = ohfp_service["deploy"]
        assert "resources" in deploy_config, "Should have resource limits"

        resources = deploy_config["resources"]
        assert "limits" in resources, "Should have resource limits"
        assert "reservations" in resources, "Should have resource reservations"

        # Check security options
        assert "security_opt" in ohfp_service, "Should have security options"
        security_opts = ohfp_service["security_opt"]
        assert (
            "no-new-privileges:true" in security_opts
        ), "Should prevent privilege escalation"

    def test_docker_compose_volumes_configuration(self, project_root):
        """Test Docker Compose volumes configuration."""
        compose_path = project_root / "docker-compose.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        # Check volumes section
        assert "volumes" in compose_config, "Should have volumes section"
        volumes = compose_config["volumes"]

        required_volumes = ["ohfp-data", "ohfp-logs"]

        for volume in required_volumes:
            assert volume in volumes, f"Should define {volume} volume"
            assert (
                volumes[volume]["driver"] == "local"
            ), f"{volume} should use local driver"

    def test_docker_compose_networks_configuration(self, project_root):
        """Test Docker Compose networks configuration."""
        compose_path = project_root / "docker-compose.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        # Check networks section
        assert "networks" in compose_config, "Should have networks section"
        networks = compose_config["networks"]

        assert "ohfp-network" in networks, "Should define ohfp-network"
        assert (
            networks["ohfp-network"]["driver"] == "bridge"
        ), "Should use bridge driver"

    def test_docker_compose_optional_services(self, project_root):
        """Test Docker Compose optional services configuration."""
        compose_path = project_root / "docker-compose.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        services = compose_config["services"]

        # Check optional services
        optional_services = ["redis", "postgres", "nginx"]

        for service in optional_services:
            if service in services:
                service_config = services[service]

                # Should have profiles for optional services
                assert "profiles" in service_config, f"{service} should have profiles"

                # Should be connected to the network
                assert "networks" in service_config, f"{service} should be on network"
                assert (
                    "ohfp-network" in service_config["networks"]
                ), f"{service} should be on ohfp-network"

    @pytest.mark.slow
    def test_docker_compose_config_validation(self, project_root):
        """Test Docker Compose configuration validation."""
        try:
            # Test development compose file
            result = subprocess.run(
                [
                    "docker-compose",
                    "-f",
                    str(project_root / "docker-compose.yml"),
                    "config",
                    "--quiet",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert (
                result.returncode == 0
            ), f"Development compose config invalid: {result.stderr}"

            # Test production compose file
            result = subprocess.run(
                [
                    "docker-compose",
                    "-f",
                    str(project_root / "docker-compose.prod.yml"),
                    "config",
                    "--quiet",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert (
                result.returncode == 0
            ), f"Production compose config invalid: {result.stderr}"

        except FileNotFoundError:
            pytest.skip("docker-compose not available")
        except subprocess.TimeoutExpired:
            pytest.fail("Docker Compose config validation timed out")

    @pytest.mark.slow
    def test_docker_compose_service_dependencies(self, project_root):
        """Test Docker Compose service dependencies."""
        compose_path = project_root / "docker-compose.yml"

        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        services = compose_config["services"]

        # Check if nginx service has proper dependencies
        if "nginx" in services:
            nginx_service = services["nginx"]
            assert "depends_on" in nginx_service, "Nginx should depend on ohfp-api"
            assert (
                "ohfp-api" in nginx_service["depends_on"]
            ), "Nginx should depend on ohfp-api"

    def test_environment_file_compatibility(self, project_root):
        """Test that .env.example is compatible with Docker Compose."""
        env_example_path = project_root / ".env.example"
        compose_path = project_root / "docker-compose.yml"

        # Read environment variables from .env.example
        env_vars = set()
        with open(env_example_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    var_name = line.split("=")[0]
                    env_vars.add(var_name)

        # Read Docker Compose environment variables
        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        ohfp_service = compose_config["services"]["ohfp-api"]
        compose_env_vars = set(ohfp_service["environment"].keys())

        # Check that important environment variables are covered
        important_vars = {
            "HF_SERVER_ENABLED",
            "HF_SERVER_HOST",
            "HF_SERVER_PORT",
            "HF_AUTH_ENABLED",
            "HF_AUTH_STRATEGY",
            "HF_PROVIDER_TYPE",
        }

        for var in important_vars:
            assert var in env_vars, f"{var} should be in .env.example"
            assert var in compose_env_vars, f"{var} should be in docker-compose.yml"
