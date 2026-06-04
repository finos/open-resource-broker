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

Hostfactory replay module
"""

import json
import logging
import pathlib
import sqlite3
import time

from open_resource_broker.impl import hfreplay

logger = logging.getLogger(__name__)

_CATEGORY_COMMAND_MAPPING = {
    "template": "get-available-templates",
    "request-status": "get-request-status",
    "return-request": "get-return-requests",
    "request": "request-machines",
    "return": "request-return-machines",
}


def _read_events(dbfile: pathlib.Path | str) -> list[tuple]:
    """Read events from the database file"""
    conn = sqlite3.connect(dbfile)
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT * FROM events
        WHERE category IN ({",".join(["?"] * len(_CATEGORY_COMMAND_MAPPING))})
        AND type = "input"
        ORDER BY timestamp ASC
        """,  # noqa: S608
        list(_CATEGORY_COMMAND_MAPPING.keys()),
    )
    rows = cursor.fetchall()

    conn.close()

    return rows


def replay(dbfile, wait) -> None:
    """Replay open-resource-broker events"""
    events = _read_events(dbfile)
    if not events:
        logger.info(
            "No matching events found for category: %s", _CATEGORY_COMMAND_MAPPING
        )
        return
    logger.info("Found %d events", len(events))
    prev_timestamp = None

    for event in events:
        _, _, category, event_id, timestamp, event_type, event_value = event
        logger.info(
            "Replaying event: category=%s, id=%s, type=%s, value=%s",
            category,
            event_id,
            event_type,
            event_value,
        )

        if wait and prev_timestamp is not None:
            delay = timestamp - prev_timestamp
            if delay > 0:
                logger.info("Waiting for %d seconds before next event", delay)
                time.sleep(delay)
        prev_timestamp = timestamp

        command = _CATEGORY_COMMAND_MAPPING.get(category)
        if not command:
            logger.warning(
                "Unknown category '%s', skipping event id=%s", category, event_id
            )
            continue

        try:
            hfreplay.replay_event(command, event_id, json.loads(event_value))
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to decode JSON for event id=%s category=%s: %s",
                event_id,
                category,
                e,
            )
            continue
