"""Generic document ingestion: PDF -> S3 -> text (Textract, pypdf fallback).

Used by procedure ingestion (procedures.py). MOCK_MODE: the PDF is written under
mock_data/ and text comes from pypdf.
"""
import io
import time
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError

import config

TEXTRACT_TIMEOUT_SECONDS = 60


class IngestError(Exception):
    pass


def utc_timestamp(epoch=None):
    """Sortable ISO-8601 UTC string with milliseconds, e.g. 2026-11-07T20:15:03.123Z"""
    dt = datetime.fromtimestamp(time.time() if epoch is None else epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def store_pdf(pdf_bytes, prefix):
    """Save the original PDF (S3, or mock_data/ in MOCK_MODE). Returns its key."""
    key = f"{prefix}{utc_timestamp()}.pdf"
    if config.MOCK_MODE:
        import store

        path = store.mock_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes)
    else:
        from aws_clients import s3

        s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf")
    return key


def _textract(key):
    """Async Textract on the S3 object (multi-page PDFs need the S3 path)."""
    from aws_clients import textract as tx

    job = tx.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": config.S3_BUCKET, "Name": key}})["JobId"]
    deadline = time.time() + TEXTRACT_TIMEOUT_SECONDS
    while True:
        resp = tx.get_document_text_detection(JobId=job)
        status = resp["JobStatus"]
        if status == "SUCCEEDED":
            break
        if status == "FAILED" or time.time() > deadline:
            raise IngestError(f"Textract job {status.lower() if status == 'FAILED' else 'timed out'}")
        time.sleep(1)
    lines = []
    while True:
        lines += [b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"]
        token = resp.get("NextToken")
        if not token:
            break
        resp = tx.get_document_text_detection(JobId=job, NextToken=token)
    return "\n".join(lines)


def pypdf_text(pdf_bytes):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(pdf_bytes, key):
    """Returns (text, method, warning)."""
    warning = None
    if not config.MOCK_MODE:
        try:
            text = _textract(key)
            if text.strip():
                return text, "Amazon Textract", None
            warning = "Textract returned no text; used pypdf instead."
        except (ClientError, BotoCoreError, IngestError) as e:
            code = e.response["Error"]["Code"] if isinstance(e, ClientError) else str(e)
            warning = f"Textract unavailable ({code}); used pypdf instead."
    try:
        text = pypdf_text(pdf_bytes)
    except Exception as e:
        raise IngestError(f"Could not read the PDF: {e}")
    if not text.strip():
        raise IngestError("No text found in the PDF (scanned image PDFs need Textract).")
    return text, "pypdf" + (" (mock mode)" if config.MOCK_MODE else ""), warning
