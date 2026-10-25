from datetime import datetime, timedelta, timezone
from .security import hash_secret, verify_secret, new_recovery_key
ERROR = 'Unable to sign in with those credentials.'
RECOVERY_ERROR = 'Unable to recover this account with those details.'
RECOVERY_MAX_ATTEMPTS = 5
RECOVERY_LOCK_MINUTES = 15
_DUMMY_HASH = None

def _dummy_verify(candidate):
    """Spend comparable time for unknown usernames so response time does not reveal them."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None: _DUMMY_HASH = hash_secret('campusone-dummy-secret')
    verify_secret(_DUMMY_HASH, candidate or '')

def _locked(stamp):
    if not stamp: return False
    try: return datetime.now(timezone.utc) < datetime.fromisoformat(stamp)
    except ValueError: return True  # unreadable lock value fails closed

class AuthService:
    def __init__(self, db): self.db = db
    def setup(self, username, password):
        username = (username or '').strip()
        if len(username) < 3: raise ValueError('Username must contain at least 3 characters.')
        if len(password or '') < 12: raise ValueError('Password must contain at least 12 characters.')
        key = new_recovery_key()
        self.db.create_admin(username, hash_secret(password), hash_secret(key))
        return key
    def login(self, username, password):
        """Administrator sign-in. Student accounts are rejected here on the server side
        (they sign in through StudentPortalService), not merely by the UI."""
        u = self.db.user_by_name(username or '')
        if not u or u['role'] != 'admin':
            _dummy_verify(password); return False, ERROR, None
        if not u['is_active'] or _locked(u['locked_until']): return False, ERROR, None
        if not verify_secret(u['password_hash'], password or ''):
            attempts = u['failed_attempts'] + 1
            lock = None
            if attempts >= 5:
                attempts = 0
                lock = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            self.db.login_failure(u['id'], attempts, lock)
            return False, ERROR, None
        self.db.login_success(u['id'])
        return True, 'Signed in.', {'id':u['id'],'username':u['username'],'role':u['role']}
    def recover(self, username, recovery_key, new_password):
        """Reset the administrator password with the one-time recovery key.
        Returns a NEW recovery key; the old one is invalidated. Attempts are throttled."""
        if len(new_password or '') < 12:
            raise ValueError('Password must contain at least 12 characters.')
        user = self.db.admin_for_recovery(username or '')
        if not user or not user['recovery_key_hash']:
            _dummy_verify(recovery_key); raise ValueError(RECOVERY_ERROR)
        failures, locked_until = self.db.recovery_state(user['id'])
        if _locked(locked_until): raise ValueError(RECOVERY_ERROR)
        if not verify_secret(user['recovery_key_hash'], str(recovery_key or '').strip().upper()):
            failures += 1; lock = None
            if failures >= RECOVERY_MAX_ATTEMPTS:
                failures = 0; lock = (datetime.now(timezone.utc) + timedelta(minutes=RECOVERY_LOCK_MINUTES)).isoformat()
            self.db.set_recovery_state(user['id'], failures, lock)
            raise ValueError(RECOVERY_ERROR)
        new_key = new_recovery_key()
        self.db.reset_admin_password(user['id'], hash_secret(new_password), hash_secret(new_key))
        self.db.set_recovery_state(user['id'], 0, None)
        return new_key
