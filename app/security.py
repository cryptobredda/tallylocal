"""Request-level security: origin checks, CSRF guard, response headers.

Threat model for a local-first app: the browser is the attacker's delivery
mechanism. Any page the user visits can POST to 127.0.0.1, and with a permissive
CORS policy it can read the response too. So:

  * no wildcard CORS, ever, and no credentialed cross-origin requests
  * cookie-authenticated state changes must carry a custom header (a custom
    header forces a CORS preflight, which a foreign origin cannot pass)
  * bearer-token requests (agents) skip the CSRF check; they are not ambient
"""
from urllib.parse import urlparse

from flask import abort, jsonify, request

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
CSRF_HEADER = 'X-Tally-Client'
CSRF_VALUE = 'web'
# Auth/setup endpoints must be reachable before a session exists.
CSRF_EXEMPT_ENDPOINTS = {
    'api.auth_status', 'api.auth_setup', 'api.auth_login', 'api.auth_logout',
    'api_v1.health',
}


def _origin_allowed(origin, allowed):
    if not origin:
        return False
    return origin.rstrip('/') in allowed


def _same_origin(value, host):
    if not value:
        return False
    return urlparse(value).netloc == host


def install_security(app):
    @app.before_request
    def csrf_guard():
        if request.method in SAFE_METHODS:
            return None
        if not request.path.startswith('/api/'):
            return None
        if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
            return None
        if request.headers.get('Authorization', '').startswith('Bearer '):
            return None

        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')
        host = request.host

        if origin and not _same_origin(origin, host):
            return jsonify({'error': {'code': 'cross_origin_blocked',
                                      'message': 'Cross-origin request blocked'}}), 403
        if origin:
            return None
        if referer and not _same_origin(referer, host):
            return jsonify({'error': {'code': 'cross_origin_blocked',
                                      'message': 'Cross-origin request blocked'}}), 403
        if request.headers.get(CSRF_HEADER) == CSRF_VALUE:
            return None
        if referer:
            return None
        # No Origin/Referer and no marker header: curl/bot. Only safe for
        # token auth, which returned above.
        return jsonify({'error': {'code': 'csrf_missing',
                                  'message': 'Add X-Tally-Client: web or use a bearer token'}}), 403

    @app.after_request
    def security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'

        origin = request.headers.get('Origin')
        allowed = app.config.get('TALLY_CORS_ORIGINS') or []
        if origin and _origin_allowed(origin, allowed):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = \
                'Content-Type, Authorization, X-Tally-Client, X-Requested-With'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response.headers['Vary'] = 'Origin'
        return response

    @app.errorhandler(500)
    def internal_error(err):
        return jsonify({'error': 'internal_error'}), 500
