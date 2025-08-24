"""
Utility functions for the Gemini Key Management script.
"""
import logging
import os
import sys
import random
import string
from datetime import datetime, timezone
from colorama import Fore, Style, init
from . import config

class ColoredFormatter(logging.Formatter):
    """A custom logging formatter that adds color to console output."""

    LOG_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        """Formats the log record with appropriate colors."""
        color = self.LOG_COLORS.get(record.levelno)
        message = super().format(record)
        if color:
            # Only color the message part for readability
            parts = message.split(" - ", 2)
            if len(parts) > 2:
                parts[2] = color + parts[2] + Style.RESET_ALL
                message = " - ".join(parts)
            else:
                message = color + message + Style.RESET_ALL
        return message

def setup_logging():
    """Sets up logging to both console and a file, with colors for the console."""
    init(autoreset=True) # Initialize Colorama

    if not os.path.exists(config.LOG_DIR):
        os.makedirs(config.LOG_DIR)

    log_filename = f"gemini_key_management_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}.log"
    log_filepath = os.path.join(config.LOG_DIR, log_filename)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Clear existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # File handler for detailed, non-colored logging
    file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s:%(module)s:%(lineno)d] - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler for concise, colored logging
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Log file: {log_filepath}")

def load_emails_from_file(filename):
    """Loads a list of emails from a text file, ignoring comments."""
    if not os.path.exists(filename):
        logging.error(f"Email file not found at '{filename}'")
        logging.info("Please create it and add one email address per line.")
        return []
    with open(filename, "r") as f:
        # Ignore empty lines and lines starting with #
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def generate_random_string(length=10):
    """Generates a random alphanumeric string of a given length."""
    letters_and_digits = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters_and_digits) for i in range(length))
