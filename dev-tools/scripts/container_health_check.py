#!/usr/bin/env python3
"""Container health check script."""

import logging
import sys
import time
import urllib.error
import urllib.request
import random
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def check_health(
    url: str = None, 
    timeout: int = None, 
    interval: int = None
) -> bool:
    """Check container health endpoint with timeout and retry logic."""
    # Use environment variables with fallbacks
    url = url or os.getenv('HEALTH_CHECK_URL', 'http://localhost:8000/health')
    timeout = timeout or int(os.getenv('HEALTH_CHECK_TIMEOUT', '60'))
    interval = interval or int(os.getenv('HEALTH_CHECK_INTERVAL', '3'))
    
    logger.info(f"Testing container health endpoint: {url}")
    logger.info(f"Timeout: {timeout}s, Retry interval: {interval}s")

    start_time = time.time()
    attempt = 0
    
    while time.time() - start_time < timeout:
        attempt += 1
        try:
            logger.info(f"Health check attempt {attempt}...")
            with urllib.request.urlopen(url, timeout=10) as response:
                if response.status == 200:
                    logger.info("Container health check passed!")
                    return True
                else:
                    logger.warning(f"Health check returned status {response.status}")
        except urllib.error.HTTPError as e:
            logger.warning(f"HTTP error {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            logger.warning(f"URL error: {e.reason}")
        except OSError as e:
            logger.warning(f"Connection error: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error: {e}")

        if time.time() - start_time < timeout:
            logger.info(f"Waiting {interval}s before next attempt...")
            time.sleep(interval)

    logger.error(f"Health check failed after {timeout}s timeout ({attempt} attempts)")
    return False


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Test container health endpoint")
    parser.add_argument("--url", help="Health check URL (default: env HEALTH_CHECK_URL or http://localhost:8000/health)")
    parser.add_argument("--timeout", type=int, help="Timeout in seconds (default: env HEALTH_CHECK_TIMEOUT or 60)")
    parser.add_argument("--interval", type=int, help="Retry interval in seconds (default: env HEALTH_CHECK_INTERVAL or 3)")
    parser.add_argument("--port", type=int, help="Use specific port (overrides URL)")

    args = parser.parse_args()

    # Override URL with port if specified
    url = args.url
    if args.port:
        url = f"http://localhost:{args.port}/health"

    success = check_health(url, args.timeout, args.interval)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
