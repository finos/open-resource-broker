"""AWS-specific authentication strategies."""

from .cognito_strategy import CognitoAuthStrategy
from .iam_strategy import IAMAuthStrategy

__all__: list[str] = ["IAMAuthStrategy", "CognitoAuthStrategy"]
