#!/usr/bin/env python3
"""Container health check script."""

import sys
import time
import urllib.error
import urllib.request


def check_health(
    url: str = "http://localhost:8000/health", timeout: int = 30, interval: int = 2
) -> bool:
    """Check container health endpoint with timeout and retry logic."""
    print("Testing container health endpoint...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    print("Container health check passed!")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            pass

        time.sleep(interval)

    print("Health check failed")
    return False


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Test container health endpoint")
    parser.add_argument("--url", default="http://localhost:8000/health", help="Health check URL")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--interval", type=int, default=2, help="Retry interval in seconds")

    args = parser.parse_args()

    success = check_health(args.url, args.timeout, args.interval)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
