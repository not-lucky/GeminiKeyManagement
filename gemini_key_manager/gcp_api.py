"""This module contains functions for interacting with various Google Cloud Platform APIs."""
import logging
import time
import concurrent.futures
from datetime import datetime, timezone
from google.cloud import resourcemanager_v3, service_usage_v1, api_keys_v2
from google.api_core import exceptions as google_exceptions
from . import config, utils

def enable_api(project_id, credentials, dry_run=False):
    """Enables the Generative Language API for a given project."""
    service_name = config.GENERATIVE_LANGUAGE_API
    service_path = f"projects/{project_id}/services/{service_name}"
    service_usage_client = service_usage_v1.ServiceUsageClient(credentials=credentials)

    try:
        logging.info(f"  Attempting to enable Generative Language API for project {project_id}...")
        if dry_run:
            logging.info(f"  [DRY RUN] Would enable API for project {project_id}")
            return True

        enable_request = service_usage_v1.EnableServiceRequest(name=service_path)
        operation = service_usage_client.enable_service(request=enable_request)
        # This is a long-running operation, so we wait for it to complete.
        operation.result()
        logging.info(f"  Successfully enabled Generative Language API for project {project_id}")
        return True

    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to enable API for project {project_id}. Skipping.")
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Error enabling API for project {project_id}: {err}")
        return False

def create_api_key(project_id, credentials, dry_run=False):
    """
    Creates a new API key in the specified project.
    The key is restricted to only allow access to the Generative Language API.
    """
    if dry_run:
        logging.info(f"  [DRY RUN] Would create API key for project {project_id}")
        # In a dry run, return a mock key object to allow the rest of the logic to proceed.
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
        logging.info(f"  Successfully created restricted API key for project {project_id}")
        return result
    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to create API key for project {project_id}. Skipping.")
        return None
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Error creating API key for project {project_id}: {err}")
        return None

def delete_api_keys(project_id, credentials, dry_run=False):
    """Deletes all API keys with the configured display name from a project."""
    deleted_keys_uids = []
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"

        keys = api_keys_client.list_keys(parent=parent)
        keys_to_delete = [key for key in keys if key.display_name == config.GEMINI_API_KEY_DISPLAY_NAME]

        if not keys_to_delete:
            logging.info(f"  No '{config.GEMINI_API_KEY_DISPLAY_NAME}' found to delete.")
            return []

        logging.info(f"  Found {len(keys_to_delete)} key(s) with display name '{config.GEMINI_API_KEY_DISPLAY_NAME}'. Deleting...")
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
        logging.warning(f"  Permission denied to list or delete API keys for project {project_id}. Skipping.")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  An API error occurred while deleting keys for project {project_id}: {err}")
    return []



def _create_single_project(project_number, creds, dry_run, timeout_seconds=300, initial_delay=5):
    """
    Creates a new GCP project and waits for it to be ready.
    Readiness is determined by successfully enabling the Generative Language API.
    """
    random_string = utils.generate_random_string()
    project_id = f"project{project_number}-{random_string}"
    display_name = f"Project{project_number}"
    
    logging.info(f"Attempting to create project: ID='{project_id}', Name='{display_name}'")

    if dry_run:
        logging.info(f"[DRY RUN] Would create project '{display_name}' with ID '{project_id}'.")
        return None

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        project_to_create = resourcemanager_v3.Project(
            project_id=project_id,
            display_name=display_name
        )
        operation = resource_manager.create_project(project=project_to_create)
        logging.info(f"Waiting for project creation operation for '{display_name}' to complete...")
        created_project = operation.result()
        logging.info(f"Successfully initiated creation for project '{display_name}'.")

        # After creation, there can be a delay before the project is fully available
        # for API enablement. This loop polls until the API can be enabled.
        start_time = time.time()
        delay = initial_delay
        while time.time() - start_time < timeout_seconds:
            if enable_api(project_id, creds):
                logging.info(f"Generative AI API enabled for project '{display_name}' ({project_id}). Project is ready.")
                return created_project
            else:
                logging.info(f"Waiting for project '{display_name}' ({project_id}) to become ready... Retrying in {delay} seconds.")
                time.sleep(delay)
                delay = min(delay * 2, 30)

        logging.error(f"Timed out waiting for project '{display_name}' ({project_id}) to become ready after {timeout_seconds} seconds.")
        return None

    except Exception as e:
        logging.error(f"Failed to create project '{display_name}': {e}")
        return None

def create_projects_if_needed(projects, creds, dry_run=False, max_workers=5):
    """Creates new GCP projects in parallel until the account has at least 12 projects."""
    existing_project_count = len(projects)
    logging.info(f"Found {existing_project_count} existing projects.")
    newly_created_projects = []

    if existing_project_count >= 12:
        logging.info("Account already has 12 or more projects. No new projects will be created.")
        return newly_created_projects

    projects_to_create_count = 12 - existing_project_count
    logging.info(f"Need to create {projects_to_create_count} more projects.")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_project_number = {
            executor.submit(_create_single_project, str(i + 1).zfill(2), creds, dry_run): i
            for i in range(existing_project_count, 12)
        }

        for future in concurrent.futures.as_completed(future_to_project_number):
            try:
                created_project = future.result()
                if created_project:
                    newly_created_projects.append(created_project)
            except Exception as exc:
                project_number = future_to_project_number[future]
                logging.error(f"Project number {project_number} generated an exception: {exc}", exc_info=True)

    return newly_created_projects
