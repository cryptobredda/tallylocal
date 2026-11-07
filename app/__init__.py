import logging
import os

from flask import Flask, g, jsonify, request, session as flask_session
from werkzeug.exceptions import HTTPException

from app.auth_service import (get_user_by_id, get_user_count, init_auth_db,
                              verify_agent_token)
from app.config import load_config
from app.database import DB_PATH, init_db, set_request_db_path
from app.security import install_security


def create_app(extra_config=None):
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config.update(load_config())
    if extra_config:
        app.config.update(extra_config)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_root = os.getenv('UPLOADS_DIR', os.path.join(os.getenv('DATA_DIR', base_dir), 'uploads'))
    app.config['UPLOAD_FOLDER'] = upload_root
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    if not app.logger.handlers:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(name)s %(message)s')

    install_security(app)
    init_db(DB_PATH)
    init_auth_db()

    try:
        from app.migrate import ensure_schema
        ensure_schema(DB_PATH)
    except Exception as exc:  # pragma: no cover - startup diagnostics
        app.logger.exception('Schema migration failed: %s', exc)

    @app.before_request
    def load_current_user():
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            token = verify_agent_token(auth[7:].strip())
            user = get_user_by_id(token.user_id) if token else None
            if user:
                g.current_user = user
                g.agent_token = token
                set_request_db_path(user.db_path)
                return
            g.current_user = None
            g.agent_token = None
            return

        g.agent_token = None
        user_id = flask_session.get('user_id')
        if user_id:
            user = get_user_by_id(user_id)
            if user:
                g.current_user = user
                set_request_db_path(user.db_path)
                return
            flask_session.clear()
        g.current_user = None
        set_request_db_path(DB_PATH)

    @app.context_processor
    def inject_setup_flag():
        return {'needs_setup': get_user_count() == 0}

    from app.routes import api_bp, main_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')

    from app.api_v1 import api_v1_bp
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')

    @app.errorhandler(Exception)
    def unhandled(err):
        if isinstance(err, HTTPException):
            return err
        app.logger.exception('Unhandled error on %s %s', request.method, request.path)
        return jsonify({'error': 'internal_error'}), 500

    return app
