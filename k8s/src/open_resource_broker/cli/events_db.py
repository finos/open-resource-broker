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

Normalizes open-resource-broker events db.
"""

import json
import logging
from datetime import datetime
from importlib.resources import files

import click
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

import open_resource_broker.events_schema as schema
from open_resource_broker.cli import log_handler

_skip = {
    "pod:disrupted_message",
    "pod:event",
    # Container statuses are split into individual columns and processed
    # separately.
    "pod:container_statuses",
    "node:events",
    "node:event",
    "request:input",
    "request:output",
    "return:input",
    "return:output",
}


logger = logging.getLogger(__name__)


def _populate_run_metadata(output_session, table_metadata) -> int:
    """Populate the run_metadata table with the metadata of the run."""
    logger.debug("Populating run metadata with: %s", table_metadata)
    run_metadata = schema.RunMetadata(
        cluster=table_metadata["cluster"],
        platform=table_metadata["platform"],
        region=table_metadata["region"],
        namespace=table_metadata["namespace"],
        date=table_metadata["date"],
    )
    output_session.add(run_metadata)
    output_session.commit()
    return run_metadata.id


def _process_container_statuses(
    output_session, run_id, pod_id, timestamp, value
) -> None:
    """Extract values from the json value of container statuses and insert
    them into the container table.
    """
    dialect = output_session.get_bind().dialect.name
    container_statuses = json.loads(value)

    for name, status in container_statuses.items():
        # Ensure that row with name exists (insert if not exists)
        if dialect == "postgresql":
            # PostgreSQL uses ON CONFLICT for upsert
            stmt = (
                sqlalchemy.dialects.postgresql.insert(schema.Container)
                .values(
                    name=name,
                    pod_id=pod_id,
                    run_id=run_id,
                )
                .on_conflict_do_nothing(index_elements=["name", "pod_id"])
            )
        elif dialect == "sqlite":
            # SQLite uses INSERT OR IGNORE for upsert
            stmt = (
                sqlalchemy.insert(schema.Container)
                .values(
                    name=name,
                    pod_id=pod_id,
                    run_id=run_id,
                )
                .prefix_with("OR IGNORE")
            )
        else:
            raise ValueError(f"Unsupported dialect: {dialect}")
        output_session.execute(stmt)

        # For each status - ready/started/scheduled - when the value is
        # true, set the timestamp as the value.
        for status_name in ["ready", "started"]:
            if status.get(status_name):
                stmt = (
                    sqlalchemy.update(schema.Container)
                    .where(
                        sqlalchemy.and_(
                            schema.Container.name == name,
                            schema.Container.pod_id == pod_id,
                            schema.Container.run_id == run_id,
                            getattr(schema.Container, status_name).is_(None),
                        )
                    )
                    .values({status_name: timestamp})
                )
                output_session.execute(stmt)

        # Set the scheduled timestamp first time we see state == "Waiting"
        if status.get("state") == "Waiting":
            stmt = (
                sqlalchemy.update(schema.Container)
                .where(
                    sqlalchemy.and_(
                        schema.Container.name == name,
                        schema.Container.pod_id == pod_id,
                        schema.Container.scheduled.is_(None),
                        schema.Container.run_id == run_id,
                    )
                )
                .values(scheduled=timestamp)
            )
            output_session.execute(stmt)

    output_session.commit()


def _transform_data(input_session, events, output_session, run_id) -> None:  # noqa: C901
    """For each category, read the events in the category and transform them
    from key/value pairs into values in the corresponding table.
    """
    # For each category in the source events database, read the events in the
    # category and transform them from key/value pairs into values in the
    # corresponding table.
    #
    # The columns of the output tables are based on the distinct value types
    # associated with each event category.
    logger.info("Transforming data for run ID: %d", run_id)
    categories = [
        category[0]
        for category in input_session.query(events.c.category).distinct().all()
    ]

    processed = set()

    for category in categories:
        logger.debug("Processing category: %s", category)
        rows = (
            input_session.query(
                events.c.id, events.c.timestamp, events.c.type, events.c.value
            )
            .filter(events.c.category == category)
            .order_by(events.c.timestamp)
            .all()
        )

        for id_, timestamp, type_, value in rows:
            # Special handling of container statuses, as they are
            # translated into container table.
            if category == "pod" and type_ == "container_statuses":
                _process_container_statuses(
                    output_session, run_id, id_, timestamp, value
                )
                continue

            if type_ == "status":
                column_name = value
                column_value = timestamp
            else:
                column_name = type_.replace("-", "_")  # SQLite compatibility
                column_value = value

            if column_value == "null":
                continue

            if f"{category}:{column_name}" in _skip:
                continue

            if (category, id_, column_name) in processed:
                continue

            processed.add((category, id_, column_name))
            # Ensure that row with name exists (insert if not exists)
            # id_ maps to the "name" in the output table
            table = category.capitalize()
            if not hasattr(schema, table):
                logger.debug("Table %s does not exist in schema.", table)
                continue

            table_class = getattr(schema, table)
            if column_name not in table_class.__table__.columns:
                logger.debug(
                    "Column %s does not exist in table %s.", column_name, table
                )
                continue

            existing_row = (
                output_session.query(table_class)
                .filter_by(name=id_, run_id=run_id)
                .first()
            )
            if not existing_row:
                new_row = table_class(name=id_, run_id=run_id)
                output_session.add(new_row)
                output_session.commit()

            # Update if column_name is null
            column_name = "input_" if column_name == "input" else column_name

            output_session.query(table_class).filter(
                table_class.name == id_,
                table_class.run_id == run_id,
                getattr(table_class, column_name).is_(None),
            ).update({column_name: column_value})

        output_session.commit()


def _run_migrations(db_url) -> None:
    """Run Alembic migrations to set up the database schema."""
    alembic_ini = files("open_resource_broker.alembic").joinpath("alembic.ini")
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.attributes["configure_logger"] = False
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    script_loc = str(files("open_resource_broker.alembic"))
    alembic_cfg.set_main_option("script_location", script_loc)
    alembic_cfg.set_main_option(
        "version_locations",
        str(files("open_resource_broker.alembic").joinpath("versions")),
    )
    alembic_cfg.set_main_option("timezone", "UTC")
    alembic_cfg.set_main_option("output_encoding", "utf-8")

    command.upgrade(alembic_cfg, "head")


def _generate_summary(output_session, run_id) -> None:
    """Generate the summary of the transformed table"""
    # The summary table has information about the test:
    # - start: first event timestamp
    # - end: last event timestamp

    # Insert summary start and finish data
    # Get the start and end times for the run

    summary_data = (
        output_session.query(
            func.max(schema.Node.created).label("run_start"),
            func.max(schema.Node.deleted).label("run_end"),
        )
        .filter(schema.Node.run_id == run_id)
        .one_or_none()
    )

    if summary_data:
        start, end = summary_data
        output_session.execute(
            schema.Summary.__table__.insert().values(
                start=start, end=end, run_id=run_id
            )
        )

        # Calculate CPU minutes
        vcpu_usage = (
            output_session.query(
                func.sum(
                    schema.Node.cpu_capacity
                    * (
                        (
                            func.coalesce(
                                schema.Node.deleted,
                                output_session.query(func.max(schema.Node.deleted))
                                .filter(
                                    schema.Node.deleted.isnot(None),
                                    schema.Node.run_id == run_id,
                                )
                                .scalar_subquery(),
                            )
                            - func.coalesce(schema.Node.created, 0)
                        )
                        / 60
                    )
                )
            )
            .filter(
                schema.Node.cpu_capacity.isnot(None),
                schema.Node.run_id == run_id,
            )
            .scalar()
            or 0
        )
        output_session.execute(
            schema.Summary.__table__.update()
            .values(cpu_minutes=vcpu_usage)
            .where(schema.Summary.run_id == run_id)
        )

        # Calculate percentage node usage
        vcpu_pod_usage = (
            output_session.query(
                func.sum(
                    schema.Pod.cpu_requested
                    * (
                        (
                            func.coalesce(
                                schema.Pod.deleted,
                                output_session.query(func.max(schema.Pod.deleted))
                                .filter(
                                    schema.Pod.deleted.isnot(None),
                                    schema.Pod.run_id == run_id,
                                )
                                .scalar_subquery(),
                            )
                            - func.coalesce(schema.Pod.created, 0)
                        )
                        / 60.0
                    )
                )
            )
            .filter(
                schema.Pod.cpu_requested.isnot(None),
                schema.Pod.run_id == run_id,
            )
            .scalar()
            or 0
        )
        percentage_node_usage = (
            vcpu_pod_usage / vcpu_usage * 100 if vcpu_usage > 0 else 0
        )
        output_session.execute(
            schema.Summary.__table__.update()
            .values(percentage_node_usage=percentage_node_usage)
            .where(schema.Summary.run_id == run_id)
        )

        output_session.commit()
        # Print summary
        summary = (
            output_session.query(schema.Summary)
            .filter(schema.Summary.run_id == run_id)
            .all()
        )

        if all(
            getattr(summary[0], field) is not None
            for field in ["start", "end", "cpu_minutes", "percentage_node_usage"]
        ):
            start_time = datetime.fromtimestamp(summary[0].start).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            end_time = datetime.fromtimestamp(summary[0].end).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            duration_hours = (summary[0].end - summary[0].start) / 3600
            cpu_minutes = summary[0].cpu_minutes
            percentage = summary[0].percentage_node_usage

            logger.info(
                "\nTest Summary:\n"
                "  Start Time:     %s\n"
                "  End Time:       %s\n"
                "  Duration:       %.2f hours\n"
                "  CPU Minutes:    %.2f\n"
                "  Node Usage:     %.2f%%\n"
                "  Run ID: %d",
                start_time,
                end_time,
                duration_hours,
                cpu_minutes,
                percentage,
                run_id,
            )


@click.group()
def run() -> None:
    """Event DB Normalization Tool."""


@run.command()
@click.option(
    "--input-db",
    type=click.Path(exists=True),
    required=True,
    help="Path to the input SQLite database.",
)
@click.option(
    "--log-level",
    type=click.Choice(
        ["info", "debug", "error", "warning", "critical"], case_sensitive=False
    ),
    default="info",
    help="Set the log level.",
)
@click.option(
    "--log-file",
    default=None,
    envvar="HF_K8S_LOG_FILE",
    help="Hostfactory log file location.",
    type=click.Path(exists=False, file_okay=True, dir_okay=False, writable=True),
)
@click.option(
    "--cluster",
    type=str,
    required=True,
    help="Cluster name to be used in the output database.",
)
@click.option(
    "--platform",
    type=str,
    required=True,
    help="Cloud provided platform (e.g. eks, gke).",
)
@click.option(
    "--region",
    type=str,
    required=True,
    help="Region of the cluster (e.g. us-east-1).",
)
@click.option(
    "--namespace",
    type=str,
    required=True,
    help="Namespace of the cluster (e.g. symphony).",
)
@click.option(
    "--date",
    type=str,
    required=True,
    help="Datetime in the format 'YYYY-MM-DD-HH-MM' (e.g. 2023-10-31-14-30).",
)
@click.argument(
    "db-url",
    type=str,
    required=True,
)
def transform(
    input_db, log_level, log_file, cluster, platform, region, namespace, date, db_url
) -> None:
    """Runs the event db normalization."""
    log_handler.setup_logging(log_level, log_file)

    table_metadata = {
        "cluster": cluster,
        "platform": platform,
        "region": region,
        "namespace": namespace,
        "date": datetime.strptime(date, "%Y-%m-%d-%H-%M"),
    }

    logger.info("Running event db normalization with the following metadata:")
    logger.info("Input database: %s", input_db)
    logger.info("Output database URL: %s", db_url)
    logger.info(
        "Cluster: %s, Platform: %s, Namespace: %s, Date: %s",
        cluster,
        platform,
        namespace,
        date,
    )

    try:
        input_engine = sqlalchemy.create_engine(f"sqlite:///{input_db}")
        output_engine = sqlalchemy.create_engine(db_url)

        input_conn = input_engine.connect()
        output_conn = output_engine.connect()

        _run_migrations(db_url)

        logger.debug("Reflecting events table from input database: %s", input_db)
        input_metadata = sqlalchemy.MetaData()
        events_table = sqlalchemy.Table(
            "events", input_metadata, autoload_with=input_engine
        )

        output_session = sessionmaker(bind=output_engine)()
        input_session = sessionmaker(bind=input_engine)()

        run_id = _populate_run_metadata(output_session, table_metadata)
        _transform_data(input_session, events_table, output_session, run_id)
        logger.info("Generating summary for run ID: %d", run_id)
        _generate_summary(output_session, run_id)

    except Exception as exc:
        logger.error("An error occurred: %s", exc)
        raise

    finally:
        input_session.close()
        output_session.close()
        input_conn.close()
        output_conn.close()

    logger.info("Transformation complete. Run metadata ID: %d", run_id)
