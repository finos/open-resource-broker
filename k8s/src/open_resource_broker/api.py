"""Morgan Stanley makes this available to you under the Apache License,
Version 2.0 (the "License"). You may obtain a copy of the License at
http://www.apache.org/licenses/LICENSE-2.0. See the NOTICE file
distributed with this work for additional information regarding
copyright ownership. Unless required by applicable law or agreed
to in writing, software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
See the License for the specific language governing permissions and
limitations under the License. Watch and manage open-resource-broker machine
requests and pods in a Kubernetes cluster.

Low level open-resource-broker API.
"""

import json
import logging
import pathlib
import tempfile

from open_resource_broker import events as hfevents
from open_resource_broker import fsutils
from open_resource_broker import validator as hfvalidator

logger = logging.getLogger(__name__)


def _load_pod_file(workdir: pathlib.Path, pod_name: str) -> dict | None:
    """Loads the pod file, will return None in case of an error or
    if the pod spec is not available in Kube.
    """
    pod_path = workdir / "pods" / pod_name
    try:
        logger.debug("Loading pod file: %s", pod_path)
        with pod_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # This could happen because the pod is being created or has been deleted
        logger.info("Pod file not found: %s in %s", pod_path, workdir)
    except json.JSONDecodeError as e:
        logger.info(
            "Failed to decode JSON from pod file %s: %s located in %s",
            pod_path,
            e,
            workdir,
        )
    return None


def _is_return_request(workdir: pathlib.Path, request_id: str) -> bool:
    """Check if the request is a return request."""
    machines_dir = pathlib.Path(workdir) / "requests" / pathlib.Path(request_id)
    is_return = not machines_dir.exists()
    logger.debug("Request ID: %s, is return request: %s", request_id, is_return)
    return is_return


def _get_machines_dir(workdir: pathlib.Path, request_id: str) -> pathlib.Path:
    """Get the machines directory based on the request id.
    Will fail if the directory does not exist
    """
    hf_reqs_dir = _get_request_dir(workdir, request_id)
    logger.info("Checking the machine dir %s", hf_reqs_dir)
    if not hf_reqs_dir.exists():
        raise FileNotFoundError(
            f"Request directory for {request_id} "
            f"not found: {hf_reqs_dir} "
            f"in workdir: {workdir}"
        )

    return hf_reqs_dir


# Should we transform this into a raise_error option


def _get_request_dir(workdir: pathlib.Path, request_id: str) -> pathlib.Path:
    """Get the request directory based on the request id.
    The path could not exist as it might be being created
    """
    if _is_return_request(workdir, request_id):
        hf_reqs_dir = workdir / pathlib.Path("return-requests")
    else:
        hf_reqs_dir = workdir / pathlib.Path("requests")

    return hf_reqs_dir / request_id


def _resolve_request_result(pod_status: str) -> str:
    machine_results_map = {
        "creating": "executing",
        "pending": "executing",
        "running": "succeed",
        "succeeded": "succeed",
        "failed": "fail",
        "unknown": "fail",
        "deleted": "fail",
    }
    return machine_results_map.get(pod_status, "fail")


def _resolve_return_result(machine_status: str) -> str:
    return "succeed" if machine_status == "terminated" else "executing"


def _resolve_machine_status(pod_status: str, is_return_req: bool) -> tuple[str, str]:
    """Resolve the machine status based on the pod status.
    machine_status : Status of machine.
    Expected values: running, terminated
    machine_result: Status of hf request related to this machine.
    Possible values:  executing, fail, succeed.
    """
    if not pod_status:
        return "terminated", "fail"

    machine_status_map = {
        "creating": "running",
        "pending": "running",
        "running": "running",
        "succeeded": "terminated",
        "failed": "terminated",
        "unknown": "terminated",
        "deleted": "terminated",
    }

    machine_status = machine_status_map.get(pod_status, "terminated")

    # Resolve the request result
    if is_return_req:
        machine_result = _resolve_return_result(machine_status)
    else:
        machine_result = _resolve_request_result(pod_status)

    return machine_status, machine_result


def _mktempdir(workdir: pathlib.Path) -> pathlib.Path:
    """Create a directory in the workdir."""
    workdir.mkdir(parents=True, exist_ok=True)
    temp_dir = tempfile.mkdtemp(dir=workdir, prefix=".")
    return pathlib.Path(temp_dir)


def _get_templates(templates: pathlib.Path) -> dict:
    """Read and validate the templates file"""
    with templates.open("r") as file:
        data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(
                "The templates file: %s must contain a JSON object", file.name
            )

    hfvalidator.validate(data)
    return data


def _write_podspec(
    tmp_path: pathlib.Path, templates: pathlib.Path, template_id: str
) -> None:
    """Write the podspec file as part of the request."""
    templates_data = _get_templates(templates)["templates"]
    for t in templates_data:
        if t["templateId"] == template_id:
            template = t
            break
    else:
        raise ValueError("Template Id: %s not found in templates file.", template_id)

    podspec_path = tmp_path / ".podspec"
    podspec_path.write_text(template["podSpec"])


def _find_podspec_path(templates: pathlib.Path, template_id: str) -> pathlib.Path:
    """Find the podspec path as part of the request."""
    templates_data = _get_templates(templates)["templates"]
    for t in templates_data:
        if t["templateId"] == template_id:
            # TODO: handle paths relative to the template?
            return pathlib.Path(t["podSpec"])

    raise ValueError("Template Id: %s not found in templates file.", template_id)


def get_available_templates(templates) -> dict:
    """Validates and returns the open-resource-broker templates file."""
    logger.info("Getting available templates: %s", templates)

    return _get_templates(pathlib.Path(templates))


def request_machines(workdir, templates, template_id, count, request_id) -> dict:
    """Request machines based on the provided open-resource-broker input JSON file.

    Generate unique open-resource-broker request id, create a directory for the request.

    For each machine requested, create a symlink in the request directory. The
    symlink is to non-existent "pending" file.

    """
    logger.info("HF Request ID: %s - Requesting machines: %s", request_id, count)

    with hfevents.EventsBuffer() as events:
        events.post(
            category="request",
            id=request_id,
            count=count,
            template_id=template_id,
        )

        # The request id is generated, directory should not exist.
        # If it does, we will raise an exception
        workdir_path = pathlib.Path(workdir)
        templates_path = pathlib.Path(templates)
        dst_path = workdir_path / "requests" / request_id
        tmp_path = _mktempdir(workdir_path)
        podspec_path = _find_podspec_path(templates_path, template_id)

        for machine_id in range(count):
            machine_name = f"{request_id}-{machine_id}"
            pod_status_path = workdir_path / "pods-status" / machine_name
            fsutils.atomic_symlink("creating", pod_status_path)
            fsutils.atomic_symlink(podspec_path, tmp_path / machine_name)

            events.post(
                category="pod",
                id=machine_name,
                status="requested",
                request_id=request_id,
                template_id=template_id,
            )

    tmp_path.rename(dst_path)

    return {
        "message": "Success",
        "requestId": request_id,
    }


def get_request_status(workdir, hf_req_ids) -> dict:
    """Get the status of open-resource-broker requests.

    For each request, first check if the request is a return request. If it is,
    look for machines in the return request directory. Otherwise, look for
    machines in the request directory.

    Machines are updated by the watcher. If machine is associated with the pod
    the symlink points to the pod info. Otherwise, the symlink points to
    non-existing "pending" file.

    For each request, request status is complete if all machines are in ready
    state. Otherwise, the request status is running. If any machine is in failed
    state, the status will be set to "complete_with_error".
    """
    workdir_path = pathlib.Path(workdir)

    response: dict = {"requests": []}

    logger.info("Getting request status: %s", hf_req_ids)
    with hfevents.EventsBuffer() as events:
        for request_id in hf_req_ids:
            running = False
            failed = False

            machines = []
            ret_request = _is_return_request(workdir, request_id)
            logger.debug(
                "Request ID: %s, is return request: %s", request_id, ret_request
            )
            machines_dir = _get_machines_dir(workdir_path, request_id)
            item_count = 0

            for file_path in fsutils.iterate_directory(directory=machines_dir):
                item_count += 1

                podname = file_path.name
                pod_status = fsutils.fetch_pod_status(workdir_path, podname)

                machine_status, machine_result = _resolve_machine_status(
                    pod_status, ret_request
                )
                logger.info(
                    "Pod status: %s, machine status: %s", pod_status, machine_status
                )

                if machine_result == "executing":
                    running = True
                    # If at least a machine is still running
                    # we want to keep the request going
                    failed = False
                    # Machine can be omitted if request is still executing.
                    if not ret_request:
                        continue

                if machine_result == "fail" and not running:
                    failed = True

                # Will not return anything if the pod is deleted or creating
                pod = _load_pod_file(workdir_path, podname)
                if pod:
                    machine = {
                        "machineId": pod["metadata"]["uid"],
                        "name": pod["metadata"]["name"],
                        "result": machine_result,
                        "status": machine_status,
                        "privateIpAddress": pod.get("status", {}).get("pod_ip", ""),
                        "publicIpAddress": "",
                        "launchtime": pod["metadata"]["creation_timestamp"],
                        "message": "Allocated by K8s open-resource-broker",
                    }
                else:
                    machine = {
                        "machineId": "",
                        "name": podname,
                        "result": machine_result,
                        "status": machine_status,
                        "privateIpAddress": "",
                        "publicIpAddress": "",
                        "launchtime": "",
                        "message": "Allocated by K8s open-resource-broker",
                    }
                machines.append(machine)

            logger.info(
                "Request ID: %s, found %d machines in %s",
                request_id,
                item_count,
                machines_dir,
            )

            status = "running" if running else "complete"
            status = "complete_with_error" if failed and not running else status
            # This will only trigger when
            # all the machines are running or failed
            if failed:
                logger.info(
                    "We have detected a failure, "
                    "we are not marking it as complete with error "
                    "to avoid Symphony deleting all the pods"
                )

            req_status = {
                "requestId": request_id,
                "message": "",
                "status": status,
                "machines": machines,
            }

            response["requests"].append(req_status)

            event_type = "return" if ret_request else "request"
            events.post(
                category=event_type,
                id=request_id,
                status=status,
            )

    return response


def request_return_machines(workdir, machines, request_id) -> dict:
    """Request to return machines based on the provided input JSON."""
    workdir_path = pathlib.Path(workdir)
    with hfevents.EventsBuffer() as events:
        events.post(
            category="return",
            id=request_id,
            count=len(machines),
        )
        logger.info("Requesting to return machines: %s %s", request_id, machines)

        tmp_path = _mktempdir(workdir_path)
        dst_path = _get_request_dir(workdir, request_id)
        for machine in machines:
            machine_name = machine["name"]
            fsutils.atomic_create_empty(tmp_path / f"{machine_name}")
            events.post(
                category="pod",
                id=machine_name,
                status="returned",
                return_id=request_id,
            )
    logger.info("Moving return request %s to %s", tmp_path, dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.rename(dst_path)

    return {
        "message": "Machines returned.",
        "requestId": request_id,
    }


def get_return_requests(workdir, machines) -> dict:
    """Get the status of CSP claimed hosts."""
    known = {machine["name"] for machine in machines}
    pods_dir = pathlib.Path(workdir) / "pods"
    workdir_path = pathlib.Path(workdir)
    actual = set()
    for file_path in fsutils.iterate_directory(directory=pods_dir):
        if fsutils.fetch_pod_status(workdir_path, file_path.name) == "deleted":
            continue

        actual.add(file_path.name)

    extra = known - actual

    response = {
        "status": "complete",
        "message": "Machines to be terminated.",
        "requests": [{"gracePeriod": 0, "machine": machine} for machine in extra],
    }

    logger.info("Machines to terminate: %r", extra)

    return response
