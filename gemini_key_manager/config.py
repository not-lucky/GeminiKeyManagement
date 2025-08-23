"""This module defines configuration constants used throughout the application."""
import os

# --- Directory Paths ---
CREDENTIALS_DIR = "credentials"
LOG_DIR = "logs"
SCHEMA_DIR = "schemas"

# --- File Names ---
EMAILS_FILE = "emails.txt"
CLIENT_SECRETS_FILE = "credentials.json"
API_KEYS_DATABASE_FILE = "api_keys_database.json"

# --- Schema Configuration ---
API_KEYS_SCHEMA_FILE = os.path.join(SCHEMA_DIR, "v1", "api_keys_database.schema.json")

# --- Google API Settings ---
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]
GENERATIVE_LANGUAGE_API = "generativelanguage.googleapis.com"
GEMINI_API_KEY_DISPLAY_NAME = "Gemini API Key"
GENERATIVE_LANGUAGE_API_KEY_DISPLAY_NAME = "Generative Language API Key"
