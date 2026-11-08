"""Persistence. Real mode: DynamoDB (labscribe-procedures) + S3. MOCK_MODE: local JSON
files in mock_data/ with the same record shapes, so the flow is testable offline."""
import json
import threading
from pathlib import Path

import config

MOCK_DIR = Path(__file__).parent / "mock_data"
_lock = threading.Lock()


def mock_path(key):
    return MOCK_DIR / key.replace(":", "-")  # keep filenames portable


def _mock_file(name):
    return MOCK_DIR / f"{name}.json"


def _mock_load(name):
    f = _mock_file(name)
    return json.loads(f.read_text()) if f.exists() else []


def _mock_save(name, items):
    MOCK_DIR.mkdir(exist_ok=True)
    _mock_file(name).write_text(json.dumps(items, indent=2))


class TableMissing(Exception):
    """The DynamoDB table hasn't been created yet (run python setup_aws.py)."""


def _table():
    from aws_clients import procedures_table

    return procedures_table


def _wrap(fn):
    from botocore.exceptions import ClientError

    try:
        return fn()
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise TableMissing(f"DynamoDB table {config.PROCEDURES_TABLE} doesn't exist yet. "
                               "Run: python setup_aws.py") from e
        raise


# ---------------- procedures ----------------
def save_procedure(proc):
    if config.MOCK_MODE:
        with _lock:
            items = [p for p in _mock_load("procedures") if p["procedure_id"] != proc["procedure_id"]]
            _mock_save("procedures", items + [proc])
        return
    _wrap(lambda: _table().put_item(Item=proc))


def get_procedure(procedure_id):
    if config.MOCK_MODE:
        with _lock:
            return next((p for p in _mock_load("procedures") if p["procedure_id"] == procedure_id), None)
    return _wrap(lambda: _table().get_item(Key={"procedure_id": procedure_id}).get("Item"))


def list_procedures():
    """Newest first."""
    if config.MOCK_MODE:
        with _lock:
            items = _mock_load("procedures")
    else:
        def scan():
            out, kwargs = [], {}
            while True:
                page = _table().scan(**kwargs)
                out += page.get("Items", [])
                if "LastEvaluatedKey" not in page:
                    return out
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

        items = _wrap(scan)
    return sorted(items, key=lambda p: p.get("approved_at", ""), reverse=True)


def delete_procedure(procedure_id):
    if config.MOCK_MODE:
        with _lock:
            _mock_save("procedures", [p for p in _mock_load("procedures") if p["procedure_id"] != procedure_id])
        return
    _wrap(lambda: _table().delete_item(Key={"procedure_id": procedure_id}))
