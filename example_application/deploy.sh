AWS_REGION=us-east-1 \
aws cloudformation deploy \
  --template-file ./template.yaml \
  --stack-name example-app-for-finops-bedrock-demo-ABC \
  --parameter-overrides InstanceType=t3.micro