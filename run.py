#!/usr/bin/env python3
import os
from app import create_app

app = create_app()

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_ENV') == 'development'
    print("=" * 50)
    print("Personal Finance Tracker")
    print("=" * 50)
    print("Server starting at http://localhost:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
