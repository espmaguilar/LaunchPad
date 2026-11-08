"""Create SafeSight AWS resources. Safe to run repeatedly (idempotent).

Usage:
    python setup_aws.py --check    # read-only: show identity + what exists
    python setup_aws.py            # create anything missing, seed zones
"""
import argparse
import sys

from botocore.exceptions import ClientError, NoCredentialsError

import config
from aws_clients import dynamodb, s3, session, sns


def ok(msg):
    print(f"  [ok]   {msg}")


def todo(msg):
    print(f"  [todo] {msg}")


# ---------- S3 ----------
def bucket_exists(name):
    try:
        s3.head_bucket(Bucket=name)
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            return False
        if code in ("403", "AccessDenied"):
            sys.exit(f"Bucket '{name}' exists but belongs to another account. Pick a new S3_BUCKET.")
        raise


def create_bucket(name):
    if bucket_exists(name):
        ok(f"S3 bucket {name} exists")
        return
    kwargs = {"Bucket": name}
    if config.AWS_REGION != "us-east-1":  # us-east-1 rejects an explicit LocationConstraint
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": config.AWS_REGION}
    s3.create_bucket(**kwargs)
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    ok(f"S3 bucket {name} created (private)")


# ---------- DynamoDB ----------
def table_exists(name):
    try:
        dynamodb.meta.client.describe_table(TableName=name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def create_table(name, key_schema, attr_defs):
    if table_exists(name):
        ok(f"DynamoDB table {name} exists")
        return
    dynamodb.create_table(
        TableName=name,
        KeySchema=key_schema,
        AttributeDefinitions=attr_defs,
        BillingMode="PAY_PER_REQUEST",
    )
    print(f"  ...    waiting for {name} to become ACTIVE")
    dynamodb.meta.client.get_waiter("table_exists").wait(TableName=name)
    ok(f"DynamoDB table {name} created")


def seed_zones():
    table = dynamodb.Table(config.ZONES_TABLE)
    for z in config.DEFAULT_ZONES:
        table.put_item(Item={**z, "alert_email": config.ALERT_EMAIL})
    ok(f"seeded {len(config.DEFAULT_ZONES)} zones: {', '.join(z['zone_id'] for z in config.DEFAULT_ZONES)}")


# ---------- SNS ----------
def find_topic_arn():
    for page in sns.get_paginator("list_topics").paginate():
        for t in page["Topics"]:
            if t["TopicArn"].endswith(":" + config.SNS_TOPIC_NAME):
                return t["TopicArn"]
    return None


def email_subscription(topic_arn, email):
    for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn):
        for sub in page["Subscriptions"]:
            if sub["Protocol"] == "email" and sub["Endpoint"].lower() == email.lower():
                return sub["SubscriptionArn"]  # "PendingConfirmation" until clicked
    return None


def create_topic_and_subscribe():
    topic_arn = sns.create_topic(Name=config.SNS_TOPIC_NAME)["TopicArn"]  # idempotent
    ok(f"SNS topic {topic_arn}")
    sub = email_subscription(topic_arn, config.ALERT_EMAIL)
    if sub is None:
        sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=config.ALERT_EMAIL)
        todo(f"subscription email sent to {config.ALERT_EMAIL} - click 'Confirm subscription' in it")
    elif sub == "PendingConfirmation":
        todo(f"{config.ALERT_EMAIL} has not confirmed yet - check inbox/spam")
    else:
        ok(f"{config.ALERT_EMAIL} subscribed and confirmed")
    return topic_arn


# ---------- main ----------
def check():
    """Read-only report. Creates nothing."""
    print("\nResources:")
    print(f"  S3 bucket {config.S3_BUCKET}: {'exists' if bucket_exists(config.S3_BUCKET) else 'MISSING'}")
    for t in (config.ZONES_TABLE, config.VIOLATIONS_TABLE):
        print(f"  DynamoDB {t}: {'exists' if table_exists(t) else 'MISSING'}")
    arn = find_topic_arn()
    print(f"  SNS topic {config.SNS_TOPIC_NAME}: {arn or 'MISSING'}")
    if arn:
        sub = email_subscription(arn, config.ALERT_EMAIL)
        state = "not subscribed" if sub is None else ("pending confirmation" if sub == "PendingConfirmation" else "confirmed")
        print(f"  Email {config.ALERT_EMAIL}: {state}")


def check_services():
    """Probe Rekognition + Bedrock permissions. Read-only, costs fractions of a cent."""
    print("\nServices:")
    # Rekognition: a tiny invalid image. Any error except AccessDenied means we're allowed.
    try:
        session.client("rekognition").detect_protective_equipment(Image={"Bytes": b"not-an-image"})
        print("  Rekognition PPE: ok")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        denied = code in ("AccessDeniedException", "AccessDenied", "UnrecognizedClientException")
        print(f"  Rekognition PPE: {'DENIED - ' + code if denied else 'ok (permission granted)'}")

    # Which Claude models / inference profiles can this account see?
    bedrock_ctl = session.client("bedrock")
    try:
        models = bedrock_ctl.list_foundation_models(byProvider="Anthropic")["modelSummaries"]
        ids = sorted(m["modelId"] for m in models if "TEXT" in m.get("outputModalities", []))
        print(f"  Bedrock Anthropic models listed: {len(ids)}")
        for i in ids:
            print(f"    {i}")
    except ClientError as e:
        print(f"  Bedrock model list: {e.response['Error']['Code']} (can't list; will still try the call below)")
    try:
        profiles = bedrock_ctl.list_inference_profiles()["inferenceProfileSummaries"]
        claude = sorted(p["inferenceProfileId"] for p in profiles if "anthropic" in p["inferenceProfileId"])
        if claude:
            print("  Claude inference profiles:")
            for i in claude:
                print(f"    {i}")
    except ClientError:
        pass

    # Textract: tiny invalid document. Any error except AccessDenied means we're allowed.
    try:
        session.client("textract").detect_document_text(Document={"Bytes": b"not-a-document"})
        print("  Textract: ok")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        denied = code in ("AccessDeniedException", "AccessDenied", "UnrecognizedClientException")
        print(f"  Textract: {'DENIED - ' + code + ' (policy upload will use pypdf)' if denied else 'ok (permission granted)'}")

    # Polly: synthesize one tiny phrase with the configured neural voice.
    try:
        session.client("polly").synthesize_speech(
            Text="Check.", OutputFormat="mp3", VoiceId=config.POLLY_VOICE_ID, Engine=config.POLLY_ENGINE
        )["AudioStream"].read()
        print(f"  Polly ({config.POLLY_VOICE_ID}, {config.POLLY_ENGINE}): ok")
    except ClientError as e:
        print(f"  Polly ({config.POLLY_VOICE_ID}, {config.POLLY_ENGINE}): FAILED - {e.response['Error']['Code']} "
              f"(voice warnings will use the browser's speech instead)")

    if not config.BEDROCK_MODEL_ID:
        print("  Bedrock converse: skipped (BEDROCK_MODEL_ID empty -> template reports)")
        return
    try:
        session.client("bedrock-runtime").converse(
            modelId=config.BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
            inferenceConfig={"maxTokens": 2000},
        )
        print(f"  Bedrock converse with {config.BEDROCK_MODEL_ID}: ok")
    except ClientError as e:
        print(f"  Bedrock converse with {config.BEDROCK_MODEL_ID}: FAILED - "
              f"{e.response['Error']['Code']}: {e.response['Error'].get('Message', '')[:200]}")
        print("    -> set BEDROCK_MODEL_ID to one of the IDs/profiles listed above "
              "(reports fall back to a template until then)")

    check_vision(session)


def _test_png(size=64):
    """A small solid-grey PNG built in-process (no Pillow needed)."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def check_vision(session):
    """AI-vision rules need a model that accepts images: try BEDROCK_MODEL_ID, then the fallback."""
    client = session.client("bedrock-runtime")
    img = _test_png()
    for model in dict.fromkeys([config.BEDROCK_MODEL_ID, config.VISION_FALLBACK_MODEL_ID]):
        if not model:
            continue
        try:
            resp = client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [
                    {"image": {"format": "png", "source": {"bytes": img}}},
                    {"text": "What colour is this image? One word."}]}],
                inferenceConfig={"maxTokens": 20},
            )
            said = "".join(b.get("text", "") for b in resp["output"]["message"]["content"]).strip()[:40]
            role = "configured model" if model == config.BEDROCK_MODEL_ID else "fallback"
            print(f"  Bedrock vision with {model} ({role}): ok (said: {said!r})")
            if model != config.BEDROCK_MODEL_ID:
                print("    -> AI-vision rules will use the fallback model automatically")
            return
        except ClientError as e:
            print(f"  Bedrock vision with {model}: FAILED - {e.response['Error']['Code']}: "
                  f"{e.response['Error'].get('Message', '')[:160]}")
    print("    -> AI-vision rules will show 'Check failed' until a vision model is available "
          "(Rekognition gear checks are unaffected)")


def create():
    print("\nCreating (idempotent):")
    create_bucket(config.S3_BUCKET)
    create_table(
        config.ZONES_TABLE,
        [{"AttributeName": "zone_id", "KeyType": "HASH"}],
        [{"AttributeName": "zone_id", "AttributeType": "S"}],
    )
    create_table(
        config.VIOLATIONS_TABLE,
        [
            {"AttributeName": "zone_id", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        [
            {"AttributeName": "zone_id", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
    )
    seed_zones()
    topic_arn = create_topic_and_subscribe()
    if config.SNS_TOPIC_ARN != topic_arn:
        print(f"\n>>> Put this in your .env:\nSNS_TOPIC_ARN={topic_arn}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="read-only; create nothing")
    args = parser.parse_args()

    for name in ("S3_BUCKET", "ALERT_EMAIL"):
        if not getattr(config, name):
            sys.exit(f"{name} is empty. Copy .env.example to .env and fill it in.")

    try:
        ident = session.client("sts").get_caller_identity()
    except NoCredentialsError:
        sys.exit("No AWS credentials found. Configure ~/.aws/credentials or set AWS_PROFILE.")
    print(f"AWS account {ident['Account']} as {ident['Arn']} in {config.AWS_REGION}")

    if args.check:
        check()
        check_services()
    else:
        create()


if __name__ == "__main__":
    main()
