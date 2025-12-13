import os
from datetime import datetime
from typing import Optional, List
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine
from werkzeug.security import generate_password_hash, check_password_hash
from app.database import DB_PATH

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_DB_PATH = os.path.join(APP_DIR, 'users.db')
USER_DATA_DIR = os.path.join(APP_DIR, 'user_data')

os.makedirs(USER_DATA_DIR, exist_ok=True)

AuthBase = declarative_base()
auth_engine = create_engine(f'sqlite:///{AUTH_DB_PATH}', connect_args={'check_same_thread': False})
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
