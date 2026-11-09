# awsprov_templates.json reference

The host template configuration file for Amazon Web Services (AWS) lists all the templates that host factory can use to provision AWS hosts. This file must be configured with the correct values from your AWS environment. Configure the template file to provision On-Demand instances, Spot instances, or a mix of On-Demand and Spot instances.

Each host template represents a class of instances that share some attributes, such as hardware configuration, installed software stack, operating system, and security settings.

## Location

- %HF_TOP%\%HF_VERSION%\providerplugins\aws\sampleconf\ on Windows.
- $HF_TOP/$HF_VERSION/providerplugins/aws/sampleconf/ on Linux®.

## Parameters

- **priceType**

  - Required. Pricing type of EC2 instances to be requested. Valid values are `ondemand` for On-Demand instances, `spot` for Spot instances, and `heterogeneous` for a mix of On-Demand and Spot instances.
  - For the heterogeneous option, a Spot Fleet request is submitted to provision Spot instances for the requested capacity. If the Spot Fleet request partially fulfills the requested capacity, the Spot Fleet request is canceled near the expiration time and a new request is submitted to provision On-Demand instances for the remaining capacity. The number of On-Demand instances can vary depending on the number Spot Instances provisioned by the first request.
  - Default is `ondemand`.

- **templateId**

  - Required. Unique name for the template. Valid value is a string of up to 64 characters, which can contain the following characters: 0-9, A-Z, a-z, -, and _.
  - Tip: Include a keyword such as `Spot` or `OnDemand` in the template ID to easily identify instances of different EC2 pricing on the cluster management console.

- **maxNumber**

  - Required. Maximum number of instances of this template that can be provisioned. Valid value is an integer in the range -1 - 10000. Use -1 to indicate an unlimited number of instances, up to a maximum of 10,000, and 0 to indicate that no instances of this template must be provisioned.
  - When the priceType parameter is set to `spot` or `heterogeneous`, `maxNumber` indicates the maximum number of *units* that can be provisioned. In this case, instances with a larger weight account for more units compared to instances with a smaller weight.
  - Note: AWS Spot Fleets can overprovision resources, because of which the maxNumber can be exceeded for templates with priceType set to spot or heterogeneous.

- **attributes**

  - Required.
    - **type**: Required. Type of the VM (string). Supported values are any EGO_MACHINE_TYPE, including X64_64 and NTX64.
    - **ncpus**: Required. Number of CPUs per host (numeric). Valid value is a positive integer higher than 0. For EC2 instance types with hyperthreading enabled, define the ncpus to be half of the vCPU value (IBM® Spectrum Symphony does not consider hyperthreads). For example, when r4.8xlarge provides 32 vCPUs, define ncpus as 16.
    - **nram**: Required. Available RAM (MB). Valid value is a positive integer higher than 0.
    - **rank**: Optional. For more information about configuring this rank parameter, see Selecting cost-based and rank-based cloud host topic.
    - **priceInfo**: Optional. For more information about configuring this priceInfo parameter and its attributes, see Selecting cost-based and rank-based cloud host topic.

- **imageId**

  - Required. Amazon Machine Image (AMI) ID that is used to launch EC2 instances. This AMI ID can be an image of an AWS instance with IBM Spectrum Symphony preinstalled or a base OS image provided by AWS.

- **subnetId**

  - Required. ID of the subnet (in the desired VPC) into which the instances are launched.
  - When priceType is set to `ondemand`, valid value is a single string ID. If you specify multiple subnets, only the first subnet ID in the list is used.
  - When priceType is set to `spot` or `heterogeneous`, valid value is a single string ID or a comma-separated array of strings.

- **vmType** or **vmTypes**

  - Required. EC2 instance type to be provisioned.
  - When priceType is set to `ondemand`, use `vmType` to specify a single string ID of the EC2 instance type to be provisioned.
  - When priceType is set to `spot` or `heterogeneous`, use `vmTypes` to specify a list of comma-separated key-value pairs. In each `key:value` pair, `key` is a string that represents the instance type and `value` is an integer that represents the weighted capacity of the number of units provided by the specified instance type. The weighted value must be an integer greater than 1, and cannot be a decimal value.

- **vmTypesOnDemand**

  - Optional. When priceType is set to `heterogeneous`, specify a list of comma-separated `key:value` strings, each of which represents the model of the EC2 instance type to be provisioned. If vmTypesOnDemand is set, the keys are used by the second spot fleet request instead of vmTypes.

- **vmTypesPriority**

  - Optional. When priceType is set to `spot` or `heterogeneous`, specify a list of comma-separated `key:value` strings, each of which defines the priority of the EC2 instance type to be provisioned.

- **rootDeviceVolumeSize**

  - Optional. For EBS-backed AMIs, size (in GiB) of the EBS root device volume for EC2 On-Demand and Spot instances launched from the template. Valid value is an integer in the range 1 – 16384 (16 TiB). Default is the root device volume size of the AMI used for provisioning instances.

- **volumeType**

  - Optional. For EBS-backed AMIs, type of device volume for EC2 On-Demand and Spot instances launched from the template. Valid value is `gp2`, `gp3`, `io1`, `io2`, and `standard`. Default is `gp2`.

- **iops**

  - Optional. For EBS-backed AMIs, the number of I/O operations per second (IOPS) for EC2 On-Demand and Spot instances launched from the template. This iops parameter requires the volumeType to be set at `io1` or `io2`. Valid value is an integer in the range 100 to 64000.

- **instanceTags**

  - Optional. Tags that must be added to provisioned instances (On-Demand, Spot, or both) to more easily organize and identify them (for example, by purpose, owner, or environment). Valid value is a string of one or more tags, where each tag is a key-value pair in the format `key=value`. Multiple tags are separated by a semicolon (;).

- **keyName**

  - Optional. Name of the Amazon EC2 key pair that can be used to connect to the launched EC2 instance.

- **securityGroupIds**

  - Optional. A list of strings for AWS security groups that are applied to the instances.

- **fleetRole**

  - Required when priceType is set to `spot` or `heterogeneous`. The role that grants permissions to launch and terminate Spot Fleet instances on behalf of the designated IAM user.

- **maxSpotPrice**

  - Optional. When priceType is set to `spot` or `heterogeneous`, maximum price that you are willing to pay for this Spot unit.

- **spotFleetRequestExpiry**

  - Optional. When priceType is set to `spot` or `heterogeneous`, time (in minutes) after which an unfulfilled Spot Fleet request is canceled. Valid value is an integer in the range specified by AWS. Default is 30 minutes.

- **allocationStrategy**

  - Optional. Allocation strategy for Spot pools. The valid values are:
    - `capacityOptimized`
    - `capacityOptimizedPrioritized`
    - `diversified`
    - `lowestPrice`
    - `priceCapacityOptimized`

    Default is `priceCapacityOptimized` for every priceType, from the AWS provider defaults (`provider_defaults.aws.template_defaults.allocation_strategy`); a provider instance's `template_defaults` can override it.

    Templates written for earlier releases that set an On-Demand strategy here (`prioritized`, or `lowestPrice` with priceType `ondemand`) keep working: the value is applied as allocationStrategyOnDemand and a deprecation warning is logged. Move it to allocationStrategyOnDemand. Any other invalid value makes the template invalid, which `templates show` and `templates validate` report.

- **allocationStrategyOnDemand**

  - Optional. Allocation strategy for On-Demand pools, for every priceType. Valid values are `lowestPrice` or `prioritized`. Default is `lowestPrice`, from the AWS provider defaults (`provider_defaults.aws.template_defaults.allocation_strategy_on_demand`); a provider instance's `template_defaults` can override it.

- **percentOnDemand**

  - Optional. When priceType is set to `spot`, specifies the percentage of On-Demand capacity to be requested in a Spot Fleet request to meet the target capacity. Valid value is an integer from 0 to 100. Default value is 0.

- **poolsCount**

  - Optional. When priceType is set to `spot` or `heterogeneous` and allocationStrategy is set to `lowestPrice`, specify the number of Spot instance pools to use. Valid value is an integer higher than 0.

- **instanceProfile**

  - Optional. An IAM instance profile to assign to the provisioned instance.

- **userDataScript**

  - Optional but recommended. Full path to a post-provisioning script, which is included in the instance and is run during instance startup.

- **launchTemplateId**

  - Optional. An administrator can create a launch template for launching an Amazon EC2 instance when priceType is set to `spot` or `heterogeneous`. Specify the ID of that pre-created launch template for this launchTemplateId parameter.

---

## Example: On-Demand Template

``
{
  "templateId": "Template-VM-SYMsmall",
  "maxNumber": 20,
  "attributes": {
    "type": "X86_64",
    "ncpus": 1,
    "nram": 1024,
    "rank": 0,
    "priceInfo": "price:0.1,billingTimeUnitType:prorated_hour,billingTimeUnitNumber:1,billingRoundoffType:unit"
  },
  "imageId": "ami-f09fbde7",
  "rootDeviceVolumeSize": 100,
  "instanceTags": "company=abc;project=awscloud;team=xyz",
  "subnetId": "subnet-20069069",
  "vmType": "t2.micro",
  "keyName": "Sym-Key",
  "instanceProfile": "arn:aws:iam::123456789013:instance-profile/myRole",
  "securityGroupIds": ["sg-541fdc29"],
  "userDataScript": "/opt/ibm/spectrumcomputing/hostfactory/1.1/providers/aws/postprovision/fresh_install.sh",
  "priceType": "ondemand"
}
``

---

## Example: Spot Template

``
{
  "templateId": "SpotTemplate-VM-SYM",
  "maxNumber": 10,
  "attributes": {
    "type": "X86_64",
    "ncpus": 2,
    "nram": 4096
  },
  "imageId": "ami-01e24be29428c15b2",
  "rootDeviceVolumeSize": 100,
  "instanceTags": "company=abc;project=awscloudspot;team=xyz",
  "subnetId": ["subnet-12a34b56", "subnet-123ab456789cdef01"],
  "vmTypes": {
    "t2.medium": 1,
    "a1.xlarge": 2
  },
  "keyName": "Sym-Key",
  "priceType": "spot",
  "allocationStrategy": "lowestPrice",
  "instanceProfile": "arn:aws:iam::123456789012:instance-profile/myRole",
  "spotFleetRequestExpiry": 40,
  "poolsCount": 1,
  "fleetRole": "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
  "securityGroupIds": ["sg-e0d4b999"],
  "userDataScript": "/opt/ibm/spectrumcomputing/hostfactory/1.1/providers/aws/postprovision/fresh_install.sh"
}
``

---

## Example: Heterogeneous Template

``
{
  "templateId": "HeteroTemplate-VM-SYM",
  "maxNumber": 10,
  "attributes": {
    "type": "X86_64",
    "ncpus": 2,
    "nram": 4096
  },
  "imageId": "ami-01e24be29428c15b2",
  "rootDeviceVolumeSize": 100,
  "instanceTags": "company=abc;project=awscloudspot;team=xyz",
  "subnetId": ["subnet-12a34b56", "subnet-123ab456789cdef01"],
  "vmTypes": {
    "t2.medium": 1,
    "a1.xlarge": 2
  },
  "vmTypesOnDemand": {
    "t2.medium": 1
  },
  "keyName": "Sym-Key",
  "priceType": "heterogeneous",
  "allocationStrategy": "lowestPrice",
  "instanceProfile": "arn:aws:iam::123456789012:instance-profile/myRole",
  "spotFleetRequestExpiry": 40,
  "poolsCount": 1,
  "fleetRole": "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
  "securityGroupIds": ["sg-e0d4b999"],
  "userDataScript": "/opt/ibm/spectrumcomputing/hostfactory/1.1/providers/aws/postprovision/fresh_install.sh"
}
``

---

## Notes

- Use `vmType` for On-Demand, `vmTypes` for Spot or Heterogeneous templates.
- `subnetId` can be a string or an array of strings.
- `instanceProfile` is the ARN of the IAM profile.
- `userDataScript` can be a Windows batch file or a Linux shell script.
- The `attributes` object is required and must include at least `type`, `ncpus`, and `nram`.
- Spot fleet fields (`allocationStrategy`, `spotFleetRequestExpiry`, `poolsCount`, `fleetRole`) are only relevant for spot pricing templates.

---

## References

- [IBM Spectrum Symphony 7.3.2 awsprov.templates.json Documentation](https://www.ibm.com/docs/en/spectrum-symphony/7.3.2?topic=factory-awsprov-templatesjson)
- [IBM Spectrum Symphony 7.3.1 awsprov.templates.json Reference](https://www.ibm.com/docs/en/spectrum-symphony/7.3.1?topic=reference-awsprov-templatesjson)