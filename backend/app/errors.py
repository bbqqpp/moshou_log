class WCLUrlError(ValueError):
    """Raised when the supplied WCL URL is invalid or unsupported."""


class WCLApiError(RuntimeError):
    """Raised when WCL returns an unsuccessful or malformed response."""


class WCLAuthError(WCLApiError):
    """Raised when WCL rejects the configured credentials."""
