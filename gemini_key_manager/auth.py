"""Implements Google Cloud authentication workflows.

Handles OAuth2 credential management including:
- Token refresh with retry logic
- Interactive authentication flows
- Credential storage/retrieval
"""
import os
import json
import logging
import time
import google.auth
from google.oauth2.credentials import Credentials
import google_auth_oauthlib.flow
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport import requests
from . import config

logger = logging.getLogger(__name__)


def get_and_refresh_credentials(email, max_retries=3, retry_delay=5):
    """Manages credential lifecycle with automated refresh and retry.
    
    Args:
        email (str): Service account email address
        max_retries (int): Maximum authentication retry attempts
        retry_delay (int): Seconds between retry attempts
    
    Returns:
        Credentials: Valid credentials or None if unrecoverable
    """
    token_file = os.path.join(config.CREDENTIALS_DIR, f"{email}.json")
    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, config.SCOPES)
        except (ValueError, json.JSONDecodeError):
            logging.warning(f"Could not decode token file for {email}. Re-authentication will be required.")
            return None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        for attempt in range(max_retries):
            try:
                logging.info(f"Refreshing credentials for {email} (attempt {attempt + 1}/{max_retries})...")
                creds.refresh(google.auth.transport.requests.Request())
                with open(token_file, "w") as token:
                    token.write(creds.to_json())
                logging.info(f"Successfully refreshed credentials for {email}.")
                return creds
            except Exception as e:
                logging.warning(f"Failed to refresh credentials for {email} on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        
        logging.error(f"Failed to refresh credentials for {email} after {max_retries} attempts.")
        return None
    
    return None

def run_interactive_auth(email, max_retries=3, retry_delay=5):
    """Executes interactive OAuth2 flow with error handling.
    
    Args:
        email (str): Target service account email
        max_retries (int): Allowed authentication attempts
        retry_delay (int): Pause between failed attempts
    
    Returns:
        Credentials: On successful authentication
    """
    for attempt in range(max_retries):
        try:
            logging.info(f"Please authenticate with: {email} (attempt {attempt + 1}/{max_retries})")
            flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                config.CLIENT_SECRETS_FILE, config.SCOPES
            )
            creds = flow.run_local_server(port=0)
            token_file = os.path.join(config.CREDENTIALS_DIR, f"{email}.json")
            with open(token_file, "w") as token:
                token.write(creds.to_json())
            return creds
        except Exception as e:
            logging.error(f"An unexpected error occurred during authentication for {email} on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                logging.info(f"Retrying authentication in {retry_delay} seconds...")
                time.sleep(retry_delay)

    logging.error(f"Failed to authenticate {email} after {max_retries} attempts.")
    return None
