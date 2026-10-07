from .models import ProvisioningError, ProvisioningStep

# Importing a small contract/error model must not initialize the action
# registry and every browser handler. Private transports also use these models.
_EXPORTS = {
    "ProvisioningService": ("service", "ProvisioningService"),
    "PrepareService": ("prepare", "PrepareService"),
    "ProvisioningStateStore": ("state", "ProvisioningStateStore"),
    "ProvisioningTransport": ("transport", "ProvisioningTransport"),
    "TransportError": ("transport", "TransportError"),
    "business_handler": ("business_handler", "business_handler"),
    "ad_account_handler": ("ad_account_handler", "ad_account_handler"),
    "funding_handler": ("funding_handler", "funding_handler"),
    "PROVISIONING_HANDLERS": ("registry", "PROVISIONING_HANDLERS"),
    "get_handler": ("registry", "get_handler"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    from importlib import import_module
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module("." + module_name, __name__), attribute)
    globals()[name] = value
    return value

__all__ = [
    "ProvisioningError",
    "ProvisioningStep",
    "ProvisioningService",
    "PrepareService",
    "ProvisioningStateStore",
    "ProvisioningTransport",
    "TransportError",
    "business_handler",
    "ad_account_handler",
    "funding_handler",
    "PROVISIONING_HANDLERS",
    "get_handler",
]
