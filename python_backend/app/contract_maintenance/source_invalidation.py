"""Durable rejection generations; no source code, credentials or accepted proofs."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from ..provisioning.models import ProvisioningError

_failed_scopes = set()


def scope(web):
    profile = getattr(web, 'profile', None)
    if profile is None:
        return None  # Pure source-selection tests have no authenticated profile.
    return hashlib.sha256(json.dumps([getattr(profile, 'name', ''),
        profile.cookies.get('c_user', ''), str(getattr(profile, 'proxy', '') or '')],
        separators=(',', ':')).encode()).hexdigest()


def _path():
    return Path(os.getenv('REMASK_DATA_DIR') or '/var/lib/remask')/'private-module-invalidations.sqlite'


def state(web):
    key = scope(web)
    if key is None:
        return getattr(web, '_module_local_rejections', (0, {}))
    path = _path()
    if (str(path), key) in _failed_scopes:
        raise ProvisioningError('PRIVATE_MODULE_INVALIDATION_UNAVAILABLE',
            'Module rejection persistence failed; no request was submitted.')
    if not path.exists():
        return 0, {}
    try:
        with closing(sqlite3.connect(path, timeout=5)) as db:
            epoch = db.execute('SELECT generation FROM scopes WHERE scope=?', (key,)).fetchone()
            rows = db.execute('SELECT operation, generation FROM rejections WHERE scope=?', (key,)).fetchall()
        return (epoch[0] if epoch else 0), dict(rows)
    except (OSError, sqlite3.Error) as error:
        raise ProvisioningError('PRIVATE_MODULE_INVALIDATION_UNAVAILABLE',
            'Module rejection state is unavailable; no request was submitted.') from error


def reject(web, operations):
    """CAS against the evidence's generation, not the time a cache is replayed."""
    if not operations:
        return state(web)
    key = scope(web)
    if key is None:
        epoch, floors = state(web)
        floors = dict(floors)
        for name, observed in operations.items():
            if observed >= floors.get(name, 0):
                epoch += 1
                floors[name] = epoch
        web._module_local_rejections = epoch, floors
        return epoch, floors
    try:
        path = _path();path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS scopes (scope TEXT PRIMARY KEY, generation INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS rejections (scope TEXT, operation TEXT, generation INTEGER NOT NULL, PRIMARY KEY(scope,operation))')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT generation FROM scopes WHERE scope=?', (key,)).fetchone()
            epoch = row[0] if row else 0
            floors = dict(db.execute('SELECT operation,generation FROM rejections WHERE scope=?', (key,)).fetchall())
            for name, observed in operations.items():
                if observed < floors.get(name, 0):
                    continue
                epoch += 1
                floors[name] = epoch
                db.execute('INSERT INTO rejections VALUES (?,?,?) ON CONFLICT(scope,operation) DO UPDATE SET generation=excluded.generation',
                    (key, name, epoch))
            db.execute('INSERT INTO scopes VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET generation=excluded.generation', (key, epoch))
            db.commit()
        return epoch, floors
    except (OSError, sqlite3.Error) as error:
        _failed_scopes.add((str(_path()), key))
        raise ProvisioningError('PRIVATE_MODULE_INVALIDATION_UNAVAILABLE',
            'Module rejection could not be retained; no request was submitted.') from error
