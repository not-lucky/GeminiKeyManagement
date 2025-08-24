"""
Custom exceptions for the Gemini Key Management script.
"""

class TermsOfServiceNotAcceptedError(Exception):
    """Raised when the Terms of Service for a required API have not been accepted."""
    def __init__(self, message, url):
        self.message = message
        self.url = url
        super().__init__(self.message)
