import base64
import re
import boto3
import os
import json
import requests
from aws_lambda_powertools.utilities.data_classes import event_source, SQSEvent

GITHUB_API_TOKEN_SECRET_ARN = os.environ["GITHUB_API_TOKEN_SECRET_ARN"]
BEDROCK_AGENT_ALIAS_ID = os.environ["BEDROCK_AGENT_ALIAS_ID"]
BEDROCK_AGENT_ID = os.environ["BEDROCK_AGENT_ID"]
BEDROCK_FM_ARN = os.environ["BEDROCK_FM_ARN"]

bedrock = boto3.client("bedrock-runtime")
bedrock_agents = boto3.client("bedrock-agent-runtime")


def get_github_webhook_secret(secret_id):
    """
    Gets the secret using the AWS Secrets Manager Extension.

    Args:
        secret_id (str): The secret ID
    Returns:
        str: The secret
    """
    headers = {"X-Aws-Parameters-Secrets-Token": os.environ.get("AWS_SESSION_TOKEN")}
    secrets_extension_endpoint = (
        "http://localhost:2773" + "/secretsmanager/get?secretId=" + f"{secret_id}"
    )

    r = requests.get(secrets_extension_endpoint, headers=headers)

    secret = json.loads(r.text)["SecretString"]
    return secret


def handle_event(event, message_id):
    event = json.loads(event)

    github_api_token = get_github_webhook_secret(GITHUB_API_TOKEN_SECRET_ARN)

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get tree structure
    sha = event["pull_request"]["head"]["sha"]
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
        "X-GitHub-Api-Version": "2022-11-28",
    }
    diff_req = requests.get(url, headers=headers)
    diff_res = diff_req.text

    headers = {
        "Accept": "application/vnd.github.raw+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    desc_req = requests.get(url, headers=headers)
    desc_res = json.loads(desc_req.text)["body"]

    output_example = json.dumps(
        {
            "changes": [
                {
                    "summary": "A security group rule is changed to allow port 33060 instead of port 3306.",
                    "resource": "MySecurityGroup",
                    "justification": "The database is now running on port 33060.",
                }
            ]
        },
        indent=2,
    )

    prompt = f"""
Carry out the following instructions step by step, outputting it to <thinking> tags, and provide your final answer in JSON format within <answer> tags.

1. Determine the the AWS resources that would be changed be as a result of the diff. Use the key in the template's `Resources` object that defines the resource as the resource name.

2. Summarise the changes being made to each resource according to the Git diff. Focus on the resource changes, rather than template changes. Each change being made to a resource should be output as an object in the `changes` list in the answer. In the summary you must include details of the before and after state of the change. Add context to this by including the name of the resource that is changing. The summary of the change should be in the `summary` key of the JSON object, the name of the resource should be in the `resource` key.

3. For each resource being changed, add information about the justification for the change.

Here is an example of the format of the output.

<output-example>
{output_example}
</output-example>

<pull-request-description>
{desc_res}
</pull-request-description>

<pull-request-diff>
{diff_res}
</pull-request-diff>

<repository-contents>"""

    # Add tag for each file
    for file, content in files.items():
        prompt += f"""
<file name="{file}">
{content}
</file>"""

    # Close out prompt
    prompt += """
</repository-contents>
"""

    # Invoke the foundation model
    bedrock_request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "top_k": 100,
    }
    bedrock_response = bedrock.invoke_model(
        body=json.dumps(bedrock_request_body), modelId=BEDROCK_FM_ARN
    )
    bedrock_res_body = json.loads(bedrock_response.get("body").read())
    content = bedrock_res_body.get("content", [])
    text = "\n\n".join([c["text"] for c in content if c["type"] == "text"])

    # Find the output from the foundation model within the <answer> tags
    match = re.search(r".*<answer>((.|\n)*?)</answer>.*", text)
    if not match:
        raise Exception("No answer found in response from foundation model.")
    fm_answer = match.group(1)

    # Build up the prompt for the knowledge base
    kb_prompt = f"""Make a judgement as to whether the proposed changes are likely to be cost effective. Think about it carefully. Consider whether the resource specification is currently sufficient. Judge whether the change would be cost-efficient. Additionally, consider alternative, more cost efficient ways to achieve what is being proposed. 

When recommending alternatives, be aware that Auto Scaling refers to adding more instances rather than automatically changing the size of instance. 

<changes>
{fm_answer}
</changes>
"""

    # Invoke the knowledge base
    agent_response = bedrock_agents.invoke_agent(
        agentId=BEDROCK_AGENT_ID,
        agentAliasId=BEDROCK_AGENT_ALIAS_ID,
        inputText=kb_prompt,
        sessionId=message_id,
    )
    completion = ""
    for event in agent_response.get("completion"):
        chunk = event["chunk"]
        completion += chunk["bytes"].decode()

    pr_comment = ":robot: Amazon Bedrock Response :robot:\n\n" + completion

    url = f"https://api.github.com/repos/alexkearns/aws-demo-shifting-finops-left-bedrock/issues/{pr_number}/comments"
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "Authorization": f"Bearer {github_api_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pr_comment_data = json.dumps({"body": pr_comment})

    req = requests.post(url, data=pr_comment_data, headers=headers)

    if req.status_code != requests.codes.created:
        req.raise_for_status()


@event_source(data_class=SQSEvent)
def lambda_handler(event: SQSEvent, context):
    for record in event.records:
        handle_event(record.body, record.message_id)
