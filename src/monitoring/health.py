"""Health check monitoring for the application."""

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from botocore.exceptions import ClientError

from config.config_manager import ConfigurationManager
from infrastructure.logging.logger import get_logger
from providers.aws.infrastructure.aws_client import AWSClient

logger = get_logger(__name__)


@dataclass
class HealthStatus:
    """Health check status."""

    name: str
    status: str  # 'healthy', 'degraded', 'unhealthy'
    details: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert health status to dictionary."""
        return {
            "name": self.name,
            "status": self.status,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "dependencies": self.dependencies,
        }


class HealthCheck:
    """Health check implementation."""

    def __init__(
        self, config: ConfigurationManager, aws_client: Optional[AWSClient] = None
    ) -> None:
        """Initialize health check."""
        self.config = config.get_config()
        self.aws_client = aws_client
        self.checks: Dict[str, Callable[[], HealthStatus]] = {}
        self.status_history: Dict[str, List[HealthStatus]] = {}
        self._lock = threading.Lock()

        # Create health check directory
        self.health_dir = Path(self.config.get("HEALTH_DIR", "./health"))
        self.health_dir.mkdir(parents=True, exist_ok=True)

        # Register default health checks
        self._register_default_checks()

        # Start background health checker if enabled
        if self.config.get("HEALTH_CHECK_ENABLED", True):
            self._start_health_checker()

    def _register_default_checks(self) -> None:
        """Register default health checks."""
        # System health checks
        self.register_check("system", self._check_system_health)
        self.register_check("disk", self._check_disk_health)

        # AWS health checks
        if self.aws_client:
            self.register_check("aws", self._check_aws_health)
            self.register_check("ec2", self._check_ec2_health)

        # Database health checks
        self.register_check("database", self._check_database_health)

        # Application health checks
        self.register_check("application", self._check_application_health)

    def register_check(self, name: str, check_func: Callable[[], HealthStatus]) -> None:
        """Register a new health check."""
        with self._lock:
            self.checks[name] = check_func
            self.status_history[name] = []

    def run_check(self, name: str) -> HealthStatus:
        """Run a specific health check."""
        if name not in self.checks:
            raise ValueError(f"Unknown health check: {name}")

        try:
            status = self.checks[name]()
            with self._lock:
                self.status_history[name].append(status)
                # Keep only last 100 statuses
                if len(self.status_history[name]) > 100:
                    self.status_history[name].pop(0)
            return status
        except Exception as e:
            logger.error("Health check %s failed: %s", name, e)
            return HealthStatus(
                name=name,
                status="unhealthy",
                details={"error": str(e)},
                dependencies=[],
            )

    def run_all_checks(self) -> Dict[str, HealthStatus]:
        """Run all registered health checks."""
        results = {}
        for name in self.checks:
            results[name] = self.run_check(name)
        return results

    def get_status(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Get health check status."""
        with self._lock:
            if name:
                if name not in self.status_history:
                    raise ValueError(f"Unknown health check: {name}")
                history = self.status_history[name]
                return {
                    "current": history[-1].to_dict() if history else None,
                    "history": [s.to_dict() for s in history],
                }
            else:
                return {
                    name: history[-1].to_dict() if history else None
                    for name, history in self.status_history.items()
                }

    def _start_health_checker(self) -> None:
        """Start background health checker thread."""

        def check_health() -> None:
            """Run health checks periodically in background thread."""
            while True:
                try:
                    results = self.run_all_checks()

                    # Write results to file
                    health_file = self.health_dir / "health.json"
                    with health_file.open("w") as f:
                        json.dump(
                            {name: status.to_dict() for name, status in results.items()},
                            f,
                            indent=2,
                        )

                    # Check for alerts
                    self._check_alerts(results)

                    interval = self.config.get("HEALTH_CHECK_INTERVAL", 60)
                    time.sleep(interval)

                except Exception as e:
                    logger.error("Health checker error: %s", e)
                    time.sleep(5)  # Shorter sleep on error

        thread = threading.Thread(target=check_health, daemon=True)
        thread.start()

    def _check_alerts(self, results: Dict[str, HealthStatus]) -> None:
        """Check health status for alerts."""
        alerts = []
        for name, status in results.items():
            if status.status == "unhealthy":
                alerts.append(
                    {
                        "check": name,
                        "status": status.status,
                        "details": status.details,
                        "timestamp": status.timestamp.isoformat(),
                    }
                )

        if alerts:
            alert_file = self.health_dir / "alerts.json"
            with alert_file.open("w") as f:
                json.dump(alerts, f, indent=2)

            logger.error("Health check alerts", extra={"alerts": alerts})

    def _check_system_health(self) -> HealthStatus:
        """Check system health."""
        import psutil

        try:
            cpu_percent = psutil.cpu_percent()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            status = "healthy"
            if cpu_percent > 90 or memory.percent > 90 or disk.percent > 90:
                status = "degraded"
            if cpu_percent > 95 or memory.percent > 95 or disk.percent > 95:
                status = "unhealthy"

            return HealthStatus(
                name="system",
                status=status,
                details={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "disk_percent": disk.percent,
                },
                dependencies=["os"],
            )
        except Exception as e:
            return HealthStatus(
                name="system",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["os"],
            )

    def _check_disk_health(self) -> HealthStatus:
        """Check disk health."""
        try:
            # Check write access
            test_file = self.health_dir / "test.txt"
            test_file.write_text("test")
            test_file.unlink()

            # Check disk space
            import shutil

            total, used, free = shutil.disk_usage("/")
            percent_used = (used / total) * 100

            status = "healthy"
            if percent_used > 90:
                status = "degraded"
            if percent_used > 95:
                status = "unhealthy"

            return HealthStatus(
                name="disk",
                status=status,
                details={
                    "total_gb": total // (2**30),
                    "used_gb": used // (2**30),
                    "free_gb": free // (2**30),
                    "percent_used": percent_used,
                },
                dependencies=["os"],
            )
        except Exception as e:
            return HealthStatus(
                name="disk",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["os"],
            )

    def _check_aws_health(self) -> HealthStatus:
        """Check AWS health."""
        if not self.aws_client:
            return HealthStatus(
                name="aws",
                status="unknown",
                details={"error": "AWS client not configured"},
                dependencies=["aws"],
            )

        try:
            # Check AWS credentials
            response = self.aws_client.sts_client.get_caller_identity()

            return HealthStatus(
                name="aws",
                status="healthy",
                details={
                    "account_id": response["Account"],
                    "user_id": response["UserId"],
                    "arn": response["Arn"],
                },
                dependencies=["aws"],
            )
        except Exception as e:
            return HealthStatus(
                name="aws",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["aws"],
            )

    def _check_ec2_health(self) -> HealthStatus:
        """Check EC2 service health."""
        if not self.aws_client:
            return HealthStatus(
                name="ec2",
                status="unknown",
                details={"error": "AWS client not configured"},
                dependencies=["aws", "ec2"],
            )

        try:
            # Check EC2 service
            response = self.aws_client.ec2_client.describe_instances(MaxResults=5)

            instance_count = sum(len(r["Instances"]) for r in response.get("Reservations", []))

            return HealthStatus(
                name="ec2",
                status="healthy",
                details={"instance_count": instance_count, "api_status": "available"},
                dependencies=["aws", "ec2"],
            )
        except ClientError as e:
            return HealthStatus(
                name="ec2",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["aws", "ec2"],
            )

    def _check_database_health(self) -> HealthStatus:
        """Check database health."""
        repo_config = self.config.get("REPOSITORY_CONFIG", {})
        repo_type = repo_config.get("type")

        if repo_type == "json":
            return self._check_json_db_health()
        elif repo_type == "sqlite":
            return self._check_sqlite_db_health()
        elif repo_type == "dynamodb":
            return self._check_dynamodb_health()
        else:
            return HealthStatus(
                name="database",
                status="unknown",
                details={"error": f"Unknown repository type: {repo_type}"},
                dependencies=["database"],
            )

    def _check_json_db_health(self) -> HealthStatus:
        """Check JSON database health."""
        try:
            repo_config = self.config["REPOSITORY_CONFIG"]["json"]
            base_path = Path(repo_config["base_path"])

            if not base_path.exists():
                return HealthStatus(
                    name="database",
                    status="unhealthy",
                    details={"error": "Database directory does not exist"},
                    dependencies=["database", "json"],
                )

            # Check write access
            test_file = base_path / "test.json"
            test_file.write_text("{}")
            test_file.unlink()

            return HealthStatus(
                name="database",
                status="healthy",
                details={"type": "json", "path": str(base_path), "write_access": True},
                dependencies=["database", "json"],
            )
        except Exception as e:
            return HealthStatus(
                name="database",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["database", "json"],
            )

    def _check_sqlite_db_health(self) -> HealthStatus:
        """Check SQLite database health."""
        try:
            import sqlite3

            repo_config = self.config["REPOSITORY_CONFIG"]["sqlite"]
            db_path = Path(repo_config["database_path"])

            if not db_path.parent.exists():
                db_path.parent.mkdir(parents=True)

            # Verify database connectivity
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            conn.close()

            return HealthStatus(
                name="database",
                status="healthy",
                details={
                    "type": "sqlite",
                    "path": str(db_path),
                    "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
                },
                dependencies=["database", "sqlite"],
            )
        except Exception as e:
            return HealthStatus(
                name="database",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["database", "sqlite"],
            )

    def _check_dynamodb_health(self) -> HealthStatus:
        """Check DynamoDB health."""
        if not self.aws_client:
            return HealthStatus(
                name="database",
                status="unknown",
                details={"error": "AWS client not configured"},
                dependencies=["database", "dynamodb"],
            )

        try:
            repo_config = self.config["REPOSITORY_CONFIG"]["dynamodb"]
            table_prefix = repo_config["table_prefix"]

            # List tables
            tables = self.aws_client.session.client("dynamodb").list_tables()
            project_tables = [t for t in tables["TableNames"] if t.startswith(table_prefix)]

            return HealthStatus(
                name="database",
                status="healthy",
                details={
                    "type": "dynamodb",
                    "table_count": len(project_tables),
                    "tables": project_tables,
                },
                dependencies=["database", "dynamodb"],
            )
        except Exception as e:
            return HealthStatus(
                name="database",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["database", "dynamodb"],
            )

    def _check_application_health(self) -> HealthStatus:
        """Check overall application health."""
        try:
            # Run all other checks
            results = {name: self.run_check(name) for name in self.checks if name != "application"}

            # Count status types
            status_counts = {"healthy": 0, "degraded": 0, "unhealthy": 0, "unknown": 0}

            for result in results.values():
                status_counts[result.status] += 1

            # Determine overall status
            if status_counts["unhealthy"] > 0:
                status = "unhealthy"
            elif status_counts["degraded"] > 0:
                status = "degraded"
            else:
                status = "healthy"

            return HealthStatus(
                name="application",
                status=status,
                details={
                    "status_counts": status_counts,
                    "checks": {name: result.status for name, result in results.items()},
                },
                dependencies=["system", "aws", "database"],
            )
        except Exception as e:
            return HealthStatus(
                name="application",
                status="unhealthy",
                details={"error": str(e)},
                dependencies=["system", "aws", "database"],
            )
