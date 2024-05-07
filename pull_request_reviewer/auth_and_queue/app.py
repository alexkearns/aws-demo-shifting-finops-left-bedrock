import json
import hashlib
import hmac
import os
import requests
import boto3

GITHUB_WEBHOOK_SECRET_ARN = os.environ["GITHUB_WEBHOOK_SECRET_ARN"]
QUEUE_URL = os.environ["QUEUE_URL"]

sqs = boto3.resource('sqs')
queue = sqs.Queue(QUEUE_URL)


def get_github_webhook_secret(secret_id):
    """
    Gets the secret using the AWS Secrets Manager Extension.

    Args:
        secret_id (str): The secret ID
    Returns:
        str: The secret
    """
    headers = {"X-Aws-Parameters-Secrets-Token": os.environ.get('AWS_SESSION_TOKEN')}
    secrets_extension_endpoint = "http://localhost:2773" + \
                                 "/secretsmanager/get?secretId=" + \
                                 f"{secret_id}"

    r = requests.get(secrets_extension_endpoint, headers=headers)

    secret = json.loads(r.text)["SecretString"]
    return secret


def lambda_handler(event, context):
    print(event)

    # Get the GitHub signature header
    headers = event['headers']
    github_signature_header = headers.get('X-Hub-Signature-256', None)

    # Verify the signature header was present
    if not github_signature_header:
        return {
            "statusCode": 401,
            "body": json.dumps({
                "message": "Unauthorized - signature header not present",
            })
        }

    # Calculate the expected signature for the event body
    github_webhook_secret = get_github_webhook_secret(GITHUB_WEBHOOK_SECRET_ARN)
    hash_object = hmac.new(
        github_webhook_secret.encode('utf-8'),
        msg=event['body'].encode('utf-8'),
        digestmod=hashlib.sha256
    )
    expected_signature = "sha256=" + hash_object.hexdigest()

    # Compare the expected signature with the signature header
    if not hmac.compare_digest(expected_signature, github_signature_header):
        return {
            "statusCode": 403,
            "body": json.dumps({
                "message": "Unauthorized - signatures do not match",
            })
        }

    body = json.loads(event['body'])
    action = body['action']

    if action != "opened":
        return {
            "statusCode": 202,
            "body": json.dumps({
                "message": "Webhook processed",
            })
        }

    queue.send_message(MessageBody=event['body'])

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Webhook processed",
        })
    }
