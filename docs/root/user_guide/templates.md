# Template Management

Templates define the configuration for virtual machines that can be provisioned through the Host Factory Plugin. This guide covers template creation, configuration, and management.

## Template Overview

Templates specify:
- **Provider API**: Which AWS service to use for provisioning
- **Instance Configuration**: VM type, image, networking
- **Resource Limits**: Maximum number of instances
- **Symphony Attributes**: CPU, memory, and other resource specifications
- **Advanced Settings**: Storage, user data, IAM roles, and tags

## Template Structure

### Basic Template Structure

```json
{
  "template_id": "unique-template-name",
  "provider_api": "RunInstances|SpotFleet|EC2Fleet|ASG",
  "max_number": 10,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncores": ["Numeric", "2"],
    "ncpus": ["Numeric", "1"],
    "nram": ["Numeric", "4096"]
  },
  "image_id": "ami-12345678",
  "vm_type": "t3.medium",
  "subnet_id": "subnet-12345678",
  "security_group_ids": ["sg-12345678"],
  "key_name": "your-ssh-key"
}
```

### Provider API Types

The `provider_api` field determines which AWS service is used:

| Provider API | AWS Service | Use Case |
|--------------|-------------|----------|
| `RunInstances` | EC2 Run Instances | Simple on-demand instances |
| `SpotFleet` | EC2 Spot Fleet | Cost-optimized spot instances |
| `EC2Fleet` | EC2 Fleet | Mixed instance types and pricing |
| `ASG` | Auto Scaling Groups | Auto-scaling capabilities |

## Template Types and Examples

### 1. On-Demand Template (RunInstances)

Simple on-demand instances for consistent workloads:

```json
{
  "template_id": "SymphonyOnDemand",
  "provider_api": "RunInstances",
  "max_number": 5,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncores": ["Numeric", "2"],
    "ncpus": ["Numeric", "1"],
    "nram": ["Numeric", "4096"]
  },
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "subnet_id": "subnet-0f984e48e6e899311",
  "vm_type": "t2.micro",
  "key_name": "HF_SSH_KEY",
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "ondemand",
  "root_device_volume_size": 20,
  "volume_type": "gp3",
  "user_data_script": "#!/bin/bash\necho 'Symphony template example' > /tmp/symphony_test.log",
  "instance_profile": "SymphonyInstanceProfile",
  "instance_tags": {
    "Name": "SymphonyInstance",
    "Environment": "Development",
    "Project": "Symphony"
  }
}
```

### 2. Spot Fleet Template (SpotFleet)

Cost-optimized spot instances with multiple instance types:

```json
{
  "template_id": "SymphonySpot",
  "provider_api": "SpotFleet",
  "max_number": 10,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncores": ["Numeric", "4"],
    "ncpus": ["Numeric", "2"],
    "nram": ["Numeric", "8192"]
  },
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7g8h9"],
  "vm_types": {
    "t2.medium": 1,
    "m5.large": 2,
    "c5.large": 3
  },
  "key_name": "HF_SSH_KEY",
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "spot",
  "fleet_role": "arn:aws:iam::123456789012:role/AWSServiceRoleForEC2SpotFleet",
  "max_spot_price": 0.05,
  "allocation_strategy": "lowestPrice",
  "spot_fleet_request_expiry": 3600,
  "root_device_volume_size": 30,
  "volume_type": "gp3",
  "user_data_script": "#!/bin/bash\necho 'Symphony spot fleet example' > /tmp/symphony_spot_test.log",
  "instance_profile": "SymphonySpotInstanceProfile",
  "instance_tags": {
    "Name": "SymphonySpotInstance",
    "Environment": "Development",
    "Project": "Symphony"
  }
}
```

### 3. Heterogeneous Fleet Template (EC2Fleet)

Mixed pricing and instance types for optimal cost and performance:

```json
{
  "template_id": "SymphonyHeterogeneous",
  "provider_api": "EC2Fleet",
  "max_number": 20,
  "attributes": {
    "type": ["String", "X86_64"],
    "ncores": ["Numeric", "8"],
    "ncpus": ["Numeric", "4"],
    "nram": ["Numeric", "16384"]
  },
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7g8h9"],
  "vm_types": {
    "c5.xlarge": 2,
    "r5.xlarge": 3
  },
  "vm_types_on_demand": {
    "m5.xlarge": 1
  },
  "vm_types_priority": {
    "c5.xlarge": 1,
    "r5.xlarge": 2,
    "m5.xlarge": 3
  },
  "key_name": "HF_SSH_KEY",
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "heterogeneous",
  "percent_on_demand": 30,
  "allocation_strategy": "lowestPrice",
  "allocation_strategy_on_demand": "prioritized",
  "pools_count": 3,
  "root_device_volume_size": 50,
  "volume_type": "gp3",
  "iops": 3000,
  "user_data_script": "#!/bin/bash\necho 'Symphony heterogeneous fleet example' > /tmp/symphony_heterogeneous_test.log",
  "instance_profile": "SymphonyHeterogeneousInstanceProfile",
  "instance_tags": {
    "Name": "SymphonyHeterogeneousInstance",
    "Environment": "Development",
    "Project": "Symphony"
  }
}
```

## Template Configuration Fields

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `template_id` | string | Unique template identifier | `"web-server-template"` |
| `provider_api` | string | AWS service to use | `"RunInstances"` |
| `max_number` | integer | Maximum instances | `10` |
| `attributes` | object | Symphony resource attributes | See attributes section |
| `image_id` | string | AMI ID or parameter | `"ami-12345678"` |
| `vm_type` | string | Instance type (single) | `"t3.medium"` |

### Networking Fields

| Field | Type | Description | Used By | Example |
|-------|------|-------------|---------|---------|
| `subnet_id` | string | Single subnet (RunInstances only) | RunInstances | `"subnet-12345678"` |
| `subnet_ids` | array | Multiple subnets (Fleet APIs) | SpotFleet, EC2Fleet | `["subnet-123", "subnet-456"]` |
| `security_group_ids` | array | Security group IDs | All APIs | `["sg-12345678"]` |
| `key_name` | string | SSH key pair name | All APIs | `"my-key-pair"` |

**Important**: 
- **RunInstances** uses `subnet_id` (singular) for single subnet
- **Fleet APIs** (SpotFleet, EC2Fleet) use `subnet_ids` (plural) for multiple subnets
- **ASG** uses launch template configuration for networking

### Instance Configuration

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `vm_types` | object | Multiple instance types with weights | `{"t3.medium": 1, "t3.large": 2}` |
| `vm_types_on_demand` | object | On-demand instance types | `{"m5.large": 1}` |
| `vm_types_priority` | object | Instance type priorities | `{"t3.medium": 1, "t3.large": 2}` |
| `price_type` | string | Pricing model | `"ondemand"`, `"spot"`, `"heterogeneous"` |

### Storage Configuration

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `root_device_volume_size` | integer | Root volume size (GB) | `20` |
| `volume_type` | string | EBS volume type | `"gp3"`, `"gp2"`, `"io1"` |
| `iops` | integer | Provisioned IOPS (io1/gp3) | `3000` |
| `throughput` | integer | Throughput (gp3) | `125` |
| `encrypted` | boolean | Enable encryption | `true` |

### Advanced Configuration

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `user_data_script` | string | Instance initialization script | `"#!/bin/bash\necho 'Hello' > /tmp/test"` |
| `instance_profile` | string | IAM instance profile | `"MyInstanceProfile"` |
| `instance_tags` | object | Instance tags | `{"Environment": "prod"}` |
| `monitoring` | boolean | Enable detailed monitoring | `true` |
| `ebs_optimized` | boolean | Enable EBS optimization | `true` |

### Fleet-Specific Configuration

#### Spot Fleet Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `fleet_role` | string | Spot fleet IAM role | `"arn:aws:iam::123:role/SpotFleetRole"` |
| `max_spot_price` | number | Maximum spot price | `0.05` |
| `allocation_strategy` | string | Allocation strategy | `"lowestPrice"`, `"diversified"` |
| `spot_fleet_request_expiry` | integer | Request expiry (seconds) | `3600` |

#### EC2 Fleet Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `percent_on_demand` | integer | Percentage on-demand | `30` |
| `allocation_strategy_on_demand` | string | On-demand allocation | `"prioritized"` |
| `pools_count` | integer | Number of spot pools | `3` |

## Symphony Attributes

Symphony attributes define the resource characteristics visible to the scheduler:

### Standard Attributes

```json
{
  "attributes": {
    "type": ["String", "X86_64"],
    "ncores": ["Numeric", "4"],
    "ncpus": ["Numeric", "2"],
    "nram": ["Numeric", "8192"],
    "storage": ["Numeric", "100"],
    "network": ["String", "high"],
    "gpu": ["Numeric", "0"]
  }
}
```

### Attribute Types

| Attribute | Type | Description | Example Values |
|-----------|------|-------------|----------------|
| `type` | String | Architecture type | `"X86_64"`, `"ARM64"` |
| `ncores` | Numeric | Number of CPU cores | `2`, `4`, `8` |
| `ncpus` | Numeric | Number of CPUs | `1`, `2` |
| `nram` | Numeric | Memory in MB | `1024`, `4096`, `8192` |
| `storage` | Numeric | Storage in GB | `20`, `100`, `500` |
| `network` | String | Network performance | `"low"`, `"moderate"`, `"high"` |
| `gpu` | Numeric | Number of GPUs | `0`, `1`, `4` |

## Template Management

### Creating Templates

#### Method 1: Configuration File

Create templates in `config/templates.json`:

```json
{
  "templates": [
    {
      "template_id": "my-template",
      "provider_api": "RunInstances",
      "max_number": 5,
      "attributes": {
        "type": ["String", "X86_64"],
        "ncores": ["Numeric", "2"],
        "ncpus": ["Numeric", "1"],
        "nram": ["Numeric", "4096"]
      },
      "image_id": "ami-12345678",
      "vm_type": "t3.medium",
      "subnet_id": "subnet-12345678",
      "security_group_ids": ["sg-12345678"]
    }
  ]
}
```

#### Method 2: SSM Parameters

Store templates in AWS Systems Manager Parameter Store:

```bash
# Store template in SSM
aws ssm put-parameter \
  --name "/hostfactory/templates/my-template" \
  --type "String" \
  --value '{"template_id": "my-template", ...}'

# Configure SSM prefix in config.json
{
  "template": {
    "ssm_parameter_prefix": "/hostfactory/templates/"
  }
}
```

### Validating Templates

```bash
# Test template validation
python run.py getAvailableTemplates

# Validate specific template
echo '{"template_id": "my-template"}' | python run.py getAvailableTemplates --data '{}'
```

### Template Testing

```bash
# Test template provisioning
echo '{
  "template_id": "my-template",
  "machine_count": 1
}' | python run.py requestMachines --data '{}'

# Check request status
python run.py getRequestStatus --request-id req-12345678-1234-1234-1234-123456789012
```

## Best Practices

### Template Design

1. **Use Descriptive IDs**: Template IDs should clearly indicate their purpose
2. **Set Appropriate Limits**: Configure `max_number` based on your capacity needs
3. **Choose Right Provider API**: Select the most appropriate AWS service for your use case
4. **Configure Networking**: Ensure subnets and security groups are properly configured
5. **Add Monitoring**: Enable detailed monitoring for production templates

### Resource Management

1. **Instance Types**: Choose instance types that match your workload requirements
2. **Storage Configuration**: Configure appropriate storage for your applications
3. **IAM Roles**: Use IAM instance profiles for secure access to AWS services
4. **Tags**: Add comprehensive tags for cost tracking and management
5. **User Data**: Use user data scripts for instance initialization

### Cost Optimization

1. **Spot Instances**: Use spot instances for fault-tolerant workloads
2. **Mixed Fleets**: Use heterogeneous fleets to balance cost and availability
3. **Right-Sizing**: Choose appropriate instance types for your workload
4. **Storage Optimization**: Use gp3 volumes for better price/performance
5. **Reserved Capacity**: Consider reserved instances for predictable workloads

## Troubleshooting Templates

### Common Issues

#### Template Not Found
```bash
# Check available templates using command line
python run.py getAvailableTemplates

# Check template configuration file
cat config/templates.json | python -m json.tool

# Verify template ID exists in configuration
grep -r "template_id" config/
```

#### Invalid Configuration
```bash
# Validate JSON syntax
python -m json.tool config/templates.json

# Check template structure using command line
python run.py getAvailableTemplates | python -m json.tool

# List all template IDs
python run.py getAvailableTemplates | grep -o '"template_id": "[^"]*"'
```

#### AWS Resource Issues
```bash
# Verify AMI exists
aws ec2 describe-images --image-ids ami-12345678

# Check subnet availability
aws ec2 describe-subnets --subnet-ids subnet-12345678

# Verify security group
aws ec2 describe-security-groups --group-ids sg-12345678
```

## Next Steps

- **[Requests](requests.md)**: Learn how to request machines using templates
- **[Configuration](configuration.md)**: Configure template defaults and settings
- **[Deployment](deployment.md)**: Deploy templates in production environments
- **[Monitoring](monitoring.md)**: Monitor template usage and performance
