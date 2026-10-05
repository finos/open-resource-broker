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

Unit tests for the events db schema (orb.k8s_legacy.events_schema).

These exercise the schema against a real in-memory SQLite database --
table creation, inserts, unique constraints, and foreign key enforcement
-- rather than mocking the ORM layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orb.k8s_legacy import events_schema as schema


@pytest.fixture()
def engine():
    """A fresh in-memory SQLite engine with foreign keys enforced."""
    eng = create_engine("sqlite:///:memory:")

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    schema.Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as sess:
        yield sess


def _make_run(session) -> schema.RunMetadata:
    run = schema.RunMetadata(
        cluster="c1",
        platform="p1",
        region="r1",
        namespace="ns1",
        date="2026-01-01",
    )
    session.add(run)
    session.commit()
    return run


@pytest.mark.unit
class TestSchemaMetadata:
    def test_all_tables_registered(self):
        table_names = set(schema.Base.metadata.tables.keys())
        assert table_names == {
            "run_metadata",
            "container",
            "pod",
            "node",
            "template",
            "request",
            "return",
            "summary",
        }

    def test_events_model_is_abstract_and_not_a_table(self):
        assert "__abstract__" not in schema.Base.metadata.tables
        with pytest.raises(AttributeError):
            schema.EventsModel.__tablename__  # noqa: B018


@pytest.mark.unit
class TestRunMetadata:
    def test_insert_and_query_round_trip(self, session):
        run = _make_run(session)
        assert run.id is not None

        fetched = session.get(schema.RunMetadata, run.id)
        assert fetched is not None
        assert fetched.cluster == "c1"
        assert fetched.platform == "p1"
        assert fetched.region == "r1"
        assert fetched.namespace == "ns1"
        assert fetched.date == "2026-01-01"

    def test_unique_constraint_rejects_duplicate(self, session):
        _make_run(session)
        dup = schema.RunMetadata(
            cluster="c1",
            platform="p1",
            region="r1",
            namespace="ns1",
            date="2026-01-01",
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_unique_constraint_allows_different_date(self, session):
        _make_run(session)
        other = schema.RunMetadata(
            cluster="c1",
            platform="p1",
            region="r1",
            namespace="ns1",
            date="2026-01-02",
        )
        session.add(other)
        session.commit()  # must not raise
        assert other.id is not None

    def test_missing_required_field_raises(self, session):
        bad = schema.RunMetadata(
            cluster="c1",
            platform="p1",
            region="r1",
            namespace="ns1",
            date=None,
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.unit
class TestContainer:
    def test_unique_constraint_on_pod_and_name(self, session):
        run = _make_run(session)
        c1 = schema.Container(name="app", pod_id="pod-1", run_id=run.id)
        session.add(c1)
        session.commit()

        c2 = schema.Container(name="app", pod_id="pod-1", run_id=run.id)
        session.add(c2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_allows_same_name_different_pod(self, session):
        run = _make_run(session)
        c1 = schema.Container(name="app", pod_id="pod-1", run_id=run.id)
        c2 = schema.Container(name="app", pod_id="pod-2", run_id=run.id)
        session.add_all([c1, c2])
        session.commit()  # must not raise
        assert c1.id != c2.id

    def test_foreign_key_rejects_unknown_run_id(self, session):
        orphan = schema.Container(name="app", pod_id="pod-x", run_id=999)
        session.add(orphan)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.unit
class TestEventsModelSubclasses:
    @pytest.mark.parametrize(
        "model_cls,extra_kwargs",
        [
            (schema.Pod, {"request_id": "req-1"}),
            (schema.Node, {"zone": "us-east-1a"}),
            (schema.Template, {"output": "yaml-output"}),
            (schema.Request, {"count": 3}),
            (schema.Return, {"count": 1}),
        ],
    )
    def test_insert_and_query_subclass(self, session, model_cls, extra_kwargs):
        run = _make_run(session)
        instance = model_cls(name="evt", run_id=run.id, **extra_kwargs)
        session.add(instance)
        session.commit()

        fetched = session.get(model_cls, instance.id)
        assert fetched is not None
        assert fetched.name == "evt"
        assert fetched.run_id == run.id

    def test_subclass_rejects_unknown_run_id(self, session):
        orphan = schema.Pod(name="evt", run_id=12345)
        session.add(orphan)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_pod_specific_columns_round_trip(self, session):
        run = _make_run(session)
        pod = schema.Pod(
            name="evt",
            run_id=run.id,
            request_id="req-1",
            return_id="ret-1",
            template_id="tmpl-1",
            cpu_requested=1.5,
            cpu_limit=2.0,
            memory_requested=512.0,
            memory_limit=1024.0,
            node_name="node-a",
            node_id="node-id-a",
            requested=1,
            pending=0,
            created=1,
            scheduled=1,
            running=1,
            ready=1,
            returned=0,
            deleted=0,
            failed=0,
        )
        session.add(pod)
        session.commit()

        fetched = session.get(schema.Pod, pod.id)
        assert fetched.cpu_requested == 1.5
        assert fetched.memory_limit == 1024.0
        assert fetched.node_name == "node-a"


@pytest.mark.unit
class TestSummary:
    def test_insert_and_query(self, session):
        run = _make_run(session)
        summary = schema.Summary(
            run_id=run.id,
            start=0,
            end=100,
            cpu_minutes=12.5,
            percentage_node_usage=0.75,
        )
        session.add(summary)
        session.commit()

        fetched = session.get(schema.Summary, summary.id)
        assert fetched.cpu_minutes == 12.5
        assert fetched.percentage_node_usage == 0.75

    def test_foreign_key_rejects_unknown_run_id(self, session):
        orphan = schema.Summary(run_id=424242, start=0, end=1)
        session.add(orphan)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.unit
class TestTableColumns:
    def test_run_metadata_columns(self):
        columns = {c.name for c in schema.RunMetadata.__table__.columns}
        assert columns == {"id", "cluster", "platform", "region", "namespace", "date"}

    def test_pod_table_name(self):
        assert schema.Pod.__tablename__ == "pod"

    def test_node_table_name(self):
        assert schema.Node.__tablename__ == "node"

    def test_inspect_reports_expected_tables(self, engine):
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == {
            "run_metadata",
            "container",
            "pod",
            "node",
            "template",
            "request",
            "return",
            "summary",
        }
