import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import DB_PATH, make_engine

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.getenv('DATA_DIR', APP_DIR)
AUTH_DB_PATH = os.getenv('USERS_DB_PATH', os.path.join(DATA_ROOT, 'users.db'))
USER_DATA_DIR = os.getenv('USER_DATA_DIR', os.path.join(DATA_ROOT, 'user_data'))

os.makedirs(USER_DATA_DIR, exist_ok=True)

AuthBase = declarative_base()
os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)
auth_engine = make_engine(AUTH_DB_PATH)
AuthSession = sessionmaker(bind=auth_engine, expire_on_commit=False)


class UserAccount(AuthBase):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    db_path = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)
    
    def to_safe_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'is_admin': self.is_admin,
            'db_path': self.db_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None
        }


def init_auth_db():
    os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)
    AuthBase.metadata.create_all(auth_engine)


def get_user_count() -> int:
    session = AuthSession()
    try:
        return session.query(UserAccount).count()
    finally:
        session.close()


def get_user_by_username(username: str) -> Optional[UserAccount]:
    session = AuthSession()
    try:
        return session.query(UserAccount).filter(UserAccount.username == username).first()
    finally:
        session.close()


def get_user_by_id(user_id: int) -> Optional[UserAccount]:
    session = AuthSession()
    try:
        return session.query(UserAccount).filter(UserAccount.id == user_id).first()
    finally:
        session.close()


def list_users() -> List[UserAccount]:
    session = AuthSession()
    try:
        return session.query(UserAccount).order_by(UserAccount.created_at.asc()).all()
    finally:
        session.close()


def create_user(username: str, password: str, is_admin: bool, db_path: Optional[str] = None) -> UserAccount:
    session = AuthSession()
    try:
        if session.query(UserAccount).filter(UserAccount.username == username).first():
            raise ValueError('Username already exists')
        
        if not db_path:
            safe_username = ''.join(ch for ch in username.lower() if ch.isalnum() or ch in ('-', '_'))
            if not safe_username:
                raise ValueError('Invalid username for filesystem path')
            db_path = os.path.join(USER_DATA_DIR, f'{safe_username}.db')
        
        user = UserAccount(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            db_path=db_path
        )
        session.add(user)
        session.commit()
        return user
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_last_login(user: UserAccount):
    session = AuthSession()
    try:
        session.query(UserAccount).filter(UserAccount.id == user.id).update({
            UserAccount.last_login_at: datetime.utcnow()
        })
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def verify_password(user: UserAccount, password: str) -> bool:
    return check_password_hash(user.password_hash, password)


def delete_user_account(user_id: int) -> Optional[dict]:
    session = AuthSession()
    try:
        user = session.query(UserAccount).filter(UserAccount.id == user_id).first()
        if not user:
            return None
        db_path = user.db_path
        session.delete(user)
        session.commit()
        return {'db_path': db_path, 'was_admin': user.is_admin}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_user_admin_status(user_id: int, is_admin: bool) -> Optional[UserAccount]:
    session = AuthSession()
    try:
        user = session.query(UserAccount).filter(UserAccount.id == user_id).first()
        if not user:
            return None
        user.is_admin = is_admin
        session.commit()
        session.refresh(user)
        return user
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_auth_store():
    auth_engine.dispose()
    if os.path.exists(AUTH_DB_PATH):
        try:
            os.remove(AUTH_DB_PATH)
        except OSError:
            pass
    AuthBase.metadata.create_all(auth_engine)


# ==================== AGENT TOKENS ====================

TOKEN_PREFIX = 'tal_'
VALID_SCOPES = {'read', 'write', 'import', 'admin'}


class AgentToken(AuthBase):
    """Scoped bearer token for agents. Lives in users.db, not the finance DB:
    authentication must resolve a user before a data file is opened."""
    __tablename__ = 'agent_tokens'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(80), nullable=False)
    prefix = Column(String(12))
    token_hash = Column(String(64), unique=True, nullable=False)
    scopes = Column(String(120), default='read')
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime)
    expires_at = Column(DateTime)
    revoked_at = Column(DateTime)

    def scope_set(self):
        return {s.strip() for s in (self.scopes or 'read').split(',') if s.strip()}

    def to_safe_dict(self):
        return {
            'id': self.id, 'name': self.name, 'prefix': self.prefix,
            'scopes': sorted(self.scope_set()),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'last_used_at': self.last_used_at.isoformat() if self.last_used_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
        }


def _hash_token(raw):
    import hashlib
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def create_agent_token(user_id, name, scopes, expires_days=None):
    import secrets
    from datetime import timedelta
    clean = sorted({s.strip() for s in (scopes or ['read']) if s.strip() in VALID_SCOPES})
    if not clean:
        raise ValueError('At least one valid scope is required')
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
    session = AuthSession()
    try:
        token = AgentToken(
            user_id=user_id, name=name.strip()[:80], prefix=raw[:12],
            token_hash=_hash_token(raw), scopes=','.join(clean), expires_at=expires_at,
        )
        session.add(token)
        session.commit()
        session.refresh(token)
        return raw, token
    finally:
        session.close()


def verify_agent_token(raw):
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    session = AuthSession()
    try:
        token = session.query(AgentToken).filter(AgentToken.token_hash == _hash_token(raw)).first()
        if not token or token.revoked_at:
            return None
        if token.expires_at and token.expires_at < datetime.utcnow():
            return None
        session.query(AgentToken).filter(AgentToken.id == token.id).update(
            {AgentToken.last_used_at: datetime.utcnow()})
        session.commit()
        return token
    finally:
        session.close()


def list_agent_tokens(user_id):
    session = AuthSession()
    try:
        return session.query(AgentToken).filter(AgentToken.user_id == user_id) \
            .order_by(AgentToken.created_at.desc()).all()
    finally:
        session.close()


def revoke_agent_token(token_id, user_id):
    session = AuthSession()
    try:
        token = session.query(AgentToken).filter(
            AgentToken.id == token_id, AgentToken.user_id == user_id).first()
        if not token:
            return False
        token.revoked_at = datetime.utcnow()
        session.commit()
        return True
    finally:
        session.close()
