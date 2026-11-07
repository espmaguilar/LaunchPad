"""Persistence + alerts. Real mode: S3, DynamoDB, SNS. MOCK_MODE: local files in
mock_data/ with the same record shapes, so the full flow is testable offline."""
import json
import threading
from pathlib import Path

import config

MOCK_DIR = Path(__file__).parent / "mock_data"
_mock_lock = threading.Lock()


def image_key(zone_id, timestamp):
    return f"violations/{zone_id}/{timestamp}.jpg"


# ---------------- mock backend ----------------
def _mock_path(key):
    return MOCK_DIR / key.replace(":", "-")  # keep filenames portable


def _mock_load():
    f = MOCK_DIR / "violations.json"
    return json.loads(f.read_text()) if f.exists() else []


def _mock_save(items):
    MOCK_DIR.mkdir(exist_ok=True)
    (MOCK_DIR / "violations.json").write_text(json.dumps(items, indent=2))


# ---------------- public API ----------------
def save_violation(item, image_bytes):
    """Upload the snapshot, then write the violation record."""
    if config.MOCK_MODE:
        with _mock_lock:
            path = _mock_path(item["image_key"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_bytes)
            items = _mock_load()
            items.append(item)
            _mock_save(items)
        return

    from aws_clients import s3, violations_table

    s3.put_object(
        Bucket=config.S3_BUCKET,
        Key=item["image_key"],
        Body=image_bytes,
        ContentType="image/jpeg",
    )
    violations_table.put_item(Item=item)


def update_violation(zone_id, timestamp, **fields):
    """Set fields on one violation record."""
    if config.MOCK_MODE:
        with _mock_lock:
            items = _mock_load()
            for it in items:
                if it["zone_id"] == zone_id and it["timestamp"] == timestamp:
                    it.update(fields)
            _mock_save(items)
        return

    from aws_clients import violations_table

    names = {f"#k{i}": k for i, k in enumerate(fields)}  # placeholders: "status"/"report" are reserved words
    values = {f":v{i}": v for i, v in enumerate(fields.values())}
    violations_table.update_item(
        Key={"zone_id": zone_id, "timestamp": timestamp},
        UpdateExpression="SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(fields))),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def set_report(zone_id, timestamp, report_json, report_status):
    update_violation(zone_id, timestamp, report=report_json, report_status=report_status)


def get_violation(zone_id, timestamp):
    if config.MOCK_MODE:
        with _mock_lock:
            return next((it for it in _mock_load()
                         if it["zone_id"] == zone_id and it["timestamp"] == timestamp), None)
    from aws_clients import violations_table

    return violations_table.get_item(Key={"zone_id": zone_id, "timestamp": timestamp}).get("Item")


def image_location(key):
    """Mock: local file path. Real: short-lived presigned S3 URL (bucket stays private)."""
    if config.MOCK_MODE:
        return _mock_path(key)
    from aws_clients import s3

    return s3.generate_presigned_url(
        "get_object", Params={"Bucket": config.S3_BUCKET, "Key": key}, ExpiresIn=300
    )


def send_alert(subject, message):
    if config.MOCK_MODE:
        MOCK_DIR.mkdir(exist_ok=True)
        with _mock_lock, open(MOCK_DIR / "alerts.log", "a") as f:
            f.write(f"=== {subject}\n{message}\n\n")
        print(f"[MOCK SNS] {subject}")
        return

    from aws_clients import sns

    sns.publish(TopicArn=config.SNS_TOPIC_ARN, Subject=subject[:100], Message=message)


def list_violations(limit=50):
    """Most recent first. limit=None returns everything."""
    if config.MOCK_MODE:
        with _mock_lock:
            items = _mock_load()
    else:
        from aws_clients import violations_table

        items, kwargs = [], {}
        while True:  # small table; a scan is fine for the demo
            page = violations_table.scan(**kwargs)
            items += page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda it: it["timestamp"], reverse=True)
    return items if limit is None else items[:limit]
