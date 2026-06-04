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

Schema validator for hf templates file
"""

import logging

from pydantic import BaseModel
from pydantic import Field
from pydantic import FilePath

logger = logging.getLogger(__name__)


class Attributes(BaseModel):
    """HF Template Attributes"""

    nram: list[str]
    ncpus: list[str]
    ncores: list[str] | None = None
    machine_type: list[str] = Field(alias="type")


class HFTemplate(BaseModel):
    """HF Template"""

    template_id: str = Field(alias="templateId")
    max_number: int = Field(alias="maxNumber")
    attributes: Attributes
    pod_spec: FilePath = Field(alias="podSpec")


class HFTemplates(BaseModel):
    """HF Templates"""

    templates: list[HFTemplate]


def validate(data: dict) -> HFTemplates:
    """Validate the data"""
    return HFTemplates(**data)
