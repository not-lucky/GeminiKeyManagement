"""
Functions for interacting with Google Cloud Platform APIs.
"""

import logging
from datetime import datetime, timezone
from google.cloud import service_usage_v1, api_keys_v2
from google.api_core import exceptions as google_exceptions
from . import config, exceptions


def enable_api(project_id, credentials, dry_run=False):
    """Manages Generative Language API enablement with error handling.

    Args:
        project_id (str): Target GCP project ID
        credentials (Credentials): Authenticated credentials
        dry_run (bool): Simulation mode flag

    Returns:
        bool: True if enabled successfully

    Raises:
        TermsOfServiceNotAcceptedError: When required ToS not accepted
    """
    service_name = config.GENERATIVE_LANGUAGE_API
    service_path = f"projects/{project_id}/services/{service_name}"
    service_usage_client = service_usage_v1.ServiceUsageClient(credentials=credentials)

    try:
        logging.info(
            f"  Attempting to enable Generative Language API for project {project_id}..."
        )
        if dry_run:
            logging.info(f"  [DRY RUN] Would enable API for project {project_id}")
            return True

        enable_request = service_usage_v1.EnableServiceRequest(name=service_path)
        operation = service_usage_client.enable_service(request=enable_request)
        # Wait for the operation to complete.
        operation.result()
        logging.info(
            f"  Successfully enabled Generative Language API for project {project_id}"
        )
        return True

    except google_exceptions.PermissionDenied:
        logging.warning(
            f"  Permission denied to enable API for project {project_id}. Skipping."
        )
        return False
    except google_exceptions.GoogleAPICallError as err:
        if "UREQ_TOS_NOT_ACCEPTED" in str(err):
            tos_url = (
                "https://console.developers.google.com/terms/generative-language-api"
            )
            raise exceptions.TermsOfServiceNotAcceptedError(
                f"Terms of Service for the Generative Language API have not been accepted for project {project_id}.",
                url=tos_url,
            )
        logging.error(f"  Error enabling API for project {project_id}: {err}")
        return False


def create_api_key(project_id, credentials, dry_run=False):
    """Generates restricted API key with security constraints.

    Args:
        project_id (str): Target GCP project ID
        credentials (Credentials): Authenticated credentials
        dry_run (bool): Simulation mode flag

    Returns:
        api_keys_v2.Key: Created key object or None on failure

    Raises:
        PermissionDenied: For insufficient credentials
    """
    if dry_run:
        logging.info(f"  [DRY RUN] Would create API key for project {project_id}")
        # Return a mock key object for dry run
        return api_keys_v2.Key(
            name=f"projects/{project_id}/locations/global/keys/mock-key-id",
            uid="mock-key-id",
            display_name=config.GEMINI_API_KEY_DISPLAY_NAME,
            key_string="mock-key-string-for-dry-run",
            create_time=datetime.now(timezone.utc),
            update_time=datetime.now(timezone.utc),
            restrictions=api_keys_v2.Restrictions(
                api_targets=[
                    api_keys_v2.ApiTarget(service=config.GENERATIVE_LANGUAGE_API)
                ]
            ),
        )

    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        api_target = api_keys_v2.ApiTarget(service=config.GENERATIVE_LANGUAGE_API)
        key = api_keys_v2.Key(
            display_name=config.GEMINI_API_KEY_DISPLAY_NAME,
            restrictions=api_keys_v2.Restrictions(api_targets=[api_target]),
        )
        request = api_keys_v2.CreateKeyRequest(
            parent=f"projects/{project_id}/locations/global",
            key=key,
        )
        logging.info("  Creating API key...")
        operation = api_keys_client.create_key(request=request)
        result = operation.result()
        logging.info(
            f"  Successfully created restricted API key for project {project_id}"
        )
        return result
    except google_exceptions.PermissionDenied:
        logging.warning(
            f"  Permission denied to create API key for project {project_id}. Skipping."
        )
        return None
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Error creating API key for project {project_id}: {err}")
        return None


def delete_api_keys(project_id, credentials, dry_run=False):
    """Deletes all API keys with the display name 'Gemini API Key' and returns their UIDs."""
    deleted_keys_uids = []
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"

        keys = api_keys_client.list_keys(parent=parent)
        keys_to_delete = [
            key
            for key in keys
            if key.display_name == config.GEMINI_API_KEY_DISPLAY_NAME
        ]

        if not keys_to_delete:
            logging.info(
                f"  No '{config.GEMINI_API_KEY_DISPLAY_NAME}' found to delete."
            )
            return []

        logging.info(
            f"  Found {len(keys_to_delete)} key(s) with display name '{config.GEMINI_API_KEY_DISPLAY_NAME}'. Deleting..."
        )
        for key in keys_to_delete:
            if dry_run:
                logging.info(f"  [DRY RUN] Would delete key: {key.uid}")
                deleted_keys_uids.append(key.uid)
                continue
            try:
                request = api_keys_v2.DeleteKeyRequest(name=key.name)
                operation = api_keys_client.delete_key(request=request)
                operation.result()
                logging.info(f"  Successfully deleted key: {key.uid}")
                deleted_keys_uids.append(key.uid)
            except google_exceptions.GoogleAPICallError as err:
                logging.error(f"  Error deleting key {key.uid}: {err}")
        return deleted_keys_uids
    except google_exceptions.PermissionDenied:
        logging.warning(
            f"  Permission denied to list or delete API keys for project {project_id}. Skipping."
        )
    except google_exceptions.GoogleAPICallError as err:
        logging.error(
            f"  An API error occurred while deleting keys for project {project_id}: {err}"
        )
    return []
