import base64
from datetime import datetime, timedelta, timezone
import json
import hashlib
import re
from pathlib import Path
import subprocess
import unittest

from cryptography import x509
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from app.payment_card_input import build_save_input, card_auth_fields
from app.payment_ptt import _encrypt_card_token, _validate_chain, _kdf, _fingerprint

VALUES = {'number':'4111111111111111','cvv':'123','month':'05','year':'2030',
          'holder':'Synthetic Test', 'postal_code':'12345'}
NONCE = '01234567-89ab-4cde-8123-456789abcdef'
PARITY = Path(__file__).with_name('fixtures') / 'payment_reference' / 'parity.cjs'


def unbase64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def node_parity(value):
    process = subprocess.run(['node',str(PARITY)],input=json.dumps(value),text=True,
                             capture_output=True,timeout=10)
    if process.returncode:
        raise AssertionError('Reference JavaScript failed: '+process.stderr)
    return json.loads(process.stdout)


class PaymentPTTTests(unittest.TestCase):
    def test_current_crypto_sources_key_operation_and_pinned_root_match_upload(self):
        fixtures=PARITY.parent
        hashes_expected={'FBPayAuthLibraryUtils.current.js':'7c8502d3f612ee6cc6af6cbd0524d667402bdd09346629f5effa76ec2a5d48a4',
                         'FBPayAuthLibraryCommon.current.js':'bbb76d00b393288223b6b253a5d3e6584f95cf188ffa795bc3ab8d0ad02c7318'}
        for name,digest in hashes_expected.items():
            # apply_patch adds a terminal newline; source hashes refer to module bytes.
            self.assertEqual(hashlib.sha256(fixtures.joinpath(name).read_text().rstrip('\n').encode()).hexdigest(),digest)
        source=fixtures.joinpath('FBPayAuthLibraryUtils.current.js').read_text()
        der=base64.b64decode(re.search(r'"(MIIC/TCCAqS[^\"]+)"',source).group(1))
        root=Path(__file__).parents[1]/'app'/'contracts'/'meta_payments_root_ca.pem'
        self.assertEqual(x509.load_pem_x509_certificate(root.read_bytes()).public_bytes(serialization.Encoding.DER),der)
        from app.payment_card_http import KEY_DOC_ID
        operation=fixtures.joinpath('PaymentsCometGetServerEncryptionKeyMutation_facebookRelayOperation.current.js').read_text()
        self.assertIn('"'+KEY_DOC_ID+'"',operation)

    def test_byte_exact_token_matches_actual_supplied_javascript_and_archived_wrapper(self):
        server, ephemeral = ec.derive_private_key(123,ec.SECP256R1()), ec.derive_private_key(456,ec.SECP256R1())
        auth, secret = card_auth_fields(VALUES)
        def der(key, private=False):
            raw = key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()) if private else key.public_bytes(
                                        serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
            return base64.b64encode(raw).decode()
        reference = node_parity({'mode':'ptt','ephemeral':der(ephemeral,True),
            'ephemeral_public':der(ephemeral.public_key()),'server_public':der(server.public_key()),
            'auth_data':auth,'secret_payload':secret})
        token = _encrypt_card_token(auth,secret,server.public_key(),nonce=NONCE,
                                   ephemeral_key=ephemeral,iv=bytes(range(12)))
        self.assertEqual(token,reference)
        outer = json.loads(unbase64(token)); parts = outer['payload'].split('.')
        self.assertEqual(outer['signatures'],[]); self.assertEqual(len(parts),6)
        self.assertEqual(json.loads(unbase64(parts[0]))['op'],'ADD_CARD')
        header=json.loads(unbase64(parts[1])); public=serialization.load_pem_public_key(header['epk']['pem'].encode())
        key=_kdf(server.exchange(ec.ECDH(),public),'',_fingerprint(server.public_key()))
        decrypted=AESGCM(key).decrypt(unbase64(parts[3]),unbase64(parts[4])+unbase64(parts[5]),
                                     (parts[1]+'.'+parts[0]).encode())
        self.assertEqual(json.loads(decrypted),secret)
        self.assertNotIn(VALUES['number'],outer['payload'])
        with self.assertRaises(InvalidTag):
            AESGCM(key).decrypt(unbase64(parts[3]),unbase64(parts[4])+unbase64(parts[5]),b'wrong-scope')

    def test_builder_matches_archived_function_including_undefined_vs_null(self):
        for optional in ({},{'network_consent':False,'recurring_consent':True,'usability_intent':'ADS_PAYMENT'}):
            args={'payment':'555666777','country':'US','currency':'USD','client_info':None,**optional}
            reference=node_parity({'mode':'builder','values':VALUES,**args})
            result=build_save_input(VALUES,token='synthetic_token',logging_data={'session_id':'synthetic-session'},**args)
            self.assertEqual(result,reference)
            self.assertEqual(result['share_to_child_payment_account_id'],None)
            self.assertEqual(result['card_data']['credit_card_number'],{'sensitive_string_value':'$e2ee'})
            self.assertNotIn(VALUES['number'],json.dumps(result))
            self.assertNotIn('biz_credential_is_sharable',result)

    def test_rejects_empty_plaintext_fallback_and_invalid_card_data(self):
        args={'payment':'555666777','country':'US','currency':'USD','client_info':None,'logging_data':{}}
        for token in ('','not a token',None):
            with self.assertRaisesRegex(ValueError,'CARD_PTT_REQUIRED'):
                build_save_input(VALUES,token=token,**args)
        for changes in ({'number':'invalid'},{'cvv':''},{'month':'13'},{'year':'2000'}):
            with self.assertRaisesRegex(ValueError,'CARD_DATA_INVALID'):
                card_auth_fields({**VALUES,**changes})

    def test_trust_chain_validates_signatures_validity_ca_and_curve(self):
        now=datetime.now(timezone.utc)
        root_key, intermediate_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))
        def cert(name,issuer,key,issuer_key,ca=False,expired=False):
            subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)])
            issuer_name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,issuer)])
            return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer_name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=2)).not_valid_after(now+timedelta(days=-1 if expired else 2))
                .add_extension(x509.BasicConstraints(ca=ca,path_length=None),critical=True)
                .add_extension(x509.KeyUsage(False,False,False,False,not ca,ca,ca,False if not ca else None,False if not ca else None),critical=True)
                .sign(issuer_key,hashes.SHA256()))
        root=cert('root','root',root_key,root_key,True)
        intermediate=cert('intermediate','root',intermediate_key,root_key,True)
        leaf=cert('leaf','intermediate',leaf_key,intermediate_key)
        def chain(leaf_cert=leaf,inter_cert=intermediate):
            return [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode() for c in (leaf_cert,inter_cert)]
        self.assertEqual(_validate_chain(chain(),root,now).public_numbers(),leaf_key.public_key().public_numbers())
        for invalid in ([],chain(cert('leaf','intermediate',leaf_key,root_key)),
                        chain(cert('leaf','intermediate',leaf_key,intermediate_key,expired=True)),
                        chain(inter_cert=cert('intermediate','root',intermediate_key,root_key)),
                        chain(cert('leaf','intermediate',ec.generate_private_key(ec.SECP384R1()),intermediate_key))):
            with self.assertRaisesRegex(ValueError,'CARD_PTT_TRUST_CHAIN_INVALID'):
                _validate_chain(invalid,root,now)


if __name__=='__main__':
    unittest.main()
