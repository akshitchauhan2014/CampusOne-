import secrets, string
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
_hasher = PasswordHasher()
def hash_secret(value):
    if not value: raise ValueError('Secret must not be empty.')
    return _hasher.hash(value)
def verify_secret(encoded, candidate):
    try: return _hasher.verify(encoded, candidate)
    except (VerificationError, InvalidHashError, TypeError): return False
def new_recovery_key():
    chars = string.ascii_uppercase + string.digits
    return '-'.join(''.join(secrets.choice(chars) for _ in range(5)) for _ in range(4))

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
def safe_export_value(value):
    """Neutralise spreadsheet formula injection (leading = + - @ tab CR LF) in any exported cell."""
    if value is None: return ""
    if isinstance(value, (int, float, bool)): return value
    value = str(value)
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value
