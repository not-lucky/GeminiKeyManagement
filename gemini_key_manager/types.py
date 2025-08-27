"""
This module defines the core data structures for the Gemini Key Management system
using TypedDicts to ensure type safety and clarity. These structures mirror the
JSON schema for the API keys database, providing a single source of truth for
data shapes throughout the application.
"""

from __future__ import annotations

from typing import List, Literal, TYPE_CHECKING, TypedDict
from datetime import datetime

if TYPE_CHECKING:
    from google.cloud.api_keys_v2.types import Key as CloudKey
    from google.cloud.api_keys_v2.types import Restrictions as CloudRestrictions


class ApiTarget(TypedDict):
    """Represents a single API target for key restrictions."""

    service: str
    methods: List[str]


class Restrictions(TypedDict):
    """Defines the API restrictions for a key."""

    api_targets: List[ApiTarget]


class KeyDetails(TypedDict):
    """Contains the detailed information for an API key."""

    key_string: str
    key_id: str
    key_name: str
    display_name: str
    creation_timestamp_utc: str
    last_updated_timestamp_utc: str


class ApiKey(TypedDict):
    """Represents a single API key, including its details and restrictions."""

    key_details: KeyDetails
    restrictions: Restrictions
    state: Literal["ACTIVE", "INACTIVE"]


class ProjectInfo(TypedDict):
    """Contains metadata about a Google Cloud project."""

    project_id: str
    project_name: str
    project_number: str
    state: str


class Project(TypedDict):
    """Represents a Google Cloud project and its associated API keys."""

    project_info: ProjectInfo
    api_keys: List[ApiKey]


class AuthenticationDetails(TypedDict):
    """Holds authentication information for a Google account."""

    token_file: str
    scopes: List[str]


class AccountDetails(TypedDict):
    """Contains details for a single Google account."""

    email: str
    authentication_details: AuthenticationDetails


class Account(TypedDict):
    """Represents a single user account and all its associated projects."""

    account_details: AccountDetails
    projects: List[Project]


class ApiKeysDatabase(TypedDict):
    """
    Defines the root structure of the JSON database file, holding all account
    and key information.
    """

    schema_version: str
    accounts: List[Account]
    generation_timestamp_utc: str
    last_modified_utc: str


class TempKey:
    """
    A temporary, mock-like key object used for database operations when a full
    cloud key object is not available or necessary. It provides a compatible
    structure for functions that expect a key-like object.

    Attributes:
        key_string (str): The actual API key string.
        uid (str): The unique identifier of the key.
        name (str): The full resource name of the key.
        display_name (str): The human-readable display name.
        create_time (datetime): The timestamp of key creation.
        update_time (datetime): The timestamp of the last update.
        restrictions (CloudRestrictions): The usage restrictions for the key.
    """

    def __init__(self, cloud_key: "CloudKey", key_string: str) -> None:
        self.key_string: str = key_string
        self.uid: str = cloud_key.uid
        self.name: str = cloud_key.name
        self.display_name: str = cloud_key.display_name
        self.create_time: datetime = cloud_key.create_time
        self.update_time: datetime = cloud_key.update_time
        self.restrictions: "CloudRestrictions" = cloud_key.restrictions
