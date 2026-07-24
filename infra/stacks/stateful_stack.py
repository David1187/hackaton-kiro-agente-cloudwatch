"""StatefulStack — DynamoDB table for the Todo CRUD API.

Separated from stateless resources (Lambdas, API Gateway) so that the table
can survive Lambda redeployments and be managed independently.
"""
import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct


class StatefulStack(cdk.Stack):
    """Stack containing stateful resources (DynamoDB table)."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, termination_protection=False, **kwargs)

        self.table = dynamodb.Table(
            self,
            "TasksTable",
            partition_key=dynamodb.Attribute(
                name="task_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
