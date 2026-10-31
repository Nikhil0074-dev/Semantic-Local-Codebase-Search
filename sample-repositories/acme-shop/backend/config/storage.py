"""Object storage (uploads, invoices) settings, read from the environment."""
import os

DEFAULT_REGION = "eu-west-1"


def load_storage_settings() -> dict:
    """Collect cloud storage configuration: bucket, region and optional endpoint override."""
    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise RuntimeError("S3_BUCKET_NAME is not set")
    return {
        "bucket": bucket,
        "region": os.environ.get("CLOUD_STORAGE_REGION", DEFAULT_REGION),
        "endpoint_url": os.environ.get("STORAGE_ENDPOINT_URL"),
    }
