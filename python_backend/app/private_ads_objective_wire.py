"""Translate selected objectives to the private CampaignGroup draft model.

The public Marketing objective enum and Ads Manager's editor record are not
interchangeable. This serializer does not alter the saved Launch or any Ad Set
optimization. Editor acceptance must still be independently verified live.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .private_ads_draft import fail

MANIFEST = Path(__file__).with_name('contracts') / 'private_ads_objective_wire_20261021.json'


class ObjectiveWireContract:
    def __init__(self):
        raw = MANIFEST.read_bytes()
        self.data = json.loads(raw)
        if self.data.get('version') != 1 or not self.data.get('sources'):
            raise fail('OBJECTIVE_WIRE_CONTRACT_INVALID', 'Private Campaign objective serialization is unavailable')
        self.digest = hashlib.sha256(raw).hexdigest()

    def campaign(self, values):
        result = copy.deepcopy(values)
        mapping = self.data['campaign_objectives'].get(values.get('objective'))
        if mapping is None:
            return result
        for field, expected in mapping.items():
            if field != 'objective' and field in values and values[field] is not expected:
                raise fail('OBJECTIVE_REPRESENTATION_CONFLICT', 'Selected Campaign objective metadata conflicts with its Traffic representation')
        result.update(mapping)
        return result
