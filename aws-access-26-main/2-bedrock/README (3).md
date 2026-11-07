import boto3
import os

# Reads credentials set by the 1-setup tutorial
client = boto3.client(
    service_name="bedrock-runtime",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
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
    streaming_response = client.converse_stream(
        modelId=model_id,
        messages=conversation,
        inferenceConfig={"maxTokens": 512, "temperature": 0.5, "topP": 0.9},
    )

    # Extract and print the streamed response text in real-time
    for chunk in streaming_response["stream"]:
        if "contentBlockDelta" in chunk:
            delta = chunk["contentBlockDelta"]["delta"]

            if delta.get("text"):
                print(delta["text"], end="")
            elif delta.get("reasoningContent") and delta["reasoningContent"].get("text"):
                print(f"Reasoning: {delta['reasoningContent']['text']}")

except Exception as e:
    print(f"ERROR: Can't invoke '{model_id}'. Reason: {e} (Exception type: {type(e).__name__})")
