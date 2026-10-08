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

    SAFE_MUTATION_FALLBACK_CODES: dict[str, frozenset[str]] = {
        # Add BM is private-only in production. CREATE_BM already has its
        # persisted-query registry plus a bounded captured-contract fallback;
        # opening Business Suite after private resolution fails only converts
        # a useful private diagnostic into BUSINESS_LOGIN_GATE and reintroduces
        # a second mutation transport.
        "BUSINESS": frozenset(),
        "AD_ACCOUNT": frozenset({
            "CREATE_AD_ACCOUNT_MUTATION_NOT_DISCOVERED",
            "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
        }),
        "FAN_PAGES": frozenset({
            "FAN_PAGE_CREATE_NOT_SUBMITTED",
            "FAN_PAGE_CREATE_UI_CHANGED",
        }),
        "PAGE_ACCESS": frozenset({
            "PAGE_SHARE_UI_UNAVAILABLE",
        }),
    }

    POLICIES: dict[str, TransportPolicy] = {
        "BUSINESS": TransportPolicy(
            capability="BUSINESS",
            primary="facebook_web_graphql",
            fallback="none_private_only",
        ),
        "AD_ACCOUNT": TransportPolicy(
            capability="AD_ACCOUNT",
            primary="facebook_private_http_contract",
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

    def __init__(self, session: Any = None, *, context: Any = None) -> None:
        self.session = session
        self._context = context if context is not None else getattr(session, "context", None)
        self._shared_browser: Any = None

    @property
    def context(self) -> Any:
        return self._context

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

    def mutation_fallback_allowed(
        self,
        capability: str,
        error_code: str,
        *,
        submit_started: bool = False,
    ) -> bool:
        """Allow transport fallback only when the mutation is proven unsent."""
        if submit_started:
            return False
        code = str(error_code or "").strip().upper()
        if not code:
            return False
        if any(
            marker in code
            for marker in (
                "RESULT_UNKNOWN",
                "SUBMITTED",
                "MAY_HAVE_BEEN_SENT",
            )
        ):
            return False
        allowed = self.SAFE_MUTATION_FALLBACK_CODES.get(
            str(capability or "").strip().upper(),
            frozenset(),
        )
        return code in allowed

    def private_controller_available(self) -> bool:
        return callable(getattr(self.session, "facebook_controller", None))

    def private_web_available(self) -> bool:
        return callable(getattr(self.session, "facebook_web", None))

    def browser_available(self) -> bool:
        return bool(
            callable(getattr(self.session, "facebook_business_browser", None))
            or self.context is not None
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
        if callable(factory):
            browser = await factory()
            self._shared_browser = browser
            return browser
        if self.context is None:
            raise RuntimeError(
                "PROFILE_BROWSER_TRANSPORT_UNAVAILABLE"
            )

        from ..facebook_business_browser import FacebookBusinessBrowser

        browser = FacebookBusinessBrowser(self.context)
        await browser.open()
        self._shared_browser = browser
        return browser

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
            self._shared_browser = None
            return

        browser = self._shared_browser
        self._shared_browser = None
        close = getattr(browser, "close", None)
        if callable(close):
            await close()

        # Compatibility for lightweight test/legacy sessions which expose the
        # cached browser slot but not close_business_browser().
        if self.session is not None and hasattr(self.session, "_business_browser"):
            try:
                setattr(self.session, "_business_browser", None)
            except Exception:
                pass

    def __getattr__(self, name: str) -> Any:
        # Transitional compatibility: handlers can be moved behind the router
        # incrementally without losing profile-session capabilities.
        if self.session is None:
            raise AttributeError(name)
        return getattr(self.session, name)


__all__ = [
    "MetaTransportRouter",
    "TransportPolicy",
]
