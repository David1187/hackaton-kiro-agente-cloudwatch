#!/usr/bin/env python3
"""CDK Application entrypoint — deploys the Todo CRUD API and Self-Healing Agent.

Stacks are split by state and domain:
  - StatefulStack: DynamoDB table (stateful, survives Lambda redeployments)
  - StatelessStack: Lambdas, API Gateway, observability (can be torn down safely)
  - AgentStack: Self-Healing Agent (AgentCore Runtime, Gateway, EventBridge)
"""
import aws_cdk as cdk

from infra.stacks.agent_stack import AgentStack
from infra.stacks.stateful_stack import StatefulStack
from infra.stacks.stateless_stack import StatelessStack

app = cdk.App()

# Transversal tags applied at App level (propagated to all resources)
cdk.Tags.of(app).add("Project", "hackaton-kiro-agente-cloudwatch")
cdk.Tags.of(app).add("Environment", "hackathon")
cdk.Tags.of(app).add("ManagedBy", "cdk")

env = cdk.Environment(region="eu-west-1")

stateful = StatefulStack(app, "TodoCrudStatefulStack", env=env)

stateless = StatelessStack(
    app,
    "TodoCrudStatelessStack",
    table=stateful.table,
    env=env,
)

AgentStack(
    app,
    "SelfHealingAgentStack",
    alarm_arns=stateless.alarm_arns,
    alarm_names=stateless.alarm_names,
    log_group_names=stateless.log_group_names,
    env=env,
)

app.synth()
