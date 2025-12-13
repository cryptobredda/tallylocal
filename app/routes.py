import os
import shutil
import hashlib
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_from_directory, current_app, session as flask_session, g
from werkzeug.utils import secure_filename
from sqlalchemy import func, and_, or_
from app.database import get_session, init_db, DB_PATH, set_request_db_path
from app.models import (
    Transaction, Category, Account, BankStatement, TransactionRule, 
    TransactionType, Settings, AccountType, Asset, AssetHolding, 
    AssetTransaction, AssetPriceCache, AssetType, AssetTransactionType,
    AssetPriceHistory, PortfolioSnapshot
)
from app.auth_service import (
    get_user_count,
    create_user,
    get_user_by_username,
    get_user_by_id,
    verify_password,
    update_last_login,
    list_users,
    delete_user_account,
    set_user_admin_status,
    reset_auth_store,
    USER_DATA_DIR
)
from app.ocr_service import ocr_service, transaction_parser, HSBCStatementParser
from app.categorization_service import categorization_service
from app.account_service import account_service
from app.analytics_service import analytics_service

# Backward compatibility
pot_service = account_service
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

main_bp = Blueprint('main', __name__)
api_bp = Blueprint('api', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'bmp', 'tiff'}


def _serialize_user(user):
    if not user:
        return None
    db_file = os.path.basename(user.db_path) if user.db_path else None
    return {
        'id': user.id,
        'username': user.username,
        'is_admin': user.is_admin,
        'db_file': db_file,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'last_login_at': user.last_login_at.isoformat() if user.last_login_at else None
    }


@api_bp.before_request
def ensure_authenticated():
    public_endpoints = {
        'api.auth_status',
        'api.auth_setup',
        'api.auth_login',
        'api.auth_logout'
    }
    if request.endpoint in public_endpoints:
        return
    user = getattr(g, 'current_user', None)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 401


def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA256 hash of a file for duplicate detection"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def require_admin():
    user = getattr(g, 'current_user', None)
    return user and user.is_admin


def get_user_upload_folder():
    base_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(APP_DIR, 'uploads'))
    user = getattr(g, 'current_user', None)
    if not user or user.db_path == DB_PATH:
        folder = base_folder
    else:
        folder = os.path.join(base_folder, f'user_{user.id}')
    os.makedirs(folder, exist_ok=True)
    return folder


def remove_user_uploads(user_id):
    base_folder = current_app.config.get('UPLOAD_FOLDER', os.path.join(APP_DIR, 'uploads'))
    user_folder = os.path.join(base_folder, f'user_{user_id}')
    if os.path.isdir(user_folder):
        shutil.rmtree(user_folder, ignore_errors=True)


@main_bp.route('/')
def index():
    return send_from_directory(current_app.static_folder, 'index.html')


@api_bp.route('/auth/status', methods=['GET'])
def auth_status():
    needs_setup = get_user_count() == 0
    user = getattr(g, 'current_user', None)
    return jsonify({
        'needs_setup': needs_setup,
        'authenticated': bool(user),
        'user': _serialize_user(user) if user else None
    })


@api_bp.route('/auth/setup', methods=['POST'])
def auth_setup():
    if get_user_count() > 0:
        return jsonify({'error': 'Setup already completed'}), 400
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400
    try:
        user = create_user(username=username, password=password, is_admin=True, db_path=DB_PATH)
        flask_session['user_id'] = user.id
        flask_session['db_path'] = user.db_path
        flask_session['username'] = user.username
        flask_session['is_admin'] = True
        g.current_user = user
        return jsonify({'status': 'ok', 'user': _serialize_user(user)})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/auth/login', methods=['POST'])
def auth_login():
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    user = get_user_by_username(username)
    if not user or not verify_password(user, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    flask_session['user_id'] = user.id
    flask_session['db_path'] = user.db_path
    flask_session['username'] = user.username
    flask_session['is_admin'] = user.is_admin
    g.current_user = user
    set_request_db_path(user.db_path)
    update_last_login(user)
    return jsonify({'status': 'ok', 'user': _serialize_user(user)})


@api_bp.route('/auth/logout', methods=['POST'])
def auth_logout():
    flask_session.clear()
    g.current_user = None
    set_request_db_path(DB_PATH)
    return jsonify({'status': 'ok'})


@api_bp.route('/admin/users', methods=['GET', 'POST'])
def manage_users():
    if not require_admin():
        return jsonify({'error': 'Forbidden'}), 403
    if request.method == 'GET':
        users = [_serialize_user(u) for u in list_users()]
        return jsonify({'users': users})
    data = request.json or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    try:
        user = create_user(username=username, password=password, is_admin=False)
        # Initialize their personal database
        init_db(user.db_path)
        fresh = get_user_by_id(user.id)
        return jsonify({'status': 'ok', 'user': _serialize_user(fresh)})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/admin/users/<int:user_id>', methods=['PUT', 'DELETE'])
def admin_user_detail(user_id):
    if not require_admin():
        return jsonify({'error': 'Forbidden'}), 403
    target = get_user_by_id(user_id)
    if not target:
        return jsonify({'error': 'User not found'}), 404
    if request.method == 'DELETE':
        if user_id == g.current_user.id:
            return jsonify({'error': 'You cannot delete the account you are using'}), 400
        if target.db_path == DB_PATH:
            return jsonify({'error': 'Primary admin cannot be deleted. Use full reset instead.'}), 400
        try:
            info = delete_user_account(user_id)
            if info and info.get('db_path') and os.path.exists(info['db_path']):
                try:
                    os.remove(info['db_path'])
                except OSError:
                    pass
            remove_user_uploads(user_id)
            with processing_lock:
                processing_status_map.pop(user_id, None)
            return jsonify({'status': 'ok'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        data = request.json or {}
        if 'is_admin' not in data:
            return jsonify({'error': 'is_admin flag required'}), 400
        if user_id == g.current_user.id and not data['is_admin']:
            return jsonify({'error': 'You cannot remove your own admin rights'}), 400
        try:
            updated = set_user_admin_status(user_id, bool(data['is_admin']))
            if not updated:
                return jsonify({'error': 'User not found'}), 404
            refreshed = get_user_by_id(user_id)
            return jsonify({'status': 'ok', 'user': _serialize_user(refreshed)})
        except Exception as e:
            return jsonify({'error': str(e)}), 500


@api_bp.route('/admin/reset', methods=['POST'])
def admin_reset_app():
    if not require_admin():
        return jsonify({'error': 'Forbidden'}), 403
    data = request.json or {}
    if data.get('confirm') != 'RESET':
        return jsonify({'error': 'Confirmation text mismatch'}), 400
    try:
        # remove user db files
        for user in list_users():
            if user.db_path and os.path.exists(user.db_path):
                try:
                    os.remove(user.db_path)
                except OSError:
                    pass
        # remove auth db and reinit
        reset_auth_store()
        # remove uploads
        uploads_root = current_app.config.get('UPLOAD_FOLDER', os.path.join(APP_DIR, 'uploads'))
        if os.path.isdir(uploads_root):
            shutil.rmtree(uploads_root, ignore_errors=True)
        os.makedirs(uploads_root, exist_ok=True)
        # remove per-user data dir
        if os.path.isdir(USER_DATA_DIR):
            shutil.rmtree(USER_DATA_DIR, ignore_errors=True)
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        # remove default DB and reinit
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        init_db(DB_PATH)
        with processing_lock:
            processing_status_map.clear()
        flask_session.clear()
        g.current_user = None
        set_request_db_path(DB_PATH)
        return jsonify({'status': 'reset'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': 'running',
        'ocr_available': ocr_service.is_available(),
        'timestamp': datetime.utcnow().isoformat()
    })


@api_bp.route('/reset', methods=['POST'])
def reset_database():
    """Reset the database - drops all data except settings"""
    session = get_session()
    try:
        # Delete all data from tables (order matters due to foreign keys)
        session.query(Transaction).delete()
        session.query(TransactionRule).delete()
        session.query(Account).delete()
        session.query(Category).delete()
        session.query(BankStatement).delete()
        session.commit()
        
        # Reinitialize default categories and main account
        from app.database import _init_default_categories, _init_default_accounts
        _init_default_categories()
        _init_default_accounts()
        
        return jsonify({
            'status': 'success',
            'message': 'Database reset successfully. Default categories and main account restored.'
        })
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@api_bp.route('/upload', methods=['POST'])
def upload_statement():
    """Upload one or more bank statement files"""
    if 'file' not in request.files and 'files' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    # Handle both single file and multiple files
    files = request.files.getlist('files') or request.files.getlist('file')
    if not files or (len(files) == 1 and files[0].filename == ''):
        return jsonify({'error': 'No file selected'}), 400
    
    upload_folder = get_user_upload_folder()
    
    session = get_session()
    results = []
    
    try:
        for file in files:
            if file.filename == '' or not allowed_file(file.filename):
                results.append({
                    'filename': file.filename or 'unknown',
                    'status': 'error',
                    'error': 'Invalid file type'
                })
                continue
            
            # Save file first to calculate hash
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            file_path = os.path.join(upload_folder, filename)
            file.save(file_path)
            
            # Calculate file hash for duplicate detection
            file_hash = calculate_file_hash(file_path)
            
            # Check for duplicate
            existing = session.query(BankStatement).filter(
                BankStatement.file_hash == file_hash
            ).first()
            
            if existing:
                # Remove the uploaded duplicate
                os.remove(file_path)
                results.append({
                    'filename': file.filename,
                    'status': 'duplicate',
                    'existing_id': existing.id,
                    'existing_filename': existing.filename,
                    'message': 'This statement has already been imported'
                })
                continue
            
            # Create statement record
            statement = BankStatement(
                filename=filename,
                file_path=file_path,
                file_hash=file_hash,
                uploaded_at=datetime.utcnow()
            )
            session.add(statement)
            session.flush()
            
            results.append({
                'id': statement.id,
                'filename': filename,
                'original_filename': file.filename,
                'status': 'uploaded'
            })
        
        session.commit()
        
        uploaded_count = len([r for r in results if r['status'] == 'uploaded'])
        duplicate_count = len([r for r in results if r['status'] == 'duplicate'])
        error_count = len([r for r in results if r['status'] == 'error'])
        
        return jsonify({
            'total': len(files),
            'uploaded': uploaded_count,
            'duplicates': duplicate_count,
            'errors': error_count,
            'results': results,
            'message': f'Uploaded {uploaded_count} files. {duplicate_count} duplicates skipped.'
        })
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@api_bp.route('/process/<int:statement_id>', methods=['POST'])
def process_statement(statement_id):
    session = get_session()
    try:
        statement = session.query(BankStatement).filter(BankStatement.id == statement_id).first()
        if not statement:
            return jsonify({'error': 'Statement not found'}), 404
        
        try:
            # Use the new HSBC parser for structured column-based parsing
            hsbc_parser = HSBCStatementParser()
            
            # Detect bank first
            bank_name = hsbc_parser.detect_bank(statement.file_path)
            statement.bank_name = bank_name
            
            # Parse transactions using column-based approach (now returns dict with balances)
            parse_result = hsbc_parser.parse_statement(statement.file_path)
            parsed_transactions = parse_result['transactions']
            
            # Store balances from statement
            statement.opening_balance = parse_result.get('opening_balance')
            statement.closing_balance = parse_result.get('closing_balance')
            
            # Store raw text for reference
            raw_text = ocr_service.process_statement(statement.file_path)
            statement.raw_text = raw_text
            
            transactions_added = []
            for tx_data in parsed_transactions:
                if not tx_data.get('date') or not tx_data.get('amount'):
                    continue
                    
                categorization = categorization_service.categorize_transaction(
                    tx_data['description'],
                    tx_data['amount'],
                    session=session  # Pass the session to avoid conflicts
                )
                
                tx_type = TransactionType.INCOME if tx_data['type'] == 'income' else TransactionType.EXPENSE
                
                transaction = Transaction(
                    date=tx_data['date'],
                    description=tx_data['description'],
                    original_description=tx_data['description'],
                    amount=tx_data['amount'],
                    transaction_type=tx_type,
                    category_id=categorization['category_id'] if categorization else None,
                    bank_name=bank_name,
                    statement_file=statement.filename
                )
                session.add(transaction)
                session.flush()  # Flush to get the ID
                
                transactions_added.append({
                    'id': transaction.id,
                    'description': tx_data['description'],
                    'amount': tx_data['amount'],
                    'type': tx_data['type'],
                    'payment_type': tx_data.get('payment_type'),
                    'category': categorization['category_name'] if categorization else None
                })
            
            # Update statement status
            statement.processed = True
            statement.processed_at = datetime.utcnow()
            statement.transactions_count = len(transactions_added)
            
            # Commit all changes
            session.commit()
            
            # Handle pot allocations after commit (separate transactions)
            for tx_data in parsed_transactions:
                if tx_data.get('type') == 'income' and tx_data.get('amount'):
                    try:
                        pot_service.auto_allocate_from_income(
                            tx_data['amount'],
                            tx_data.get('description', '')
                        )
                    except Exception as e:
                        print(f"Warning: Failed to allocate to pots: {e}")
            
            return jsonify({
                'status': 'success',
                'statement_id': statement_id,
                'bank_detected': bank_name,
                'transactions_count': len(transactions_added),
                'opening_balance': parse_result.get('opening_balance'),
                'closing_balance': parse_result.get('closing_balance'),
                'transactions': transactions_added
            })
            
        except Exception as e:
            session.rollback()
            statement.error_message = str(e)
            try:
                session.commit()
            except:
                pass
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500
    finally:
        session.close()


# Global processing status tracker
import threading
import time

PROCESSING_TEMPLATE = {
    'is_processing': False,
    'started_at': None,
    'current_file': None,
    'current_index': 0,
    'total_files': 0,
    'completed': [],
    'errors': [],
    'phase': 'idle'
}
processing_status_map = {}
processing_lock = threading.Lock()


def _status_key():
    user = getattr(g, 'current_user', None)
    return user.id if user else 'global'


def _get_processing_status(create=True):
    key = _status_key()
    status = processing_status_map.get(key)
    if not status and create:
        status = PROCESSING_TEMPLATE.copy()
        status['completed'] = []
        status['errors'] = []
        processing_status_map[key] = status
    return status


def update_processing_status(**kwargs):
    with processing_lock:
        status = _get_processing_status()
        status.update(kwargs)

@api_bp.route('/process-status', methods=['GET'])
def get_processing_status():
    """Get current processing status for progress tracking"""
    with processing_lock:
        status = _get_processing_status()
        elapsed = None
        if status['started_at']:
            elapsed = time.time() - status['started_at']
        
        return jsonify({
            'is_processing': status['is_processing'],
            'phase': status['phase'],
            'current_file': status['current_file'],
            'current_index': status['current_index'],
            'total_files': status['total_files'],
            'completed_count': len(status['completed']),
            'error_count': len(status['errors']),
            'elapsed_seconds': elapsed,
            'completed': status['completed'][-5:],
            'errors': status['errors'][-5:]
        })

@api_bp.route('/process-all', methods=['POST'])
def process_all_pending():
    """Process all unprocessed statements"""
    from app.ocr_service import reset_ocr_service
    from app.models import Account, AccountType
    
    # Check if already processing
    with processing_lock:
        status = _get_processing_status()
        if status['is_processing']:
            return jsonify({'error': 'Processing already in progress'}), 409
    
    # Get account_id from request body (optional)
    data = request.get_json() or {}
    target_account_id = data.get('account_id')
    
    session = get_session()
    try:
        # If no account specified, use the main account
        if not target_account_id:
            main_account = session.query(Account).filter(
                Account.account_type == AccountType.MAIN,
                Account.is_active == True
            ).first()
            if main_account:
                target_account_id = main_account.id
        
        pending = session.query(BankStatement).filter(
            BankStatement.processed == False,
            BankStatement.error_message == None
        ).all()
        
        # Get statement IDs and filenames upfront
        pending_items = [(s.id, s.filename) for s in pending]
        session.close()
        
        if not pending_items:
            return jsonify({'processed': 0, 'errors': 0, 'results': [], 'message': 'No pending statements'})
        
        # Initialize processing status
        update_processing_status(
            is_processing=True,
            started_at=time.time(),
            current_file=None,
            current_index=0,
            total_files=len(pending_items),
            completed=[],
            errors=[],
            phase='initializing'
        )
        
        # Create single parser instance for all statements
        hsbc_parser = HSBCStatementParser()
        
        update_processing_status(phase='processing')
        
        results = []
        for idx, (stmt_id, filename) in enumerate(pending_items):
            update_processing_status(
                current_file=filename,
                current_index=idx + 1
            )
            
            session = get_session()
            statement = session.query(BankStatement).filter(BankStatement.id == stmt_id).first()
            
            if not statement:
                continue
                
            try:
                bank_name = hsbc_parser.detect_bank(statement.file_path)
                statement.bank_name = bank_name
                statement.account_id = target_account_id  # Link statement to account
                
                parse_result = hsbc_parser.parse_statement(statement.file_path)
                parsed_transactions = parse_result['transactions']
                
                statement.opening_balance = parse_result.get('opening_balance')
                statement.closing_balance = parse_result.get('closing_balance')
                
                tx_count = 0
                for tx_data in parsed_transactions:
                    if not tx_data.get('date') or not tx_data.get('amount'):
                        continue
                    
                    categorization = categorization_service.categorize_transaction(
                        tx_data['description'],
                        tx_data['amount'],
                        session=session
                    )
                    
                    tx_type = TransactionType.INCOME if tx_data['type'] == 'income' else TransactionType.EXPENSE
                    
                    transaction = Transaction(
                        date=tx_data['date'],
                        description=tx_data['description'],
                        original_description=tx_data['description'],
                        amount=tx_data['amount'],
                        transaction_type=tx_type,
                        category_id=categorization['category_id'] if categorization else None,
                        account_id=target_account_id,  # Assign to selected account
                        bank_name=bank_name,
                        statement_file=statement.filename
                    )
                    session.add(transaction)
                    tx_count += 1
                
                statement.processed = True
                statement.processed_at = datetime.utcnow()
                statement.transactions_count = tx_count
                session.commit()
                
                result = {
                    'id': stmt_id,
                    'filename': statement.filename,
                    'status': 'success',
                    'transactions_count': tx_count
                }
                results.append(result)
                
                with processing_lock:
                    status = _get_processing_status()
                    status['completed'].append(result)
                
            except Exception as e:
                import traceback
                error_detail = f"{str(e)}\n{traceback.format_exc()}"
                print(f"ERROR processing statement {statement.filename}: {error_detail}")
                
                # Reset OCR engine on error (PaddleOCR can get into corrupted state)
                try:
                    reset_ocr_service()
                except:
                    pass
                
                result = {
                    'id': stmt_id,
                    'filename': statement.filename if statement else 'unknown',
                    'status': 'error',
                    'error': str(e)
                }
                results.append(result)
                
                with processing_lock:
                    status = _get_processing_status()
                    status['errors'].append(result)
            finally:
                session.close()
        
        # Mark processing as complete
        update_processing_status(
            is_processing=False,
            phase='complete',
            current_file=None
        )
        
        return jsonify({
            'processed': len([r for r in results if r['status'] == 'success']),
            'errors': len([r for r in results if r['status'] == 'error']),
            'results': results
        })
    except Exception as e:
        update_processing_status(
            is_processing=False,
            phase='idle',
            current_file=None
        )
        return jsonify({'error': str(e)}), 500


@api_bp.route('/transactions', methods=['POST'])
def create_transaction():
    """Create a new manual transaction"""
    session = get_session()
    try:
        data = request.json
        
        # Validate required fields
        if not data.get('date'):
            return jsonify({'error': 'Date is required'}), 400
        if not data.get('description'):
            return jsonify({'error': 'Description is required'}), 400
        if data.get('amount') is None:
            return jsonify({'error': 'Amount is required'}), 400
        
        # Parse date
        tx_date = datetime.fromisoformat(data['date'].replace('Z', '+00:00')) if isinstance(data['date'], str) else data['date']
        
        # Determine transaction type
        tx_type_str = data.get('type', 'expense').lower()
        if tx_type_str == 'income':
            tx_type = TransactionType.INCOME
        elif tx_type_str == 'transfer':
            tx_type = TransactionType.TRANSFER
        else:
            tx_type = TransactionType.EXPENSE
        
        # Get account_id - default to main account if not specified
        account_id = data.get('account_id')
        if not account_id:
            main_account = session.query(Account).filter(
                Account.account_type == AccountType.MAIN,
                Account.is_active == True
            ).first()
            if main_account:
                account_id = main_account.id
        
        transaction = Transaction(
            date=tx_date,
            description=data['description'],
            original_description=data['description'],
            amount=abs(float(data['amount'])),
            transaction_type=tx_type,
            category_id=data.get('category_id'),
            account_id=account_id,
            notes=data.get('notes'),
            tags=data.get('tags'),
            is_recurring=data.get('is_recurring', False)
        )
        
        session.add(transaction)
        session.commit()
        
        return jsonify({
            'id': transaction.id,
            'message': 'Transaction created successfully'
        }), 201
        
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@api_bp.route('/transactions', methods=['GET'])
def get_transactions():
    from app.merchant_service import recognize_merchant
    
    session = get_session()
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        category_id = request.args.get('category_id', type=int)
        pot_id = request.args.get('pot_id', type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        query = session.query(Transaction).order_by(Transaction.date.desc())
        
        if category_id:
            query = query.filter(Transaction.category_id == category_id)
        if pot_id:
            query = query.filter(Transaction.pot_id == pot_id)
        if start_date:
            query = query.filter(Transaction.date >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(Transaction.date <= datetime.fromisoformat(end_date))
        
        total = query.count()
        transactions = query.offset((page - 1) * per_page).limit(per_page).all()
        
        tx_list = []
        for t in transactions:
            tx_data = {
                'id': t.id,
                'date': t.date.isoformat(),
                'description': t.description,
                'original_description': t.original_description,
                'amount': t.amount,
                'type': t.transaction_type.value,
                'category_id': t.category_id,
                'category_name': t.category.name if t.category else None,
                'category_color': t.category.color if t.category else None,
                'pot_id': t.pot_id,
                'pot_name': t.pot.name if t.pot else None,
                'account_id': t.account_id,
                'account_name': t.account.name if t.account else None,
                'account_type': t.account.account_type.value if t.account and t.account.account_type else None,
                'account_color': t.account.color if t.account else None,
                'is_one_off': t.is_one_off,
                'is_recurring': t.is_recurring,
                'is_internal_transfer': t.is_internal_transfer,
                'linked_transaction_id': t.linked_transaction_id,
                'balance_after': t.balance_after,
                'notes': t.notes,
                'tags': t.tags,
                'bank_name': t.bank_name,
                'account_number': t.account_number,
                'statement_file': t.statement_file,
                'created_at': t.created_at.isoformat() if t.created_at else None,
                'updated_at': t.updated_at.isoformat() if t.updated_at else None
            }
            # Add merchant info if recognized
            merchant = recognize_merchant(t.description)
            if merchant:
                tx_data['merchant'] = merchant
            tx_list.append(tx_data)
        
        return jsonify({
            'transactions': tx_list,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        })
    finally:
        session.close()


@api_bp.route('/transactions/<int:transaction_id>', methods=['PUT'])
def update_transaction(transaction_id):
    session = get_session()
    try:
        transaction = session.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not transaction:
            return jsonify({'error': 'Transaction not found'}), 404
        
        data = request.json
        description = transaction.description
        
        if 'description' in data:
            transaction.description = data['description']
            description = data['description']
        
        if 'category_id' in data:
            transaction.category_id = data['category_id']
        
        if 'pot_id' in data:
            transaction.pot_id = data['pot_id']
        
        if 'is_one_off' in data:
            transaction.is_one_off = data['is_one_off']
        
        # Handle transfer with receiving account
        linked_tx_id = None
        if data.get('is_transfer') and data.get('receiving_account_id'):
            receiving_account_id = data['receiving_account_id']
            
            # Mark this transaction as internal transfer
            transaction.is_internal_transfer = True
            
            # Delete any existing linked transaction
            if transaction.linked_transaction_id:
                old_linked = session.query(Transaction).filter(
                    Transaction.id == transaction.linked_transaction_id
                ).first()
                if old_linked:
                    session.delete(old_linked)
            
            # Create the opposite transaction in the receiving account
            # If original is expense (outgoing), receiving account gets income (incoming)
            # If original is income (incoming), receiving account gets expense (outgoing)
            opposite_type = TransactionType.INCOME if transaction.transaction_type == TransactionType.EXPENSE else TransactionType.EXPENSE
            
            linked_tx = Transaction(
                date=transaction.date,
                description=f"Transfer from {transaction.account.name if transaction.account else 'External'}: {transaction.description}",
                original_description=transaction.original_description,
                amount=transaction.amount,
                transaction_type=opposite_type,
                category_id=transaction.category_id,
                account_id=receiving_account_id,
                is_internal_transfer=True,
                bank_name=transaction.bank_name
            )
            session.add(linked_tx)
            session.flush()  # Get the ID
            
            # Link them together
            transaction.linked_transaction_id = linked_tx.id
            linked_tx.linked_transaction_id = transaction.id
            linked_tx_id = linked_tx.id
        elif not data.get('is_transfer'):
            # If changing away from transfer, remove linked transaction
            if transaction.linked_transaction_id:
                old_linked = session.query(Transaction).filter(
                    Transaction.id == transaction.linked_transaction_id
                ).first()
                if old_linked:
                    session.delete(old_linked)
                transaction.linked_transaction_id = None
            transaction.is_internal_transfer = False
        
        # Commit the changes
        session.commit()
        
        # Capture values before any other operations that might affect the session
        result = {
            'id': transaction_id,
            'description': transaction.description,
            'category_id': transaction.category_id,
            'pot_id': transaction.pot_id,
            'is_one_off': transaction.is_one_off,
            'linked_transaction_id': linked_tx_id,
            'message': 'Transaction updated successfully'
        }
        
        # Learn from categorization after commit (uses its own session)
        if 'category_id' in data:
            is_one_off = data.get('is_one_off', False)
            try:
                categorization_service.learn_from_categorization(
                    description,
                    data['category_id'],
                    data.get('pot_id'),
                    is_one_off
                )
            except Exception as e:
                print(f"Warning: Failed to learn from categorization: {e}")
        
        return jsonify(result)
    finally:
        session.close()


@api_bp.route('/transactions/<int:transaction_id>', methods=['DELETE'])
def delete_transaction(transaction_id):
    session = get_session()
    try:
        transaction = session.query(Transaction).filter(Transaction.id == transaction_id).first()
        if not transaction:
            return jsonify({'error': 'Transaction not found'}), 404
        
        session.delete(transaction)
        session.commit()
        
        return jsonify({'message': 'Transaction deleted successfully'})
    finally:
        session.close()


@api_bp.route('/transactions/bulk-update', methods=['PUT', 'POST'])
def bulk_update_transactions():
    """Update multiple transactions at once"""
    session = get_session()
    try:
        data = request.json
        ids = data.get('ids', data.get('transaction_ids', []))
        updates = data.get('updates', {})
        
        if not ids:
            return jsonify({'error': 'No transaction IDs provided'}), 400
        
        if not updates:
            return jsonify({'error': 'No updates provided'}), 400
        
        # Build the update query
        transactions = session.query(Transaction).filter(Transaction.id.in_(ids)).all()
        
        is_transfer = updates.get('is_transfer', False)
        receiving_account_id = updates.get('receiving_account_id')
        
        updated_count = 0
        linked_created = 0
        
        for tx in transactions:
            if 'category_id' in updates:
                tx.category_id = updates['category_id']
            if 'account_id' in updates:
                tx.account_id = updates['account_id']
            if 'pot_id' in updates:
                tx.pot_id = updates['pot_id'] if updates['pot_id'] is not None else None
            
            # Handle transfer with receiving account
            if is_transfer:
                tx.is_internal_transfer = True
                
                # Delete any existing linked transaction
                if tx.linked_transaction_id:
                    old_linked = session.query(Transaction).filter(
                        Transaction.id == tx.linked_transaction_id
                    ).first()
                    if old_linked:
                        session.delete(old_linked)
                    tx.linked_transaction_id = None
                
                # Create linked transaction if receiving account specified
                if receiving_account_id:
                    opposite_type = TransactionType.INCOME if tx.transaction_type == TransactionType.EXPENSE else TransactionType.EXPENSE
                    
                    linked_tx = Transaction(
                        date=tx.date,
                        description=f"Transfer from {tx.account.name if tx.account else 'External'}: {tx.description}",
                        original_description=tx.original_description,
                        amount=tx.amount,
                        transaction_type=opposite_type,
                        category_id=tx.category_id,
                        account_id=receiving_account_id,
                        is_internal_transfer=True,
                        bank_name=tx.bank_name
                    )
                    session.add(linked_tx)
                    session.flush()
                    
                    tx.linked_transaction_id = linked_tx.id
                    linked_tx.linked_transaction_id = tx.id
                    linked_created += 1
            
            updated_count += 1
        
        session.commit()
        
        message = f'Successfully updated {updated_count} transactions'
        if linked_created > 0:
            message += f', created {linked_created} linked transactions'
        
        return jsonify({
            'message': message,
            'updated': updated_count,
            'linked_created': linked_created
        })
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@api_bp.route('/categories', methods=['GET'])
def get_categories():
    categories = categorization_service.get_all_categories()
    return jsonify({'categories': categories})


@api_bp.route('/categories', methods=['POST'])
def create_category():
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Category name required'}), 400
    
    category = categorization_service.create_category(
        name=data['name'],
        color=data.get('color', '#6366f1'),
        icon=data.get('icon', 'tag')
    )
    return jsonify(category)


@api_bp.route('/categories/<int:category_id>', methods=['DELETE'])
def delete_category(category_id):
    session = get_session()
    try:
        category = session.query(Category).filter(Category.id == category_id).first()
        if not category:
            return jsonify({'error': 'Category not found'}), 404
        
        if category.is_system:
            return jsonify({'error': 'Cannot delete system category'}), 400
        
        session.delete(category)
        session.commit()
        return jsonify({'message': 'Category deleted successfully'})
    finally:
        session.close()


@api_bp.route('/rules', methods=['GET'])
def get_rules():
    rules = categorization_service.get_rules()
    return jsonify({'rules': rules})


@api_bp.route('/rules', methods=['POST'])
def create_rule():
    data = request.json
    session = get_session()
    try:
        rule = TransactionRule(
            pattern=data['pattern'],
            match_type=data.get('match_type', 'contains'),
            category_id=data.get('category_id'),
            pot_id=data.get('pot_id'),
            priority=data.get('priority', 0),
            is_active=True
        )
        session.add(rule)
        session.commit()
        return jsonify({
            'id': rule.id,
            'pattern': rule.pattern,
            'match_type': rule.match_type,
            'category_id': rule.category_id,
            'pot_id': rule.pot_id
        })
    finally:
        session.close()


@api_bp.route('/rules/<int:rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    session = get_session()
    try:
        rule = session.query(TransactionRule).filter(TransactionRule.id == rule_id).first()
        if not rule:
            return jsonify({'error': 'Rule not found'}), 404
        
        session.delete(rule)
        session.commit()
        return jsonify({'message': 'Rule deleted successfully'})
    finally:
        session.close()


# ============== Account Routes (formerly Pots) ==============

@api_bp.route('/accounts', methods=['GET'])
def get_accounts():
    """Get all accounts with their balances"""
    accounts = account_service.get_all_accounts()
    return jsonify({'accounts': accounts})


@api_bp.route('/accounts/main', methods=['GET'])
def get_main_account():
    """Get the main account"""
    account = account_service.get_main_account()
    if not account:
        return jsonify({'error': 'Main account not found'}), 404
    return jsonify(account)


@api_bp.route('/accounts/balance', methods=['GET'])
def get_total_balance():
    """Get balance summary (main account + sub-accounts separately)"""
    balance = account_service.get_total_balance()
    return jsonify(balance)


@api_bp.route('/accounts', methods=['POST'])
def create_account():
    """Create a new sub-account"""
    data = request.json
    if not data.get('name'):
        return jsonify({'error': 'Account name required'}), 400
    
    try:
        account = account_service.create_account(
            name=data['name'],
            account_type=data.get('account_type', 'sub'),
            color=data.get('color', '#10b981'),
            icon=data.get('icon', 'wallet'),
            description=data.get('description')
        )
        return jsonify(account)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/accounts/<int:account_id>', methods=['GET'])
def get_account(account_id):
    """Get a single account by ID"""
    account = account_service.get_account(account_id)
    if not account:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify(account)


@api_bp.route('/accounts/<int:account_id>', methods=['PUT'])
def update_account(account_id):
    """Update an account"""
    data = request.json
    account = account_service.update_account(account_id, **data)
    if not account:
        return jsonify({'error': 'Account not found'}), 404
    return jsonify(account)


@api_bp.route('/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    """Delete a sub-account (cannot delete main account)"""
    try:
        success = account_service.delete_account(account_id)
        if not success:
            return jsonify({'error': 'Account not found'}), 404
        return jsonify({'message': 'Account deleted successfully'})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/accounts/<int:account_id>/transactions', methods=['GET'])
def get_account_transactions(account_id):
    """Get transactions for a specific account"""
    include_transfers = request.args.get('include_transfers', 'true').lower() == 'true'
    limit = request.args.get('limit', 50, type=int)
    transactions = account_service.get_account_transactions(account_id, include_transfers, limit)
    return jsonify({'transactions': transactions})


@api_bp.route('/transfers', methods=['POST'])
def create_transfer():
    """Create a transfer between accounts"""
    data = request.json
    
    if not data.get('from_account_id'):
        return jsonify({'error': 'Source account required'}), 400
    if not data.get('to_account_id'):
        return jsonify({'error': 'Destination account required'}), 400
    if not data.get('amount') or data['amount'] <= 0:
        return jsonify({'error': 'Valid amount required'}), 400
    
    try:
        transfer = account_service.create_transfer(
            from_account_id=data['from_account_id'],
            to_account_id=data['to_account_id'],
            amount=data['amount'],
            description=data.get('description'),
            date=datetime.fromisoformat(data['date']) if data.get('date') else None
        )
        return jsonify(transfer)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@api_bp.route('/transfers', methods=['GET'])
def get_transfers():
    """Get transfer history"""
    limit = request.args.get('limit', 50, type=int)
    transfers = account_service.get_transfer_history(limit)
    return jsonify({'transfers': transfers})


# ============== Backward Compatibility (Pots) ==============
# These routes maintain backward compatibility with existing frontend

@api_bp.route('/pots', methods=['GET'])
def get_pots():
    """Backward compatibility: Get all accounts as pots"""
    accounts = account_service.get_all_accounts()
    # Transform to pot format
    pots = []
    for acc in accounts:
        pots.append({
            'id': acc['id'],
            'name': acc['name'],
            'target_amount': 0,
            'current_amount': acc['balance'],
            'allocated_total': acc['total_in'],
            'spent_total': acc['total_out'],
            'available': acc['balance'],
            'color': acc['color'],
            'icon': acc['icon'],
            'auto_allocate_percentage': 0,
            'progress': 0,
            'is_main': acc['is_main'],
            'account_type': acc['account_type']
        })
    return jsonify({'pots': pots})


@api_bp.route('/analytics/dashboard', methods=['GET'])
def get_dashboard():
    stats = analytics_service.get_dashboard_stats()
    return jsonify(stats)


@api_bp.route('/analytics/monthly', methods=['GET'])
def get_monthly_summary():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    summary = analytics_service.get_monthly_summary(year, month)
    return jsonify(summary)


@api_bp.route('/analytics/summary', methods=['GET'])
def get_date_range_summary():
    """Get analytics summary for a date range"""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    summary = analytics_service.get_date_range_summary(start_date, end_date)
    return jsonify(summary)


@api_bp.route('/analytics/comprehensive', methods=['GET'])
def get_comprehensive_analytics():
    """Get comprehensive analytics for a date range including all charts and statistics"""
    start_date = request.args.get('start')
    end_date = request.args.get('end')
    analytics = analytics_service.get_comprehensive_analytics(start_date, end_date)
    return jsonify(analytics)


@api_bp.route('/analytics/trends', methods=['GET'])
def get_spending_trends():
    months = request.args.get('months', 6, type=int)
    trends = analytics_service.get_spending_trends(months)
    return jsonify({'trends': trends})


@api_bp.route('/analytics/category/<int:category_id>/trends', methods=['GET'])
def get_category_trends(category_id):
    months = request.args.get('months', 6, type=int)
    trends = analytics_service.get_category_trends(category_id, months)
    return jsonify({'trends': trends})


@api_bp.route('/analytics/merchants', methods=['GET'])
def get_top_merchants():
    limit = request.args.get('limit', 10, type=int)
    days = request.args.get('days', 0, type=int)  # Default 0 = use latest month
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    merchants = analytics_service.get_top_merchants(limit, days, year, month)
    return jsonify({'merchants': merchants})


@api_bp.route('/analytics/recurring', methods=['GET'])
def get_recurring_transactions():
    recurring = analytics_service.get_recurring_transactions()
    return jsonify({'recurring': recurring})


@api_bp.route('/analytics/categories', methods=['GET'])
def get_all_time_categories():
    """Get all-time category breakdown for analytics page"""
    data = analytics_service.get_all_time_category_breakdown()
    return jsonify(data)


@api_bp.route('/analytics/balance-breakdown', methods=['GET'])
def get_balance_breakdown():
    """Get detailed breakdown of how the balance was calculated"""
    breakdown = analytics_service.get_balance_breakdown()
    return jsonify(breakdown)


@api_bp.route('/statements', methods=['GET'])
def get_statements():
    session = get_session()
    try:
        statements = session.query(BankStatement).order_by(
            BankStatement.uploaded_at.desc()
        ).all()
        
        return jsonify({
            'statements': [
                {
                    'id': s.id,
                    'filename': s.filename,
                    'bank_name': s.bank_name,
                    'processed': s.processed,
                    'transactions_count': s.transactions_count,
                    'uploaded_at': s.uploaded_at.isoformat(),
                    'error_message': s.error_message
                }
                for s in statements
            ]
        })
    finally:
        session.close()


# ==================== BUDGET ENDPOINTS ====================

from app.models import MonthlyBudget

@api_bp.route('/budgets', methods=['GET'])
def get_budgets():
    """Get all budgets, optionally filtered by year/month"""
    session = get_session()
    try:
        year = request.args.get('year', type=int)
        month = request.args.get('month', type=int)
        
        query = session.query(MonthlyBudget)
        if year:
            query = query.filter(MonthlyBudget.year == year)
        if month:
            query = query.filter(MonthlyBudget.month == month)
        
        budgets = query.all()
        
        # Get actual spending for each budget
        result = []
        for b in budgets:
            start_date = datetime(b.year, b.month, 1)
            if b.month == 12:
                end_date = datetime(b.year + 1, 1, 1)
            else:
                end_date = datetime(b.year, b.month + 1, 1)
            
            actual = session.query(func.sum(Transaction.amount)).filter(
                and_(
                    Transaction.category_id == b.category_id,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).scalar() or 0
            
            result.append({
                'id': b.id,
                'year': b.year,
                'month': b.month,
                'category_id': b.category_id,
                'category_name': b.category.name if b.category else None,
                'category_color': b.category.color if b.category else None,
                'budget_amount': b.budget_amount,
                'actual_amount': float(actual),
                'remaining': b.budget_amount - float(actual),
                'percentage_used': (float(actual) / b.budget_amount * 100) if b.budget_amount > 0 else 0,
                'rollover': b.rollover
            })
        
        return jsonify({'budgets': result})
    finally:
        session.close()


@api_bp.route('/budgets', methods=['POST'])
def create_budget():
    """Create or update a budget for a category/month"""
    session = get_session()
    try:
        data = request.json
        
        year = data.get('year', datetime.now().year)
        month = data.get('month', datetime.now().month)
        category_id = data.get('category_id')
        budget_amount = data.get('budget_amount')
        
        if not category_id or budget_amount is None:
            return jsonify({'error': 'category_id and budget_amount are required'}), 400
        
        # Check if budget already exists for this category/month
        existing = session.query(MonthlyBudget).filter(
            and_(
                MonthlyBudget.year == year,
                MonthlyBudget.month == month,
                MonthlyBudget.category_id == category_id
            )
        ).first()
        
        if existing:
            existing.budget_amount = budget_amount
            existing.rollover = data.get('rollover', False)
            budget_id = existing.id
        else:
            budget = MonthlyBudget(
                year=year,
                month=month,
                category_id=category_id,
                budget_amount=budget_amount,
                rollover=data.get('rollover', False)
            )
            session.add(budget)
            session.flush()
            budget_id = budget.id
        
        session.commit()
        
        return jsonify({
            'id': budget_id,
            'message': 'Budget saved successfully'
        })
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@api_bp.route('/budgets/<int:budget_id>', methods=['DELETE'])
def delete_budget(budget_id):
    """Delete a budget"""
    session = get_session()
    try:
        budget = session.query(MonthlyBudget).filter(MonthlyBudget.id == budget_id).first()
        if not budget:
            return jsonify({'error': 'Budget not found'}), 404
        
        session.delete(budget)
        session.commit()
        
        return jsonify({'message': 'Budget deleted successfully'})
    finally:
        session.close()


@api_bp.route('/budgets/summary', methods=['GET'])
def get_budget_summary():
    """Get budget summary for current month with alerts"""
    session = get_session()
    try:
        now = datetime.now()
        year = request.args.get('year', now.year, type=int)
        month = request.args.get('month', now.month, type=int)
        
        budgets = session.query(MonthlyBudget).filter(
            and_(
                MonthlyBudget.year == year,
                MonthlyBudget.month == month
            )
        ).all()
        
        start_date = datetime(year, month, 1)
        if month == 12:
            end_date = datetime(year + 1, 1, 1)
        else:
            end_date = datetime(year, month + 1, 1)
        
        total_budgeted = 0
        total_spent = 0
        alerts = []
        categories_data = []
        
        for b in budgets:
            actual = session.query(func.sum(Transaction.amount)).filter(
                and_(
                    Transaction.category_id == b.category_id,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).scalar() or 0
            
            total_budgeted += b.budget_amount
            total_spent += float(actual)
            
            percentage = (float(actual) / b.budget_amount * 100) if b.budget_amount > 0 else 0
            
            cat_data = {
                'category_id': b.category_id,
                'category_name': b.category.name if b.category else 'Unknown',
                'budgeted': b.budget_amount,
                'spent': float(actual),
                'remaining': b.budget_amount - float(actual),
                'percentage': percentage
            }
            categories_data.append(cat_data)
            
            # Generate alerts
            if percentage >= 100:
                alerts.append({
                    'type': 'exceeded',
                    'category': b.category.name if b.category else 'Unknown',
                    'message': f'{b.category.name} is over budget by {formatCurrency(float(actual) - b.budget_amount)}'
                })
            elif percentage >= 80:
                alerts.append({
                    'type': 'warning',
                    'category': b.category.name if b.category else 'Unknown',
                    'message': f'{b.category.name} is at {percentage:.0f}% of budget'
                })
        
        return jsonify({
            'year': year,
            'month': month,
            'total_budgeted': total_budgeted,
            'total_spent': total_spent,
            'total_remaining': total_budgeted - total_spent,
            'overall_percentage': (total_spent / total_budgeted * 100) if total_budgeted > 0 else 0,
            'categories': categories_data,
            'alerts': alerts
        })
    finally:
        session.close()


def formatCurrency(amount):
    """Helper to format currency for alerts"""
    return f"£{abs(amount):,.2f}"


# ==================== EXPORT/IMPORT ENDPOINTS ====================

@api_bp.route('/export/transactions', methods=['GET'])
def export_transactions():
    """Export transactions as CSV"""
    import csv
    import io
    from flask import Response
    
    session = get_session()
    try:
        # Get filter parameters
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        category_id = request.args.get('category_id', type=int)
        account_id = request.args.get('account_id', type=int)
        
        query = session.query(Transaction).order_by(Transaction.date.desc())
        
        if start_date:
            query = query.filter(Transaction.date >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(Transaction.date <= datetime.fromisoformat(end_date))
        if category_id:
            query = query.filter(Transaction.category_id == category_id)
        if account_id:
            query = query.filter(Transaction.account_id == account_id)
        
        transactions = query.all()
        
        # Create CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow([
            'Date', 'Description', 'Amount', 'Type', 'Category', 'Account',
            'Notes', 'Tags', 'Is Recurring', 'Bank Name'
        ])
        
        for t in transactions:
            writer.writerow([
                t.date.strftime('%Y-%m-%d'),
                t.description,
                t.amount,
                t.transaction_type.value,
                t.category.name if t.category else '',
                t.account.name if t.account else '',
                t.notes or '',
                t.tags or '',
                'Yes' if t.is_recurring else 'No',
                t.bank_name or ''
            ])
        
        output.seek(0)
        
        filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    finally:
        session.close()


@api_bp.route('/export/full', methods=['GET'])
def export_full_data():
    """Export all data as JSON for full backup/migration"""
    import json as json_lib
    from flask import Response
    
    session = get_session()
    try:
        # Export categories
        categories = session.query(Category).all()
        categories_data = [
            {
                'name': c.name,
                'color': c.color,
                'icon': c.icon,
                'is_system': c.is_system
            }
            for c in categories
        ]
        
        # Export accounts
        accounts = session.query(Account).all()
        accounts_data = [
            {
                'name': a.name,
                'account_type': a.account_type.value,
                'color': a.color,
                'icon': a.icon,
                'description': a.description,
                'opening_balance': a.opening_balance,
                'is_active': a.is_active,
                'is_archived': a.is_archived
            }
            for a in accounts
        ]
        
        # Export transactions with all fields
        transactions = session.query(Transaction).order_by(Transaction.date).all()
        transactions_data = [
            {
                'date': t.date.isoformat(),
                'description': t.description,
                'original_description': t.original_description,
                'amount': t.amount,
                'transaction_type': t.transaction_type.value,
                'balance_after': t.balance_after,
                'category_name': t.category.name if t.category else None,
                'account_name': t.account.name if t.account else None,
                'is_internal_transfer': t.is_internal_transfer,
                'is_one_off': t.is_one_off,
                'is_recurring': t.is_recurring,
                'notes': t.notes,
                'tags': t.tags,
                'bank_name': t.bank_name,
                'account_number': t.account_number,
                'statement_file': t.statement_file,
                'created_at': t.created_at.isoformat() if t.created_at else None,
                'updated_at': t.updated_at.isoformat() if t.updated_at else None
            }
            for t in transactions
        ]
        
        # Export transaction rules
        rules = session.query(TransactionRule).all()
        rules_data = [
            {
                'pattern': r.pattern,
                'match_type': r.match_type,
                'category_name': r.category.name if r.category else None,
                'account_name': r.account.name if r.account else None,
                'is_active': r.is_active,
                'priority': r.priority
            }
            for r in rules
        ]
        
        # Export budgets
        budgets = session.query(MonthlyBudget).all()
        budgets_data = [
            {
                'category_name': b.category.name if b.category else None,
                'year': b.year,
                'month': b.month,
                'budget_amount': b.budget_amount,
                'rollover': b.rollover
            }
            for b in budgets
        ]
        
        # Export assets
        assets = session.query(Asset).all()
        assets_data = [
            {
                'symbol': a.symbol,
                'name': a.name,
                'asset_type': a.asset_type.value,
                'api_id': a.api_id,
                'is_tracked': a.is_tracked,
                'color': a.color
            }
            for a in assets
        ]
        
        # Export asset transactions
        asset_transactions = session.query(AssetTransaction).all()
        asset_transactions_data = [
            {
                'asset_symbol': t.asset.symbol,
                'transaction_type': t.transaction_type.value,
                'quantity': t.quantity,
                'price_per_unit': t.price_per_unit,
                'total_value': t.total_value,
                'fee': t.fee,
                'transaction_date': t.transaction_date.isoformat(),
                'notes': t.notes,
                'is_historical_price': t.is_historical_price
            }
            for t in asset_transactions
        ]
        
        # Note: Price history and portfolio snapshots are NOT exported
        # They can be recalculated from asset transactions after import
        
        export_data = {
            'export_version': '2.0',
            'export_date': datetime.now().isoformat(),
            'categories': categories_data,
            'accounts': accounts_data,
            'transactions': transactions_data,
            'rules': rules_data,
            'budgets': budgets_data,
            'assets': assets_data,
            'asset_transactions': asset_transactions_data,
            'summary': {
                'total_categories': len(categories_data),
                'total_accounts': len(accounts_data),
                'total_transactions': len(transactions_data),
                'total_rules': len(rules_data),
                'total_budgets': len(budgets_data),
                'total_assets': len(assets_data),
                'total_asset_transactions': len(asset_transactions_data)
            }
        }
        
        filename = f"finance_tracker_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        return Response(
            json_lib.dumps(export_data, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )
    finally:
        session.close()


@api_bp.route('/import/full', methods=['POST'])
def import_full_data():
    """Import data from JSON backup - merges with existing data"""
    import json as json_lib
    
    session = get_session()
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if not file.filename.endswith('.json'):
            return jsonify({'error': 'File must be JSON format'}), 400
        
        data = json_lib.loads(file.read().decode('utf-8'))
        
        stats = {
            'categories_created': 0,
            'categories_skipped': 0,
            'accounts_created': 0,
            'accounts_skipped': 0,
            'transactions_created': 0,
            'transactions_skipped': 0,
            'rules_created': 0,
            'rules_skipped': 0,
            'budgets_created': 0,
            'budgets_skipped': 0
        }
        
        # Import categories first (needed for transactions)
        category_map = {}  # name -> id mapping
        existing_categories = {c.name: c for c in session.query(Category).all()}
        
        for cat_data in data.get('categories', []):
            name = cat_data['name']
            if name in existing_categories:
                category_map[name] = existing_categories[name].id
                stats['categories_skipped'] += 1
            else:
                new_cat = Category(
                    name=name,
                    color=cat_data.get('color', '#6366f1'),
                    icon=cat_data.get('icon', 'tag'),
                    is_system=cat_data.get('is_system', False)
                )
                session.add(new_cat)
                session.flush()
                category_map[name] = new_cat.id
                stats['categories_created'] += 1
        
        # Import accounts
        account_map = {}  # name -> id mapping
        existing_accounts = {a.name: a for a in session.query(Account).all()}
        
        for acc_data in data.get('accounts', []):
            name = acc_data['name']
            if name in existing_accounts:
                account_map[name] = existing_accounts[name].id
                stats['accounts_skipped'] += 1
            else:
                acc_type = AccountType.CHECKING
                try:
                    acc_type = AccountType(acc_data.get('account_type', 'checking'))
                except:
                    pass
                
                new_acc = Account(
                    name=name,
                    account_type=acc_type,
                    color=acc_data.get('color', '#10b981'),
                    icon=acc_data.get('icon', 'wallet'),
                    description=acc_data.get('description'),
                    opening_balance=acc_data.get('opening_balance', 0.0),
                    is_active=acc_data.get('is_active', True),
                    is_archived=acc_data.get('is_archived', False)
                )
                session.add(new_acc)
                session.flush()
                account_map[name] = new_acc.id
                stats['accounts_created'] += 1
        
        # Import transactions - check for duplicates by date+description+amount
        existing_tx_keys = set()
        for t in session.query(Transaction).all():
            key = (t.date.date().isoformat(), t.description, t.amount)
            existing_tx_keys.add(key)
        
        for tx_data in data.get('transactions', []):
            date_str = tx_data['date']
            date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            if date.tzinfo:
                date = date.replace(tzinfo=None)
            
            key = (date.date().isoformat(), tx_data['description'], tx_data['amount'])
            
            if key in existing_tx_keys:
                stats['transactions_skipped'] += 1
                continue
            
            tx_type = TransactionType.EXPENSE
            try:
                tx_type = TransactionType(tx_data.get('transaction_type', 'expense'))
            except:
                pass
            
            category_id = None
            if tx_data.get('category_name') and tx_data['category_name'] in category_map:
                category_id = category_map[tx_data['category_name']]
            
            account_id = None
            if tx_data.get('account_name') and tx_data['account_name'] in account_map:
                account_id = account_map[tx_data['account_name']]
            
            new_tx = Transaction(
                date=date,
                description=tx_data['description'],
                original_description=tx_data.get('original_description'),
                amount=tx_data['amount'],
                transaction_type=tx_type,
                balance_after=tx_data.get('balance_after'),
                category_id=category_id,
                account_id=account_id,
                is_internal_transfer=tx_data.get('is_internal_transfer', False),
                is_one_off=tx_data.get('is_one_off', False),
                is_recurring=tx_data.get('is_recurring', False),
                notes=tx_data.get('notes'),
                tags=tx_data.get('tags'),
                bank_name=tx_data.get('bank_name'),
                account_number=tx_data.get('account_number'),
                statement_file=tx_data.get('statement_file')
            )
            session.add(new_tx)
            existing_tx_keys.add(key)
            stats['transactions_created'] += 1
        
        # Import rules
        existing_rules = {r.pattern: r for r in session.query(TransactionRule).all()}
        
        for rule_data in data.get('rules', []):
            pattern = rule_data['pattern']
            if pattern in existing_rules:
                stats['rules_skipped'] += 1
            else:
                category_id = None
                if rule_data.get('category_name') and rule_data['category_name'] in category_map:
                    category_id = category_map[rule_data['category_name']]
                
                account_id = None
                if rule_data.get('account_name') and rule_data['account_name'] in account_map:
                    account_id = account_map[rule_data['account_name']]
                
                new_rule = TransactionRule(
                    pattern=pattern,
                    match_type=rule_data.get('match_type', 'contains'),
                    category_id=category_id,
                    account_id=account_id,
                    is_active=rule_data.get('is_active', True),
                    priority=rule_data.get('priority', 0)
                )
                session.add(new_rule)
                stats['rules_created'] += 1
        
        # Import budgets
        existing_budgets = set()
        for b in session.query(MonthlyBudget).all():
            key = (b.category_id, b.year, b.month)
            existing_budgets.add(key)
        
        for budget_data in data.get('budgets', []):
            category_id = None
            if budget_data.get('category_name') and budget_data['category_name'] in category_map:
                category_id = category_map[budget_data['category_name']]
            
            if not category_id:
                stats['budgets_skipped'] += 1
                continue
            
            key = (category_id, budget_data['year'], budget_data['month'])
            if key in existing_budgets:
                stats['budgets_skipped'] += 1
            else:
                new_budget = MonthlyBudget(
                    category_id=category_id,
                    year=budget_data['year'],
                    month=budget_data['month'],
                    budget_amount=budget_data['budget_amount'],
                    rollover=budget_data.get('rollover', False)
                )
                session.add(new_budget)
                stats['budgets_created'] += 1
        
        # Import assets
        stats['assets_created'] = 0
        stats['assets_skipped'] = 0
        asset_map = {}  # symbol -> id mapping
        existing_assets = {a.symbol: a for a in session.query(Asset).all()}
        
        for asset_data in data.get('assets', []):
            symbol = asset_data['symbol']
            if symbol in existing_assets:
                asset_map[symbol] = existing_assets[symbol].id
                stats['assets_skipped'] += 1
            else:
                asset_type = AssetType.OTHER
                try:
                    asset_type = AssetType(asset_data.get('asset_type', 'other'))
                except:
                    pass
                
                new_asset = Asset(
                    symbol=symbol,
                    name=asset_data['name'],
                    asset_type=asset_type,
                    api_id=asset_data.get('api_id'),
                    is_tracked=asset_data.get('is_tracked', True),
                    color=asset_data.get('color', '#f59e0b')
                )
                session.add(new_asset)
                session.flush()
                asset_map[symbol] = new_asset.id
                
                # Create holding record
                new_holding = AssetHolding(
                    asset_id=new_asset.id,
                    quantity=0,
                    total_cost_basis=0
                )
                session.add(new_holding)
                stats['assets_created'] += 1
        
        # Import asset transactions
        stats['asset_transactions_created'] = 0
        stats['asset_transactions_skipped'] = 0
        existing_asset_tx_keys = set()
        for t in session.query(AssetTransaction).all():
            key = (t.asset_id, t.transaction_date.isoformat(), t.quantity, t.price_per_unit)
            existing_asset_tx_keys.add(key)
        
        for tx_data in data.get('asset_transactions', []):
            asset_symbol = tx_data.get('asset_symbol')
            if not asset_symbol or asset_symbol not in asset_map:
                stats['asset_transactions_skipped'] += 1
                continue
            
            asset_id = asset_map[asset_symbol]
            tx_date = datetime.fromisoformat(tx_data['transaction_date'].replace('Z', '+00:00'))
            if tx_date.tzinfo:
                tx_date = tx_date.replace(tzinfo=None)
            
            key = (asset_id, tx_date.isoformat(), tx_data['quantity'], tx_data['price_per_unit'])
            if key in existing_asset_tx_keys:
                stats['asset_transactions_skipped'] += 1
                continue
            
            tx_type = AssetTransactionType.BUY
            try:
                tx_type = AssetTransactionType(tx_data.get('transaction_type', 'buy'))
            except:
                pass
            
            new_tx = AssetTransaction(
                asset_id=asset_id,
                transaction_type=tx_type,
                quantity=tx_data['quantity'],
                price_per_unit=tx_data['price_per_unit'],
                total_value=tx_data.get('total_value', tx_data['quantity'] * tx_data['price_per_unit']),
                fee=tx_data.get('fee', 0),
                transaction_date=tx_date,
                notes=tx_data.get('notes'),
                is_historical_price=tx_data.get('is_historical_price', False)
            )
            session.add(new_tx)
            existing_asset_tx_keys.add(key)
            stats['asset_transactions_created'] += 1
        
        # Recalculate asset holdings based on imported transactions
        for symbol, asset_id in asset_map.items():
            holding = session.query(AssetHolding).filter(AssetHolding.asset_id == asset_id).first()
            if holding:
                # Calculate from transactions
                txs = session.query(AssetTransaction).filter(AssetTransaction.asset_id == asset_id).all()
                quantity = 0
                cost_basis = 0
                for tx in txs:
                    if tx.transaction_type == AssetTransactionType.BUY:
                        quantity += tx.quantity
                        cost_basis += tx.total_value + (tx.fee or 0)
                    elif tx.transaction_type == AssetTransactionType.SELL:
                        if quantity > 0:
                            ratio = tx.quantity / quantity
                            cost_basis *= (1 - ratio)
                        quantity -= tx.quantity
                
                holding.quantity = max(0, quantity)
                holding.total_cost_basis = max(0, cost_basis)
                holding.average_buy_price = cost_basis / quantity if quantity > 0 else 0
        
        session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Import completed successfully',
            'stats': stats
        })
        
    except Exception as e:
        session.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


# ==================== ANALYTICS ENHANCEMENTS ====================

@api_bp.route('/analytics/income-sources', methods=['GET'])
def get_income_sources():
    """Get breakdown of income by source/description"""
    session = get_session()
    try:
        days = request.args.get('days', 365, type=int)
        start_date = datetime.now() - timedelta(days=days)
        
        results = session.query(
            Transaction.description,
            func.sum(Transaction.amount).label('total'),
            func.count(Transaction.id).label('count')
        ).filter(
            and_(
                Transaction.date >= start_date,
                Transaction.transaction_type == TransactionType.INCOME,
                or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
            )
        ).group_by(
            Transaction.description
        ).order_by(
            func.sum(Transaction.amount).desc()
        ).all()
        
        total_income = sum(r.total for r in results)
        
        return jsonify({
            'income_sources': [
                {
                    'source': r.description,
                    'total': float(r.total),
                    'count': r.count,
                    'percentage': (float(r.total) / total_income * 100) if total_income > 0 else 0
                }
                for r in results
            ],
            'total_income': total_income
        })
    finally:
        session.close()


@api_bp.route('/analytics/savings-rate', methods=['GET'])
def get_savings_rate():
    """Calculate savings rate over time"""
    session = get_session()
    try:
        months = request.args.get('months', 12, type=int)
        
        results = []
        now = datetime.now()
        
        for i in range(months):
            # Calculate date for each month going back
            if now.month - i > 0:
                year = now.year
                month = now.month - i
            else:
                year = now.year - 1
                month = 12 + (now.month - i)
            
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)
            
            income = session.query(func.sum(Transaction.amount)).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    Transaction.transaction_type == TransactionType.INCOME,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).scalar() or 0
            
            expenses = session.query(func.sum(Transaction.amount)).filter(
                and_(
                    Transaction.date >= start_date,
                    Transaction.date < end_date,
                    Transaction.transaction_type == TransactionType.EXPENSE,
                    or_(Transaction.is_internal_transfer == False, Transaction.is_internal_transfer == None)
                )
            ).scalar() or 0
            
            savings = float(income) - float(expenses)
            savings_rate = (savings / float(income) * 100) if income > 0 else 0
            
            results.append({
                'period': f"{year}-{month:02d}",
                'income': float(income),
                'expenses': float(expenses),
                'savings': savings,
                'savings_rate': savings_rate
            })
        
        # Reverse to get chronological order
        results.reverse()
        
        # Calculate averages
        avg_income = sum(r['income'] for r in results) / len(results) if results else 0
        avg_expenses = sum(r['expenses'] for r in results) / len(results) if results else 0
        avg_savings_rate = sum(r['savings_rate'] for r in results) / len(results) if results else 0
        
        return jsonify({
            'monthly_data': results,
            'averages': {
                'income': avg_income,
                'expenses': avg_expenses,
                'savings_rate': avg_savings_rate
            }
        })
    finally:
        session.close()


# ==================== ASSET MANAGEMENT ENDPOINTS ====================

@api_bp.route('/assets', methods=['GET'])
def get_assets():
    """Get all assets with holdings and prices"""
    from app.asset_service import asset_service
    assets = asset_service.get_all_assets()
    return jsonify({'assets': assets})


@api_bp.route('/assets/<int:asset_id>', methods=['GET'])
def get_asset(asset_id):
    """Get single asset with full details"""
    from app.asset_service import asset_service
    asset = asset_service.get_asset(asset_id)
    if not asset:
        return jsonify({'error': 'Asset not found'}), 404
    return jsonify(asset)


@api_bp.route('/assets', methods=['POST'])
def create_asset():
    """Create a new asset"""
    from app.asset_service import asset_service
    data = request.json
    
    result = asset_service.create_asset(
        symbol=data.get('symbol'),
        name=data.get('name'),
        asset_type=data.get('asset_type', 'crypto'),
        is_tracked=data.get('is_tracked', True),
        api_id=data.get('api_id'),
        color=data.get('color')
    )
    
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route('/assets/<int:asset_id>', methods=['DELETE'])
def delete_asset(asset_id):
    """Delete an asset"""
    from app.asset_service import asset_service
    result = asset_service.delete_asset(asset_id)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route('/assets/supported', methods=['GET'])
def get_supported_assets():
    """Get list of supported assets for selection"""
    from app.price_service import price_service
    return jsonify(price_service.get_supported_assets())


@api_bp.route('/assets/transactions', methods=['GET'])
def get_asset_transactions():
    """Get all asset transactions"""
    from app.asset_service import asset_service
    asset_id = request.args.get('asset_id', type=int)
    limit = request.args.get('limit', 50, type=int)
    transactions = asset_service.get_transaction_history(asset_id, limit)
    return jsonify({'transactions': transactions})


@api_bp.route('/assets/transactions', methods=['POST'])
def add_asset_transaction():
    """Add a new asset transaction"""
    from app.asset_service import asset_service
    from app.portfolio_service import portfolio_service
    data = request.json
    
    transaction_date = datetime.fromisoformat(data.get('transaction_date').replace('Z', '+00:00'))
    if transaction_date.tzinfo:
        transaction_date = transaction_date.replace(tzinfo=None)
    
    result = asset_service.add_transaction(
        asset_id=data.get('asset_id'),
        transaction_type=data.get('transaction_type', 'buy'),
        quantity=float(data.get('quantity', 0)),
        price_per_unit=float(data.get('price_per_unit', 0)),
        transaction_date=transaction_date,
        fee=float(data.get('fee', 0)),
        notes=data.get('notes'),
        auto_fetch_price=data.get('auto_fetch_price', False)
    )
    
    if 'error' in result:
        return jsonify(result), 400
    
    # Trigger portfolio recalculation (fetch historical prices + update snapshots)
    try:
        from app.models import AssetTransaction
        from app.database import get_session
        session = get_session()
        tx = session.query(AssetTransaction).filter(AssetTransaction.id == result['id']).first()
        if tx:
            portfolio_service.on_transaction_added(tx)
        session.close()
    except Exception as e:
        print(f"Portfolio update error (non-blocking): {e}")
    
    return jsonify(result)


@api_bp.route('/assets/transactions/<int:transaction_id>', methods=['PUT'])
def update_asset_transaction(transaction_id):
    """Update an asset transaction"""
    from app.asset_service import asset_service
    data = request.json
    
    kwargs = {}
    if 'quantity' in data:
        kwargs['quantity'] = float(data['quantity'])
    if 'price_per_unit' in data:
        kwargs['price_per_unit'] = float(data['price_per_unit'])
    if 'transaction_date' in data:
        td = datetime.fromisoformat(data['transaction_date'].replace('Z', '+00:00'))
        kwargs['transaction_date'] = td.replace(tzinfo=None) if td.tzinfo else td
    if 'fee' in data:
        kwargs['fee'] = float(data['fee'])
    if 'notes' in data:
        kwargs['notes'] = data['notes']
    if 'transaction_type' in data:
        kwargs['transaction_type'] = data['transaction_type']
    
    result = asset_service.update_transaction(transaction_id, **kwargs)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route('/assets/transactions/<int:transaction_id>', methods=['DELETE'])
def delete_asset_transaction(transaction_id):
    """Delete an asset transaction"""
    from app.asset_service import asset_service
    result = asset_service.delete_transaction(transaction_id)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route('/assets/portfolio/summary', methods=['GET'])
def get_portfolio_summary():
    """Get portfolio summary"""
    from app.asset_service import asset_service
    return jsonify(asset_service.get_portfolio_summary())


@api_bp.route('/assets/portfolio/allocation', methods=['GET'])
def get_portfolio_allocation():
    """Get portfolio allocation for pie chart"""
    from app.asset_service import asset_service
    return jsonify({'allocation': asset_service.get_portfolio_allocation()})


@api_bp.route('/assets/portfolio/history', methods=['GET'])
def get_portfolio_history():
    """Get portfolio value history - reads from pre-calculated snapshots"""
    from app.portfolio_service import portfolio_service
    days = request.args.get('days', 90, type=int)
    return jsonify({'history': portfolio_service.get_portfolio_history(days)})


@api_bp.route('/assets/portfolio/metrics', methods=['GET'])
def get_portfolio_metrics():
    """Get detailed portfolio performance metrics"""
    from app.asset_service import asset_service
    asset_id = request.args.get('asset_id', type=int)
    return jsonify(asset_service.get_performance_metrics(asset_id))


@api_bp.route('/assets/portfolio/snapshot', methods=['POST'])
def take_portfolio_snapshot():
    """Take a snapshot of current portfolio value"""
    from app.asset_service import asset_service
    result = asset_service.take_portfolio_snapshot()
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@api_bp.route('/assets/prices/refresh', methods=['POST'])
def refresh_asset_prices():
    """Refresh prices for all tracked assets"""
    from app.price_service import price_service
    result = price_service.update_price_cache()
    return jsonify(result)


@api_bp.route('/assets/prices/check', methods=['GET'])
def check_price_refresh_needed():
    """Check if prices need refreshing"""
    from app.price_service import price_service
    return jsonify({'needs_refresh': price_service.should_refresh_prices()})


@api_bp.route('/assets/portfolio/update', methods=['POST'])
def run_portfolio_update():
    """Run daily portfolio update - fetches new prices and calculates snapshots"""
    from app.portfolio_service import portfolio_service
    result = portfolio_service.run_daily_update()
    return jsonify(result)


@api_bp.route('/assets/portfolio/recalculate', methods=['POST'])
def recalculate_portfolio():
    """Force recalculate all portfolio snapshots from scratch"""
    from app.portfolio_service import portfolio_service
    from app.database import get_session
    from app.models import Asset, AssetTransaction
    from sqlalchemy import func
    
    session = get_session()
    try:
        assets = session.query(Asset).filter(Asset.is_tracked == True).all()
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Collect asset data
        asset_data = []
        for asset in assets:
            earliest = session.query(func.min(AssetTransaction.transaction_date)).filter(
                AssetTransaction.asset_id == asset.id
            ).scalar()
            
            if earliest:
                asset_data.append({
                    'id': asset.id,
                    'symbol': asset.symbol,
                    'api_id': asset.api_id,
                    'asset_type': asset.asset_type,
                    'earliest': earliest
                })
        
        session.close()
        
        # Fetch historical prices
        prices_fetched = 0
        for ad in asset_data:
            fetched = portfolio_service.fetch_historical_prices_for_asset(
                ad['id'], ad['symbol'], ad['api_id'], ad['asset_type'],
                ad['earliest'], today
            )
            prices_fetched += fetched
        
        # Now recalculate all snapshots
        snapshots = portfolio_service.calculate_portfolio_snapshots()
        
        return jsonify({
            'prices_fetched': prices_fetched,
            'snapshots_calculated': snapshots
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
