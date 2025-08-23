"""Handles Google Cloud authentication, including token refresh and interactive OAuth2 flows."""
import os
import json
import logging
import time
import google.auth
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport import requests
from . import config

def get_and_refresh_credentials(email, max_retries=3, retry_delay=5):
    """
    Attempts to load credentials from a token file and refresh them if they are expired.
    This function operates non-interactively and will not prompt the user to log in.
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
    """
    Initiates an interactive, browser-based OAuth2 flow to get new credentials for a user.
    The new credentials are then saved to a token file for future non-interactive use.
    """
    for attempt in range(max_retries):
        try:
            logging.info(f"Please authenticate with: {email} (attempt {attempt + 1}/{max_retries})")
            flow = InstalledAppFlow.from_client_secrets_file(
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
