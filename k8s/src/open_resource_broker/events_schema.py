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

Events db transformation schema.
"""

import sqlalchemy
from sqlalchemy import Column
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""


class RunMetadata(Base):
    """Metadata for the run table."""

    __tablename__ = "run_metadata"
    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    region = Column(String, nullable=False)
    namespace = Column(String, nullable=False)
    date = Column(String, nullable=False)

    __table_args__ = (
        sqlalchemy.UniqueConstraint("cluster", "platform", "namespace", "date"),
    )


class Container(Base):
    """Container table to store container statuses."""

    __tablename__ = "container"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    pod_id = Column(String, nullable=True)
    ready = Column(Integer, nullable=True)
    started = Column(Integer, nullable=True)
    scheduled = Column(Integer, nullable=True)
    run_id = Column(Integer, nullable=False)

    __table_args__ = (
        sqlalchemy.UniqueConstraint("pod_id", "name"),
        sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),
    )


class EventsModel(Base):
    """Events Base model for all event tables."""

    __abstract__ = True
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    run_id = Column(Integer, nullable=False)


class Pod(EventsModel):
    """Pod table to store pod statuses."""

    __tablename__ = "pod"
    request_id = Column(String, nullable=True)
    return_id = Column(String, nullable=True)
    template_id = Column(String, nullable=True)
    cpu_requested = Column(Float, nullable=True)
    cpu_limit = Column(Float, nullable=True)
    memory_requested = Column(Float, nullable=True)
    memory_limit = Column(Float, nullable=True)
    node_name = Column(String, nullable=True)
    node_id = Column(String, nullable=True)
    requested = Column(Integer, nullable=True)
    pending = Column(Integer, nullable=True)
    created = Column(Integer, nullable=True)
    scheduled = Column(Integer, nullable=True)
    running = Column(Integer, nullable=True)
    ready = Column(Integer, nullable=True)
    returned = Column(Integer, nullable=True)
    deleted = Column(Integer, nullable=True)
    failed = Column(Integer, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)


class Node(EventsModel):
    """Node table to store node statuses."""

    __tablename__ = "node"
    conditions = Column(String, nullable=True)
    cpu_capacity = Column(Float, nullable=True)
    cpu_allocatable = Column(Float, nullable=True)
    memory_capacity = Column(Float, nullable=True)
    memory_allocatable = Column(Float, nullable=True)
    cpu_reserved = Column(Float, nullable=True)
    memory_reserved = Column(Float, nullable=True)
    eviction_empty = Column(Integer, nullable=True)
    zone = Column(String, nullable=True)
    region = Column(String, nullable=True)
    node_size = Column(String, nullable=True)
    capacity_type = Column(String, nullable=True)
    created = Column(Integer, nullable=True)
    ready = Column(Integer, nullable=True)
    deleted = Column(Integer, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)


class Template(EventsModel):
    """Template table to store pod templates."""

    __tablename__ = "template"
    output = Column(String, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)


class Request(EventsModel):
    """Request table to store pod requests."""

    __tablename__ = "request"
    count = Column(Integer, nullable=True)
    template_id = Column(String, nullable=True)
    running = Column(Integer, nullable=True)
    complete = Column(Integer, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)


class Return(EventsModel):
    """Return table to store pod return statuses."""

    __tablename__ = "return"
    count = Column(Integer, nullable=True)
    running = Column(Integer, nullable=True)
    complete = Column(Integer, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)


class Summary(Base):
    """Summary table to store summary information."""

    __tablename__ = "summary"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, nullable=False)
    start = Column(Integer, nullable=True)
    end = Column(Integer, nullable=True)
    cpu_minutes = Column(Float, nullable=True)
    percentage_node_usage = Column(Float, nullable=True)

    __table_args__ = (sqlalchemy.ForeignKeyConstraint(["run_id"], ["run_metadata.id"]),)
