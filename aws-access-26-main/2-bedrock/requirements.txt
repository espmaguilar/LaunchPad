import boto3
from botocore.exceptions import ClientError
import os
from dotenv import load_dotenv

load_dotenv()

# Reads credentials set by the 1-setup tutorial
access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
session_token = os.getenv("AWS_SESSION_TOKEN")  # Required for Workshop Studio session credentials

client = boto3.client(
    service_name="bedrock-runtime",
    aws_access_key_id=access_key_id,
    aws_secret_access_key=secret_access_key,
    aws_session_token=session_token,
    region_name="us-east-1",
)

# The model or inference ID for the model you want to use
# !If you get an error with "Retry your request with the ID or ARN", use the inference ID version below

model_id = "us.meta.llama4-scout-17b-instruct-v1:0"  # Inference profile ID (requires us. prefix)

# Some models don't require an inference profile and use a plain Model ID instead:
# model_id = "amazon.titan-text-lite-v1"

# The message you want to send to the model
user_message = "Summarize AWS"

conversation = [
    {
        "role": "user",
        "content": [{"text": user_message}],
    }
]

try:
    response = client.converse(
        modelId=model_id,
        messages=conversation,
        inferenceConfig={"maxTokens": 512, "temperature": 0.5, "topP": 0.9},
    )

    response_actual = response["output"]["message"]["content"]

    # Some models provide reasoning context and lay out this JSON differently
    # Ex: reasoningContent comes after the main text response, so swap [0] and [1]
    # You can always debug by printing the full response
    # print(response_actual)

    if response_actual[0].get("reasoningContent"):
        reasoning_text = response_actual[0]["reasoningContent"]["reasoningText"]["text"]
        print("Reasoning:")
        print(reasoning_text)
        response_text = response_actual[1]["text"]
    else:
        response_text = response_actual[0]["text"]

    print("\nResponse:")
    print(response_text)
except Exception as e:
    print(f"ERROR: Can't invoke '{model_id}'. Reason: {e} (Exception type: {type(e).__name__})")
