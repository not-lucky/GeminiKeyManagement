"""Centralized configuration for Gemini Key Management system.

Contains:
- Directory paths for credentials and logs
- API endpoint configurations
- Security scopes and schema locations
"""

import os
from typing import List

# --- DIRECTORIES ---
CREDENTIALS_DIR: str = "credentials"
LOG_DIR: str = "logs"
SCHEMA_DIR: str = "schemas"

# --- FILENAMES ---
EMAILS_FILE: str = "emails.txt"
CLIENT_SECRETS_FILE: str = "credentials.json"
API_KEYS_DATABASE_FILE: str = "api_keys_database.json"

# --- SCHEMA ---
API_KEYS_SCHEMA_FILE: str = os.path.join(
    SCHEMA_DIR, "v1", "api_keys_database.schema.json"
)

# --- GOOGLE API ---
SCOPES: List[str] = [
    "https://www.googleapis.com/auth/cloud-platform",
]
GENERATIVE_LANGUAGE_API: str = "generativelanguage.googleapis.com"
GEMINI_API_KEY_DISPLAY_NAME: str = "Gemini API Key"
GENERATIVE_LANGUAGE_API_KEY_DISPLAY_NAME: str = "Generative Language API Key"