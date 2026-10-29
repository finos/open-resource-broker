"""AWS identifier patterns and tag limits.

Standard-library-only leaf module. Domain value objects validate against these
values directly and the configuration models use them as field defaults, so the
domain layer never has to import the configuration layer.
"""

PATTERNS: dict[str, str] = {
    "subnet": r"^subnet-[0-9a-f]{8,17}$",
    "security_group": r"^sg-[0-9a-f]{8,17}$",
    "ec2_instance": r"^i-[0-9a-f]{8,17}$",
    "ami": r"^(ami-[0-9a-f]{8,17}|/aws/service/.+)$",
    "ec2_fleet": r"^fleet-[0-9a-f]{8,17}$",
    "launch_template": r"^lt-[0-9a-f]{8,17}$",
    "instance_type": r"^[a-z][0-9]+[a-z]*\.[a-z0-9]+$",
    "tag_key": r"^[a-zA-Z0-9\s\._:/=+\-@]{1,128}$",
    "arn": r"^arn:aws:[a-zA-Z0-9\-]+:[a-zA-Z0-9\-]*:[0-9]{12}:.+$",
    "account_id": r"^\d{12}$",
    "region": r"^[a-z]{2}-[a-z]+-\d$",
}

TAG_KEY_LENGTH = 128
TAG_VALUE_LENGTH = 256
MAX_TAGS_PER_RESOURCE = 50
MAX_SECURITY_GROUPS = 5
MAX_SUBNETS = 16
