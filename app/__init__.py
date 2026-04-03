import os
from flask import Flask, g, session as flask_session
from flask_cors import CORS
from app.database import init_db, set_request_db_path, DB_PATH
from app.auth_service import (
    init_auth_db,
    get_user_count,
    get_user_by_id,
)

def create_app():
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-only-fallback')
    
    # Set upload folder to absolute path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_root = os.getenv('UPLOADS_DIR', os.path.join(os.getenv('DATA_DIR', base_dir), 'uploads'))
    app.config['UPLOAD_FOLDER'] = upload_root
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    CORS(app, supports_credentials=True)
    
    init_db(DB_PATH)
    init_auth_db()
    
    @app.before_request
    def load_current_user():
        user_id = flask_session.get('user_id')
        if user_id:
            user = get_user_by_id(user_id)
            if user:
                g.current_user = user
                set_request_db_path(user.db_path)
                return
            # user missing - clear session
            flask_session.clear()
        g.current_user = None
        set_request_db_path(DB_PATH)
    
    @app.context_processor
    def inject_setup_flag():
        return {'needs_setup': get_user_count() == 0}
    
    from app.routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    
    return app
