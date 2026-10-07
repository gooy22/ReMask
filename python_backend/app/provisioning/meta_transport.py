from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    capability: str
    primary: str
    fallback: str


class MetaTransportRouter:
    """Profile-bound transport facade for Meta actions.

    Action handlers depend on this facade instead of choosing session
    implementations directly. The router preserves the same profile cookies,
    proxy and user-agent across private HTTP/GraphQL and Chromium fallback.
    """

    POLICIES: dict[str, TransportPolicy] = {
        "BUSINESS": TransportPolicy(
            capability="BUSINESS",
            primary="facebook_web_graphql",
            fallback="chromium_business_suite",
        ),
        "AD_ACCOUNT": TransportPolicy(
            capability="AD_ACCOUNT",
            primary="facebook_private_graphql",
            fallback="chromium_live_contract_capture",
        ),
        "FAN_PAGES": TransportPolicy(
            capability="FAN_PAGES",
            primary="facebook_private_inventory",
            fallback="chromium_page_mutation",
        ),
        "PAGE_ACCESS": TransportPolicy(
            capability="PAGE_ACCESS",
            primary="facebook_private_verify",
            fallback="chromium_business_settings",
        ),
        "INVENTORY": TransportPolicy(
            capability="INVENTORY",
            primary="facebook_private_http_relay",
            fallback="chromium_inventory",
        ),
    }

    def __init__(self, session: Any) -> None:
        self.session = session

    @property
    def context(self) -> Any:
        return getattr(self.session, "context", None)

    def policy(self, capability: str) -> TransportPolicy:
        key = str(capability or "").strip().upper()
        return self.POLICIES.get(
            key,
            TransportPolicy(
                capability=key or "UNKNOWN",
                primary="profile_private_transport",
                fallback="chromium_fallback",
            ),
        )

    def private_controller_available(self) -> bool:
        return callable(getattr(self.session, "facebook_controller", None))

    def private_web_available(self) -> bool:
        return callable(getattr(self.session, "facebook_web", None))

    def browser_available(self) -> bool:
        return callable(
            getattr(self.session, "facebook_business_browser", None)
        )

    async def facebook_web(self):
        factory = getattr(self.session, "facebook_web", None)
        if not callable(factory):
            raise RuntimeError(
                "PROFILE_PRIVATE_WEB_TRANSPORT_UNAVAILABLE"
            )
        return await factory()

    async def facebook_controller(self):
        factory = getattr(self.session, "facebook_controller", None)
        if not callable(factory):
            raise RuntimeError(
                "PROFILE_PRIVATE_CONTROLLER_UNAVAILABLE"
            )
        return await factory()

    async def facebook_business_browser(self):
        factory = getattr(self.session, "facebook_business_browser", None)
        if not callable(factory):
            raise RuntimeError(
                "PROFILE_BROWSER_TRANSPORT_UNAVAILABLE"
            )
        return await factory()

    def browser_lease(self, **kwargs: Any):
        """Create an isolated Chromium lease behind the transport facade."""
        factory = getattr(self.session, "browser_lease", None)
        if callable(factory):
            return factory(**kwargs)

        from ..facebook_business_browser import FacebookBusinessBrowser

        return FacebookBusinessBrowser(
            self.context,
            **kwargs,
        )

    async def close_business_browser(self) -> None:
        closer = getattr(self.session, "close_business_browser", None)
        if callable(closer):
            await closer()

    def __getattr__(self, name: str) -> Any:
        # Transitional compatibility: handlers can be moved behind the router
        # incrementally without losing profile-session capabilities.
        return getattr(self.session, name)


__all__ = [
    "MetaTransportRouter",
    "TransportPolicy",
]
