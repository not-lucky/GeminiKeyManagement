"""This module contains the core functions that perform actions on GCP projects."""
import os
import logging
import threading
import concurrent.futures
from datetime import datetime, timezone
from google.api_core import exceptions as google_exceptions
from google.cloud import resourcemanager_v3, api_keys_v2
from . import config, gcp_api, database

class TempKey:
    """A temporary container for key data to ensure compatibility with database functions."""
    def __init__(self, cloud_key, key_string):
        self.key_string = key_string
        self.uid = cloud_key.uid
        self.name = cloud_key.name
        self.display_name = cloud_key.display_name
        self.create_time = cloud_key.create_time
        self.update_time = cloud_key.update_time
        self.restrictions = cloud_key.restrictions

def reconcile_project_keys(project, creds, dry_run, db_lock, account_entry):
    """
    Compares the API keys in a GCP project with the local database and syncs them.
    
    This function will:
    1. Fetch all keys from the GCP project.
    2. Fetch all keys for the project from the local database.
    3. Add keys that only exist in GCP to the local database.
    4. Mark keys as INACTIVE in the local database if they no longer exist in GCP.

    Returns:
        bool: True if a Gemini-specific API key already exists in the project, False otherwise.
    """
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
            # If the project is not yet in our database, create a new entry for it.
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
                # The key object from the list_keys method does not include the key string.
                # A separate API call is required to fetch the unencrypted key string.
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

def process_project_for_action(project, creds, action, dry_run, db_lock, account_entry):
    """Coordinates the sequence of operations for a single project based on the specified action."""
    project_id = project.project_id
    logging.info(f"- Starting to process project: {project_id} ({project.display_name})")

    if action == 'create':
        gemini_key_exists = reconcile_project_keys(project, creds, dry_run, db_lock, account_entry)
        if gemini_key_exists:
            logging.info(f"  '{config.GEMINI_API_KEY_DISPLAY_NAME}' already exists in project {project_id}. Skipping creation.")
            return

        if gcp_api.enable_api(project_id, creds, dry_run=dry_run):
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
    """
    Orchestrates the entire process for a single user account.
    
    This includes finding all accessible projects and then running the specified
    action ('create' or 'delete') on each project concurrently.
    """
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
                    "token_file": os.path.join(config.CREDENTIALS_DIR, f"{email}.json"),
                    "scopes": config.SCOPES
                }
            },
            "projects": []
        }
        api_keys_data["accounts"].append(account_entry)

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        projects = list(resource_manager.search_projects())

        if action == 'create':
            new_projects = gcp_api.create_projects_if_needed(projects, creds, dry_run)
            projects.extend(new_projects)

        if not projects:
            logging.info(f"No projects found for {email}.")
            return

        logging.info(f"Found {len(projects)} projects. Processing with up to {max_workers} workers...")
        
        db_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_project = {
                executor.submit(process_project_for_action, project, creds, action, dry_run, db_lock, account_entry): project
                for project in projects
            }
            for future in concurrent.futures.as_completed(future_to_project):
                project = future_to_project[future]
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f"Project {project.project_id} generated an exception: {exc}", exc_info=True)

    except google_exceptions.PermissionDenied as err:
        logging.error(f"Permission denied for account {email}. Check IAM roles.")
        logging.error(f"  Error: {err}")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"An API error occurred while processing account {email}: {err}")
