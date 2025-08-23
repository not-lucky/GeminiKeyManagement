"""This module provides utility functions for logging, file handling, and string generation."""
import logging
import os
import sys
import random
import string
from datetime import datetime, timezone
from colorama import Fore, Style, init
from . import config

class ColoredFormatter(logging.Formatter):
    """A logging formatter that adds color to console output for different log levels."""

    LOG_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        """Applies color to the formatted log message."""
        color = self.LOG_COLORS.get(record.levelno)
        message = super().format(record)
        if color:
            # For better readability, only color the message part of the log string.
            parts = message.split(" - ", 2)
            if len(parts) > 2:
                parts[2] = color + parts[2] + Style.RESET_ALL
                message = " - ".join(parts)
            else:
                message = color + message + Style.RESET_ALL
        return message

def setup_logging():
    """
    Configures the root logger to output to both a timestamped file and the console.
    Console output is colorized for readability.
    """
    init(autoreset=True) # Required for colorama on Windows

    if not os.path.exists(config.LOG_DIR):
        os.makedirs(config.LOG_DIR)

    log_filename = f"gemini_key_management_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}.log"
    log_filepath = os.path.join(config.LOG_DIR, log_filename)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Avoids duplicate log messages if the function is called multiple times.
    if logger.hasHandlers():
        logger.handlers.clear()

    # The file handler logs detailed, non-colored messages.
    file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s:%(module)s:%(lineno)d] - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # The console handler logs concise, colored messages.
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Log file: {log_filepath}")

def load_emails_from_file(filename):
    """
    Reads a list of email addresses from a text file.
    It ignores empty lines and lines that start with a '#' comment character.
    """
    if not os.path.exists(filename):
        logging.error(f"Email file not found at '{filename}'")
        logging.info("Please create it and add one email address per line.")
        return []
    with open(filename, "r") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def generate_random_string(length=10):
    """Generates a random alphanumeric string for creating unique project IDs."""
    letters_and_digits = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters_and_digits) for i in range(length))
