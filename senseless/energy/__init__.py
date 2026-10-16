"""Local, provider-independent usage, tariffs and bill importers."""


class BillingError(ValueError):
    """Safe to return to a client: never include uploaded values in errors."""
