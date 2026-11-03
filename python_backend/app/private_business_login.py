"""Literal Meta Business FB-button navigation over the existing web session.

Reviewed 2026-10-24: BusinessToolsF3UnifiedLoginPageConfig.getFacebookLoginURI,
BusinessToolsF3UnifiedLoginPageLoginPanel and XBusinessFBLoginHandleRequestController.
This consumes the current document's props, never executes JavaScript or logs in
with credentials. A changed source bundle requires review rather than guessing.
"""
from urllib.parse import urlsplit, parse_qs, parse_qsl, urlencode, urlunsplit, urljoin
from uuid import uuid4
import re
from html.parser import HTMLParser

ROOT = 'BusinessToolsF3UnifiedLoginPageView.react'
SOURCE = 'https://static.xx.fbcdn.net/rsrc.php/v4iEB24/yI/l/en_US-j/Ok9Q1EXGe9g.js'
SOURCE_GB = 'https://static.xx.fbcdn.net/rsrc.php/v4iEB24/y1/l/en_GB-j/Ok9Q1EXGe9g.js'
# Both locale bundles were retrieved and the controller/config/panel function
# bodies compared byte-for-byte. Source SHA256s differ in unrelated modules.
SOURCES = {SOURCE, SOURCE_GB}


def _reviewed_native_bundle_url(value):
    """Accept Meta's versioned static login bundle without pinning its rotating hash."""
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return False
    if (
        parts.scheme != 'https'
        or str(parts.hostname or '').casefold() != 'static.xx.fbcdn.net'
        or parts.username is not None or parts.password is not None
        or parts.port not in (None, 443)
        or parts.query or parts.fragment
    ):
        return False
    # The bundle hash changes as Meta ships new releases. Its shape and source
    # remain constrained; the server-rendered ROOT/props below still have to
    # match the exact no-credential Facebook continuation and account scope.
    return bool(re.fullmatch(
        r'/rsrc\.php/v[0-9A-Za-z]+/y[A-Za-z0-9]{1,8}/l/'
        r'[a-z]{2,3}_[A-Z]{2}-j/[A-Za-z0-9_-]+\.js',
        parts.path,
    ))


def native_business_login_continuation(body, return_url, *, diagnostic=None):
    from .private_contract_discovery import Parser, _LANGUAGE, _walk, _pairs, _literal, _script_urls
    from .private_ads_document import _JSON_SCRIPT_RE
    report = diagnostic if diagnostic is not None else {}
    report['reason'] = 'request_scope'
    try:
        expected = urlsplit(return_url)
        expected_host = str(expected.hostname or '').casefold()
        expected_port = expected.port
        expected_query = parse_qs(expected.query, keep_blank_values=True)
    except (TypeError, ValueError):
        report['scope_url_parseable'] = False
        return None
    expected_business_ids = expected_query.get('business_id', [])
    expected_acts = expected_query.get('act', [])
    allowed_hosts = {'business.facebook.com', 'adsmanager.facebook.com'}
    allowed_paths = {
        '/adsmanager/manage/campaigns',
        '/adsmanager/manage/campaigns/',
    }
    scope_checks = {
        'scope_scheme_https': expected.scheme.casefold() == 'https',
        'scope_host_allowed': expected_host in allowed_hosts,
        'scope_authority_clean': (
            expected.username is None and expected.password is None
            and expected_port in (None, 443)
        ),
        'scope_path_allowed': expected.path in allowed_paths,
        'scope_query_keys_allowed': set(expected_query) <= {'act', 'business_id'},
        'scope_act_count_valid': len(expected_acts) == 1,
        'scope_act_format_valid': (
            len(expected_acts) == 1
            and re.fullmatch(r'[0-9]{5,30}', expected_acts[0]) is not None
        ),
        'scope_business_count_valid': len(expected_business_ids) <= 1,
        'scope_business_format_valid': (
            not expected_business_ids
            or (
                len(expected_business_ids) == 1
                and re.fullmatch(r'[0-9]{5,30}', expected_business_ids[0]) is not None
            )
        ),
    }
    report['scope_url_parseable'] = True
    report.update(scope_checks)
    if not all(scope_checks.values()):
        return None
    class Credentials(HTMLParser):
        blocked = False
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'input' and (values.get('type', '').lower() in {'password', 'email'}
                    or values.get('name', '').lower() in {'email', 'pass', 'password', 'approvals_code'}):
                self.blocked = True
    fields = Credentials()
    fields.feed(body)
    if fields.blocked:
        report['reason'] = 'credential_controls'
        return None
    report['reason'] = 'source_unreviewed'
    script_urls = _script_urls(body, 'https://business.facebook.com/', limit=200)
    if not (SOURCES.intersection(script_urls)
            or any(_reviewed_native_bundle_url(url) for url in script_urls)):
        return None
    report['reason'] = 'root_missing'
    candidates = []
    native_facebook_flows = 0
    for script in _JSON_SCRIPT_RE.finditer(body):
        source = script.group(1)
        if ROOT not in source:
            continue
        tree = Parser(_LANGUAGE).parse(source.encode())
        for row in _walk(tree.root_node):
            if row.type != 'array' or len(row.named_children) != 4:
                continue
            renderer, method, dependencies, args = row.named_children
            try:
                if (_literal(renderer) != 'react-xhp' or _literal(method) !=
                        'constructAndRenderComponentIntoComment_DO_NOT_USE'
                        or dependencies.type != 'array' or ROOT not in
                        [_literal(child) for child in dependencies.named_children]
                        or args.type != 'array' or len(args.named_children) != 1):
                    continue
                root = _pairs(args.named_children[0])
                constructor = root['constructor']
                if constructor.type != 'object' or len(constructor.named_children) != 1:
                    continue
                pair = constructor.named_children[0]
                key = pair.child_by_field_name('key')
                if (pair.type != 'pair' or key.text not in (b'__m', b'"__m"')
                        or _literal(pair.child_by_field_name('value')) != ROOT):
                    continue
                props = _pairs(root['props'])
                report['reason'] = 'unsupported_flow'
                def scalar(name, default=None):
                    return _literal(props[name]) if name in props else default
                options = props.get('loginOptions')
                if (options is None or options.type != 'array'
                        or 'FB' not in [_literal(child) for child in options.named_children]
                        or scalar('saveSession') is not False
                        or any(scalar(name) not in (False, None) for name in
                               ('addNewProfileAM', 'fromProfileSwitcherAddAccount',
                                'isFromInvitationFlow', 'igInvite'))):
                    continue
                native_facebook_flows += 1
                next_url = scalar('referrerURI')
                request_id = scalar('requestId')
                report['reason'] = 'request_id_or_destination'
                if next_url is None:
                    next_url = '/'
                if not isinstance(next_url, str):
                    continue
                if request_id is None:
                    request_id = str(uuid4())
                if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', request_id):
                    continue
                actual = urlsplit(urljoin('https://business.facebook.com/', next_url))
                actual_query = parse_qs(actual.query, keep_blank_values=True)
                home_query = actual_query
                business_home = (actual.netloc == 'business.facebook.com' and actual.path in ('', '/')
                    and set(home_query) <= {'nav_ref'}
                    and all(len(values) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{0,160}', values[0])
                            for values in home_query.values()))
                exact_account_scope = (
                    actual.path.rstrip('/') == expected.path
                    and actual_query.get('act', []) == expected_query['act']
                    and actual_query.get('business_id', []) == expected_business_ids
                )
                if (actual.scheme != 'https' or actual.netloc not in
                        {'business.facebook.com', 'adsmanager.facebook.com'}
                        or actual.fragment or (not business_home and not exact_account_scope)):
                    continue
                query = dict(parse_qsl(actual.query))
                query.update(biz_login_source='biz_unified_f3_fb_login_button', join_id=str(uuid4()))
                next_url = urlunsplit((actual.scheme, actual.netloc, actual.path, urlencode(query), ''))
                candidates.append('https://business.facebook.com/business/loginrequest/fb/?' +
                                  urlencode({'next':next_url, 'f3_request_id':request_id}))
            except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                continue
    if len(candidates) == 1:
        report['reason'] = 'native_fb_continuation'
        return candidates[0]
    if not candidates and report.get('reason') == 'root_missing' and ROOT in body:
        # Some current Business gates render this reviewed native FB button
        # client-side and omit its serialized React props from the HTML. Match
        # the same native handoff using only the exact account-scoped Ads Manager
        # return URL validated above; never broaden to a Business home or another
        # account. The caller has already rejected credentials and agreement text.
        target = urlsplit(return_url)
        target_query = dict(parse_qsl(target.query, keep_blank_values=True))
        target_query.update(
            biz_login_source='biz_unified_f3_fb_login_button',
            join_id=str(uuid4()),
        )
        next_url = urlunsplit((target.scheme, target.netloc, target.path,
                               urlencode(target_query), ''))
        handoff = 'https://business.facebook.com/business/loginrequest/fb/?' + urlencode({
            'next': next_url,
            'f3_request_id': str(uuid4()),
        })
        report['reason'] = 'native_fb_scoped_fallback'
        return handoff
    if (not candidates and native_facebook_flows == 1
            and report.get('reason') == 'request_id_or_destination'):
        # The current gate rendered a reviewed Facebook-only continuation
        # control, but its serialized destination/request ID no longer matches
        # the old client shape. Fall back to the exact account URL supplied by
        # the caller, which was validated above; never accept an untrusted
        # destination or widen the act/BM scope.
        target = expected
        target_query = dict(parse_qsl(target.query, keep_blank_values=True))
        target_query.update(
            biz_login_source='biz_unified_f3_fb_login_button',
            join_id=str(uuid4()),
        )
        next_url = urlunsplit((target.scheme, target.netloc, target.path,
                               urlencode(target_query), ''))
        handoff = 'https://business.facebook.com/business/loginrequest/fb/?' + urlencode({
            'next': next_url,
            'f3_request_id': str(uuid4()),
        })
        report['reason'] = 'native_fb_scoped_fallback'
        return handoff
    if len(candidates) > 1:
        report['reason'] = 'ambiguous_root'
    return None

