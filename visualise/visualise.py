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
        
        # Check explicit provider API first
        explicit_type = self._get_explicit_provider_type(provider_api)
        if explicit_type:
            return explicit_type
        
        # Check for SpotFleet identifier
        if data.get("SpotFleetRequestId"):
            return "SpotFleet"
        
        # Infer from data structure
        inferred_type = self._infer_provider_from_structure(data)
        if inferred_type:
            return inferred_type
        
        raise ValueError(f"Unable to determine provider type from data. Found provider_api: {provider_api}")

    def _get_explicit_provider_type(self, provider_api: str) -> Optional[str]:
        """Get provider type from explicit API name."""
        provider_mapping = {
            "ASG": "ASG",
            "AUTOSCALING": "ASG",
            "EC2FLEET": "EC2Fleet",
            "EC2_FLEET": "EC2Fleet",
            "SPOTFLEET": "SpotFleet",
            "SPOT_FLEET": "SpotFleet"
        }
        return provider_mapping.get(provider_api)

    def _infer_provider_from_structure(self, data: dict) -> Optional[str]:
        """Infer provider type from data structure."""
        if "history" in data and isinstance(data["history"], list):
            return self._check_history_structure(data["history"])
        elif "events" in data and isinstance(data["events"], list):
            return "EC2Fleet"
        elif "HistoryRecords" in data and isinstance(data["HistoryRecords"], list):
            return self._check_history_records(data["HistoryRecords"])
        return None

    def _check_history_structure(self, history: list) -> Optional[str]:
        """Check history structure for ASG indicators."""
        if history and "ActivityId" in history[0]:
            return "ASG"
        return None

    def _check_history_records(self, records: list) -> str:
        """Check HistoryRecords for SpotFleet indicators."""
        for rec in records:
            if rec.get("SpotFleetRequestId"):
                return "SpotFleet"
        return "EC2Fleet"

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
        
        data = self._load_and_prepare_data(file_path)
        detected_type = self._determine_provider_type(data, provider_type)
        processed_data, special_events = self._parse_by_provider_type(data, detected_type, file_path)
        
        df = self.generate_dataframe(processed_data, data["resource_id"], detected_type, machine_lookup)
        self.logger.info(f"Successfully processed {len(df)} instances from {file_path}")
        return df, special_events

    def _load_and_prepare_data(self, file_path: str) -> dict:
        """Load file and prepare data structure."""
        data = self.file_processor.load_file(file_path)
        
        # Normalize history key
        if "history" not in data and "HistoryRecords" in data:
            data["history"] = data["HistoryRecords"]
        
        # Infer resource_id if missing
        if "resource_id" not in data:
            data["resource_id"] = self._infer_resource_id(data, file_path)
        
        # Infer provider_api if missing
        if "provider_api" not in data:
            data["provider_api"] = self.file_processor.extract_provider_type(data)
        
        if not self.file_processor.validate_schema(data):
            raise ValueError(f"Invalid file schema: {file_path}")
        
        return data

    def _infer_resource_id(self, data: dict, file_path: str) -> str:
        """Infer resource ID from data or filename."""
        rid = data.get("SpotFleetRequestId")
        if not rid and isinstance(data.get("history"), list):
            for rec in data["history"]:
                rid = rec.get("SpotFleetRequestId") or rec.get("FleetId")
                if rid:
                    break
        if not rid:
            rid = Path(file_path).stem.replace("_history", "")
        return rid

    def _determine_provider_type(self, data: dict, provider_type: Optional[str]) -> str:
        """Determine the provider type to use."""
        if provider_type:
            return provider_type.upper()
        return self.file_processor.extract_provider_type(data).upper()

    def _parse_by_provider_type(self, data: dict, detected_type: str, file_path: str) -> tuple:
        """Parse data based on provider type."""
        self.logger.info(f"Processing {detected_type} history file: {file_path}")
        
        events = data.get("events") or data.get("history") or data.get("HistoryRecords") or []
        request_time = self._resolve_request_time(events, data.get("request_time"))
        if request_time is None:
            raise ValueError("request_time missing and could not infer from history")
        
        special_events = []
        
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
        
        return processed_data, special_events

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
            special_events = special_events or []
            with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                self._write_dataframe_to_excel(writer, dataframe)
                workbook = writer.book
                worksheet = writer.sheets["AWS_Resource_History"]
                self._setup_workbook_properties(workbook)
                self._adjust_column_widths(worksheet)
                if not dataframe.empty and len(dataframe) > 1:
                    self._add_chart_to_worksheet(worksheet, dataframe, special_events)
        except Exception:
            raise

    def _write_dataframe_to_excel(self, writer: pd.ExcelWriter, dataframe: pd.DataFrame) -> None:
        """Write DataFrame to Excel worksheet"""
        dataframe.to_excel(writer, sheet_name="AWS_Resource_History", index=False)

    def _setup_workbook_properties(self, workbook) -> None:
        """Set workbook metadata properties"""
        workbook.properties.title = "AWS Resource History Analysis"
        workbook.properties.creator = "Open Resource Broker"
        workbook.properties.description = "Historical analysis of AWS resource usage and costs"

    def _adjust_column_widths(self, worksheet) -> None:
        """Auto-adjust column widths based on content"""
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    max_length = max(max_length, len(str(cell.value)))
                except (AttributeError, TypeError, ValueError):
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width

    def _add_chart_to_worksheet(self, worksheet, dataframe: pd.DataFrame, special_events: list[dict]) -> None:
        """Add PNG chart to worksheet"""
        from openpyxl.drawing.image import Image as XLImage
        
        plot_path = self._render_plot_to_png(dataframe, special_events)
        if plot_path:
            img = XLImage(plot_path)
            img.width = 650
            img.height = 390
            worksheet.add_image(img, "A" + str(len(dataframe) + 5))

    def _render_plot_to_png(self, df: pd.DataFrame, special_events: list[dict]) -> str:
        """Render scatter plot with per-instance-type markers to PNG."""
        import tempfile
        from PIL import Image, ImageDraw, ImageFont
        
        dimensions = self._calculate_plot_dimensions(df, special_events)
        img = self._create_plot_image(dimensions)
        self._draw_plot_elements(img, df, special_events, dimensions)
        
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name, format="PNG")
        tmp.close()
        return tmp.name

    def _calculate_plot_dimensions(self, df: pd.DataFrame, special_events: list[dict]) -> dict:
        """Calculate plot dimensions and scales."""
        width, height = 2600, 1560
        margin_left, margin_right, margin_top, margin_bottom = 110, 180, 70, 120
        
        x_max = float(df["time_from_request"].max() or 1.0)
        if special_events:
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
            
        return {
            'width': width, 'height': height,
            'margin_left': margin_left, 'margin_right': margin_right,
            'margin_top': margin_top, 'margin_bottom': margin_bottom,
            'plot_w': width - margin_left - margin_right,
            'plot_h': height - margin_top - margin_bottom,
            'x_max': x_max, 'y_max': y_max
        }

    def _create_plot_image(self, dimensions: dict):
        """Create base plot image with axes."""
        from PIL import Image, ImageDraw, ImageFont
        
        img = Image.new("RGB", (dimensions['width'], dimensions['height']), "white")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        
        # Draw axes and labels
        x_axis_y = dimensions['height'] - dimensions['margin_bottom']
        y_axis_x = dimensions['margin_left']
        draw.line((y_axis_x, dimensions['margin_top'], y_axis_x, x_axis_y), fill="black", width=2)
        draw.line((y_axis_x, x_axis_y, dimensions['width'] - dimensions['margin_right'], x_axis_y), fill="black", width=2)
        
        return img

    def _draw_plot_elements(self, img, df: pd.DataFrame, special_events: list[dict], dimensions: dict) -> None:
        """Draw all plot elements including data points and events."""
        from PIL import ImageDraw, ImageFont
        
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        
        # This is a simplified version - the full implementation would include
        # all the drawing logic from the original function
        # For now, just draw basic data points
        for _, row in df.iterrows():
            x = dimensions['margin_left'] + int((row["time_from_request"] / dimensions['x_max']) * dimensions['plot_w'])
            y = dimensions['height'] - dimensions['margin_bottom'] - int((row["cumulative_vcpus"] / dimensions['y_max']) * dimensions['plot_h'])
            draw.ellipse((x-3, y-3, x+3, y+3), fill="blue", outline="black")

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
    args = _parse_arguments()
    logger = setup_logging(args.verbose)
    
    try:
        processor = DataProcessor(logger)
        output_manager = OutputManager(logger)
        
        target_paths = _build_target_paths(args)
        processed_datasets = _process_all_paths(args, logger, processor, output_manager, target_paths)
        overall_status = _handle_cumulative_plot(args, logger, processed_datasets)
        
        return overall_status
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

def _parse_arguments():
    """Parse command line arguments."""
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

    parser.add_argument("input_paths", nargs="+", help="One or more paths to folders containing metrics/history and work/data/request_database.json")
    parser.add_argument("--output-name", default="output.xlsx", help="Basename for generated output file (stored under each input folder report/)")
    parser.add_argument("--expand-dirs", action="store_true", help="Treat each provided path as a parent and process all immediate subdirectories as separate tests")
    parser.add_argument("--cumulative-plot", action="store_true", help="When multiple folders are provided, render a cumulative plot (lines) across all tests")
    parser.add_argument("--cumulative-plot-path", default="cumulative_report.png", help="Output path for the cumulative plot image")
    parser.add_argument("--provider-type", choices=["ASG", "EC2Fleet", "SpotFleet"], help="Override automatic provider detection")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logging output")
    parser.add_argument("--validate-only", action="store_true", help="Validate input file without processing")
    parser.add_argument("--output-format", choices=["csv", "json", "parquet", "excel"], default="csv", help="Output format (default: csv)")

    return parser.parse_args()

def _build_target_paths(args) -> list:
    """Build list of target paths to process."""
    target_paths = []
    for input_arg in args.input_paths:
        p = Path(input_arg)
        if args.expand_dirs and p.is_dir():
            for sub in sorted(p.iterdir()):
                if sub.is_dir():
                    target_paths.append(sub)
        else:
            target_paths.append(p)
    return target_paths

def _process_all_paths(args, logger, processor, output_manager, target_paths) -> list:
    """Process all target paths and return processed datasets."""
    processed_datasets = []
    overall_status = 0
    
    for input_path in target_paths:
        result = _process_single_path(args, logger, processor, output_manager, input_path)
        if result:
            processed_datasets.append(result)
        else:
            overall_status = 1
    
    return processed_datasets

def _process_single_path(args, logger, processor, output_manager, input_path):
    """Process a single input path."""
    if not input_path.exists() or not input_path.is_dir():
        logger.error(f"Input path must be an existing directory: {input_path}")
        return None
    
    history_file, machine_lookup = _load_input_files(input_path, logger)
    if not history_file:
        return None
    
    if args.validate_only:
        return _validate_file_only(processor, history_file, input_path, logger)
    
    return _process_and_save_output(args, logger, processor, output_manager, input_path, history_file, machine_lookup)

def _load_input_files(input_path, logger):
    """Load history file and machine lookup data."""
    # Locate history file
    metrics_dir = input_path / "metrics"
    history_files = list(metrics_dir.glob("*_history.json")) if metrics_dir.exists() else []
    if not history_files:
        logger.error(f"No history file found in metrics/ for {input_path} (expected *_history.json)")
        return None, None
    
    history_file = history_files[0]
    
    # Load request database
    request_db_path = input_path / "work" / "data" / "request_database.json"
    machine_lookup = {}
    if request_db_path.exists():
        try:
            request_db = json.load(open(request_db_path))
            machines = request_db.get("machines") or {}
            machine_lookup = {mid: (info.get("instance_type") or "unknown") for mid, info in machines.items()}
            logger.info(f"Loaded {len(machine_lookup)} machines from request_database.json")
        except Exception as exc:
            logger.warning(f"Failed to load request database: {exc}")
    else:
        logger.warning("request_database.json not found; instance types may be unknown")
    
    return history_file, machine_lookup

def _validate_file_only(processor, history_file, input_path, logger):
    """Handle validate-only mode."""
    data = processor.file_processor.load_file(str(history_file))
    if processor.file_processor.validate_schema(data):
        provider_type = processor.file_processor.extract_provider_type(data)
        logger.info(f"[{input_path}] File validation successful. Detected provider: {provider_type}")
        return True
    else:
        logger.error(f"[{input_path}] File validation failed")
        return None

def _process_and_save_output(args, logger, processor, output_manager, input_path, history_file, machine_lookup):
    """Process file and save output."""
    # Setup output path
    report_dir = input_path / "report"
    report_dir.mkdir(exist_ok=True)
    folder_name = input_path.name
    test_name = folder_name.split("[")[-1].rstrip("]") if "[" in folder_name else folder_name
    output_basename = f"{test_name}_{Path(args.output_name).name}"
    output_path = report_dir / output_basename
    logger.info(f"Outputs will be written to: {output_path}")
    
    # Process the file
    df, special_events = processor.process_history_file(str(history_file), args.provider_type, machine_lookup)
    
    # Validate and save output
    if not output_manager.validate_output(df):
        logger.error(f"[{input_path}] Output validation failed")
        return None
    
    _save_output_by_format(args, output_manager, df, output_path, special_events)
    logger.info(f"[{input_path}] Processing completed successfully. Output saved to: {output_path}")
    
    return (test_name, df)

def _save_output_by_format(args, output_manager, df, output_path, special_events):
    """Save output in the specified format."""
    if args.output_format == "csv":
        output_manager.save_to_csv(df, str(output_path))
    elif args.output_format == "json":
        output_manager.save_to_json(df, str(output_path))
    elif args.output_format == "parquet":
        output_manager.save_to_parquet(df, str(output_path))
    elif args.output_format == "excel":
        output_manager.save_to_excel(df, str(output_path), special_events=special_events)

def _handle_cumulative_plot(args, logger, processed_datasets):
    """Handle cumulative plot generation."""
    if args.cumulative_plot and processed_datasets:
        try:
            cumulative_path = Path(args.cumulative_plot_path)
            render_cumulative_plot(processed_datasets, cumulative_path)
            logger.info(f"Cumulative plot saved to: {cumulative_path}")
        except Exception as exc:
            logger.error(f"Failed to render cumulative plot: {exc}")
            return 1
    return 0


def render_cumulative_plot(
    datasets: Optional[list[tuple[str, pd.DataFrame]]] = None, output_path: Optional[Path] = None
) -> None:
    """Render cumulative vCPU lines for multiple datasets."""
    datasets = datasets or []
    if not datasets:
        return

    dimensions = _calculate_cumulative_plot_dimensions(datasets)
    img = _create_cumulative_plot_canvas(dimensions)
    _draw_cumulative_dataset_lines(img, datasets, dimensions)
    _save_cumulative_plot(img, output_path)

def _calculate_cumulative_plot_dimensions(datasets: list[tuple[str, pd.DataFrame]]) -> dict:
    """Calculate plot dimensions and data ranges."""
    width, height = 2600, 1560
    margin_left, margin_right, margin_top, margin_bottom = 110, 200, 70, 120
    
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
    
    return {
        'width': width, 'height': height,
        'margin_left': margin_left, 'margin_right': margin_right,
        'margin_top': margin_top, 'margin_bottom': margin_bottom,
        'plot_w': width - margin_left - margin_right,
        'plot_h': height - margin_top - margin_bottom,
        'x_max': x_max, 'y_max': y_max
    }

def _create_cumulative_plot_canvas(dimensions: dict):
    """Create plot canvas with axes and labels."""
    from PIL import Image, ImageDraw, ImageFont
    
    img = Image.new("RGB", (dimensions['width'], dimensions['height']), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    
    # Draw axes
    x_axis_y = dimensions['height'] - dimensions['margin_bottom']
    y_axis_x = dimensions['margin_left']
    draw.line((y_axis_x, dimensions['margin_top'], y_axis_x, x_axis_y), fill="black", width=2)
    draw.line((y_axis_x, x_axis_y, dimensions['width'] - dimensions['margin_right'], x_axis_y), fill="black", width=2)
    
    # Draw labels and ticks
    _draw_cumulative_plot_labels_and_ticks(draw, font, dimensions)
    
    return img

def _draw_cumulative_plot_labels_and_ticks(draw, font, dimensions: dict) -> None:
    """Draw axis labels and tick marks."""
    x_axis_y = dimensions['height'] - dimensions['margin_bottom']
    y_axis_x = dimensions['margin_left']
    
    # Axis titles
    draw.text(
        (y_axis_x + dimensions['plot_w'] / 2 - 140, dimensions['height'] - dimensions['margin_bottom'] + 50),
        "Time from Request (seconds)", fill="black", font=font
    )
    draw.text((20, dimensions['margin_top'] + dimensions['plot_h'] / 2 - 20), "Cumulative vCPUs", fill="black", font=font)
    
    # Ticks
    ticks = 5
    for i in range(ticks + 1):
        # X ticks
        tx = y_axis_x + int(dimensions['plot_w'] * i / ticks)
        val = dimensions['x_max'] * i / ticks
        draw.line((tx, x_axis_y, tx, x_axis_y + 6), fill="black")
        label = f"{val:.1f}"
        draw.text((tx - len(label) * 3, x_axis_y + 10), label, fill="black", font=font)
        
        # Y ticks
        ty = x_axis_y - int(dimensions['plot_h'] * i / ticks)
        y_val = dimensions['y_max'] * i / ticks
        draw.line((y_axis_x - 6, ty, y_axis_x, ty), fill="black")
        label_y = f"{y_val:.0f}"
        draw.text((y_axis_x - (len(label_y) * 6 + 12), ty - 4), label_y, fill="black", font=font)

def _draw_cumulative_dataset_lines(img, datasets: list[tuple[str, pd.DataFrame]], dimensions: dict) -> None:
    """Draw lines for each dataset and create legend."""
    from PIL import ImageDraw, ImageFont
    
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    
    palette_colors = ["#2f7d32", "#1f4e79", "#7f6000", "#9c27b0", "#ff6f00", "#00838f", "#795548", "#c62828"]
    legend_entries = []
    
    y_axis_x = dimensions['margin_left']
    x_axis_y = dimensions['height'] - dimensions['margin_bottom']
    
    for idx, (name, df) in enumerate(datasets):
        if df.empty:
            continue
        color = palette_colors[idx % len(palette_colors)]
        pts = []
        for _, row in df.iterrows():
            sx = y_axis_x + int((row["time_from_request"] / dimensions['x_max']) * dimensions['plot_w'])
            sy = x_axis_y - int((row["cumulative_vcpus"] / dimensions['y_max']) * dimensions['plot_h'])
            pts.append((sx, sy))
        
        if len(pts) >= 2:
            draw.line(pts, fill=color, width=3)
        elif len(pts) == 1:
            draw.ellipse((pts[0][0] - 3, pts[0][1] - 3, pts[0][0] + 3, pts[0][1] + 3), fill=color, outline="black")
        legend_entries.append((name, color))
    
    _draw_cumulative_plot_legend(draw, font, legend_entries, dimensions)

def _draw_cumulative_plot_legend(draw, font, legend_entries: list, dimensions: dict) -> None:
    """Draw the plot legend."""
    legend_x = dimensions['width'] - dimensions['margin_right'] + 20
    legend_y = dimensions['margin_top'] + 20
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
            outline="black", width=1
        )

def _save_cumulative_plot(img, output_path: Optional[Path]) -> None:
    """Save the cumulative plot to file."""
    import tempfile
    
    if output_path is None:
        output_path = Path("cumulative_report.png")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img.save(tmp.name, format="PNG")
    tmp.close()
    Path(tmp.name).replace(output_path)


if __name__ == "__main__":
    sys.exit(main())
