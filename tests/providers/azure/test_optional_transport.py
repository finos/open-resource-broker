"""Azure discovery does not require the optional HTTP transport."""

import subprocess
import sys


def test_registration_without_httpx():
    code = "; ".join(
        [
            "import sys",
            "sys.modules['httpx'] = None",
            "from orb.providers.azure.provider_plugin import AzurePlugin",
            "AzurePlugin.register_plugin()",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
