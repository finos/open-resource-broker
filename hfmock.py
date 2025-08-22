#!/usr/bin/env python
"""Mock HostFactory server for testing and development."""

"""Mock HostFactory server for testing and development."""

import argparse
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Configure logging
log = logging.getLogger("hfmock")
log.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Console handler
console_handler = logging.StreamHandler()
# console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

# File handler with rotation
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler("hfmock.log", maxBytes=10 * 1024 * 1024, backupCount=5)  # 10MB
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

log.addHandler(console_handler)
log.addHandler(file_handler)

# Global variables for cleanup
temp_files = []
running = True


def cleanup_temp_files():
    """Clean up temporary files."""
    for file_path in temp_files:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                log.debug(f"Removed temporary file: {file_path}")
        except Exception as e:
            log.error(f"Error removing temporary file {file_path}: {e}")


def signal_handler(signum, frame):
    """Handle termination signals."""
    global running
    log.info(f"Received signal {signum}. Cleaning up...")
    running = False
    # cleanup_temp_files()
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def write_request_json_to_a_tmp_file(data: Dict[str, Any]) -> str:
    """
    Writes JSON data to a temporary file with random hex name.

    Args:
        data: Data to be written as JSON

    Returns:
        str: Path to the created temporary file
    """
    random_hex = hex(random.getrandbits(32))[2:]
    request_file = os.path.join("/tmp/", f"input_{random_hex}.json")
    log.debug(f"Creating temporary file: {request_file}")

    try:
        with open(request_file, "w") as file:
            json.dump(data, file, indent=4)
        temp_files.append(request_file)  # Add to cleanup list
        return request_file
    except Exception as e:
        log.error(f"Error writing temporary file: {e}")
        raise


def run_bash_script(script_path: str, argument: str, timeout: int = 300) -> Dict[str, Any]:
    """
    Run a bash script with timeout and error handling.

    Args:
        script_path: Path to the script to run
        argument: Argument to pass to the script
        timeout: Timeout in seconds

    Returns:
        Dict containing stdout, stderr, and return code
    """
    try:
        result = subprocess.run(
            ["/bin/bash", script_path, "-f", argument],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"stdout": result.stdout, "stderr": result.stderr, "return_code": result.returncode}
    except subprocess.TimeoutExpired:
        log.error(f"Script execution timed out after {timeout} seconds")
        return {"stdout": "", "stderr": f"Timeout after {timeout} seconds", "return_code": -1}
    except subprocess.CalledProcessError as e:
        log.error(f"Script execution failed: {e}")
        return {"stdout": e.stdout, "stderr": e.stderr, "return_code": e.returncode}
    except Exception as e:
        log.error(f"Unexpected error running script: {e}")
        return {"stdout": "", "stderr": str(e), "return_code": -1}


class HostFactoryMock:
    """Mock implementation of Host Factory functionality."""

    def __init__(self):
        """Initialize mock host factory with target configuration."""
        # TARGET="IBM_SYMPHONY"
        TARGET = "AWS_PLUGIN"

        if TARGET == "IBM_SYMPHONY":
            os.environ["HF_PROVIDER_CONFDIR"] = (
                "/opt/ibm/spectrumcomputing/hostfactory/conf/providers/awsinst"
            )
            os.environ["HF_LOGDIR"] = "/home/egoadmin/logs"
            os.environ["HF_WORKDIR"] = "/home/egoadmin/workdir"
            os.environ["HF_LOG_LEVEL"] = "DEBUG"
            os.environ["LOG_DESTINATION"] = "file"
            os.environ["PROVIDER_NAME"] = "awsinst"

            hf_scripts_location = Path(
                "/opt/ibm/spectrumcomputing/hostfactory/1.2/providerplugins/aws/scripts/"
            )
        elif TARGET == "AWS_PLUGIN":
            os.environ["AWS_PROVIDER_LOG_DIR"] = "./logs"
            os.environ["LOG_DESTINATION"] = "file"

            hf_scripts_location = Path("./scripts/")

        self.get_available_templates_script = os.path.join(
            hf_scripts_location, "getAvailableTemplates.sh"
        )
        self.request_machines_script = os.path.join(hf_scripts_location, "requestMachines.sh")
        self.get_request_status_script = os.path.join(hf_scripts_location, "getRequestStatus.sh")
        self.request_return_machines_script = os.path.join(
            hf_scripts_location, "requestReturnMachines.sh"
        )
        self.get_return_requests_script = os.path.join(hf_scripts_location, "getReturnRequests.sh")

    def get_available_templates(self) -> Dict[str, Any]:
        """Get available templates."""
        request = {}
        log.debug(f"input_request: {json.dumps(request, indent=4)}")

        request_file_name = write_request_json_to_a_tmp_file(request)

        res = run_bash_script(self.get_available_templates_script, request_file_name)
        log.debug(f"response: {res}")

        if res["return_code"] != 0:
            log.error(f"Error getting templates: {res['stderr']}")
            return {"error": "Failed to get templates", "message": res["stderr"]}

        try:
            # Extract the JSON part from stdout
            stdout = res["stdout"]
            # Find the JSON part (starts with '{' and ends with '}')
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Fix common JSON formatting issues
                json_str = json_str.replace(']\n  "', '],\n  "')

                result = json.loads(json_str)
                log.info(f"response: {json.dumps(result, indent=4)}")
                return result
            else:
                log.error("Could not find JSON in response")
                return {
                    "error": "Invalid response format",
                    "message": "Could not find JSON in response",
                }
        except json.JSONDecodeError as e:
            log.error(f"Error parsing template response: {e}")
            # Try to fix the JSON manually
            stdout = res["stdout"]
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Add missing comma after templates array
                json_str = json_str.replace(']\n  "', '],\n  "')
                try:
                    result = json.loads(json_str)
                    log.info(f"Fixed JSON response: {json.dumps(result, indent=4)}")
                    return result
                except json.JSONDecodeError:
                    pass

            return {"error": "Invalid response format", "message": str(e)}

    def request_machines(self, template_name: str, machine_count: int) -> Dict[str, Any]:
        """Request machines using specified template."""
        request = {"template": {"templateId": template_name, "machineCount": machine_count}}
        log.debug(f"input_request: {json.dumps(request, indent=4)}")

        request_file_name = write_request_json_to_a_tmp_file(request)

        res = run_bash_script(self.request_machines_script, request_file_name)
        log.debug(f"response: {res}")

        if res["return_code"] != 0:
            log.error(f"Error requesting machines: {res['stderr']}")
            return {"error": "Failed to request machines", "message": res["stderr"]}

        try:
            # Extract the JSON part from stdout
            stdout = res["stdout"]
            # Find the JSON part (starts with '{' and ends with '}')
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Fix common JSON formatting issues
                json_str = json_str.replace(']\n  "', '],\n  "')

                result = json.loads(json_str)
                log.info(f"response: {json.dumps(result, indent=4)}")
                return result
            else:
                log.error("Could not find JSON in response")
                return {
                    "error": "Invalid response format",
                    "message": "Could not find JSON in response",
                }
        except json.JSONDecodeError as e:
            log.error(f"Error parsing request response: {e}")
            # Try to fix the JSON manually
            stdout = res["stdout"]
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Add missing comma after templates array
                json_str = json_str.replace(']\n  "', '],\n  "')
                try:
                    result = json.loads(json_str)
                    log.info(f"Fixed JSON response: {json.dumps(result, indent=4)}")
                    return result
                except json.JSONDecodeError:
                    pass

            return {"error": "Invalid response format", "message": str(e)}

    def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """Get status of a request."""
        request = {"requests": [{"requestId": f"{request_id}"}]}
        log.debug(f"input_request: {json.dumps(request, indent=4)}")

        request_file_name = write_request_json_to_a_tmp_file(request)

        res = run_bash_script(self.get_request_status_script, request_file_name)
        log.debug(f"response: {res}")

        if res["return_code"] != 0:
            log.error(f"Error getting request status: {res['stderr']}")
            return {"error": "Failed to get request status", "message": res["stderr"]}

        try:
            # Extract the JSON part from stdout
            stdout = res["stdout"]
            # Find the JSON part (starts with '{' and ends with '}')
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Fix common JSON formatting issues
                json_str = json_str.replace(']\n  "', '],\n  "')

                result = json.loads(json_str)
                log.info(f"response: {json.dumps(result, indent=4)}")
                return result
            else:
                log.error("Could not find JSON in response")
                return {
                    "error": "Invalid response format",
                    "message": "Could not find JSON in response",
                }
        except json.JSONDecodeError as e:
            log.error(f"Error parsing status response: {e}")
            # Try to fix the JSON manually
            stdout = res["stdout"]
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Add missing comma after templates array
                json_str = json_str.replace(']\n  "', '],\n  "')
                try:
                    result = json.loads(json_str)
                    log.info(f"Fixed JSON response: {json.dumps(result, indent=4)}")
                    return result
                except json.JSONDecodeError:
                    pass

            return {"error": "Invalid response format", "message": str(e)}

    def request_return_machines(self, machine_names: List[str]) -> Dict[str, Any]:
        """Request machines to be returned."""
        mn_list = [{"name": machine_name} for machine_name in machine_names]
        request = {"machines": mn_list}
        log.debug(f"input_request: {json.dumps(request, indent=4)}")

        request_file_name = write_request_json_to_a_tmp_file(request)

        res = run_bash_script(self.request_return_machines_script, request_file_name)
        log.debug(f"response: {res}")

        if res["return_code"] != 0:
            log.error(f"Error returning machines: {res['stderr']}")
            return {"error": "Failed to return machines", "message": res["stderr"]}

        try:
            # Extract the JSON part from stdout
            stdout = res["stdout"]
            # Find the JSON part (starts with '{' and ends with '}')
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Fix common JSON formatting issues
                json_str = json_str.replace(']\n  "', '],\n  "')

                result = json.loads(json_str)
                log.info(f"response: {json.dumps(result, indent=4)}")
                return result
            else:
                log.error("Could not find JSON in response")
                return {
                    "error": "Invalid response format",
                    "message": "Could not find JSON in response",
                }
        except json.JSONDecodeError as e:
            log.error(f"Error parsing return response: {e}")
            # Try to fix the JSON manually
            stdout = res["stdout"]
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Add missing comma after templates array
                json_str = json_str.replace(']\n  "', '],\n  "')
                try:
                    result = json.loads(json_str)
                    log.info(f"Fixed JSON response: {json.dumps(result, indent=4)}")
                    return result
                except json.JSONDecodeError:
                    pass

            return {"error": "Invalid response format", "message": str(e)}

    def get_return_requests(self, machine_names: List[str]) -> Dict[str, Any]:
        """Get return requests for specified machines."""
        mn_list = [{"name": machine_name} for machine_name in machine_names]
        request = {"machines": mn_list}
        log.debug(f"input_request: {json.dumps(request, indent=4)}")

        request_file_name = write_request_json_to_a_tmp_file(request)

        res = run_bash_script(self.get_return_requests_script, request_file_name)
        log.debug(f"response: {res}")

        if res["return_code"] != 0:
            log.error(f"Error getting return requests: {res['stderr']}")
            return {"error": "Failed to get return requests", "message": res["stderr"]}

        try:
            # Extract the JSON part from stdout
            stdout = res["stdout"]
            # Find the JSON part (starts with '{' and ends with '}')
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Fix common JSON formatting issues
                json_str = json_str.replace(']\n  "', '],\n  "')

                result = json.loads(json_str)
                log.info(f"response: {json.dumps(result, indent=4)}")
                return result
            else:
                log.error("Could not find JSON in response")
                return {
                    "error": "Invalid response format",
                    "message": "Could not find JSON in response",
                }
        except json.JSONDecodeError as e:
            log.error(f"Error parsing return requests response: {e}")
            # Try to fix the JSON manually
            stdout = res["stdout"]
            json_start = stdout.find("{")
            json_end = stdout.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = stdout[json_start:json_end]
                # Add missing comma after templates array
                json_str = json_str.replace(']\n  "', '],\n  "')
                try:
                    result = json.loads(json_str)
                    log.info(f"Fixed JSON response: {json.dumps(result, indent=4)}")
                    return result
                except json.JSONDecodeError:
                    pass

            return {"error": "Invalid response format", "message": str(e)}


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Mocking HF requests")

    parser.add_argument(
        "--getAvailableTemplates", action="store_true", help="Invokes getAvailableTemplates"
    )

    parser.add_argument(
        "--requestMachines",
        nargs=2,
        help="Invokes requestMachines with 2 parameters",
        type=str,
        metavar=("Template Name", "Number of Machines to Request"),
    )

    parser.add_argument("--getRequestStatus", type=str, help="Invokes getRequestStatus")

    parser.add_argument(
        "--requestReturnMachines",
        nargs="+",
        type=str,
        help="Invokes requestReturnMachines specify a list of machine names as input",
    )

    parser.add_argument(
        "--getReturnRequests",
        nargs="+",
        type=str,
        help="Invokes getReturnRequests specify a list of machine names as input",
    )

    return parser.parse_args()


if __name__ == "__main__":
    try:
        random.seed(int(time.time()))
        FLAGS = parse_arguments()
        hfm = HostFactoryMock()

        if FLAGS.getAvailableTemplates:
            res = hfm.get_available_templates()
            log.info(json.dumps(res, indent=4))

        if FLAGS.requestMachines:
            template_name = FLAGS.requestMachines[0]
            try:
                machine_count = int(FLAGS.requestMachines[1])
            except ValueError:
                log.error("Machine count must be an integer")
                sys.exit(1)

            res = hfm.request_machines(template_name, machine_count)
            log.info(json.dumps(res, indent=4))

        if FLAGS.getRequestStatus:
            request_id = FLAGS.getRequestStatus
            # Single status check without polling
            res = hfm.get_request_status(request_id)
            log.info(json.dumps(res, indent=4))

        if FLAGS.requestReturnMachines:
            res = hfm.request_return_machines(FLAGS.requestReturnMachines)
            log.info(json.dumps(res, indent=4))

        if FLAGS.getReturnRequests:
            res = hfm.get_return_requests(FLAGS.getReturnRequests)
            log.info(json.dumps(res, indent=4))

    except Exception as e:
        log.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        pass
