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

Test events db normalization helpers.
"""

import json

import pytest
import sqlalchemy
from click.testing import CliRunner
from sqlalchemy.orm import sessionmaker

import orb.k8s_legacy.events_schema as schema
from orb.k8s_legacy.cli import events_db


@pytest.fixture
def output_session():
    """A fresh in-memory sqlite output database with the full schema."""
    engine = sqlalchemy.create_engine("sqlite://")
    schema.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def events_table_factory():
    """Builds an in-memory sqlite "events" input db/session + table pair."""
    created = []

    def _make(rows):
        engine = sqlalchemy.create_engine("sqlite://")
        metadata = sqlalchemy.MetaData()
        events_table = sqlalchemy.Table(
            "events",
            metadata,
            sqlalchemy.Column("id", sqlalchemy.String),
            sqlalchemy.Column("timestamp", sqlalchemy.Integer),
            sqlalchemy.Column("category", sqlalchemy.String),
            sqlalchemy.Column("type", sqlalchemy.String),
            sqlalchemy.Column("value", sqlalchemy.String),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            for row in rows:
                conn.execute(events_table.insert().values(**row))
        session = sessionmaker(bind=engine)()
        created.append((session, engine))
        return session, events_table

    yield _make
    for session, engine in created:
        session.close()
        engine.dispose()


def _run_metadata():
    return {
        "cluster": "test-cluster",
        "platform": "eks",
        "region": "us-east-1",
        "namespace": "symphony",
        "date": "2024-01-01-00-00",
    }


class TestPopulateRunMetadata:
    """Validate _populate_run_metadata."""

    def test_inserts_row_and_returns_id(self, output_session) -> None:
        """A new run metadata row is committed and its id returned."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        assert run_id is not None

        row = output_session.query(schema.RunMetadata).filter_by(id=run_id).one()
        assert row.cluster == "test-cluster"
        assert row.platform == "eks"
        assert row.namespace == "symphony"

    def test_second_insert_gets_distinct_id(self, output_session) -> None:
        """Each call creates a new, distinct run id."""
        run_id_1 = events_db._populate_run_metadata(output_session, _run_metadata())
        other_metadata = _run_metadata()
        other_metadata["cluster"] = "other-cluster"
        run_id_2 = events_db._populate_run_metadata(output_session, other_metadata)
        assert run_id_1 != run_id_2


class TestProcessContainerStatuses:
    """Validate _process_container_statuses."""

    def test_inserts_and_updates_sqlite(self, output_session) -> None:
        """Container rows are inserted and timestamps set for sqlite."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        value = json.dumps(
            {
                "app": {"ready": True, "started": True, "state": "Running"},
                "init": {"ready": False, "started": False, "state": "Waiting"},
            }
        )
        events_db._process_container_statuses(output_session, run_id, "pod-1", 1000, value)

        app_row = (
            output_session.query(schema.Container)
            .filter_by(name="app", pod_id="pod-1", run_id=run_id)
            .one()
        )
        assert app_row.ready == 1000
        assert app_row.started == 1000

        init_row = (
            output_session.query(schema.Container)
            .filter_by(name="init", pod_id="pod-1", run_id=run_id)
            .one()
        )
        assert init_row.scheduled == 1000
        assert init_row.ready is None

    def test_existing_timestamp_is_not_overwritten(self, output_session) -> None:
        """Once a timestamp is set, a later event does not overwrite it."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        first = json.dumps({"app": {"ready": True, "started": False}})
        events_db._process_container_statuses(output_session, run_id, "pod-1", 1000, first)

        second = json.dumps({"app": {"ready": True, "started": False}})
        events_db._process_container_statuses(output_session, run_id, "pod-1", 2000, second)

        row = (
            output_session.query(schema.Container)
            .filter_by(name="app", pod_id="pod-1", run_id=run_id)
            .one()
        )
        assert row.ready == 1000

    def test_unsupported_dialect_raises(self, output_session, monkeypatch) -> None:
        """An unsupported SQL dialect raises a ValueError."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())

        monkeypatch.setattr(
            output_session.get_bind().dialect.__class__, "name", "oracle", raising=False
        )
        value = json.dumps({"app": {"ready": True}})
        with pytest.raises(ValueError, match="Unsupported dialect"):
            events_db._process_container_statuses(output_session, run_id, "pod-1", 1000, value)


class TestTransformData:
    """Validate _transform_data category/type translation."""

    def test_status_type_sets_column_from_value(self, output_session, events_table_factory) -> None:
        """A 'status' typed row sets the column named by its value to the timestamp."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "status",
                    "value": "running",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        row = output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).one()
        assert row.running == 500

    def test_non_status_type_uses_value_as_column_value(
        self, output_session, events_table_factory
    ) -> None:
        """A non-status typed row stores its value directly in the column."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "node-name",
                    "value": "node-a",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        row = output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).one()
        assert row.node_name == "node-a"

    def test_null_value_is_skipped(self, output_session, events_table_factory) -> None:
        """Rows whose value is the literal string 'null' are skipped entirely."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "node-name",
                    "value": "null",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        assert output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).count() == 0

    def test_skip_set_categories_are_ignored(self, output_session, events_table_factory) -> None:
        """Category:type combinations in the _skip set are never written."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "event",
                    "value": "some-event",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        assert output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).count() == 0

    def test_unknown_table_is_skipped(self, output_session, events_table_factory) -> None:
        """Categories without a matching schema table are skipped."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "thing-1",
                    "timestamp": 500,
                    "category": "unknown_category",
                    "type": "state",
                    "value": "x",
                },
            ]
        )
        # Should not raise even though "Unknown_category" has no schema table.
        events_db._transform_data(input_session, events_table, output_session, run_id)

    def test_unknown_column_is_skipped(self, output_session, events_table_factory) -> None:
        """Columns that don't exist on the target table are skipped."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "no-such-column",
                    "value": "x",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)
        assert output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).count() == 0

    def test_duplicate_category_id_column_is_processed_once(
        self, output_session, events_table_factory
    ) -> None:
        """A later duplicate (category, id, column) event is ignored."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 500,
                    "category": "pod",
                    "type": "node-name",
                    "value": "node-a",
                },
                {
                    "id": "pod-1",
                    "timestamp": 600,
                    "category": "pod",
                    "type": "node-name",
                    "value": "node-b",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        row = output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).one()
        assert row.node_name == "node-a"

    def test_container_statuses_dispatches_to_container_table(
        self, output_session, events_table_factory
    ) -> None:
        """pod:container_statuses rows populate the Container table instead of Pod."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        value = json.dumps({"app": {"ready": True, "started": True}})
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "pod-1",
                    "timestamp": 700,
                    "category": "pod",
                    "type": "container_statuses",
                    "value": value,
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        container_row = (
            output_session.query(schema.Container)
            .filter_by(name="app", pod_id="pod-1", run_id=run_id)
            .one()
        )
        assert container_row.ready == 700
        # No Pod row should be created from this category/type.
        assert output_session.query(schema.Pod).filter_by(name="pod-1", run_id=run_id).count() == 0

    def test_input_column_name_is_remapped(self, output_session, events_table_factory) -> None:
        """The reserved 'input' column name is remapped to 'input_'."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        input_session, events_table = events_table_factory(
            [
                {
                    "id": "req-1",
                    "timestamp": 100,
                    "category": "template",
                    "type": "output",
                    "value": "template-output",
                },
            ]
        )
        events_db._transform_data(input_session, events_table, output_session, run_id)

        row = output_session.query(schema.Template).filter_by(name="req-1", run_id=run_id).one()
        assert row.output == "template-output"


class TestGenerateSummary:
    """Validate _generate_summary computation."""

    def test_no_nodes_produces_summary_row_with_null_fields(self, output_session) -> None:
        """With no node data for the run, the summary row has null start/end."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())
        events_db._generate_summary(output_session, run_id)

        summary = output_session.query(schema.Summary).filter_by(run_id=run_id).one()
        assert summary.start is None
        assert summary.end is None

    def test_computes_cpu_minutes_and_node_usage_percentage(self, output_session) -> None:
        """CPU minutes and node usage percentage are computed from node/pod data."""
        run_id = events_db._populate_run_metadata(output_session, _run_metadata())

        output_session.add(
            schema.Node(
                name="node-1",
                run_id=run_id,
                cpu_capacity=4.0,
                created=0,
                deleted=600,
            )
        )
        output_session.add(
            schema.Pod(
                name="pod-1",
                run_id=run_id,
                cpu_requested=2.0,
                created=0,
                deleted=600,
            )
        )
        output_session.commit()

        events_db._generate_summary(output_session, run_id)

        summary = output_session.query(schema.Summary).filter_by(run_id=run_id).one()
        assert summary.cpu_minutes == pytest.approx(40.0)
        assert summary.percentage_node_usage == pytest.approx(50.0)
        assert summary.start == 0
        assert summary.end == 600


class TestTransformCommand:
    """Validate the `transform` click command end to end."""

    def test_missing_required_option_fails_fast(self) -> None:
        """Omitting a required option exits non-zero without touching any db."""
        runner = CliRunner()
        result = runner.invoke(events_db.run, ["transform", "sqlite:///unused.db"])
        assert result.exit_code != 0
        assert "input-db" in result.output or "Missing option" in result.output

    def test_full_transform_pipeline(self, tmp_path) -> None:
        """A full run migrates the output db, transforms, and summarizes data."""
        input_db_path = tmp_path / "input.db"
        output_db_path = tmp_path / "output.db"
        input_engine = sqlalchemy.create_engine(f"sqlite:///{input_db_path}")
        metadata = sqlalchemy.MetaData()
        events_table = sqlalchemy.Table(
            "events",
            metadata,
            sqlalchemy.Column("id", sqlalchemy.String),
            sqlalchemy.Column("timestamp", sqlalchemy.Integer),
            sqlalchemy.Column("category", sqlalchemy.String),
            sqlalchemy.Column("type", sqlalchemy.String),
            sqlalchemy.Column("value", sqlalchemy.String),
        )
        metadata.create_all(input_engine)
        with input_engine.begin() as conn:
            conn.execute(
                events_table.insert().values(
                    id="node-1", timestamp=0, category="node", type="created", value="0"
                )
            )
            conn.execute(
                events_table.insert().values(
                    id="node-1",
                    timestamp=600,
                    category="node",
                    type="cpu-capacity",
                    value="4.0",
                )
            )
        input_engine.dispose()

        runner = CliRunner()
        result = runner.invoke(
            events_db.run,
            [
                "transform",
                "--input-db",
                str(input_db_path),
                "--cluster",
                "test-cluster",
                "--platform",
                "eks",
                "--region",
                "us-east-1",
                "--namespace",
                "symphony",
                "--date",
                "2024-01-01-00-00",
                f"sqlite:///{output_db_path}",
            ],
        )

        assert result.exit_code == 0, result.output

        output_engine = sqlalchemy.create_engine(f"sqlite:///{output_db_path}")
        session = sessionmaker(bind=output_engine)()
        try:
            run_metadata = session.query(schema.RunMetadata).one()
            assert run_metadata.cluster == "test-cluster"
            node = session.query(schema.Node).filter_by(name="node-1").one()
            assert node.cpu_capacity == 4.0
        finally:
            session.close()
            output_engine.dispose()
