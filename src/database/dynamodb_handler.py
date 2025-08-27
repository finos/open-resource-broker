import boto3
from typing import Any, Dict, Optional, List
from .database_handler_interface import BaseDatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class DynamoDBHandler(BaseDatabaseHandler):
    def __init__(self, region_name: str = "us-east-1"):
        self.dynamodb = boto3.resource('dynamodb', region_name=region_name)

    def insert(self, table: str, key: str, value: Dict[str, Any]) -> None:
        table = self.dynamodb.Table(table)
        value['id'] = key  # Ensure the key is part of the item
        table.put_item(Item=value)

    def get(self, table: str, key: str) -> Optional[Dict[str, Any]]:
        table = self.dynamodb.Table(table)
        response = table.get_item(Key={'id': key})
        return response.get('Item')

    def update(self, table: str, key: str, value: Dict[str, Any]) -> None:
        table = self.dynamodb.Table(table)
        value['id'] = key  # Ensure the key is part of the item
        table.put_item(Item=value)

    def delete(self, table: str, key: str) -> None:
        table = self.dynamodb.Table(table)
        table.delete_item(Key={'id': key})

    def query(self, table: str, conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
        table = self.dynamodb.Table(table)
        filter_expression = " AND ".join([f"{k} = :{k}" for k in conditions.keys()])
        expression_values = {f":{k}": v for k, v in conditions.items()}
        
        response = table.scan(
            FilterExpression=filter_expression,
            ExpressionAttributeValues=expression_values
        )
        return response['Items']

    def scan(self, table: str) -> List[Dict[str, Any]]:
        table = self.dynamodb.Table(table)
        response = table.scan()
        return response['Items']
