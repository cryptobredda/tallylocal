import os
from flask import Flask
from flask_cors import CORS
from app.database import init_db, remove_session

def create_app():
    app = Flask(__name__, static_folder='../static', static_url_path='')
    app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
    
    # Set upload folder to absolute path
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.config['UPLOAD_FOLDER'] = os.path.join(base_dir, 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload
    
    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    CORS(app)
    
    init_db()
    
    # Clean up database session after each request
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        remove_session()
    
    from app.routes import main_bp, api_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    
    return app
