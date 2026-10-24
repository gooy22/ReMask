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


def native_gate_render_shape(body):
    """Code identifiers only: never page props, field values or auth data."""
    from .private_ads_document import _JSON_SCRIPT_RE
    scripts = [match.group(1) for match in _JSON_SCRIPT_RE.finditer(body)]
    calls, constructors = [], []
    identifier = r'[A-Za-z_$][A-Za-z0-9_.$@-]{0,219}'
    for source in scripts:
        source = source.replace(r'\"', '"')
        for module, method in re.findall(r'\[\s*"(' + identifier + r')"\s*,\s*"(' + identifier + r')"', source):
            if any(word in (module + method).lower() for word in ('render', 'react', 'hydrate')):
                pair = [module.split('@', 1)[0], method]
                if pair not in calls:
                    calls.append(pair)
        for module in re.findall(r'(?:"__m"|\b__m)\s*:\s*"(' + identifier + r')"', source):
            if 'login' in module.lower() and module not in constructors:
                constructors.append(module)
    return {'root_in_body': ROOT in body,
            'root_script_count': sum(ROOT in source for source in scripts),
            'renderer_calls': calls[:8], 'login_constructors': constructors[:8]}


def native_business_login_continuation(body, return_url, *, diagnostic=None):
    from .private_contract_discovery import Parser, _LANGUAGE, _walk, _pairs, _literal, _script_urls
    from .private_ads_document import _JSON_SCRIPT_RE
    report = diagnostic if diagnostic is not None else {}
    report['reason'] = 'request_scope'
    expected = urlsplit(return_url)
    expected_query = parse_qs(expected.query)
    expected_campaign = (
        expected.scheme == 'https'
        and expected.netloc in {'business.facebook.com', 'adsmanager.facebook.com'}
        and expected.path == '/adsmanager/manage/campaigns'
        and not expected.fragment
        and all(
            len(expected_query.get(key, [])) == 1
            and expected_query[key][0].isascii()
            and expected_query[key][0].isdigit()
            and 5 <= len(expected_query[key][0]) <= 30
            for key in ('act', 'business_id')
        )
    )
    expected_inventory_home = (
        expected.scheme == 'https'
        and expected.netloc == 'business.facebook.com'
        and expected.path == '/latest/home'
        and not expected.fragment
        and set(expected_query) <= {'nav_ref'}
        and all(
            len(values) == 1
            and len(values[0]) <= 160
            and values[0].isascii()
            and all(char.isalnum() or char in '_-' for char in values[0])
            for values in expected_query.values()
        )
    )
    if not (expected_campaign or expected_inventory_home):
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
    if not SOURCES.intersection(_script_urls(body, 'https://business.facebook.com/', limit=200)):
        return None
    report['reason'] = 'root_missing'
    candidates = []
    for script in _JSON_SCRIPT_RE.finditer(body):
        source = script.group(1)
        if ROOT not in source:
            continue
        tree = Parser(_LANGUAGE).parse(source.encode())
        for row in _walk(tree.root_node):
            if row.type != 'array' or len(row.named_children) not in (4, 5):
                continue
            renderer, method, dependencies, args = row.named_children[:4]
            try:
                renderer_name = _literal(renderer)
                # Same ServerJSDefine module-name/hash resolution used by
                # context_from_document for native Ads Manager initializers.
                if (not isinstance(renderer_name, str) or renderer_name.split('@', 1)[0]
                        not in {'react-xhp', 'ReactRenderer'} or _literal(method) !=
                        'constructAndRenderComponentIntoComment_DO_NOT_USE'):
                    continue
                report['reason'] = 'render_record'
                if (len(row.named_children) == 5 and
                        (type(_literal(row.named_children[4])) is not int or _literal(row.named_children[4]) < 0)):
                    continue
                if dependencies.type == 'array':
                    if not all(isinstance(_literal(child), str) for child in dependencies.named_children):
                        continue
                elif dependencies.type != 'null':
                    continue
                # Dependencies and optional call ID are loader bookkeeping.
                # The literal constructor below proves which UI is rendered.
                report['reason'] = 'render_arguments'
                if args.type != 'array' or len(args.named_children) != 1:
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
                actual_query = parse_qs(actual.query)
                allowed_home_query = {'nav_ref', 'biz_login_source', 'join_id'}
                safe_home_query = (
                    set(actual_query) <= allowed_home_query
                    and all(
                        len(values) == 1
                        and len(values[0]) <= 160
                        and values[0].isascii()
                        and all(char.isalnum() or char in '_-' for char in values[0])
                        for values in actual_query.values()
                    )
                )
                business_home = (
                    actual.netloc == 'business.facebook.com'
                    and actual.path in ('', '/')
                    and safe_home_query
                )
                inventory_home = (
                    expected_inventory_home
                    and actual.netloc == 'business.facebook.com'
                    and actual.path == '/latest/home'
                    and safe_home_query
                )
                campaign_return = (
                    expected_campaign
                    and actual.path.rstrip('/') == expected.path
                    and all(
                        actual_query.get(key) == expected_query.get(key)
                        for key in ('act', 'business_id')
                    )
                )
                if (
                    actual.scheme != 'https'
                    or actual.netloc not in {'business.facebook.com', 'adsmanager.facebook.com'}
                    or actual.fragment
                    or not (business_home or inventory_home or campaign_return)
                ):
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
    if len(candidates) > 1:
        report['reason'] = 'ambiguous_root'
    return None
