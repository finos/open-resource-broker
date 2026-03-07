"""AWS Launch Template Management Module."""

from .manager import AWSLaunchTemplateManager, LaunchTemplateResult

__all__: list[str] = ["AWSLaunchTemplateManager", "LaunchTemplateResult"]
