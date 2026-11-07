"""Shared boto3 clients/resources. Credentials come from the standard AWS chain
(~/.aws/credentials, AWS_PROFILE, or env vars) - never from code."""
import boto3

import config

session = boto3.session.Session(region_name=config.AWS_REGION)

rekognition = session.client("rekognition")
s3 = session.client("s3")
sns = session.client("sns")
bedrock = session.client("bedrock-runtime")
polly = session.client("polly")
dynamodb = session.resource("dynamodb")

zones_table = dynamodb.Table(config.ZONES_TABLE)
violations_table = dynamodb.Table(config.VIOLATIONS_TABLE)
