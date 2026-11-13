"""TOTP and high-entropy one-use recovery codes for administrator accounts."""
import base64
import hashlib
import hmac
import secrets
import time
import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def _key(secret, purpose):
    return hmac.new(secret.encode(),purpose.encode(),hashlib.sha256).digest()

def _aad(user_id):
    return f"metroestate:admin-mfa:v1:{user_id}".encode()

def new_secret():
    return pyotp.random_base32()

def seal_secret(app_secret,user_id,secret):
    nonce=secrets.token_bytes(12)
    ciphertext=AESGCM(_key(app_secret,"metroestate-mfa-encryption-v1")).encrypt(nonce,secret.encode(),_aad(user_id))
    return base64.urlsafe_b64encode(nonce+ciphertext).decode()

def open_secret(app_secret,user_id,ciphertext):
    raw=base64.urlsafe_b64decode(ciphertext)
    return AESGCM(_key(app_secret,"metroestate-mfa-encryption-v1")).decrypt(raw[:12],raw[12:],_aad(user_id)).decode()

def matching_step(secret,code,last_step,now=None):
    if len(code)!=6 or not code.isascii() or not code.isdigit():
        return None
    step=int((time.time() if now is None else now)//30)
    totp=pyotp.TOTP(secret)
    for candidate in (step,step-1,step+1):
        if candidate>last_step and hmac.compare_digest(totp.generate_otp(candidate),code):
            return candidate
    return None

def _recovery_hash(app_secret,user_id,code):
    key=_key(app_secret,"metroestate-mfa-recovery-v1")
    return hmac.new(key,f"{user_id}:{code.strip().lower().replace('-','')}".encode(),hashlib.sha256).hexdigest()

def new_recovery_codes(app_secret,user_id):
    codes=[secrets.token_hex(16) for _ in range(10)]
    return codes,[_recovery_hash(app_secret,user_id,code) for code in codes]

def consume_recovery_code(app_secret,user_id,hashes,code):
    if not code:
        return None
    digest=_recovery_hash(app_secret,user_id,code)
    for index,stored in enumerate(hashes or []):
        if hmac.compare_digest(digest,stored):
            return hashes[:index]+hashes[index+1:]
    return None
