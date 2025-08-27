"""
Main entry point for the Gemini Key Management script.
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
import concurrent.futures
from typing import List, Dict

from google.oauth2.credentials import Credentials

from . import utils, config, auth, database, actions


def main() -> None:
    """Orchestrates API key lifecycle management workflow.

    Handles:
    - Command line argument parsing
    - Credential management
    - Multi-account processing
    - Thread pool execution
    """
    parser = argparse.ArgumentParser(
        description="Manage Gemini API keys in Google Cloud projects."
    )
    parser.add_argument(
        "action",
        choices=["create", "delete"],
        help="The action to perform: 'create' or 'delete' API keys.",
    )
    parser.add_argument(
        "--email",
        help="Specify a single email address to process. Required for 'delete'. If not provided for 'create', emails will be read from emails.txt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the run without making any actual changes to Google Cloud resources.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="The maximum number of concurrent projects to process.",
    )
    parser.add_argument(
        "--auth-retries",
        type=int,
        default=3,
        help="Number of retries for a failed authentication attempt.",
    )
    parser.add_argument(
        "--auth-retry-delay",
        type=int,
        default=5,
        help="Delay in seconds between authentication retries.",
    )
    args = parser.parse_args()

    utils.setup_logging()
    logging.info(f"Program arguments: {vars(args)}")

    if args.action == "delete" and not args.email:
        parser.error("the --email argument is required for the 'delete' action")

    if not os.path.exists(config.CLIENT_SECRETS_FILE):
        logging.error(
            f"OAuth client secrets file not found at '{config.CLIENT_SECRETS_FILE}'"
        )
        logging.error("Please follow the setup instructions in README.md to create it.")
        sys.exit(1)

    if not os.path.exists(config.CREDENTIALS_DIR):
        os.makedirs(config.CREDENTIALS_DIR)

    schema = database.load_schema(config.API_KEYS_SCHEMA_FILE)
    api_keys_data = database.load_keys_database(config.API_KEYS_DATABASE_FILE, schema)

    emails_to_process: List[str] = []
    if args.email:
        emails_to_process.append(args.email)
    elif args.action == "delete":
        logging.error(
            "The 'delete' action requires the --email argument to specify which account's keys to delete."
        )
        sys.exit(1)
    else:  # action is 'create' and no email provided
        emails_to_process = utils.load_emails_from_file(config.EMAILS_FILE)
        if not emails_to_process:
            logging.info("No emails found in emails.txt. Exiting.")
            sys.exit(1)

    creds_map: Dict[str, Credentials] = {}
    emails_needing_interactive_auth: List[str] = []

    logging.info("Checking credentials and refreshing tokens for all accounts...")

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.max_workers
    ) as executor:
        future_to_email = {
            executor.submit(
                auth.get_and_refresh_credentials,
                email,
                max_retries=args.auth_retries,
                retry_delay=args.auth_retry_delay,
            ): email
            for email in emails_to_process
        }

        for future in concurrent.futures.as_completed(future_to_email):
            email = future_to_email[future]
            try:
                creds = future.result()
                if creds:
                    creds_map[email] = creds
                else:
                    emails_needing_interactive_auth.append(email)
            except Exception as exc:
                logging.error(
                    f"Credential check for {email} generated an exception: {exc}",
                    exc_info=True,
                )
                emails_needing_interactive_auth.append(email)

    if emails_needing_interactive_auth:
        logging.info("\n--- INTERACTIVE AUTHENTICATION REQUIRED ---")
        logging.info(
            f"The following accounts require manual authentication: {', '.join(sorted(emails_needing_interactive_auth))}"
        )

        for email in sorted(emails_needing_interactive_auth):
            creds = auth.run_interactive_auth(
                email, max_retries=args.auth_retries, retry_delay=args.auth_retry_delay
            )
            if creds:
                logging.info(f"Successfully authenticated {email}.")
                creds_map[email] = creds
            else:
                logging.warning(
                    f"Authentication failed or was cancelled for {email}. This account will be skipped."
                )

    logging.info("\n--- Credential checking complete ---")

    for email in emails_to_process:
        if email in creds_map:
            actions.process_account(
                email,
                creds_map[email],
                args.action,
                api_keys_data,
                schema,
                dry_run=args.dry_run,
                max_workers=args.max_workers,
            )
        else:
            logging.warning(
                f"Skipping account {email} because authentication was not successful."
            )