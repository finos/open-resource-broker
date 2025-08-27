from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from src.models.base_model import BaseModel

@dataclass
class LaunchTemplateVersion(BaseModel):
    launchTemplateId: str
    launchTemplateName: str
    versionNumber: int
    versionDescription: Optional[str]
    createTime: datetime
    createdBy: str
    defaultVersion: bool
    launchTemplateData: Dict[str, Any]
    additionalProperties: Dict[str, Any] = field(default_factory=dict)

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> 'LaunchTemplateVersion':
    #     return cls(
    #         launchTemplateId=data['LaunchTemplateId'],
    #         launchTemplateName=data['LaunchTemplateName'],
    #         versionNumber=data['VersionNumber'],
    #         versionDescription=data.get('VersionDescription'),
    #         createTime=datetime.fromisoformat(data['CreateTime']).replace(tzinfo=timezone.utc),
    #         createdBy=data['CreatedBy'],
    #         defaultVersion=data['DefaultVersion'],
    #         launchTemplateData=data['LaunchTemplateData']
    #     )

    # def to_dict(self) -> Dict[str, Any]:
    #     return {
    #         'LaunchTemplateId': self.launchTemplateId,
    #         'LaunchTemplateName': self.launchTemplateName,
    #         'VersionNumber': self.versionNumber,
    #         'VersionDescription': self.versionDescription,
    #         'CreateTime': self.createTime.isoformat(),
    #         'CreatedBy': self.createdBy,
    #         'DefaultVersion': self.defaultVersion,
    #         'LaunchTemplateData': self.launchTemplateData
    #     }

@dataclass
class LaunchTemplate:
    launchTemplateId: str
    launchTemplateName: str
    createTime: datetime
    createdBy: str
    defaultVersionNumber: int
    latestVersionNumber: int
    tags: Dict[str, str] = field(default_factory=dict)
    versions: List[LaunchTemplateVersion] = field(default_factory=list)

    # @classmethod
    # def from_dict(cls, data: Dict[str, Any]) -> 'LaunchTemplate':
    #     template = cls(
    #         launchTemplateId=data['LaunchTemplateId'],
    #         launchTemplateName=data['LaunchTemplateName'],
    #         createTime=datetime.fromisoformat(data['CreateTime']).replace(tzinfo=timezone.utc),
    #         createdBy=data['CreatedBy'],
    #         defaultVersionNumber=data['DefaultVersionNumber'],
    #         latestVersionNumber=data['LatestVersionNumber'],
    #         tags={tag['Key']: tag['Value'] for tag in data.get('Tags', [])}
    #     )
    #     if 'Versions' in data:
    #         template.versions = [LaunchTemplateVersion.from_dict(v) for v in data['Versions']]
    #     return template

    # def to_dict(self) -> Dict[str, Any]:
    #     return {
    #         'LaunchTemplateId': self.launchTemplateId,
    #         'LaunchTemplateName': self.launchTemplateName,
    #         'CreateTime': self.createTime.isoformat(),
    #         'CreatedBy': self.createdBy,
    #         'DefaultVersionNumber': self.defaultVersionNumber,
    #         'LatestVersionNumber': self.latestVersionNumber,
    #         'Tags': [{'Key': k, 'Value': v} for k, v in self.tags.items()],
    #         'Versions': [v.to_dict() for v in self.versions]
    #     }

    def add_version(self, version: LaunchTemplateVersion) -> None:
        self.versions.append(version)
        self.latestVersionNumber = max(self.latestVersionNumber, version.versionNumber)
        if version.defaultVersion:
            self.defaultVersionNumber = version.versionNumber

    def get_version(self, version_number: int) -> Optional[LaunchTemplateVersion]:
        for version in self.versions:
            if version.versionNumber == version_number:
                return version
        return None

    def get_default_version(self) -> Optional[LaunchTemplateVersion]:
        return self.get_version(self.defaultVersionNumber)

    def get_latest_version(self) -> Optional[LaunchTemplateVersion]:
        return self.get_version(self.latestVersionNumber)

    @classmethod
    def from_describe_launch_templates(cls, data: Dict[str, Any]) -> 'LaunchTemplate':
        return cls.from_dict(data)

    @classmethod
    def from_paginated_describe_launch_templates(cls, paginator: Any) -> List['LaunchTemplate']:
        templates = []
        for page in paginator:
            for template_data in page.get('LaunchTemplates', []):
                templates.append(cls.from_describe_launch_templates(template_data))
        return templates

    def __str__(self) -> str:
        return f"LaunchTemplate(id={self.launchTemplateId}, name={self.launchTemplateName}, default_version={self.defaultVersionNumber}, latest_version={self.latestVersionNumber})"
