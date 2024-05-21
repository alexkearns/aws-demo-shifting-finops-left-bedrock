import boto3
from aws_lambda_powertools.utilities.data_classes import event_source, BedrockAgentEvent
from aws_lambda_powertools.event_handler import BedrockAgentResolver
from datetime import datetime, timedelta

app = BedrockAgentResolver()
cloudformation = boto3.client("cloudformation")
cloudwatch = boto3.client("cloudwatch")


def get_metrics_for_resource(resource):
    type = resource["ResourceType"]
    physical_id = resource["PhysicalResourceId"]

    response = {}

    if type == "AWS::EC2::Instance":
        cpu_metrics = cloudwatch.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": physical_id}],
            StartTime=(datetime.now() - timedelta(days=7)).isoformat(),
            EndTime=datetime.now().isoformat(),
            Period=86400,
            Statistics=["Average"],
        )

        response[cpu_metrics["Label"]] = [
            {
                "timestamp": dp["Timestamp"].isoformat(),
                "value": dp["Average"],
                "unit": dp["Unit"],
            }
            for dp in cpu_metrics["Datapoints"]
        ]

    else:
        raise Exception("Unsupported resource type")

    return response


def get_resource_from_cloudformation_stack(stack_id, resource_id):
    response = cloudformation.describe_stack_resource(
        StackName=stack_id, LogicalResourceId=resource_id
    )

    return response["StackResourceDetail"]


@app.get(
    "/cloudwatch-metrics-by-cfn-resource",
    description="Get CloudWatch metrics for a resource given a CloudFormation stack ID and logical resource ID.",
)
def determine_metrics(stack_id, resource_id):
    resource = get_resource_from_cloudformation_stack(stack_id, resource_id)
    result = get_metrics_for_resource(resource)

    return {"metrics": result}


def lambda_handler(event: BedrockAgentEvent, context):
    return app.resolve(event, context)
