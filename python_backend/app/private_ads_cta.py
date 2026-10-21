"""Executable website CTA subset. Source enum membership alone is insufficient."""
import json
import hashlib
from pathlib import Path

from .private_ads_draft import fail

MANIFEST = Path(__file__).with_name('contracts')/'private_ads_cta_20261021.json'


def capabilities():
    policy = json.loads(MANIFEST.read_text())
    return {'cta_types':list(policy['web_actions']), 'cta_configuration':{
        'revision':policy['revision'],'formats':list(policy['formats']),
        'source_digest':hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        'destinations':list(policy['destinations']),'default':policy['default'],
        'availability':'SUPPORTED','checked_live':False,'account_eligibility_verified':False,
        'other_actions':'UNAVAILABLE','existing_post':'ACCOUNT_DEPENDENT'}}


def validate_cta(launch):
    policy = json.loads(MANIFEST.read_text())
    creative = launch['creative']
    if creative.get('format') == 'INSTAGRAM_POST':
        if creative.get('call_to_action'):
            raise fail('CREATIVE_CTA_UNAVAILABLE','An existing Instagram post retains its own CTA; select its post identity without a CTA override')
        return
    destination = launch['adset'].get('destination_type') or 'WEBSITE'
    if destination not in policy['destinations']:
        raise fail('CREATIVE_CTA_UNAVAILABLE','This destination has no reviewed CTA value adapter; a website CTA cannot be substituted')
    selected = creative.get('call_to_action',policy['default'])
    if selected not in policy['web_actions']:
        raise fail('CREATIVE_CTA_UNAVAILABLE','Selected CTA has no implemented website Creative adapter')
    if creative.get('format','SINGLE') == 'CAROUSEL':
        for index,card in enumerate(creative['carousel_cards']):
            action = card.get('call_to_action') or selected
            if action not in policy['web_actions']:
                raise fail('CREATIVE_CTA_UNAVAILABLE',f'Carousel card {index+1} CTA has no implemented website adapter')
