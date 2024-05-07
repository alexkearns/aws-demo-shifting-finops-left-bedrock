import base64
import boto3
import os
import json
import requests

GITHUB_API_TOKEN_SECRET_ARN = os.environ["GITHUB_API_TOKEN_SECRET_ARN"]
BEDROCK_FM_ARN = os.environ["BEDROCK_FM_ARN"]

bedrock = boto3.client("bedrock-runtime")


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
    github_api_token = get_github_webhook_secret(GITHUB_API_TOKEN_SECRET_ARN)

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    # Get tree structure
    sha = "a280b565ccd7a0c80bcbeaf158bfe0fc97753cef"
    url = f"https://api.github.com/repos/alexkearns/aws-demo-shifting-finops-left-bedrock/git/trees/{sha}?recursive=1"
    r = requests.get(url, headers=headers)
    tree = json.loads(r.text)
    files_in_tree = [f for f in tree["tree"] if f["type"] == "blob"]

    # Get files
    files = {}
    for file in files_in_tree:
        req = requests.get(file["url"], headers=headers)
        res = json.loads(req.text)
        content = base64.b64decode(res["content"])
        files[file["path"]] = content.decode("utf-8")

    # Get diff
    pr_number = event["number"]
    url = f"https://api.github.com/repos/alexkearns/aws-demo-shifting-finops-left-bedrock/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.diff+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    req = requests.get(url, headers=headers)
    res = req.text

    # Initialise prompt
    prompt = f"""Explain the pull request.

<pull-request-diff>
{res}
</pull-request-diff>

<repository-contents-at-HEAD>"""

    # Add tag for each file
    for file, content in files.items():
        prompt += f'''
<file name="{file}">
{content}
</file>'''

    # Close out prompt
    prompt += """
</repository-contents-at-HEAD>
"""

    bedrock_request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    bedrock_response = bedrock.invoke_model(
        body=json.dumps(bedrock_request_body),
        modelId=BEDROCK_FM_ARN
    )

    bedrock_res_body = json.loads(bedrock_response.get('body').read())

    content = bedrock_res_body.get("content", [])
    text = "\n\n".join([c["text"] for c in content if c["type"] == "text"])
    pr_comment = ":robot:  " + text

    url = f"https://api.github.com/repos/alexkearns/aws-demo-shifting-finops-left-bedrock/issues/{pr_number}/comments"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    pr_comment_data = json.dumps({
        "body": pr_comment
    })

    req = requests.post(url, data=pr_comment_data, headers=headers)

    if req.status_code == requests.codes.created:
        return {
            "status": "Success"
        }
    else:
        req.raise_for_status()