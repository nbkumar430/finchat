"""Optional backup/restore of SQLite chat DB to Google Cloud Storage."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)


def restore_chat_db_if_configured(settings: Settings) -> bool:
    """Download SQLite file from GCS if bucket/object configured and policy says so."""
    bucket = settings.gcs_chat_db_bucket.strip()
    obj = settings.gcs_chat_db_object.strip()
    if not bucket or not obj:
        return False

    if not settings.restore_chat_db_from_gcs and os.path.isfile(settings.chat_sqlite_path):
        logger.info("Chat DB file exists; skip GCS restore (set RESTORE_CHAT_DB_FROM_GCS=true to force).")
        return False

    try:
        from google.cloud import storage
    except ImportError:
        logger.warning("google-cloud-storage not installed; cannot restore chat DB from GCS")
        return False

    client = storage.Client(project=settings.gcp_project_id)
    b = client.bucket(bucket)
    blob = b.blob(obj)
    if not blob.exists():
        logger.info("GCS object gs://%s/%s not found; starting fresh SQLite", bucket, obj)
        return False

    path = settings.chat_sqlite_path
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    blob.download_to_filename(path)
    logger.info("Restored chat DB from gs://%s/%s → %s", bucket, obj, path)
    return True


def backup_chat_db_if_configured(settings: Settings) -> None:
    """Upload SQLite file to GCS on shutdown (best-effort)."""
    if not settings.backup_chat_db_on_shutdown:
        return
    bucket = settings.gcs_chat_db_bucket.strip()
    obj = settings.gcs_chat_db_object.strip()
    if not bucket or not obj:
        return

    path = settings.chat_sqlite_path
    if not os.path.isfile(path):
        logger.info("No chat DB file at %s; skip GCS backup", path)
        return

    try:
        from google.cloud import storage
    except ImportError:
        logger.warning("google-cloud-storage not installed; cannot backup chat DB to GCS")
        return

    try:
        client = storage.Client(project=settings.gcp_project_id)
        b = client.bucket(bucket)
        blob = b.blob(obj)
        blob.upload_from_filename(path)
        logger.info("Backed up chat DB to gs://%s/%s", bucket, obj)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GCS chat DB backup failed (non-fatal): %s", exc)
