#!/usr/bin/env python3
"""Container health check script."""

import logging
import sys
import time
import urllib.error
import urllib.request
import random

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def find_available_port(start_port: int = 8001) -> int:
    """Find an available port starting from start_port."""
    import socket
    
    for port in range(start_port, start_port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except OSError:
            continue
    
    # Fallback to random port
    return start_port + random.randint(0, 1000)


def check_health(
    url: str = "http://localhost:8000/health", 
    timeout: int = 60, 
    interval: int = 3
) -> bool:
    """Check container health endpoint with timeout and retry logic."""
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
    parser.add_argument("--url", default="http://localhost:8000/health", help="Health check URL")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
    parser.add_argument("--interval", type=int, default=3, help="Retry interval in seconds")
    parser.add_argument("--port", type=int, help="Use specific port (overrides URL)")

    args = parser.parse_args()

    # Override URL with port if specified
    if args.port:
        args.url = f"http://localhost:{args.port}/health"

    success = check_health(args.url, args.timeout, args.interval)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
