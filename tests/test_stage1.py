from campusone.db import Database
from campusone.auth import AuthService
from campusone.security import hash_secret, verify_secret

def test_schema_and_connection(tmp_path):
    db=Database(tmp_path/'campusone.db'); db.initialize(); db.initialize()
    assert db.connect().execute('PRAGMA user_version').fetchone()[0] == 10
    c=db.connect(); assert c.execute('PRAGMA foreign_keys').fetchone()[0] == 1; assert c.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'; c.close()
def test_auth_and_hashing(tmp_path):
    db=Database(tmp_path/'campusone.db'); db.initialize(); auth=AuthService(db)
    key=auth.setup('administrator','a-very-long-password')
    assert len(key.split('-'))==4
    with db.connect() as c:
        row=c.execute('SELECT password_hash,recovery_key_hash FROM users').fetchone()
        assert row['password_hash'] != 'a-very-long-password' and row['recovery_key_hash'] != key
    assert verify_secret(row['password_hash'],'a-very-long-password')
    assert auth.login('administrator','wrong')[0] is False
    assert auth.login('administrator','a-very-long-password')[0] is True
    auth.recover('administrator', key, 'another-long-password')
    assert auth.login('administrator','a-very-long-password')[0] is False
    assert auth.login('administrator','another-long-password')[0] is True
