"""Positive-only recovery of a managed Page outside the target portfolio.

This reads embedded managed-Page JSON using the profile HTTP session. It never
discovers doc_ids, interprets CSS, starts Chromium, mutates an asset, or treats
an empty/unhydrated document as permission for another CREATE.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit

from .facebook_page_discovery import _extract_pages_from_browser_document
from .private_auth import private_auth_error

log = logging.getLogger('remask_worker')


async def managed_page_candidates(web, *, actor, name, excluded):
    candidates = {}
    surfaces = ('https://www.facebook.com/pages/?category=your_pages', 'https://www.facebook.com/')
    for url in surfaces:
        try:
            status, document, final = await asyncio.wait_for(web.fetch_text(url, max_bytes=4_000_000), timeout=10)
        except Exception as exc:
            auth_error = private_auth_error(exc)
            if auth_error is not None:
                raise auth_error from exc
            log.info('FP profile recovery actor=%s result=read_unavailable error_type=%s browser_started=False',
                actor, type(exc).__name__)
            continue
        parts = urlsplit(str(final or ''))
        if '/login' in parts.path.lower() or '/checkpoint' in parts.path.lower() or 'login_form' in document.lower():
            from .provisioning.models import ProvisioningError
            code = 'CHECKPOINT_REQUIRED' if '/checkpoint' in parts.path.lower() else 'SESSION_EXPIRED'
            raise ProvisioningError(code, 'Restore the profile session to reconcile the retained Page CREATE.', retryable=True)
        if status != 200 or parts.scheme != 'https' or parts.hostname != 'www.facebook.com':
            continue
        # Bootstrap actor alone cannot identify a later redirect/document. The
        # document must independently identify this exact cookie actor too.
        users = {match for variant in web._match_sources(document) for match in re.findall(
            r'CurrentUserInitialData.{0,2000}?"USER_ID"\s*:\s*"(\d+)"', variant, re.DOTALL)}
        if users != {actor}:
            log.info('FP profile recovery actor=%s result=document_actor_unconfirmed browser_started=False', actor)
            continue
        for row in _extract_pages_from_browser_document(document):
            page = str(row.get('id') or '')
            if page.isdigit() and page not in excluded and row.get('name') == name:
                candidates[page] = row
        log.info('FP profile recovery actor=%s managed_candidates=%s browser_started=False', actor, sorted(candidates))
    return sorted(candidates)
