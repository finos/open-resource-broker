"""Startup validation for ORB application."""

import json
import os
import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import NoCredentialsError, ClientError
from pydantic import ValidationError

from cli.console import print_error, print_info, print_command
from config.schemas.app_schema import AppConfig
from _package import DOCS_URL


class StartupValidator:
    """Validates ORB startup requirements with fail-fast behavior."""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.config_data: Optional[dict] = None
        self.app_config: Optional[AppConfig] = None
    
    def validate_startup(self) -> None:
        """Validate startup requirements. Exit on critical failures."""
        try:
            self._validate_critical()
            self._validate_important()
        except SystemExit:
            raise
        except Exception as e:
            self._error(f"Unexpected validation error: {e}")
            sys.exit(1)
    
    def _validate_critical(self) -> None:
        """Critical validation - must pass to start."""
        # 1. Config file exists
        if not self._find_config_file():
            print_error("Configuration file not found")
            self._print_config_help()
            sys.exit(1)
        
        # 2. Config is valid JSON
        try:
            with open(self.config_path) as f:
                self.config_data = json.load(f)
        except json.JSONDecodeError as e:
            print_error(f"Invalid JSON in config file: {self.config_path}")
            print_error(f"  {e}")
            print_info("")
            print_info("To fix:")
            print_info(f"  1. Check JSON syntax in: {self.config_path}")
            print_command("  2. Or reinitialize: orb init --force")
            sys.exit(1)
        except Exception as e:
            print_error(f"Cannot read config file: {self.config_path}")
            print_error(f"  {e}")
            print_info("")
            print_info("To fix:")
            print_info(f"  1. Check file permissions")
            print_command("  2. Or reinitialize: orb init --force")
            sys.exit(1)
        
        # 3. Config validates against Pydantic schema
        try:
            self.app_config = AppConfig(**self.config_data)
        except ValidationError as e:
            print_error(f"Invalid configuration in: {self.config_path}")
            for error in e.errors():
                field = " -> ".join(str(x) for x in error["loc"])
                print_error(f"  {field}: {error['msg']}")
            print_info("")
            print_info("To fix:")
            print_info(f"  1. Edit config file: {self.config_path}")
            print_command("  2. Or reinitialize: orb init --force")
            sys.exit(1)
    
    def _validate_important(self) -> None:
        """Important validation - warn but continue."""
        # 1. Templates file exists
        if not self._check_templates_file():
            print_info("Templates file not found")
            print_command("  Run: orb templates generate")
        
        # 2. AWS credentials configured
        if not self._check_aws_credentials():
            print_warning("AWS credentials not configured")
            print_command("  Configure with: aws configure")
            print_info("  Or set environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY")
    
    def _find_config_file(self) -> bool:
        """Find config file using discovery hierarchy."""
        if self.config_path and Path(self.config_path).exists():
            return True
        
        # Discovery hierarchy
        candidates = [
            os.environ.get("ORB_CONFIG_FILE"),
            os.path.join(os.environ.get("ORB_CONFIG_DIR", ""), "config.json"),
            os.path.join(os.environ.get("HF_PROVIDER_CONFDIR", ""), "config.json"),
            "./config/config.json",
        ]
        
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                self.config_path = candidate
                return True
        
        return False
    
    def _check_templates_file(self) -> bool:
        """Check if templates file exists."""
        if not self.app_config:
            return False
        
        # Get config directory
        config_dir = Path(self.config_path).parent if self.config_path else Path("./config")
        
        # Try provider-specific and generic names
        candidates = [
            config_dir / "awsprov_templates.json",
            config_dir / "templates.json",
            config_dir / "templates" / "awsprov_templates.json",
            config_dir / "templates" / "templates.json",
        ]
        
        return any(candidate.exists() for candidate in candidates)
    
    def _check_aws_credentials(self) -> bool:
        """Check if AWS credentials are configured."""
        if not self.app_config:
            return False
        
        try:
            # Get AWS config from first provider
            providers = self.app_config.provider.providers
            if not providers:
                return True  # No AWS providers configured
            
            aws_provider = next((p for p in providers if p.type == "aws"), None)
            if not aws_provider:
                return True  # No AWS providers
            
            # Try to get AWS credentials
            profile = aws_provider.config.get("profile", "default")
            region = aws_provider.config.get("region", "us-east-1")
            
            session = boto3.Session(profile_name=profile, region_name=region)
            session.client('sts').get_caller_identity()
            return True
            
        except (NoCredentialsError, ClientError):
            return False
        except Exception:
            return True  # Don't fail on unexpected errors
    
    def _print_config_help(self) -> None:
        """Print helpful config location information."""
        print_info("")
        print_info("Configuration not found in:")
        print_info(f"  - {Path.cwd() / 'config' / 'config.json'}")
        if env_dir := os.environ.get("HF_PROVIDER_CONFDIR"):
            print_info(f"  - {env_dir}/config.json")
        print_info("")
        print_info("To initialize:")
        print_command("  orb init")
        print_info("")
        print_info("Or specify config:")
        print_command("  orb --config /path/to/config.json templates list")
        print_info("")
        print_info(f"Documentation: {DOCS_URL}")
    
    def _error(self, message: str) -> None:
        """Print error message to stderr."""
        print_error(message)
    
    def _warn(self, message: str) -> None:
        """Print warning message to stderr."""
        print_warning(message)