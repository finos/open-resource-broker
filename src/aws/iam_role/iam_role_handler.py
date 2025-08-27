import boto3
import json
from typing import Any, Dict, List
from botocore.exceptions import ClientError
from src.helpers.logger import setup_logging

logger = setup_logging()


class IAMRoleHandler:
    """
    Handler for managing IAM roles for AWS operations (e.g., Spot Fleet roles).
    """

    def __init__(self, region_name: str):
        self.iam_client = boto3.client("iam", region_name=region_name)

    def create_spot_fleet_role(self, role_name: str) -> str:
        """
        Create an IAM role for Spot Fleet.

        :param role_name: The name of the role to create.
        :return: The ARN of the created role.
        """
        try:
            trust_policy = {
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "spotfleet.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }

            response = self.iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy)
            )

            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn="arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
            )

            logger.info(f"Created Spot Fleet role '{role_name}' successfully.")
            return response['Role']['Arn']

        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                logger.warning(f"Role '{role_name}' already exists. Retrieving ARN...")
                return self.get_role_arn(role_name)
            else:
                logger.error(f"Failed to create Spot Fleet role '{role_name}': {e}")
                raise

    def get_role_arn(self, role_name: str) -> str:
        """
        Retrieve the ARN of an existing IAM role.

        :param role_name: The name of the role to retrieve.
        :return: The ARN of the role.
        """
        try:
            response = self.iam_client.get_role(RoleName=role_name)
            logger.info(f"Retrieved ARN for role '{role_name}'.")
            return response['Role']['Arn']
        except ClientError as e:
            logger.error(f"Failed to retrieve ARN for role '{role_name}': {e}")
            raise ValueError(f"Role {role_name} not found: {str(e)}")

    def delete_role(self, role_name: str) -> None:
        """
        Delete an IAM role.

        :param role_name: The name of the role to delete.
        """
        try:
            # First, detach all policies
            attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
            for policy in attached_policies.get('AttachedPolicies', []):
                self.iam_client.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy['PolicyArn']
                )
                logger.info(f"Detached policy {policy['PolicyArn']} from role '{role_name}'.")

            # Then delete the role
            self.iam_client.delete_role(RoleName=role_name)
            logger.info(f"Deleted role '{role_name}' successfully.")
        except ClientError as e:
            logger.error(f"Failed to delete role '{role_name}': {e}")
            raise

    def list_roles(self) -> List[Dict[str, Any]]:
        """
        List all IAM roles in the account.

        :return: A list of dictionaries containing role information.
        """
        try:
            paginator = self.iam_client.get_paginator('list_roles')
            roles = []
            for page in paginator.paginate():
                roles.extend(page['Roles'])
            logger.info(f"Retrieved {len(roles)} IAM roles.")
            return roles
        except ClientError as e:
            logger.error(f"Failed to list IAM roles: {e}")
            raise

    def attach_policy_to_role(self, role_name: str, policy_arn: str) -> None:
        """
        Attach an IAM policy to a role.

        :param role_name: The name of the role to attach the policy to.
        :param policy_arn: The ARN of the policy to attach.
        """
        try:
            self.iam_client.attach_role_policy(
                RoleName=role_name,
                PolicyArn=policy_arn
            )
            logger.info(f"Attached policy {policy_arn} to role '{role_name}'.")
        except ClientError as e:
            logger.error(f"Failed to attach policy {policy_arn} to role '{role_name}': {e}")
            raise

    def detach_policy_from_role(self, role_name: str, policy_arn: str) -> None:
        """
        Detach an IAM policy from a role.

        :param role_name: The name of the role to detach the policy from.
        :param policy_arn: The ARN of the policy to detach.
        """
        try:
            self.iam_client.detach_role_policy(
                RoleName=role_name,
                PolicyArn=policy_arn
            )
            logger.info(f"Detached policy {policy_arn} from role '{role_name}'.")
        except ClientError as e:
            logger.error(f"Failed to detach policy {policy_arn} from role '{role_name}': {e}")
            raise
