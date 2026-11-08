"""Per-client passive request evidence, excluding header/URL credential values."""
import hashlib
import json
import re


def route(url):
    host = str(getattr(url, 'host', '') or '')
    path = str(getattr(url, 'path', '') or '')
    if host != 'facebook.com' and not re.fullmatch(r'[a-z][a-z0-9-]{0,39}\.facebook\.com', host):
        host = 'unreviewed'
    if host == 'unreviewed' or path not in {'/','/me','/login','/login/','/login.php',
            '/login/device-based/regular/login/','/business/loginpage/','/latest/home',
            '/adsmanager/manage/campaigns','/checkpoint/'}:
        path = 'unreviewed'
    return {'host':host, 'path':path}


def cookie_evidence(header, actor):
    auth = {'c_user':'', 'xs':''}
    names, conflicts = set(), False
    if len(header) > 131072:
        return {'header_auth_state':'unavailable'}
    for part in header.split(';'):
        name, separator, value = part.strip().partition('=')
        if not separator:
            continue
        names.add(name)
        if name in auth:
            value = value.strip('"')
            if auth[name] and auth[name] != value:
                conflicts = True
            auth[name] = value
    state = ('conflicting' if conflicts else 'absent' if not auth['c_user'] else
             'matches' if auth['c_user'] == actor else 'mismatch')
    return {'header_auth_state':state, 'header_cookie_count':len(names),
        'header_auth_cookie_names':sorted(names & set(auth)),
        'header_auth_cookie_fingerprint':hashlib.sha256(json.dumps(auth,
            sort_keys=True,separators=(',', ':')).encode()).hexdigest()}


class SessionWireEvidence:
    def __init__(self, actor, user_agent):
        self.actor, self.user_agent = actor, user_agent
        self.requests, self.responses, self.errors = [], [], 0
        self.client = None

    async def attach(self, web):
        private = await web._ensure_session()
        self.client = private._client
        self.client.event_hooks.setdefault('request', []).append(self.request)
        self.client.event_hooks.setdefault('response', []).append(self.response)

    async def request(self, request):
        try:
            if len(self.requests) >= 16:
                self.errors += 1
                return
            self.requests.append({**route(request.url),
                'method':'GET' if request.method == 'GET' else 'other',
                'user_agent_matches':request.headers.get('user-agent') == self.user_agent,
                **cookie_evidence(request.headers.get('cookie', ''), self.actor)})
        except Exception:
            self.errors += 1

    async def response(self, response):
        try:
            if len(self.responses) >= 16:
                self.errors += 1
                return
            changed, deleted = set(), set()
            for raw in response.headers.get_list('set-cookie'):
                match = re.match(r'\s*(c_user|xs)\s*=\s*([^;]*)', raw, re.I)
                if not match:
                    continue
                name, value = match.group(1).lower(), match.group(2).strip('"').strip()
                changed.add(name)
                if not value or value.lower() == 'deleted' or re.search(r';\s*max-age\s*=\s*0(?:\s*;|\s*$)', raw, re.I):
                    deleted.add(name)
            self.responses.append({**route(response.url), 'status':response.status_code,
                'protocol':response.http_version if response.http_version in {'HTTP/2','HTTP/1.1'} else 'other',
                'auth_cookie_set':sorted(changed), 'auth_cookie_deleted':sorted(deleted)})
        except Exception:
            self.errors += 1

    def snapshot(self):
        return {'requests':self.requests, 'responses':self.responses, 'measurement_errors':self.errors}

    def detach(self):
        if self.client is not None:
            for kind, callback in (('request',self.request),('response',self.response)):
                hooks = self.client.event_hooks.get(kind, [])
                if callback in hooks:
                    hooks.remove(callback)
