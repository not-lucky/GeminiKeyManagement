"""Exception hierarchy for Gemini Key Management system.

Defines domain-specific exceptions for:
- Terms of Service compliance failures
- Permission-related errors
- API operation constraints
"""

class TermsOfServiceNotAcceptedError(Exception):
    """Indicates unaccepted Terms of Service for critical API operations.
    
    Attributes:
        message (str): Human-readable error description
        url (str): URL for Terms of Service acceptance portal
    """
    def __init__(self, message, url):
        self.message = message
        self.url = url
        super().__init__(self.message)
