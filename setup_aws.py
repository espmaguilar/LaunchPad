"""Create LabScribe AWS resources. Safe to run repeatedly (idempotent).

Creates: the private S3 bucket (manual PDFs, snapshots) and the DynamoDB table
labscribe-procedures (PK procedure_id, on-demand billing).

Usage:
    python setup_aws.py --check    # read-only: identity, what exists, service permissions
    python setup_aws.py            # create anything missing
"""
import argparse
import sys

from botocore.exceptions import ClientError, NoCredentialsError

import config
from aws_clients import dynamodb, s3, session


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


# ---------- main ----------
def check():
    """Read-only report. Creates nothing."""
    print("\nResources:")
    print(f"  S3 bucket {config.S3_BUCKET}: {'exists' if bucket_exists(config.S3_BUCKET) else 'MISSING'}")
    t = config.PROCEDURES_TABLE
    print(f"  DynamoDB {t}: {'exists' if table_exists(t) else 'MISSING  -> run: python setup_aws.py'}")


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
        print("  Bedrock converse: skipped (BEDROCK_MODEL_ID empty -> steps must be entered by hand)")
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
              "(manual ingestion falls back to the numbered-list parser until then)")

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
    """The Scribe and PPE checks need a model that accepts images: BEDROCK_MODEL_ID, then the fallback."""
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
                print(f"    -> BEDROCK_MODEL_ID ({config.BEDROCK_MODEL_ID}) does NOT accept images; the Scribe and PPE "
                      "checks will use the fallback model automatically")
            return
        except ClientError as e:
            print(f"  Bedrock vision with {model}: FAILED - {e.response['Error']['Code']}: "
                  f"{e.response['Error'].get('Message', '')[:160]}")
    print("    -> NO vision model available: the Scribe and PPE checks won't work until BEDROCK_MODEL_ID "
          "(or VISION_FALLBACK_MODEL_ID) is a model that accepts images")


def create():
    print("\nCreating (idempotent):")
    create_bucket(config.S3_BUCKET)
    create_table(
        config.PROCEDURES_TABLE,
        [{"AttributeName": "procedure_id", "KeyType": "HASH"}],
        [{"AttributeName": "procedure_id", "AttributeType": "S"}],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="read-only; create nothing")
    args = parser.parse_args()

    for name in ("S3_BUCKET",):
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
