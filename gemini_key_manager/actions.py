"""Implements core business logic for Gemini API key lifecycle management.

This module handles:
- Project reconciliation between cloud state and local database
- API key creation/deletion operations
- Thread-safe database interactions
- Interactive Terms of Service acceptance workflows
"""
import logging
import threading
import time
import concurrent.futures
from datetime import datetime, timezone
from google.api_core import exceptions as google_exceptions
from google.cloud import resourcemanager_v3, api_keys_v2
from . import config, gcp_api, database, utils
from .exceptions import TermsOfServiceNotAcceptedError

class TempKey:
    """Mock key object compatible with database operations.
    
    Provides a temporary representation of an API key for database insertion
    when direct API key string retrieval is not possible.

    Attributes:
        key_string (str): The actual API key string
        uid (str): Unique identifier of the key
        name (str): Full resource name of the key
        display_name (str): Human-readable display name
        create_time (datetime): Key creation timestamp
        update_time (datetime): Last update timestamp
        restrictions (api_keys_v2.Restrictions): Key usage restrictions
    """
    def __init__(self, cloud_key, key_string):
        self.key_string = key_string
        self.uid = cloud_key.uid
        self.name = cloud_key.name
        self.display_name = cloud_key.display_name
        self.create_time = cloud_key.create_time
        self.update_time = cloud_key.update_time
        self.restrictions = cloud_key.restrictions

class TosAcceptanceHelper:
    """Manages Terms of Service acceptance workflow with thread synchronization.
    
    Coordinates interactive ToS acceptance across multiple threads to prevent
    duplicate prompts and ensure proper sequencing.

    Attributes:
        lock (threading.Lock): Synchronizes access to prompt state
        prompted_event (threading.Event): Signals ToS acceptance completion
        prompt_in_progress (bool): Indicates active prompt display status
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.prompted_event = threading.Event()
        self.prompt_in_progress = False

def _enable_api_with_interactive_retry(project_id, creds, dry_run, tos_helper):
    """Attempts to enable API with retry logic for ToS acceptance.
    
    Args:
        project_id (str): Target GCP project ID
        creds (Credentials: Authenticated Google credentials
        dry_run (bool): Simulation mode flag
        tos_helper (TosAcceptanceHelper): ToS workflow coordinator

    Returns:
        bool: True if API enabled successfully
    
    Raises:
        GoogleAPICallError: For non-ToS related API failures
    """
    while True:
        try:
            if gcp_api.enable_api(project_id, creds, dry_run=dry_run):
                return True
            return False
        except TermsOfServiceNotAcceptedError as err:
            with tos_helper.lock:
                if not tos_helper.prompt_in_progress:
                    tos_helper.prompt_in_progress = True
                    logging.error(err.message)
                    logging.error(f"Accept terms at: {err.url}")
                    input("Press Enter after accepting Terms of Service...")
                    tos_helper.prompted_event.set()
            tos_helper.prompted_event.wait()
        except Exception as e:
            logging.error(f"API enablement error for {project_id}: {e}", exc_info=True)
            return False

def reconcile_project_keys(project, creds, dry_run, db_lock, account_entry):
    """Reconciles cloud and local database API key states.
    
    Args:
        project (Project): GCP Project resource
        creds (Credentials): Authenticated credentials
        dry_run (bool): Simulation mode flag
        db_lock (threading.Lock): Database access lock
        account_entry (dict): Account data structure
    
    Returns:
        bool: True if Gemini key exists, False otherwise
    """
    project_id = project.project_id
    logging.info(f"Reconciling keys for {project_id}")
    gemini_key_exists = False

    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=creds)
        parent = f"projects/{project_id}/locations/global"
        
        cloud_keys_list = list(api_keys_client.list_keys(parent=parent))
        for key in cloud_keys_list:
            if key.display_name in [config.GEMINI_API_KEY_DISPLAY_NAME, config.GENERATIVE_LANGUAGE_API_KEY_DISPLAY_NAME]:
                gemini_key_exists = True
        
        cloud_keys = {key.uid: key for key in cloud_keys_list}
        
        project_entry = next((p for p in account_entry["projects"] if p.get("project_info", {}).get("project_id") == project_id), None)
        
        if not project_entry:
            project_entry = {
                "project_info": {
                    "project_id": project.project_id,
                    "project_name": project.display_name,
                    "project_number": project.name.split('/')[-1],
                    "state": str(project.state)
                },
                "api_keys": []
            }
            with db_lock:
                account_entry["projects"].append(project_entry)
        
        local_keys = {key['key_details']['key_id']: key for key in project_entry.get('api_keys', [])}

        cloud_uids = set(cloud_keys.keys())
        local_uids = set(local_keys.keys())

        synced_uids = cloud_uids.intersection(local_uids)
        cloud_only_uids = cloud_uids - local_uids
        local_only_uids = local_uids - cloud_uids

        for uid in synced_uids:
            logging.info(f"Key {uid} synchronized")

        for uid in cloud_only_uids:
            key_object = cloud_keys[uid]
            logging.info(f"Adding cloud-only key {uid} ({key_object.display_name})")
            if dry_run:
                logging.info(f"[DRY RUN] Would fetch key string for {uid}")
                continue
            
            try:
                key_string_response = api_keys_client.get_key_string(name=key_object.name)
                hydrated_key = TempKey(key_object, key_string_response.key_string)
                with db_lock:
                    database.add_key_to_database(account_entry, project, hydrated_key)
            except google_exceptions.PermissionDenied:
                logging.warning(f"Permission denied to get key string for {uid}")
            except google_exceptions.GoogleAPICallError as err:
                logging.error(f"Key string error for {uid}: {err}")

        for uid in local_only_uids:
            logging.info(f"Marking local-only key {uid} as INACTIVE")
            if dry_run:
                logging.info(f"[DRY RUN] Would deactivate {uid}")
                continue
            
            with db_lock:
                local_keys[uid]['state'] = 'INACTIVE'
                local_keys[uid]['key_details']['last_updated_timestamp_utc'] = datetime.now(timezone.utc).isoformat()
        
        return gemini_key_exists

    except google_exceptions.PermissionDenied:
        logging.warning(f"Permission denied listing keys for {project_id}")
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"API error during reconciliation: {err}")
        return False

def _create_and_process_new_project(project_number, creds, dry_run, db_lock, account_entry, tos_helper):
    """Creates and initializes new GCP project with API key.
    
    Args:
        project_number (str): Sequential project identifier
        creds (Credentials): Authenticated credentials
        dry_run (bool): Simulation mode flag
        db_lock (threading.Lock): Database access lock
        account_entry (dict): Account data structure
        tos_helper (TosAcceptanceHelper): ToS workflow coordinator
    """
    random_string = utils.generate_random_string()
    project_id = f"project{project_number}-{random_string}"
    display_name = f"Project{project_number}"
    
    logging.info(f"Creating project: {display_name} ({project_id})")

    if dry_run:
        logging.info(f"[DRY RUN] Would create {display_name}")
        return

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        project_to_create = resourcemanager_v3.Project(project_id=project_id, display_name=display_name)
        operation = resource_manager.create_project(project=project_to_create)
        logging.info(f"Awaiting project creation: {display_name}")
        created_project = operation.result()
        logging.info(f"Project created: {display_name}")

        if _enable_api_with_interactive_retry(project_id, creds, dry_run, tos_helper):
            logging.info(f"API enabled for {display_name}")
            key_object = gcp_api.create_api_key(project_id, creds, dry_run=dry_run)
            if key_object:
                with db_lock:
                    database.add_key_to_database(account_entry, created_project, key_object)
        else:
            logging.error(f"API enablement failed for {display_name}")

    except Exception as e:
        logging.error(f"Project creation failed: {e}", exc_info=True)

def process_project_for_action(project, creds, action, dry_run, db_lock, account_entry, tos_helper):
    """Executes specified action on a single GCP project.
    
    Args:
        project (Project): Target GCP project
        creds (Credentials): Authenticated credentials
        action (str): 'create' or 'delete' action
        dry_run (bool): Simulation mode flag
        db_lock (threading.Lock): Database access lock
        account_entry (dict): Account data structure
        tos_helper (TosAcceptanceHelper): ToS workflow coordinator
    """
    project_id = project.project_id
    logging.info(f"Processing {project_id} ({project.display_name})")

    if action == 'create':
        gemini_key_exists = reconcile_project_keys(project, creds, dry_run, db_lock, account_entry)
        if gemini_key_exists:
            logging.info(f"Existing Gemini key in {project_id}")
            return

        if _enable_api_with_interactive_retry(project_id, creds, dry_run, tos_helper):
            key_object = gcp_api.create_api_key(project_id, creds, dry_run=dry_run)
            if key_object:
                with db_lock:
                    database.add_key_to_database(account_entry, project, key_object)
    elif action == 'delete':
        deleted_keys_uids = gcp_api.delete_api_keys(project_id, creds, dry_run=dry_run)
        if deleted_keys_uids:
            with db_lock:
                database.remove_keys_from_database(account_entry, project_id, deleted_keys_uids)
    
    logging.info(f"Completed processing {project_id}")

def process_account(email, creds, action, api_keys_data, dry_run=False, max_workers=5):
    """Orchestrates account-level key management operations.
    
    Args:
        email (str): Service account email
        creds (Credentials): Authenticated credentials
        action (str): 'create' or 'delete' action
        api_keys_data (dict): Database structure
        dry_run (bool): Simulation mode flag
        max_workers (int): Max concurrent operations
    """
    logging.info(f"Processing account: {email} ({action})")
    if dry_run:
        logging.info("*** DRY RUN ACTIVE ***")

    if not creds:
        logging.warning(f"Invalid credentials for {email}")
        return

    account_entry = next((acc for acc in api_keys_data["accounts"] if acc.get("account_details", {}).get("email") == email), None)
    if not account_entry:
        account_entry = {
            "account_details": {
                "email": email,
                "authentication_details": {
                    "token_file": f"{config.CREDENTIALS_DIR}/{email}.json",
                    "scopes": config.SCOPES
                }
            },
            "projects": []
        }
        api_keys_data["accounts"].append(account_entry)

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        existing_projects = list(resource_manager.search_projects())
        
        if not existing_projects and action == 'create':
            logging.warning(f"No projects found for {email}")
            logging.warning("Possible reasons: No projects or unaccepted ToS")
            logging.warning(f"Verify ToS: https://console.cloud.google.com/iam-admin/settings?user={email}")

        projects_to_create_count = 0
        if action == 'create':
            if len(existing_projects) < 12:
                projects_to_create_count = 12 - len(existing_projects)

        tos_helper = TosAcceptanceHelper()
        db_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for project in existing_projects:
                futures.append(executor.submit(process_project_for_action, project, creds, action, dry_run, db_lock, account_entry, tos_helper))

            if action == 'create' and projects_to_create_count > 0:
                for i in range(len(existing_projects), 12):
                    project_number = str(i + 1).zfill(2)
                    futures.append(executor.submit(_create_and_process_new_project, project_number, creds, dry_run, db_lock, account_entry, tos_helper))
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f"Task error: {exc}", exc_info=True)

    except google_exceptions.PermissionDenied as err:
        logging.error(f"Permission denied for {email}: {err}")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"API error processing {email}: {err}")
