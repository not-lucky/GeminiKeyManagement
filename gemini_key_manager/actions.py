"""
Core action functions for the Gemini Key Management script.
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

# Helper class to create a mock key object compatible with add_key_to_database
class TempKey:
    def __init__(self, cloud_key, key_string):
        self.key_string = key_string
        self.uid = cloud_key.uid
        self.name = cloud_key.name
        self.display_name = cloud_key.display_name
        self.create_time = cloud_key.create_time
        self.update_time = cloud_key.update_time
        self.restrictions = cloud_key.restrictions

class TosAcceptanceHelper:
    """Helper class to manage the interactive ToS acceptance process using an Event."""
    def __init__(self):
        self.lock = threading.Lock()
        self.prompted_event = threading.Event()
        self.prompt_in_progress = False

def _enable_api_with_interactive_retry(project_id, creds, dry_run, tos_helper):
    """Calls the enable_api function with a retry loop that handles ToS exceptions."""
    while True:
        try:
            if gcp_api.enable_api(project_id, creds, dry_run=dry_run):
                return True
            else:
                return False
        except TermsOfServiceNotAcceptedError as err:
            with tos_helper.lock:
                if not tos_helper.prompt_in_progress:
                    tos_helper.prompt_in_progress = True
                    logging.error(err.message)
                    logging.error(f"Please accept the terms by visiting this URL: {err.url}")
                    input("Press Enter to continue after accepting the Terms of Service...")
                    tos_helper.prompted_event.set()
            
            tos_helper.prompted_event.wait()
        except Exception as e:
            logging.error(f"An unexpected error occurred while trying to enable API for project {project_id}: {e}", exc_info=True)
            return False

def reconcile_project_keys(project, creds, dry_run, db_lock, account_entry):
    """Reconciles API keys between Google Cloud and the local database for a single project."""
    project_id = project.project_id
    logging.info(f"  Reconciling keys for project {project_id}")
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
            logging.info(f"    Key {uid} is synchronized.")

        for uid in cloud_only_uids:
            key_object = cloud_keys[uid]
            logging.info(f"    Key {uid} ({key_object.display_name}) found in cloud only. Adding to local database.")
            if dry_run:
                logging.info(f"    [DRY RUN] Would fetch key string for {uid} and add to database.")
                continue
            
            try:
                key_string_response = api_keys_client.get_key_string(name=key_object.name)
                hydrated_key = TempKey(key_object, key_string_response.key_string)
                with db_lock:
                    database.add_key_to_database(account_entry, project, hydrated_key)
            except google_exceptions.PermissionDenied:
                logging.warning(f"    Permission denied to get key string for {uid}. Skipping.")
            except google_exceptions.GoogleAPICallError as err:
                logging.error(f"    Error getting key string for {uid}: {err}")

        for uid in local_only_uids:
            logging.info(f"    Key {uid} found in local database only. Marking as INACTIVE.")
            if dry_run:
                logging.info(f"    [DRY RUN] Would mark key {uid} as INACTIVE.")
                continue
            
            with db_lock:
                local_keys[uid]['state'] = 'INACTIVE'
                local_keys[uid]['key_details']['last_updated_timestamp_utc'] = datetime.now(timezone.utc).isoformat()
        
        return gemini_key_exists

    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to list keys for project {project_id}. Skipping reconciliation.")
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  An API error occurred while reconciling keys for project {project_id}: {err}")
        return False

def _create_and_process_new_project(project_number, creds, dry_run, db_lock, account_entry, tos_helper):
    """Creates a single project, waits for API enablement, and creates the key."""
    random_string = utils.generate_random_string()
    project_id = f"project{project_number}-{random_string}"
    display_name = f"Project{project_number}"
    
    logging.info(f"Attempting to create project: ID='{project_id}', Name='{display_name}'")

    if dry_run:
        logging.info(f"[DRY RUN] Would create project '{display_name}' with ID '{project_id}'.")
        return

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        project_to_create = resourcemanager_v3.Project(project_id=project_id, display_name=display_name)
        operation = resource_manager.create_project(project=project_to_create)
        logging.info(f"Waiting for project creation operation for '{display_name}' to complete...")
        created_project = operation.result()
        logging.info(f"Successfully initiated creation for project '{display_name}'.")

        if _enable_api_with_interactive_retry(project_id, creds, dry_run, tos_helper):
            logging.info(f"Generative AI API enabled for project '{display_name}' ({project_id}). Project is ready.")
            key_object = gcp_api.create_api_key(project_id, creds, dry_run=dry_run)
            if key_object:
                with db_lock:
                    database.add_key_to_database(account_entry, created_project, key_object)
        else:
            logging.error(f"Failed to enable API for new project '{display_name}' ({project_id}). Skipping key creation.")

    except Exception as e:
        logging.error(f"Failed to create project '{display_name}': {e}", exc_info=True)

def process_project_for_action(project, creds, action, dry_run, db_lock, account_entry, tos_helper):
    """Processes a single existing project for the given action in a thread-safe manner."""
    project_id = project.project_id
    logging.info(f"- Starting to process project: {project_id} ({project.display_name})")

    if action == 'create':
        gemini_key_exists = reconcile_project_keys(project, creds, dry_run, db_lock, account_entry)
        if gemini_key_exists:
            logging.info(f"  '{config.GEMINI_API_KEY_DISPLAY_NAME}' already exists in project {project_id}. Skipping creation.")
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
    
    logging.info(f"- Finished processing project: {project_id}")

def process_account(email, creds, action, api_keys_data, dry_run=False, max_workers=5):
    """Processes a single account for the given action."""
    logging.info(f"--- Processing account: {email} for action: {action} ---")
    if dry_run:
        logging.info("*** DRY RUN MODE ENABLED ***")

    if not creds:
        logging.warning(f"Could not get credentials for {email}. Skipping.")
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
            logging.warning(f"No projects found for {email}. This could be due to several reasons:")
            logging.warning("  1. The account truly has no projects.")
            logging.warning("  2. The Cloud Resource Manager API Terms of Service have not been accepted.")
            logging.warning(f"Please ensure the ToS are accepted by visiting: https://console.cloud.google.com/iam-admin/settings?user={email}")

        projects_to_create_count = 0
        if action == 'create':
            if len(existing_projects) < 12:
                projects_to_create_count = 12 - len(existing_projects)

        tos_helper = TosAcceptanceHelper()
        db_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            # Submit tasks for existing projects
            for project in existing_projects:
                futures.append(executor.submit(process_project_for_action, project, creds, action, dry_run, db_lock, account_entry, tos_helper))

            # Submit tasks for new projects
            if action == 'create' and projects_to_create_count > 0:
                for i in range(len(existing_projects), 12):
                    project_number = str(i + 1).zfill(2)
                    futures.append(executor.submit(_create_and_process_new_project, project_number, creds, dry_run, db_lock, account_entry, tos_helper))
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f"A task in the thread pool generated an exception: {exc}", exc_info=True)

    except google_exceptions.PermissionDenied as err:
        logging.error(f"Permission denied for account {email}. Check IAM roles.")
        logging.error(f"  Error: {err}")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"An API error occurred while processing account {email}: {err}")
