"""Shared boto3 clients/resources. Credentials come from the standard AWS chain
(~/.aws/credentials, AWS_PROFILE, or env vars) - never from code."""
import boto3

import config

session = boto3.session.Session(region_name=config.AWS_REGION)

rekognition = session.client("rekognition")
s3 = session.client("s3")
bedrock = session.client("bedrock-runtime")
polly = session.client("polly")
textract = session.client("textract")
dynamodb = session.resource("dynamodb")

procedures_table = dynamodb.Table(config.PROCEDURES_TABLE)
