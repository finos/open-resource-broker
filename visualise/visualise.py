#!/usr/bin/env python3
"""
AWS Resource History Analyzer

A Python-based post-analysis tool designed to process history files from AWS Auto Scaling Groups (ASG),
EC2 Fleet, and Spot Fleet operations. The tool extracts timing and capacity metrics to generate
standardized CSV reports for performance analysis.
"""

import argparse
import json
import logging
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import boto3
import pandas as pd
from botocore.exceptions import ClientError
from dateutil import parser as date_parser
from PIL import Image, ImageDraw, ImageFont


class HistoryFileProcessor:
    """Base class for processing AWS resource history files"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def load_file(self, file_path: str) -> dict:
        """Load and validate JSON history file"""
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            self.logger.info(f"Successfully loaded file: {file_path}")
            return data
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in file {file_path}: {e}")
            raise
        except FileNotFoundError:
            self.logger.error(f"File not found: {file_path}")
            raise
        except Exception as e:
            self.logger.error(f"Error loading file {file_path}: {e}")
            raise

    def extract_provider_type(self, data: dict) -> str:
        """Determine AWS service type from file content"""
        provider_api = data.get("provider_api", "").upper()

        if provider_api in ["ASG", "AUTOSCALING"]:
            return "ASG"
        elif provider_api in ["EC2FLEET", "EC2_FLEET"]:
            return "EC2Fleet"
        elif provider_api in ["SPOTFLEET", "SPOT_FLEET"]:
            return "SpotFleet"
        elif data.get("SpotFleetRequestId"):
            return "SpotFleet"
        else:
            # Try to infer from structure
            if "history" in data and isinstance(data["history"], list):
                if data["history"] and "ActivityId" in data["history"][0]:
                    return "ASG"
            elif "events" in data and isinstance(data["events"], list):
                return "EC2Fleet"  # Default for events structure
            elif "HistoryRecords" in data and isinstance(data["HistoryRecords"], list):
                # Spot/EC2 fleet describe history style
                # Check for SpotFleetRequestId inside
                for rec in data["HistoryRecords"]:
                    if rec.get("SpotFleetRequestId"):
                        return "SpotFleet"
                return "EC2Fleet"

            raise ValueError(
                f"Unable to determine provider type from data. Found provider_api: {provider_api}"
            )

    def validate_schema(self, data: dict) -> bool:
        """Validate input file structure"""
        if "resource_id" not in data:
            # Allow downstream inference; warn here
            self.logger.warning("Missing resource_id; will attempt to infer")
        if "provider_api" not in data:
            self.logger.warning("Missing provider_api; will attempt to infer")

        provider_type = self.extract_provider_type(data).upper()

        if provider_type == "ASG":
            if "history" not in data or not isinstance(data["history"], list):
                self.logger.error("ASG files must have 'history' array")
                return False
        elif provider_type in ["EC2FLEET", "SPOTFLEET"]:
            # Accept either 'events' (new format) or 'history' (describe_*_history response)
            has_events = "events" in data and isinstance(data["events"], list)
            has_history = "history" in data and isinstance(data["history"], list)
            if not (has_events or has_history):
                self.logger.error(f"{provider_type} files must have 'events' or 'history' array")
                return False

        return True


class ASGHistoryParser:
    """Parser for Auto Scaling Group history files"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def parse_activities(self, history_data: list, request_time: Optional[datetime] = None) -> list:
        """Extract instance creation activities from ASG history."""
        activities = []

        for activity in history_data:
            try:
                # Only process successful instance launches
                if activity.get(
                    "StatusCode"
                ) == "Successful" and "Launching a new EC2 instance:" in activity.get(
                    "Description", ""
                ):
                    instance_id = self.extract_instance_id(activity["Description"])
                    request_time_local = request_time or self.extract_request_time(
                        activity.get("Cause", "")
                    )
                    creation_time = self.normalize_timestamp(
                        activity["StartTime"]
                    )  # TODO: Should we use StartTime or EndTime

                    if instance_id and request_time_local and creation_time:
                        activities.append(
                            {
                                "instance_id": instance_id,
                                "creation_time": creation_time,
                                "request_time": request_time_local,
                                "activity_id": activity.get("ActivityId"),
                                "status": activity.get("StatusCode"),
                                "capacity": 1,  # Default capacity for ASG instances
                            }
                        )
                    else:
                        self.logger.warning(
                            f"Skipping activity {activity.get('ActivityId')}: missing required data"
                        )

            except Exception as e:
                self.logger.warning(
                    f"Error parsing activity {activity.get('ActivityId', 'unknown')}: {e}"
                )
                continue

        return activities

    def extract_request_time(self, cause_text: str) -> Optional[datetime]:
        """Parse initial request timestamp from cause description"""
        # Pattern: "At 2025-12-04T16:02:25Z a user request created..."
        self.logger.warning(f"Extracting request time from cause: {cause_text}")
        pattern = r"At (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"
        match = re.search(pattern, cause_text)

        if match:
            try:
                return self.normalize_timestamp(match.group(1))
            except Exception as e:
                self.logger.warning(f"Error parsing request time from cause: {e}")

        return None

    def extract_instance_id(self, description: str) -> Optional[str]:
        """Extract EC2 instance ID from activity description"""
        # Pattern: "Launching a new EC2 instance: i-xxxxxxxxxxxxxxxxx"
        pattern = r"i-[a-f0-9]{17}"
        match = re.search(pattern, description)

        if match:
            return match.group(0)

        return None

    def normalize_timestamp(self, timestamp_str: str) -> datetime:
        """Convert timestamp string to timezone-aware UTC datetime"""
        try:
            dt = date_parser.parse(timestamp_str)
            # Ensure timezone-aware and convert to UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt.astimezone(datetime.now().astimezone().tzinfo).replace(tzinfo=None)
        except Exception as e:
            raise ValueError(f"Unable to parse timestamp '{timestamp_str}': {e}")


class EC2FleetHistoryParser:
    """Parser for EC2 Fleet history files"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def _extract_instance_type_from_event(self, event: dict) -> Optional[str]:
        """Parse instance type from EventDescription JSON if present."""
        try:
            info = event.get("EventInformation", {})
            desc = info.get("EventDescription")
            if desc and isinstance(desc, str):
                parsed = json.loads(desc)
                return parsed.get("instanceType")
        except Exception as exc:
            self.logger.debug(f"Could not parse instance type from event description: {exc}")
        return None

    def parse_events(self, history_data: list, request_time: datetime) -> tuple[list, list]:
        """Extract instance creation events from EC2 Fleet history. Returns (instances, special_events)."""
        events = []
        special_events = []

        for event in history_data:
            try:
                event_type = str(event.get("EventType", "")).lower()
                info = event.get("EventInformation", {}) or {}
                status = event.get("Status") or info.get("EventSubType")

                if (
                    event_type in ["instance-change", "instancechange", "instancechangeevent"]
                    and status
                ):
                    instance_id = event.get("InstanceId") or info.get("InstanceId")
                    creation_time = self.normalize_timestamp(event["Timestamp"])
                    capacity = event.get("WeightedCapacity", 1)
                    instance_type = self._extract_instance_type_from_event(event)

                    if instance_id and creation_time:
                        events.append(
                            {
                                "instance_id": instance_id,
                                "creation_time": creation_time,
                                "request_time": request_time,
                                "activity_id": f"fleet-event-{event.get('Timestamp', 'unknown')}",
                                "status": status,
                                "capacity": capacity,
                                "instance_type": instance_type,
                            }
                        )
                    else:
                        self.logger.warning("Skipping event: missing required data")
                else:
                    # Capture non-instance-change or informational events
                    try:
                        special_events.append(
                            {
                                "timestamp": self.normalize_timestamp(event["Timestamp"]),
                                "event_type": event.get("EventType"),
                                "event_subtype": info.get("EventSubType") or status,
                                "description": info.get("EventDescription"),
                            }
                        )
                    except Exception as exc:
                        self.logger.debug(f"Failed to record special event: {exc}")

            except Exception as e:
                self.logger.warning(f"Error parsing event: {e}")
                continue

        return events, special_events

    def normalize_timestamp(self, timestamp_str: str) -> datetime:
        """Convert timestamp string to timezone-aware UTC datetime"""
        try:
            dt = date_parser.parse(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt.astimezone(datetime.now().astimezone().tzinfo).replace(tzinfo=None)
        except Exception as e:
            raise ValueError(f"Unable to parse timestamp '{timestamp_str}': {e}")


class SpotFleetHistoryParser:
    """Parser for Spot Fleet history files"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def _extract_instance_type_from_event(self, event: dict) -> Optional[str]:
        """Parse instance type from EventDescription JSON if present."""
        try:
            info = event.get("EventInformation", {})
            desc = info.get("EventDescription")
            if desc and isinstance(desc, str):
                parsed = json.loads(desc)
                return parsed.get("instanceType")
        except Exception as exc:
            self.logger.debug(f"Could not parse instance type from event description: {exc}")
        return None

    def parse_events(self, history_data: list, request_time: datetime) -> tuple[list, list]:
        """Extract instance creation events from Spot Fleet history. Returns (instances, special_events)."""
        events = []
        special_events = []

        for event in history_data:
            try:
                event_type = str(event.get("EventType", "")).lower()
                info = event.get("EventInformation", {}) or {}
                status = event.get("Status") or info.get("EventSubType")

                if (
                    event_type in ["instance-change", "instancechange", "instancechangeevent"]
                    and status
                ):
                    instance_id = event.get("InstanceId") or info.get("InstanceId")
                    creation_time = self.normalize_timestamp(event["Timestamp"])
                    capacity = event.get("WeightedCapacity", 1)
                    instance_type = self._extract_instance_type_from_event(event)

                    if instance_id and creation_time:
                        events.append(
                            {
                                "instance_id": instance_id,
                                "creation_time": creation_time,
                                "request_time": request_time,
                                "activity_id": f"spot-event-{event.get('Timestamp', 'unknown')}",
                                "status": status,
                                "capacity": capacity,
                                "instance_type": instance_type,
                            }
                        )
                    else:
                        self.logger.warning("Skipping event: missing required data")
                else:
                    # Capture non-instance-change or informational events
                    try:
                        special_events.append(
                            {
                                "timestamp": self.normalize_timestamp(event["Timestamp"]),
                                "event_type": event.get("EventType"),
                                "event_subtype": info.get("EventSubType") or status,
                                "description": info.get("EventDescription"),
                            }
                        )
                    except Exception as exc:
                        self.logger.debug(f"Failed to record special event: {exc}")

            except Exception as e:
                self.logger.warning(f"Error parsing event: {e}")
                continue

        return events, special_events

    def normalize_timestamp(self, timestamp_str: str) -> datetime:
        """Convert timestamp string to timezone-aware UTC datetime"""
        try:
            dt = date_parser.parse(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt.astimezone(datetime.now().astimezone().tzinfo).replace(tzinfo=None)
        except Exception as e:
            raise ValueError(f"Unable to parse timestamp '{timestamp_str}': {e}")


class DataProcessor:
    """Core data processing and DataFrame generation"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.file_processor = HistoryFileProcessor(logger)
        self.asg_parser = ASGHistoryParser(logger)
        self.ec2_parser = EC2FleetHistoryParser(logger)
        self.spot_parser = SpotFleetHistoryParser(logger)
        self._vcpu_cache_path = Path(__file__).with_name("instance_vcpu_mapping.json")
        self._vcpu_cache = self._load_vcpu_cache()
        self._ec2_client = None

    def _load_vcpu_cache(self) -> dict[str, int]:
        """Load instance->vCPU mapping from JSON file, falling back to empty dict."""
        try:
            if self._vcpu_cache_path.exists():
                with open(self._vcpu_cache_path, encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        # Coerce values to int where possible
                        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
            else:
                self.logger.info(
                    "vCPU cache file not found at %s; will create on first lookup",
                    self._vcpu_cache_path,
                )
        except Exception as exc:
            self.logger.warning("Failed to load vCPU cache from %s: %s", self._vcpu_cache_path, exc)
        return {}

    def _save_vcpu_cache(self) -> None:
        """Persist the vCPU cache to disk."""
        try:
            with open(self._vcpu_cache_path, "w", encoding="utf-8") as fh:
                json.dump(self._vcpu_cache, fh, indent=2, sort_keys=True)
        except Exception as exc:
            self.logger.warning("Failed to write vCPU cache to %s: %s", self._vcpu_cache_path, exc)

    def _ensure_ec2_client(self):
        """Lazily create a boto3 EC2 client for DescribeInstanceTypes calls."""
        if self._ec2_client is None:
            try:
                self._ec2_client = boto3.client("ec2")
            except Exception as exc:  # pragma: no cover - environment-specific
                self.logger.warning("Failed to create EC2 client: %s", exc)
                self._ec2_client = None
        return self._ec2_client

    def _fetch_vcpu_from_aws(self, instance_type: str) -> Optional[int]:
        """Query AWS for the vCPU count of the given instance type."""
        client = self._ensure_ec2_client()
        if not client:
            return None
        try:
            resp = client.describe_instance_types(InstanceTypes=[instance_type])
            types = resp.get("InstanceTypes") or []
            if not types:
                return None
            vcpu_info = types[0].get("VCpuInfo") or {}
            vcpus = vcpu_info.get("DefaultVCpus")
            return int(vcpus) if vcpus is not None else None
        except ClientError as exc:
            self.logger.warning("describe_instance_types failed for %s: %s", instance_type, exc)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.warning("Unexpected error fetching vCPU for %s: %s", instance_type, exc)
        return None

    def process_history_file(
        self,
        file_path: str,
        provider_type: Optional[str] = None,
        machine_lookup: Optional[dict] = None,
    ) -> pd.DataFrame:
        """Main processing method to convert history file to DataFrame"""
        machine_lookup = machine_lookup or {}

        # Load and validate file
        data = self.file_processor.load_file(file_path)

        # Normalize history key
        if "history" not in data and "HistoryRecords" in data:
            data["history"] = data["HistoryRecords"]

        # Infer resource_id/provider_api if missing
        if "resource_id" not in data:
            rid = data.get("SpotFleetRequestId")
            if not rid and isinstance(data.get("history"), list):
                for rec in data["history"]:
                    rid = rec.get("SpotFleetRequestId") or rec.get("FleetId")
                    if rid:
                        break
            if not rid:
                # Fallback to filename stem
                rid = Path(file_path).stem.replace("_history", "")
            data["resource_id"] = rid

        if "provider_api" not in data:
            data["provider_api"] = self.file_processor.extract_provider_type(data)

        if not self.file_processor.validate_schema(data):
            raise ValueError(f"Invalid file schema: {file_path}")

        # Determine provider type
        if provider_type:
            detected_type = provider_type.upper()
        else:
            detected_type = self.file_processor.extract_provider_type(data).upper()

        self.logger.info(f"Processing {detected_type} history file: {file_path}")

        # Parse based on provider type
        processed_data = []
        special_events: list[dict] = []

        # Normalize events/history list once for all provider types
        events = data.get("events") or data.get("history") or data.get("HistoryRecords") or []
        request_time = self._resolve_request_time(events, data.get("request_time"))
        if request_time is None:
            raise ValueError("request_time missing and could not infer from history")

        if detected_type == "ASG":
            processed_data = self.asg_parser.parse_activities(events, request_time)
        elif detected_type == "EC2FLEET":
            processed_data, special = self.ec2_parser.parse_events(events, request_time)
            special_events.extend(special)
        elif detected_type == "SPOTFLEET":
            processed_data, special = self.spot_parser.parse_events(events, request_time)
            special_events.extend(special)
        else:
            raise ValueError(f"Unsupported provider type: {detected_type}")

        # Generate DataFrame
        df = self.generate_dataframe(
            processed_data, data["resource_id"], detected_type, machine_lookup
        )

        self.logger.info(f"Successfully processed {len(df)} instances from {file_path}")
        return df, special_events

    def _resolve_request_time(
        self, events: list, request_time_str: Optional[str]
    ) -> Optional[datetime]:
        """
        Determine request_time for fleet/spot based on history content:
        1) Prefer the 'submitted' fleetRequestChange event timestamp
        2) Fall back to explicit request_time in the file
        3) Finally fall back to earliest event timestamp
        """
        submission_time = self._find_submission_time(events)
        if submission_time:
            return submission_time

        if request_time_str:
            try:
                return self.normalize_timestamps(request_time_str)
            except Exception as exc:
                self.logger.warning("Failed to parse request_time '%s': %s", request_time_str, exc)

        return self._infer_request_time(events)

    def _find_submission_time(self, events: list) -> Optional[datetime]:
        """Locate the fleet submission event and return its timestamp."""
        candidates: list[datetime] = []
        for ev in events:
            info = ev.get("EventInformation") or {}
            if not isinstance(info, dict):
                info = {}
            subtype = info.get("EventSubType") or ev.get("EventSubType") or ev.get("Status")
            if isinstance(subtype, str) and subtype.lower() == "submitted":
                ts = ev.get("Timestamp")
                if ts:
                    try:
                        candidates.append(self.normalize_timestamps(ts))
                    except Exception as exc:
                        self.logger.debug(
                            "Skipping submitted event with invalid timestamp: %s", exc
                        )
        if candidates:
            return min(candidates)
        return None

    def _infer_request_time(self, events: list) -> Optional[datetime]:
        """Infer request_time from earliest event timestamp when no submitted event is found."""
        self.logger.warning("Request time missing; inferring from earliest event")
        timestamps = []
        for ev in events:
            ts = ev.get("Timestamp") or ev.get("StartTime") or ev.get("EndTime")
            if ts:
                try:
                    timestamps.append(self.normalize_timestamps(ts))
                except Exception:
                    continue
        if timestamps:
            return min(timestamps)
        return None

    def calculate_timing_metrics(self, creation_time: datetime, request_time: datetime) -> float:
        """Calculate time difference in seconds"""
        try:
            delta = creation_time - request_time
            return delta.total_seconds()
        except Exception as e:
            self.logger.warning(f"Error calculating timing metrics: {e}")
            return 0.0

    def normalize_timestamps(self, timestamp_str: str) -> datetime:
        """Convert various timestamp formats to timezone-aware UTC datetime objects"""
        try:
            dt = date_parser.parse(timestamp_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt.astimezone(datetime.now().astimezone().tzinfo).replace(tzinfo=None)
        except Exception as e:
            raise ValueError(f"Unable to parse timestamp '{timestamp_str}': {e}")

    def generate_dataframe(
        self, processed_data: list, resource_id: str, provider_api: str, machine_lookup: dict
    ) -> pd.DataFrame:
        """Create pandas DataFrame from processed data"""
        if not processed_data:
            self.logger.warning("No valid data found to process")
            return pd.DataFrame(
                columns=[
                    "ec2_creation_time",
                    "time_from_request",
                    "capacity_represented",
                    "instance_id",
                    "provider_api",
                    "resource_id",
                    "status",
                    "request_time",
                    "activity_id",
                    "creation_time_utc_seconds",
                    "instance_type",
                    "vcpu_count",
                    "cumulative_vcpus",
                ]
            )

        # Build DataFrame rows
        rows = []
        for item in processed_data:
            time_diff = self.calculate_timing_metrics(item["creation_time"], item["request_time"])

            # Convert creation time to UTC timestamp in seconds
            utc_seconds = item["creation_time"].timestamp()

            instance_id = item["instance_id"]

            # Extract instance type from parsed data or request DB and get vCPU count
            parsed_instance_type = item.get("instance_type")
            lookup_instance_type = machine_lookup.get(instance_id)
            instance_type = parsed_instance_type or lookup_instance_type or "unknown"

            # Log detailed debugging information when we encounter unknown instance types
            if instance_type == "unknown":
                self.logger.warning(f"Instance {instance_id} has unknown instance type:")
                self.logger.warning(f"  - From parsed data: {parsed_instance_type}")
                self.logger.warning(f"  - From request DB: {lookup_instance_type}")
                self.logger.warning("  - Using default: 1 vCPU")
            elif parsed_instance_type and not lookup_instance_type:
                self.logger.debug(
                    f"Instance {instance_id} type '{instance_type}' from parsed data (not in request DB)"
                )
            elif lookup_instance_type and not parsed_instance_type:
                self.logger.debug(
                    f"Instance {instance_id} type '{instance_type}' from request DB (not in parsed data)"
                )

            vcpu_count = self.get_vcpu_count(instance_type)

            row = {
                "ec2_creation_time": item["creation_time"],
                "time_from_request": time_diff,
                "capacity_represented": item["capacity"],
                "instance_id": item["instance_id"],
                "provider_api": provider_api,
                "resource_id": resource_id,
                "status": item["status"],
                "request_time": item["request_time"],
                "activity_id": item["activity_id"],
                "creation_time_utc_seconds": utc_seconds,
                "instance_type": instance_type,
                "vcpu_count": vcpu_count,
            }
            rows.append(row)

        df = pd.DataFrame(rows)

        # Ensure proper data types
        df["ec2_creation_time"] = pd.to_datetime(df["ec2_creation_time"])
        df["request_time"] = pd.to_datetime(df["request_time"])
        df["time_from_request"] = df["time_from_request"].astype(float)
        df["capacity_represented"] = df["capacity_represented"].astype(int)
        df["creation_time_utc_seconds"] = df["creation_time_utc_seconds"].astype(float)
        df["vcpu_count"] = df["vcpu_count"].astype(int)

        # Sort primarily by time_from_request, then by cumulative CPU for identical timestamps
        df = df.sort_values("time_from_request", kind="stable").reset_index(drop=True)
        df["cumulative_vcpus"] = df["vcpu_count"].cumsum()
        df = df.sort_values(["time_from_request", "cumulative_vcpus"], kind="stable").reset_index(
            drop=True
        )
        # Recompute cumulative after final ordering to align with plotted order
        df["cumulative_vcpus"] = df["vcpu_count"].cumsum()
        pd.set_option("display.max_rows", None)
        pd.set_option("display.max_columns", None)  # optional
        print(df["time_from_request"], df["cumulative_vcpus"])

        return df

    def extract_instance_type(self, item: dict) -> str:
        """Return instance type from parsed data, or 'unknown' if absent."""
        return item.get("instance_type") or "unknown"

    def get_vcpu_count(self, instance_type: str) -> int:
        """Get vCPU count for given instance type with on-disk cache and AWS lookup."""
        if not instance_type:
            return 1

        cached = self._vcpu_cache.get(instance_type)
        if cached is not None:
            return int(cached)

        if instance_type == "unknown":
            return 1

        # Fallback to AWS DescribeInstanceTypes
        vcpus = self._fetch_vcpu_from_aws(instance_type)
        if vcpus is not None:
            self._vcpu_cache[instance_type] = vcpus
            self._save_vcpu_cache()
            return vcpus

        # Default when lookup fails
        self.logger.warning(
            "Could not determine vCPU count for instance type '%s'; defaulting to 1",
            instance_type,
        )
        self._vcpu_cache[instance_type] = 1
        self._save_vcpu_cache()
        return 1


class OutputManager:
    """Handle CSV/JSON/Parquet file generation and data export"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def save_to_csv(self, dataframe: pd.DataFrame, output_path: str) -> None:
        """Save DataFrame to CSV file with proper formatting"""
        try:
            # Format timestamps for CSV output
            df_copy = dataframe.copy()
            if not df_copy.empty:
                # Convert datetime columns to string format
                if "ec2_creation_time" in df_copy.columns:
                    df_copy["ec2_creation_time"] = df_copy["ec2_creation_time"].apply(
                        lambda x: x.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if pd.notnull(x) else ""
                    )
                if "request_time" in df_copy.columns:
                    df_copy["request_time"] = df_copy["request_time"].apply(
                        lambda x: x.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if pd.notnull(x) else ""
                    )

            df_copy.to_csv(output_path, index=False, encoding="utf-8")
            self.logger.info(f"Successfully saved CSV to: {output_path}")

        except Exception as e:
            self.logger.error(f"Error saving CSV file: {e}")
            raise

    def save_to_json(self, dataframe: pd.DataFrame, output_path: str) -> None:
        """Save DataFrame to JSON Lines file"""
        try:
            # Format timestamps for JSON output
            df_copy = dataframe.copy()
            if not df_copy.empty:
                # Convert datetime columns to string format
                if "ec2_creation_time" in df_copy.columns:
                    df_copy["ec2_creation_time"] = df_copy["ec2_creation_time"].apply(
                        lambda x: x.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if pd.notnull(x) else ""
                    )
                if "request_time" in df_copy.columns:
                    df_copy["request_time"] = df_copy["request_time"].apply(
                        lambda x: x.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if pd.notnull(x) else ""
                    )

            with open(output_path, "w", encoding="utf-8") as f:
                for _, row in df_copy.iterrows():
                    json.dump(row.to_dict(), f)
                    f.write("\n")

            self.logger.info(f"Successfully saved JSON Lines to: {output_path}")

        except Exception as e:
            self.logger.error(f"Error saving JSON file: {e}")
            raise

    def save_to_parquet(self, dataframe: pd.DataFrame, output_path: str) -> None:
        """Save DataFrame to Parquet file"""
        try:
            dataframe.to_parquet(output_path, compression="snappy", index=False)
            self.logger.info(f"Successfully saved Parquet to: {output_path}")

        except Exception as e:
            self.logger.error(f"Error saving Parquet file: {e}")
            raise

    def save_to_excel(
        self, dataframe: pd.DataFrame, output_path: str, special_events: list[dict] | None = None
    ) -> None:
        """Save DataFrame to Excel file with formatting and charts"""
        try:
            # Create Excel writer with openpyxl engine for formatting and charts
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                # Write the main data
                dataframe.to_excel(writer, sheet_name="AWS_Resource_History", index=False)
                special_events = special_events or []

                # Get the workbook and worksheet
                workbook = writer.book
                worksheet = writer.sheets["AWS_Resource_History"]

                # Auto-adjust column widths
                for column in worksheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            max_length = max(max_length, len(str(cell.value)))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)  # Cap at 50 characters
                    worksheet.column_dimensions[column_letter].width = adjusted_width

                # Add PNG plot rendered in Python (Pillow)
                if not dataframe.empty and len(dataframe) > 1:
                    from openpyxl.drawing.image import Image as XLImage

                    def render_plot_to_png(df: pd.DataFrame, special_events: list[dict]) -> str:
                        """Render scatter plot with per-instance-type markers to PNG."""
                        width, height = 2600, 1560
                        margin_left, margin_right, margin_top, margin_bottom = 110, 180, 70, 120
                        plot_w = width - margin_left - margin_right
                        plot_h = height - margin_top - margin_bottom

                        x_max = float(df["time_from_request"].max() or 1.0)
                        # Include special events in x-range
                        if special_events:
                            # derive base time from request_time min
                            base_time = df["request_time"].min()
                            event_offsets = []
                            for ev in special_events:
                                ts = ev.get("timestamp")
                                if ts and base_time:
                                    event_offsets.append((ts - base_time).total_seconds())
                            if event_offsets:
                                x_max = max(x_max, max(event_offsets))

                        y_max = float(df["cumulative_vcpus"].max() or 1.0)
                        if x_max <= 0:
                            x_max = 1.0
                        if y_max <= 0:
                            y_max = 1.0

                        img = Image.new("RGB", (width, height), "white")
                        draw = ImageDraw.Draw(img)
                        font = ImageFont.load_default()

                        # Axes
                        x_axis_y = height - margin_bottom
                        y_axis_x = margin_left
                        draw.line((y_axis_x, margin_top, y_axis_x, x_axis_y), fill="black", width=2)
                        draw.line(
                            (y_axis_x, x_axis_y, width - margin_right, x_axis_y),
                            fill="black",
                            width=2,
                        )

                        # Axis titles
                        draw.text(
                            (y_axis_x + plot_w / 2 - 140, height - margin_bottom + 50),
                            "Time from Request (seconds)",
                            fill="black",
                            font=font,
                        )
                        draw.text(
                            (20, margin_top + plot_h / 2 - 20),
                            "Cumulative vCPUs",
                            fill="black",
                            font=font,
                        )

                        # Ticks and labels
                        ticks = 5
                        for i in range(ticks + 1):
                            # X ticks
                            tx = y_axis_x + int(plot_w * i / ticks)
                            val = x_max * i / ticks
                            draw.line((tx, x_axis_y, tx, x_axis_y + 6), fill="black")
                            label = f"{val:.1f}"
                            draw.text(
                                (tx - len(label) * 3, x_axis_y + 10), label, fill="black", font=font
                            )
                            # Y ticks
                            ty = x_axis_y - int(plot_h * i / ticks)
                            y_val = y_max * i / ticks
                            draw.line((y_axis_x - 6, ty, y_axis_x, ty), fill="black")
                            label_y = f"{y_val:.0f}"
                            draw.text(
                                (y_axis_x - (len(label_y) * 6 + 12), ty - 4),
                                label_y,
                                fill="black",
                                font=font,
                            )

                        # Build a palette of shapes/colors and assign per unique instance type
                        palette_shapes = [
                            "circle",
                            "triangle",
                            "square",
                            "diamond",
                            "plus",
                            "x",
                            "circle",
                            "square",
                        ]
                        palette_colors = [
                            "#2f7d32",
                            "#1f4e79",
                            "#7f6000",
                            "#9c27b0",
                            "#ff6f00",
                            "#00838f",
                            "#795548",
                            "#c62828",
                        ]
                        unique_types = sorted(set(df["instance_type"]))
                        marker_map = {}
                        for idx, inst_type in enumerate(unique_types):
                            shape = palette_shapes[idx % len(palette_shapes)]
                            color = palette_colors[idx % len(palette_colors)]
                            marker_map[inst_type] = (shape, color)

                        def data_to_px(px, py):
                            sx = y_axis_x + int((px / x_max) * plot_w)
                            sy = x_axis_y - int((py / y_max) * plot_h)
                            return sx, sy

                        def draw_marker(sx: int, sy: int, shape: str, color: str):
                            if shape == "circle":
                                r = 7
                                draw.ellipse(
                                    (sx - r, sy - r, sx + r, sy + r), fill=color, outline="black"
                                )
                            elif shape == "square":
                                r = 7
                                draw.rectangle(
                                    (sx - r, sy - r, sx + r, sy + r), fill=color, outline="black"
                                )
                            elif shape == "triangle":
                                r = 8
                                draw.polygon(
                                    [(sx, sy - r), (sx - r, sy + r), (sx + r, sy + r)],
                                    fill=color,
                                    outline="black",
                                )
                            elif shape == "diamond":
                                r = 8
                                draw.polygon(
                                    [(sx, sy - r), (sx - r, sy), (sx, sy + r), (sx + r, sy)],
                                    fill=color,
                                    outline="black",
                                )
                            elif shape == "plus":
                                draw.line((sx - 7, sy, sx + 7, sy), fill=color, width=2)
                                draw.line((sx, sy - 7, sx, sy + 7), fill=color, width=2)
                            elif shape == "x":
                                draw.line((sx - 7, sy - 7, sx + 7, sy + 7), fill=color, width=2)
                                draw.line((sx - 7, sy + 7, sx + 7, sy - 7), fill=color, width=2)

                        legend_entries = {}
                        special_legends: list[tuple[str, str, str]] = []

                        # Draw points and collect legend entries
                        for _, row in df.iterrows():
                            sx, sy = data_to_px(row["time_from_request"], row["cumulative_vcpus"])
                            shape, color = marker_map.get(
                                row["instance_type"], ("circle", "#444444")
                            )
                            draw_marker(sx, sy, shape, color)
                            if row["instance_type"] not in legend_entries:
                                legend_entries[row["instance_type"]] = (shape, color)

                        # Legend box
                        legend_x = width - margin_right + 40  # move legend further right of plot
                        legend_y = margin_top + 20
                        draw.text(
                            (legend_x, legend_y - 20), "Instance Types", fill="black", font=font
                        )
                        for idx, (inst_type, (shape, color)) in enumerate(legend_entries.items()):
                            ly = legend_y + idx * 20
                            draw_marker(legend_x + 10, ly + 5, shape, color)
                            draw.text((legend_x + 25, ly - 2), inst_type, fill="black", font=font)

                        # Special event markers along X-axis (y=0)
                        if special_events:
                            base_time = df["request_time"].min()
                            for ev in special_events:
                                ts = ev.get("timestamp")
                                if not ts or not base_time:
                                    continue
                                offset = (ts - base_time).total_seconds()
                                sx = y_axis_x + int((offset / x_max) * plot_w)
                                sy = x_axis_y  # y=0 line
                                subtype = (
                                    ev.get("event_subtype") or ev.get("event_type") or ""
                                ).lower()
                                if subtype == "submitted":
                                    draw_marker(sx, sy, "circle", "#f5c400")  # yellow
                                elif subtype == "active":
                                    draw_marker(sx, sy, "circle", "#2e7d32")  # green
                                else:
                                    draw_marker(sx, sy, "x", "#d32f2f")  # red cross

                            # Legend entries for special events
                            special_legends = [
                                ("submitted event", "circle", "#f5c400"),
                                ("active event", "circle", "#2e7d32"),
                                ("other event", "x", "#d32f2f"),
                            ]
                            legend_y_extra = legend_y + (len(legend_entries) + 1) * 20
                            draw.text(
                                (legend_x, legend_y_extra - 20),
                                "Special Events",
                                fill="black",
                                font=font,
                            )
                            for idx, (label, shape, color) in enumerate(special_legends):
                                ly = legend_y_extra + idx * 20
                                draw_marker(legend_x + 10, ly + 5, shape, color)
                                draw.text((legend_x + 25, ly - 2), label, fill="black", font=font)

                        # Draw border around legend area
                        legend_height = (len(legend_entries) + 1 + len(special_legends) + 1) * 20
                        border_padding = 8
                        draw.rectangle(
                            (
                                legend_x - border_padding,
                                legend_y - 30,
                                legend_x + 220,
                                legend_y + legend_height,
                            ),
                            outline="black",
                            width=1,
                        )

                        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        img.save(tmp.name, format="PNG")
                        tmp.close()
                        return tmp.name

                    img_path = render_plot_to_png(dataframe, special_events)
                    img = XLImage(img_path)
                    img.anchor = "K2"
                    worksheet.add_image(img)

                # Special events sheet
                if special_events:
                    special_rows = []
                    for ev in special_events:
                        special_rows.append(
                            {
                                "timestamp": ev.get("timestamp"),
                                "event_type": ev.get("event_type"),
                                "event_subtype": ev.get("event_subtype"),
                                "description": ev.get("description"),
                            }
                        )
                    special_df = pd.DataFrame(special_rows)
                    special_df.to_excel(writer, sheet_name="Special Events", index=False)

                # Add a summary sheet
                summary_data = {
                    "Metric": [
                        "Total Instances",
                        "Provider Type",
                        "Resource ID",
                        "Fastest Creation Time (seconds)",
                        "Slowest Creation Time (seconds)",
                        "Average Creation Time (seconds)",
                        "Success Rate (%)",
                        "First Instance Created",
                        "Last Instance Created",
                        "Total vCPUs",
                        "Average vCPUs per Instance",
                    ],
                    "Value": [
                        len(dataframe),
                        dataframe["provider_api"].iloc[0] if not dataframe.empty else "N/A",
                        dataframe["resource_id"].iloc[0] if not dataframe.empty else "N/A",
                        dataframe["time_from_request"].min() if not dataframe.empty else "N/A",
                        dataframe["time_from_request"].max() if not dataframe.empty else "N/A",
                        round(dataframe["time_from_request"].mean(), 2)
                        if not dataframe.empty
                        else "N/A",
                        100.0
                        if not dataframe.empty and (dataframe["status"] == "Successful").all()
                        else "N/A",
                        dataframe["ec2_creation_time"].min() if not dataframe.empty else "N/A",
                        dataframe["ec2_creation_time"].max() if not dataframe.empty else "N/A",
                        dataframe["cumulative_vcpus"].max() if not dataframe.empty else "N/A",
                        round(dataframe["vcpu_count"].mean(), 2) if not dataframe.empty else "N/A",
                    ],
                }

                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

                # Auto-adjust summary sheet columns
                summary_sheet = writer.sheets["Summary"]
                for column in summary_sheet.columns:
                    max_length = 0
                    column_letter = column[0].column_letter
                    for cell in column:
                        try:
                            max_length = max(max_length, len(str(cell.value)))
                        except:
                            pass
                    adjusted_width = min(max_length + 2, 50)
                    summary_sheet.column_dimensions[column_letter].width = adjusted_width

            self.logger.info(f"Successfully saved Excel with charts to: {output_path}")

        except Exception as e:
            self.logger.error(f"Error saving Excel file: {e}")
            raise

    def validate_output(self, dataframe: pd.DataFrame) -> bool:
        """Validate DataFrame structure before export"""
        required_columns = [
            "ec2_creation_time",
            "time_from_request",
            "capacity_represented",
            "instance_id",
            "provider_api",
            "resource_id",
            "status",
            "request_time",
            "activity_id",
            "vcpu_count",
            "cumulative_vcpus",
        ]

        for col in required_columns:
            if col not in dataframe.columns:
                self.logger.error(f"Missing required column: {col}")
                return False

        if dataframe.empty:
            self.logger.warning("DataFrame is empty")
            return True

        # Validate data types
        if not pd.api.types.is_datetime64_any_dtype(dataframe["ec2_creation_time"]):
            self.logger.error("ec2_creation_time must be datetime type")
            return False

        if not pd.api.types.is_numeric_dtype(dataframe["time_from_request"]):
            self.logger.error("time_from_request must be numeric type")
            return False

        return True


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    return logging.getLogger(__name__)


def main():
    """Main entry point for the AWS Resource History Analyzer"""
    parser = argparse.ArgumentParser(
        description="AWS Resource History Analyzer - Process AWS resource history files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s history.json output.csv
  %(prog)s --provider-type ASG history.json output.csv
  %(prog)s --output-format json history.json output.json
  %(prog)s --validate-only history.json
        """,
    )

    parser.add_argument(
        "input_paths",
        nargs="+",
        help="One or more paths to folders containing metrics/history and work/data/request_database.json",
    )
    parser.add_argument(
        "--output-name",
        default="output.xlsx",
        help="Basename for generated output file (stored under each input folder report/)",
    )
    parser.add_argument(
        "--expand-dirs",
        action="store_true",
        help="Treat each provided path as a parent and process all immediate subdirectories as separate tests",
    )
    parser.add_argument(
        "--cumulative-plot",
        action="store_true",
        help="When multiple folders are provided, render a cumulative plot (lines) across all tests",
    )
    parser.add_argument(
        "--cumulative-plot-path",
        default="cumulative_report.png",
        help="Output path for the cumulative plot image",
    )

    parser.add_argument(
        "--provider-type",
        choices=["ASG", "EC2Fleet", "SpotFleet"],
        help="Override automatic provider detection",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logging output")
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate input file without processing"
    )
    parser.add_argument(
        "--output-format",
        choices=["csv", "json", "parquet", "excel"],
        default="csv",
        help="Output format (default: csv)",
    )

    args = parser.parse_args()

    # Setup logging
    logger = setup_logging(args.verbose)

    try:
        # Initialize processor
        processor = DataProcessor(logger)
        output_manager = OutputManager(logger)

        overall_status = 0
        processed_datasets: list[tuple[str, pd.DataFrame]] = []

        # Build list of target folders
        target_paths: list[Path] = []
        for input_arg in args.input_paths:
            p = Path(input_arg)
            if args.expand_dirs and p.is_dir():
                # Add all immediate subdirectories
                for sub in sorted(p.iterdir()):
                    if sub.is_dir():
                        target_paths.append(sub)
            else:
                target_paths.append(p)

        for input_path in target_paths:
            if not input_path.exists() or not input_path.is_dir():
                logger.error(f"Input path must be an existing directory: {input_path}")
                overall_status = 1
                continue

            # Locate history file inside metrics/
            metrics_dir = input_path / "metrics"
            history_files = list(metrics_dir.glob("*_history.json")) if metrics_dir.exists() else []
            if not history_files:
                logger.error(
                    f"No history file found in metrics/ for {input_path} (expected *_history.json)"
                )
                overall_status = 1
                continue
            history_file = history_files[0]

            # Load request database for instance type lookup
            request_db_path = input_path / "work" / "data" / "request_database.json"
            machine_lookup: dict[str, str] = {}
            if request_db_path.exists():
                try:
                    request_db = json.load(open(request_db_path))
                    machines = request_db.get("machines") or {}
                    machine_lookup = {
                        mid: (info.get("instance_type") or "unknown")
                        for mid, info in machines.items()
                    }
                    logger.info(f"Loaded {len(machine_lookup)} machines from request_database.json")
                except Exception as exc:
                    logger.warning(f"Failed to load request database: {exc}")
            else:
                logger.warning("request_database.json not found; instance types may be unknown")

            # Validate only mode
            if args.validate_only:
                data = processor.file_processor.load_file(str(history_file))
                if processor.file_processor.validate_schema(data):
                    provider_type = processor.file_processor.extract_provider_type(data)
                    logger.info(
                        f"[{input_path}] File validation successful. Detected provider: {provider_type}"
                    )
                else:
                    logger.error(f"[{input_path}] File validation failed")
                    overall_status = 1
                continue

            report_dir = input_path / "report"
            report_dir.mkdir(exist_ok=True)
            folder_name = input_path.name
            test_name = (
                folder_name.split("[")[-1].rstrip("]") if "[" in folder_name else folder_name
            )
            output_basename = f"{test_name}_{Path(args.output_name).name}"
            output_path = report_dir / output_basename
            logger.info(f"Outputs will be written to: {output_path}")

            # Process the file
            df, special_events = processor.process_history_file(
                str(history_file), args.provider_type, machine_lookup
            )
            processed_datasets.append((test_name, df))

            # Validate output
            if not output_manager.validate_output(df):
                logger.error(f"[{input_path}] Output validation failed")
                overall_status = 1
                continue

            # Save output based on format
            if args.output_format == "csv":
                output_manager.save_to_csv(df, str(output_path))
            elif args.output_format == "json":
                output_manager.save_to_json(df, str(output_path))
            elif args.output_format == "parquet":
                output_manager.save_to_parquet(df, str(output_path))
            elif args.output_format == "excel":
                output_manager.save_to_excel(df, str(output_path), special_events=special_events)

            logger.info(
                f"[{input_path}] Processing completed successfully. Output saved to: {output_path}"
            )

        if args.cumulative_plot and processed_datasets:
            try:
                cumulative_path = Path(args.cumulative_plot_path)
                render_cumulative_plot(processed_datasets, cumulative_path)
                logger.info(f"Cumulative plot saved to: {cumulative_path}")
            except Exception as exc:
                logger.error(f"Failed to render cumulative plot: {exc}")
                overall_status = 1

        return overall_status

    except Exception as e:
        logger.error(f"Processing failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def render_cumulative_plot(
    datasets: list[tuple[str, pd.DataFrame]] = None, output_path: Path = None
) -> None:
    """Render cumulative vCPU lines for multiple datasets."""
    datasets = datasets or []
    if not datasets:
        return

    width, height = 2600, 1560
    margin_left, margin_right, margin_top, margin_bottom = 110, 200, 70, 120
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    x_max = 0.0
    y_max = 0.0
    for _, df in datasets:
        if df.empty:
            continue
        x_max = max(x_max, float(df["time_from_request"].max() or 0))
        y_max = max(y_max, float(df["cumulative_vcpus"].max() or 0))
    if x_max <= 0:
        x_max = 1.0
    if y_max <= 0:
        y_max = 1.0

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # Axes
    x_axis_y = height - margin_bottom
    y_axis_x = margin_left
    draw.line((y_axis_x, margin_top, y_axis_x, x_axis_y), fill="black", width=2)
    draw.line((y_axis_x, x_axis_y, width - margin_right, x_axis_y), fill="black", width=2)
    draw.text(
        (y_axis_x + plot_w / 2 - 140, height - margin_bottom + 50),
        "Time from Request (seconds)",
        fill="black",
        font=font,
    )
    draw.text((20, margin_top + plot_h / 2 - 20), "Cumulative vCPUs", fill="black", font=font)

    # Ticks
    ticks = 5
    for i in range(ticks + 1):
        tx = y_axis_x + int(plot_w * i / ticks)
        val = x_max * i / ticks
        draw.line((tx, x_axis_y, tx, x_axis_y + 6), fill="black")
        label = f"{val:.1f}"
        draw.text((tx - len(label) * 3, x_axis_y + 10), label, fill="black", font=font)

        ty = x_axis_y - int(plot_h * i / ticks)
        y_val = y_max * i / ticks
        draw.line((y_axis_x - 6, ty, y_axis_x, ty), fill="black")
        label_y = f"{y_val:.0f}"
        draw.text((y_axis_x - (len(label_y) * 6 + 12), ty - 4), label_y, fill="black", font=font)

    palette_colors = [
        "#2f7d32",
        "#1f4e79",
        "#7f6000",
        "#9c27b0",
        "#ff6f00",
        "#00838f",
        "#795548",
        "#c62828",
    ]

    legend_entries = []
    for idx, (name, df) in enumerate(datasets):
        if df.empty:
            continue
        color = palette_colors[idx % len(palette_colors)]
        pts = []
        # for _, row in df.sort_values("time_from_request").iterrows():
        for _, row in df.iterrows():
            sx = y_axis_x + int((row["time_from_request"] / x_max) * plot_w)
            sy = x_axis_y - int((row["cumulative_vcpus"] / y_max) * plot_h)
            pts.append((sx, sy))
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=3)
        elif len(pts) == 1:
            draw.ellipse(
                (pts[0][0] - 3, pts[0][1] - 3, pts[0][0] + 3, pts[0][1] + 3),
                fill=color,
                outline="black",
            )
        legend_entries.append((name, color))

    # Legend
    legend_x = width - margin_right + 20
    legend_y = margin_top + 20
    draw.text((legend_x, legend_y - 20), "Tests", fill="black", font=font)
    for idx, (label, color) in enumerate(legend_entries):
        ly = legend_y + idx * 20
        draw.line((legend_x + 5, ly + 5, legend_x + 25, ly + 5), fill=color, width=3)
        draw.text((legend_x + 30, ly - 2), label, fill="black", font=font)
    if legend_entries:
        border_padding = 8
        legend_height = (len(legend_entries) + 1) * 20
        draw.rectangle(
            (legend_x - border_padding, legend_y - 30, legend_x + 200, legend_y + legend_height),
            outline="black",
            width=1,
        )

    if output_path is None:
        output_path = Path("cumulative_report.png")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.save(tmp.name, format="PNG")
    tmp.close()
    Path(tmp.name).replace(output_path)


if __name__ == "__main__":
    sys.exit(main())
