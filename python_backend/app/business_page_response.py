"""Bounded, credential-free evidence for the pinned Business Page mutation."""
from __future__ import annotations

import hashlib
import json
import re

from .private_page_ownership import _id

_SECRET_KEY = re.compile(r'cookie|token|auth|fb_?dtsg|jazoest|\blsd\b|^xs$|c_user', re.I)

def _message(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[a-zA-Z]+', '[redacted]', value)
    value = re.sub(r'(?i)\b(?:fb_dtsg|lsd|jazoest|access_token|authorization|cookie|xs|c_user)\b\s*[:=]\s*\S+', '[redacted]', value)
    value = re.sub(r'[A-Za-z0-9_./+=-]{48,}', '[redacted]', value)
    return ' '.join(value.split())[:500]


def inspect_create_response(payload):
    data = payload.get('data') if isinstance(payload, dict) else None
    result = data.get('additional_profile_plus_create') if isinstance(data, dict) else None
    additional = result.get('additional_profile') if isinstance(result, dict) else None
    delegate = additional.get('delegate_page') if isinstance(additional, dict) else None
    page_id = _id(delegate.get('id')) if isinstance(delegate, dict) else ''
    profile_id = _id(additional.get('id')) if isinstance(additional, dict) else ''
    code = str(result.get('error_code') or '') if isinstance(result, dict) else ''
    category = result.get('error_category') if isinstance(result, dict) else ''
    category = category if category in {'user', 'system', 'integrity'} else ''
    message = _message(result.get('error_message')) if isinstance(result, dict) else ''
    name_error = _message(result.get('name_error')) if isinstance(result, dict) else ''
    errors = payload.get('errors') if isinstance(payload, dict) else None
    errors = errors if isinstance(errors, list) else [errors] if errors else []
    error_rows = []
    for error in errors[:5]:
        if not isinstance(error, dict):
            error_rows.append({'message': _message(error)})
            continue
        extensions = error.get('extensions') if isinstance(error.get('extensions'), dict) else {}
        error_code = str(error.get('code', extensions.get('code', '')) or '')
        error_rows.append({'code': error_code[:40] if re.fullmatch(r'[A-Za-z0-9_]{1,40}', error_code) else '',
            'message': _message(error.get('message'))})
    top_error = str(payload.get('error') or '') if isinstance(payload, dict) else ''
    # Only an explicit application-level user/integrity/name rejection with a
    # returned null additional_profile is definitive. Null/missing data, generic
    # server errors and partial IDs remain ambiguous even with an empty read.
    rejected = bool(isinstance(result, dict) and 'additional_profile' in result
        and additional is None and not errors and not top_error
        and (name_error or (message and category in {'user', 'integrity'})))
    return {'version': 1, 'page_id': page_id, 'additional_profile_id': profile_id,
        'meta_error_code': code if code.isdigit() else '', 'meta_error_category': category,
        'error_message': message, 'name_error': name_error, 'graphql_errors': error_rows,
        'top_error_code': top_error if top_error.isdigit() else '',
        'top_error_message': _message(payload.get('errorSummary')) if isinstance(payload, dict) else '',
        'response_has_errors': bool(errors or top_error or message or name_error or code),
        'response_fields': sorted(k for k in payload if isinstance(k, str)
            and not _SECRET_KEY.search(k))[:20] if isinstance(payload, dict) else [],
        'data_fields': sorted(k for k in data if isinstance(k, str)
            and not _SECRET_KEY.search(k))[:20] if isinstance(data, dict) else [],
        'result_fields': sorted(k for k in result if isinstance(k, str)
            and not _SECRET_KEY.search(k))[:20] if isinstance(result, dict) else [],
        'response_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        'outcome': 'REJECTED' if rejected else 'RESULT_UNVERIFIED' if page_id else 'RESULT_UNKNOWN'}


def rejection_error(evidence):
    from .provisioning.models import ProvisioningError
    name_error = evidence.get('name_error')
    reason = name_error or evidence.get('error_message') or 'Meta rejected Page creation.'
    code = 'FAN_PAGE_NAME_REJECTED' if name_error else 'FAN_PAGE_CREATE_REJECTED'
    meta_code = evidence.get('meta_error_code')
    return ProvisioningError(code, reason + (' (Meta code ' + meta_code + ')' if meta_code else ''), retryable=False)
