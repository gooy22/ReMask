"""Positive-only recovery of a managed Page outside the target portfolio.

This reads embedded managed-Page JSON using the profile HTTP session. It never
discovers doc_ids, interprets CSS, starts Chromium, mutates an asset, or treats
an empty/unhydrated document as permission for another CREATE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import urlsplit

from .facebook_page_discovery import _extract_pages_from_browser_document
from .private_auth import private_auth_error

log = logging.getLogger('remask_worker')


async def managed_page_candidates(web, *, actor, name, excluded, business='', diagnostics=None):
    from fb_worker import FacebookWebSession
    diagnostics = diagnostics if diagnostics is not None else []
    candidates = {}
    # The Business session can work while www documents fail. Both origins
    # expose authenticated bootstrap JSON; neither is an absence proof. Only
    # explicit managed Page records become candidates, followed by exact PAGE
    # ownership/claim eligibility verification in the action engine.
    business_surfaces = (
        'https://business.facebook.com/latest/settings/pages/?business_id=' + business,
        'https://business.facebook.com/latest/home?business_id=' + business,
    ) if re.fullmatch(r'\d{5,30}', business) else ()
    surfaces = business_surfaces + ('https://www.facebook.com/pages/?category=your_pages', 'https://www.facebook.com/')
    for url in surfaces:
        requested = urlsplit(url)
        evidence = {'host': requested.hostname, 'path': requested.path}
        try:
            status, document, final = await asyncio.wait_for(
                web.fetch_text(url, max_bytes=4_000_000, document_navigation=True), timeout=10)
        except Exception as exc:
            evidence.update(result='read_unavailable', error_type=type(exc).__name__)
            diagnostics.append(evidence)
            auth_error = private_auth_error(exc)
            if auth_error is not None:
                raise auth_error from exc
            log.info('FP profile recovery actor=%s result=read_unavailable error_type=%s browser_started=False',
                actor, type(exc).__name__)
            continue
        parts = urlsplit(str(final or ''))
        evidence.update(status=status, **FacebookWebSession._document_failure_evidence(status, document))
        diagnostics.append(evidence)
        log.info('FP profile recovery actor=%s evidence=%s browser_started=False', actor,
            json.dumps(evidence, separators=(',', ':')))
        if '/login' in parts.path.lower() or '/checkpoint' in parts.path.lower() or 'login_form' in document.lower():
            from .provisioning.models import ProvisioningError
            code = 'CHECKPOINT_REQUIRED' if '/checkpoint' in parts.path.lower() else 'SESSION_EXPIRED'
            raise ProvisioningError(code, 'Restore the profile session to reconcile the retained Page CREATE.', retryable=True)
        if status in {401, 403, 429}:
            from .provisioning.models import ProvisioningError
            evidence['result'] = 'session_rejected' if status != 429 else 'rate_limited'
            code = 'SESSION_EXPIRED' if status != 429 else 'META_RATE_LIMITED'
            raise ProvisioningError(code, 'Page recovery read was rejected with HTTP ' + str(status) +
                '; the original CREATE remains retained.', retryable=True)
        if status != 200 or parts.scheme != 'https' or parts.hostname != requested.hostname:
            evidence['result'] = 'http_rejected' if status != 200 else 'origin_unconfirmed'
            continue
        # Bootstrap actor alone cannot identify a later redirect/document. The
        # document must independently identify this exact cookie actor too.
        users = {match for variant in web._match_sources(document) for match in re.findall(
            r'CurrentUserInitialData.{0,2000}?"USER_ID"\s*:\s*"(\d+)"', variant, re.DOTALL)}
        if users != {actor}:
            evidence['result'] = 'document_actor_unconfirmed'
            log.info('FP profile recovery actor=%s result=document_actor_unconfirmed browser_started=False', actor)
            continue
        for row in _extract_pages_from_browser_document(document):
            page = str(row.get('id') or '')
            if page.isdigit() and page not in excluded and row.get('name') == name:
                candidates[page] = row
        evidence['result'] = 'managed_document_read'
        evidence['candidate_ids'] = sorted(candidates)
        log.info('FP profile recovery actor=%s managed_candidates=%s browser_started=False', actor, sorted(candidates))
    return sorted(candidates)
