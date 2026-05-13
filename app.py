# -*- coding: utf-8 -*-
import os
import socket
import qrcode
import datetime
import json
import hashlib
from flask import Flask, render_template_string, request, session, redirect, url_for, jsonify, flash, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from functools import wraps

# Configuration
app = Flask(__name__)
app.secret_key = 'super_secret_key_for_session_management' # Change in production
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'currency.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database Path Configuration
if 'ANDROID_PRIVATE' in os.environ:
    # On Android, use the private storage to ensure writability
    db_path = os.path.join(os.environ['ANDROID_PRIVATE'], 'currency.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
else:
    # On Desktop, use the local directory
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'currency.db')

db = SQLAlchemy(app)

# --- Default Permissions ---
DEFAULT_PERMISSIONS = {
    'can_send_user': True,
    'can_send_bank': False,     # Send FROM Bank
    'can_receive_pass_go': True,
    'can_mint': False,
    'can_burn': False,
    'can_reset': False,
    'can_manage_permissions': False,
    'can_delete_user': False,
    'can_toggle_monopoly': False,
    'can_act_as_banker': False
}

# --- Constants ---
AVAILABLE_COLORS = [
    '#3b82f6', # Blue
    '#10b981', # Emerald
    '#f59e0b', # Amber
    '#8b5cf6', # Violet
    '#ec4899', # Pink
    '#6366f1', # Indigo
    '#14b8a6', # Teal
    '#f97316', # Orange
    '#84cc16', # Lime
    '#06b6d4', # Cyan
    '#d946ef', # Fuchsia
    '#e11d48'  # Rose
]

def get_next_available_color():
    """Finds the first color in AVAILABLE_COLORS that isn't currently used by any user."""
    try:
        # Get all currently used colors
        from sqlalchemy.sql import text
        # We use a raw query or simple session query to avoid circular dependency issues if called early
        # But here we are inside the app context usually
        existing_users = User.query.all()
        used_colors = {u.color_hex for u in existing_users}
        
        # Add system reserved colors just in case
        used_colors.add('#EF4444') # Free Parking Red
        used_colors.add('#E2E8F0') # Bank Grey

        for color in AVAILABLE_COLORS:
            if color not in used_colors:
                return color
        
        # If all taken, return random or default
        import random
        return random.choice(AVAILABLE_COLORS)
    except:
        return AVAILABLE_COLORS[0]

# --- Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    balance = db.Column(db.Integer, default=100) # Start with 100 credits
    is_admin = db.Column(db.Boolean, default=False) # Legacy high-level flag (mostly UI)
    is_banker = db.Column(db.Boolean, default=False) # Legacy flag
    is_root = db.Column(db.Boolean, default=False) # Protected Host User
    permissions_json = db.Column(db.Text, default=json.dumps(DEFAULT_PERMISSIONS))
    last_pass_go = db.Column(db.DateTime, default=lambda: datetime.datetime.utcnow() - datetime.timedelta(days=1))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    color_hex = db.Column(db.String(20), default='#3b82f6') # User-customizable color

    def get_permissions(self):
        try:
            stored = json.loads(self.permissions_json)
            # Merge with defaults to ensure all keys exist
            perms = DEFAULT_PERMISSIONS.copy()
            if isinstance(stored, dict):
                perms.update(stored)
            return perms
        except:
            return DEFAULT_PERMISSIONS.copy()

    def has_permission(self, perm):
        if self.is_root: return True
        perms = self.get_permissions()
        return perms.get(perm, False)

    def update_permission(self, perm, value):
        perms = self.get_permissions()
        perms[perm] = value
        self.permissions_json = json.dumps(perms)

    def has_admin_access(self):
        if self.is_root or self.is_admin: return True
        # Permissions that require access to the Admin Dashboard
        admin_perms = ['can_manage_permissions', 'can_delete_user', 'can_mint', 'can_burn', 'can_reset', 'can_toggle_monopoly']
        for p in admin_perms:
            if self.has_permission(p):
                return True
        return False

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(80), nullable=True) # Null for System/Mint
    receiver = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    note = db.Column(db.String(200))

class SystemSetting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200))

# --- Helpers ---
def get_setting(key, default=None):
    setting = SystemSetting.query.get(key)
    return setting.value if setting else default

def set_setting(key, value):
    setting = SystemSetting.query.get(key)
    if not setting:
        setting = SystemSetting(key=key, value=value)
        db.session.add(setting)
    else:
        setting.value = value
    db.session.commit()

def init_bank_user():
    bank = User.query.filter_by(username='Bank').first()
    if not bank:
        # Bank has special permissions but isn't a login user usually
        bank = User(username='Bank', balance=0, color='#1e3a8a', is_banker=True)
        db.session.add(bank)
        db.session.commit()
        print("Bank user initialized.")
    return bank

def _socket_fallback_ip():
    """Socket-based IP discovery (works when routing to internet exists)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def get_local_ip():
    """
    Return the Wi‑Fi LAN IP for URLs and QR codes (never 127.0.0.1 or cellular when
    avoidable). On Android: no hostname -I; WifiManager, then NetworkInterface
    (site‑local), then socket. On desktop: hostname -I then socket.
    """
    if 'ANDROID_PRIVATE' in os.environ:
        try:
            from jnius import autoclass
            # 1. Try WifiManager (Good for Client Mode)
            try:
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                Context = autoclass('android.content.Context')
                activity = PythonActivity.mActivity
                wifi = activity.getSystemService(Context.WIFI_SERVICE)
                info = wifi.getConnectionInfo()
                if info:
                    ip_int = info.getIpAddress()
                    if ip_int:
                        ip = "%d.%d.%d.%d" % (
                            ip_int & 0xff, (ip_int >> 8) & 0xff,
                            (ip_int >> 16) & 0xff, (ip_int >> 24) & 0xff
                        )
                        if ip != "0.0.0.0" and not ip.startswith("127."):
                            return ip
            except Exception:
                pass

            # 2. NetworkInterface Scan (Good for Hotspot or Fallback)
            NetworkInterface = autoclass('java.net.NetworkInterface')
            Collections = autoclass('java.util.Collections')
            interfaces = Collections.list(NetworkInterface.getNetworkInterfaces())
            candidates = []
            for ni in interfaces:
                name = ni.getName()
                try:
                    addrs = Collections.list(ni.getInetAddresses())
                    for addr in addrs:
                        if addr.isLoopbackAddress(): continue
                        host = addr.getHostAddress()
                        if '.' in host and ':' not in host:
                            score = 0
                            if 'wlan' in name or 'ap0' in name or 'tether' in name: score += 20
                            if host.startswith('192.168.'): score += 10
                            if host.startswith('172.'): score += 5
                            if host.startswith('10.'): score += 1
                            candidates.append((score, host))
                except Exception:
                    continue
            
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]
        except Exception:
            pass

    return _socket_fallback_ip()

def format_currency(value):
    if value is None:
        return "$0"
    return "${:,.0f}".format(value)

app.jinja_env.filters['currency'] = format_currency

# --- Permissions Decorator ---
def requires_permission(perm):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('index'))
            user = User.query.filter_by(username=session['user']).first()
            if not user or not user.has_permission(perm):
                flash(f"Access Denied: Requires '{perm}' permission.")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Routes ---

@app.route('/cycle_color', methods=['POST'])
def cycle_color():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user = User.query.filter_by(username=session['user']).first()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Get currently used colors
    active_users = User.query.all()
    used_colors = {u.color_hex for u in active_users if u.username != user.username}
    # Reserve System Colors
    used_colors.add('#EF4444') # Free Parking Red
    used_colors.add('#E2E8F0') # Bank Grey

    # Find next available color
    current_index = 0
    if user.color_hex in AVAILABLE_COLORS:
        current_index = AVAILABLE_COLORS.index(user.color_hex)
    
    for i in range(1, len(AVAILABLE_COLORS) + 1):
        next_index = (current_index + i) % len(AVAILABLE_COLORS)
        candidate = AVAILABLE_COLORS[next_index]
        if candidate not in used_colors:
            user.color_hex = candidate
            db.session.commit()
            return jsonify({'success': True, 'new_color': candidate})
            
    return jsonify({'success': False, 'error': 'No colors available'})

@app.route('/auto_login/<username>')
def auto_login(username):
    # Security: Only allow localhost to use this backdoor
    if request.remote_addr != '127.0.0.1':
        return "Unauthorized", 403

    username = username.strip()
    if not username:
        return "Invalid Username", 400

    user = User.query.filter_by(username=username).first()
    if not user:
        # Create Host User (Root)
        start_balance = 1500 if get_setting('monopoly_mode') == '1' else 100
        # Root user gets all permissions implicitly via is_root=True
        user = User(username=username, balance=start_balance, is_admin=True, is_banker=True, is_root=True, color_hex=get_next_available_color())
        db.session.add(user)
        db.session.commit()
    else:
        # Ensure Root privileges if logging in via this method (Host Re-login)
        user.is_admin = True
        user.is_banker = True
        user.is_root = True
        db.session.commit()

    session['user'] = user.username
    session['is_admin'] = True
    
    return redirect(url_for('index'))

@app.route('/host_login')
def host_login():
    return redirect(url_for('admin')) # Deprecated

@app.route('/')
def index():
    init_bank_user()
    
    if 'user' not in session:
        users = User.query.order_by(User.balance.desc()).all()
        active_users = [u for u in users if u.balance > 0]
        chart_labels = [u.username for u in active_users]
        chart_data = [u.balance for u in active_users]
        chart_colors = [u.color_hex for u in active_users]
        monopoly_mode = get_setting('monopoly_mode') == '1'
        
        return render_template_string(LOGIN_TEMPLATE, 
                                      request_args=request.args,
                                      chart_labels=json.dumps(chart_labels),
                                      chart_data=json.dumps(chart_data),
                                      chart_colors=json.dumps(chart_colors),
                                      monopoly_mode=monopoly_mode)
    
    current_user = User.query.filter_by(username=session['user']).first()
    if not current_user:
        session.pop('user', None)
        return redirect(url_for('index'))

    # Transaction History
    history = Transaction.query.filter(
        (Transaction.sender == current_user.username) | 
        (Transaction.receiver == current_user.username)
    ).order_by(Transaction.timestamp.desc()).limit(20).all()

    # All Users (Including self for Banker actions)
    all_players = User.query.filter(User.username != 'Bank', User.username != 'Free Parking').order_by(User.username.asc()).all()
    recents = []
    bank_user = User.query.filter_by(username='Bank').first()
    if bank_user:
        recents.append(bank_user)
    recents.extend(all_players)

    send_to = request.args.get('send_to', '')
    
    last_tx = Transaction.query.filter(
        (Transaction.sender == current_user.username) |
        (Transaction.receiver == current_user.username)
    ).order_by(Transaction.id.desc()).first()
    last_tx_id = last_tx.id if last_tx else 0
    
    my_ip = get_local_ip()
    request_url = f"http://{my_ip}:8080/?send_to={current_user.username}"
    monopoly_mode = get_setting('monopoly_mode') == '1'
    free_parking = User.query.filter_by(username='Free Parking').first()
    fp_balance = free_parking.balance if free_parking else None
    
    # Check granular permissions for UI
    perms = current_user.get_permissions()
    # Root override for UI flags
    if current_user.is_root:
        perms = {k: True for k in DEFAULT_PERMISSIONS.keys()}

    return render_template_string(DASHBOARD_TEMPLATE, 
                                  user=current_user, 
                                  history=history, 
                                  recents=recents,
                                  send_to=send_to,
                                  request_url=request_url,
                                  initial_tx_id=last_tx_id,
                                  monopoly_mode=monopoly_mode,
                                  free_parking_balance=fp_balance,
                                  is_admin=current_user.has_admin_access(), # Dynamic check
                                  perms=perms)

@app.route('/admin/export')
@requires_permission('can_manage_permissions')
def admin_export():
    # 1. Gather Data
    users = User.query.all()
    transactions = Transaction.query.all()
    settings = SystemSetting.query.all()

    data = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "users": [
            {
                "username": u.username,
                "balance": u.balance,
                "is_admin": u.is_admin,
                "is_banker": u.is_banker,
                "is_root": u.is_root,
                "permissions_json": u.permissions_json,
                "last_pass_go": u.last_pass_go.isoformat() if u.last_pass_go else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "color_hex": u.color_hex
            } for u in users
        ],
        "transactions": [
            {
                "sender": t.sender,
                "receiver": t.receiver,
                "amount": t.amount,
                "timestamp": t.timestamp.isoformat(),
                "note": t.note
            } for t in transactions
        ],
        "settings": [
            {"key": s.key, "value": s.value} for s in settings
        ]
    }

    # 2. Add Checksum
    json_str = json.dumps(data, sort_keys=True)
    checksum = hashlib.sha256(json_str.encode()).hexdigest()
    
    export_package = {
        "data": data,
        "checksum": checksum
    }

    return jsonify(export_package), 200, {
        'Content-Disposition': f'attachment; filename=game_state_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    }

@app.route('/admin/import', methods=['POST'])
@requires_permission('can_manage_permissions')
def admin_import():
    if 'file' not in request.files:
        flash("No file uploaded")
        return redirect(url_for('admin_dashboard'))
    
    file = request.files['file']
    if file.filename == '':
        flash("No file selected")
        return redirect(url_for('admin_dashboard'))

    try:
        package = json.load(file)
        data = package.get('data')
        checksum = package.get('checksum')

        # Verify Checksum
        json_str = json.dumps(data, sort_keys=True)
        calculated = hashlib.sha256(json_str.encode()).hexdigest()
        
        if calculated != checksum:
            flash("Integrity Check Failed: Backup file may have been tampered with.")
            return redirect(url_for('admin_dashboard'))

        # 3. Restore Data
        db.session.query(Transaction).delete()
        db.session.query(User).delete()
        db.session.query(SystemSetting).delete()

        # Users
        for u_data in data['users']:
            user = User(
                username=u_data['username'],
                balance=u_data['balance'],
                is_admin=u_data['is_admin'],
                is_banker=u_data['is_banker'],
                is_root=u_data['is_root'],
                permissions_json=u_data['permissions_json'],
                color_hex=u_data['color_hex']
            )
            if u_data['last_pass_go']:
                user.last_pass_go = datetime.datetime.fromisoformat(u_data['last_pass_go'])
            if u_data['created_at']:
                user.created_at = datetime.datetime.fromisoformat(u_data['created_at'])
            db.session.add(user)

        # Transactions
        for t_data in data['transactions']:
            tx = Transaction(
                sender=t_data['sender'],
                receiver=t_data['receiver'],
                amount=t_data['amount'],
                timestamp=datetime.datetime.fromisoformat(t_data['timestamp']),
                note=t_data['note']
            )
            db.session.add(tx)

        # Settings
        for s_data in data['settings']:
            s = SystemSetting(key=s_data['key'], value=s_data['value'])
            db.session.add(s)

        db.session.commit()
        flash("Game State Restored Successfully!")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Import Error: {str(e)}")

    return redirect(url_for('admin_dashboard'))

@app.route('/how-to-play')
def how_to_play():
    if 'user' not in session:
        return redirect(url_for('index'))
    return render_template_string(WELCOME_GUIDE_TEMPLATE)

@app.route('/stats')
def stats():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    # 1. Game Duration
    first_tx = Transaction.query.order_by(Transaction.timestamp.asc()).first()
    if first_tx:
        duration = datetime.datetime.utcnow() - first_tx.timestamp
        hours, remainder = divmod(duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
    else:
        duration_str = "0h 0m 0s"

    # 2. Inflation Analysis
    # Money from Void (Pass Go rewards)
    pass_go_total = db.session.query(db.func.sum(Transaction.amount)).filter(Transaction.note == "Pass Go Reward").scalar() or 0
    # Money from Void (Mints)
    mint_total = db.session.query(db.func.sum(Transaction.amount)).filter(Transaction.sender == "MINT").scalar() or 0
    
    void_money = pass_go_total + mint_total
    
    # Total Supply
    users = User.query.all()
    total_supply = sum(u.balance for u in users)
    
    inflation_pct = (void_money / total_supply * 100) if total_supply > 0 else 0

    # 3. Player Timeline
    timeline_users = User.query.filter(User.username != 'Bank').order_by(User.created_at.asc()).all()

    return render_template_string(STATS_TEMPLATE, 
                                  duration=duration_str,
                                  pass_go_total=pass_go_total,
                                  mint_total=mint_total,
                                  total_supply=total_supply,
                                  inflation_pct=round(inflation_pct, 1),
                                  timeline_users=timeline_users)

@app.route('/admin/help')
def admin_help():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user or not user.has_admin_access():
        flash("Access Denied.")
        return redirect(url_for('index'))
        
    return render_template_string(ADMIN_HELP_TEMPLATE)

@app.route('/leaderboard')
def leaderboard():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    users = User.query.order_by(User.balance.desc()).all()
    total_circulation = sum(u.balance for u in users)
    active_users = [u for u in users if u.balance > 0]
    chart_labels = [u.username for u in active_users]
    chart_data = [u.balance for u in active_users]
    chart_colors = [u.color_hex for u in active_users]
    
    return render_template_string(LEADERBOARD_TEMPLATE, 
                                  users=users, 
                                  total_circulation=total_circulation,
                                  chart_labels=json.dumps(chart_labels),
                                  chart_data=json.dumps(chart_data),
                                  chart_colors=json.dumps(chart_colors))

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username').strip()
    if not username:
        flash("Username cannot be empty")
        return redirect(url_for('index'))
        
    if username.lower() == 'bank':
        flash("Cannot login as System Bank")
        return redirect(url_for('index'))
    
    user = User.query.filter_by(username=username).first()
    if not user:
        start_balance = 1500 if get_setting('monopoly_mode') == '1' else 100
        user = User(username=username, balance=start_balance, color_hex=get_next_available_color())
        db.session.add(user)
        db.session.commit()
    
    session['user'] = user.username
    session['is_admin'] = user.is_admin
    send_to = request.args.get('send_to')
    if send_to:
        return redirect(url_for('index', send_to=send_to))
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/transfer', methods=['POST'])
def transfer():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    current_user = User.query.filter_by(username=session['user']).first()
    recipient_name = request.form.get('recipient')
    source_name = request.form.get('source')
    
    try:
        amount = int(float(request.form.get('amount')))
    except ValueError:
        flash("Invalid amount")
        return redirect(url_for('index'))

    if amount <= 0:
        flash("Amount must be positive")
        return redirect(url_for('index'))

    # --- Recipient Logic ---
    real_recipient = None
    if recipient_name == 'bank':
        real_recipient = User.query.filter_by(username='Bank').first()
    elif recipient_name == 'parking':
        real_recipient = User.query.filter_by(username='Free Parking').first()
    else:
        real_recipient = User.query.filter_by(username=recipient_name).first()

    if not real_recipient:
        flash(f"Recipient '{recipient_name}' not found")
        return redirect(url_for('index'))
    
    # --- RBAC Source Logic ---
    real_sender = None
    note = "Transfer"
    
    if source_name == 'bank':
        if not current_user.has_permission('can_send_bank') and not current_user.has_permission('can_act_as_banker'):
             flash("Access Denied: Cannot send from Bank")
             return redirect(url_for('index'))
        real_sender = User.query.filter_by(username='Bank').first()
        note = f"Bank Access by {current_user.username}"
    elif source_name == 'parking':
        # Restriction: Free Parking collection requires Bank Access or Banker Role
        if not current_user.has_permission('can_send_bank') and not current_user.has_permission('can_act_as_banker'):
             flash("Access Denied: Cannot send from Free Parking")
             return redirect(url_for('index'))
        
        real_sender = User.query.filter_by(username='Free Parking').first()
        note = f"Parking collection by {current_user.username}"
    elif source_name == 'me' or source_name == current_user.username or not source_name:
        if not current_user.has_permission('can_send_user'):
            flash("Access Denied: You are frozen.")
            return redirect(url_for('index'))
        real_sender = current_user
    else:
        # Banker trying to move money from another user
        if not current_user.has_permission('can_act_as_banker'):
            flash("Access Denied: Only Bankers can move others' money.")
            return redirect(url_for('index'))
        
        real_sender = User.query.filter_by(username=source_name).first()
        if not real_sender:
            flash(f"Source user '{source_name}' not found")
            return redirect(url_for('index'))
        note = f"Banker {current_user.username} moved funds"

    if real_sender.username == real_recipient.username:
        flash("Cannot send money to the same account")
        return redirect(url_for('index'))

    # Balance Check
    if real_sender.balance < amount and real_sender.username != 'Bank':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": "Insufficient funds"}), 400
        flash("Insufficient funds")
        return redirect(url_for('index'))

    # Execute
    real_sender.balance -= amount
    real_recipient.balance += amount
    
    tx = Transaction(sender=real_sender.username, receiver=real_recipient.username, amount=amount, note=note)
    db.session.add(tx)
    db.session.commit()
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({"success": True, "new_balance": current_user.balance})

    flash(f"Transferred {amount} from {real_sender.username} to {real_recipient.username}")
    return redirect(url_for('index', send_to=real_recipient.username))

@app.route('/qr_image')
def qr_image():
    if 'user' not in session: return "Unauthorized", 403
    user = session['user']
    ip = get_local_ip()
    data = f"http://{ip}:8080/?send_to={user}"
    import io
    from flask import send_file
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

@app.route('/api/users')
def api_users():
    search = request.args.get('q', '').lower()
    if search:
        users = User.query.filter(User.username.ilike(f'%{search}%')).all()
    else:
        users = User.query.all()
    return jsonify([u.username for u in users])

@app.route('/api/status')
def api_status():
    if 'user' not in session: return jsonify({'error': 'logged_out'}), 401
    user = User.query.filter_by(username=session['user']).first()
    if not user: return jsonify({'error': 'user_not_found'}), 404
    
    last_tx = Transaction.query.filter(
        (Transaction.sender == user.username) | 
        (Transaction.receiver == user.username)
    ).order_by(Transaction.id.desc()).first()
    last_tx_id = last_tx.id if last_tx else 0
    
    free_parking = User.query.filter_by(username='Free Parking').first()
    fp_balance = free_parking.balance if free_parking else None
    
    # Calculate remaining Pass Go cooldown
    COOLDOWN = 30
    remaining_cooldown = 0
    if user.last_pass_go:
        elapsed = (datetime.datetime.utcnow() - user.last_pass_go).total_seconds()
        if elapsed < COOLDOWN:
            remaining_cooldown = int(COOLDOWN - elapsed)

    return jsonify({
        'balance': user.balance,
        'last_tx_id': last_tx_id,
        'free_parking_balance': fp_balance,
        'pass_go_cooldown': remaining_cooldown
    })

@app.route('/pass_go', methods=['POST'])
@requires_permission('can_receive_pass_go')
def pass_go():
    if get_setting('monopoly_mode') != '1':
        flash("Monopoly Mode is disabled")
        return redirect(url_for('index'))
    
    user = User.query.filter_by(username=session['user']).first()
    
    # Cooldown Check (30 seconds)
    COOLDOWN = 30
    if user.last_pass_go:
        elapsed = (datetime.datetime.utcnow() - user.last_pass_go).total_seconds()
        if elapsed < COOLDOWN:
            remaining = int(COOLDOWN - elapsed)
            flash(f"Pass Go locked! Wait {remaining}s")
            return redirect(url_for('index'))

    if user:
        amount = 200
        # Money created from void (Inflation)
        user.balance += amount
        user.last_pass_go = datetime.datetime.utcnow()
        db.session.add(Transaction(sender='Pass Go', receiver=user.username, amount=amount, note="Pass Go Reward"))
        db.session.commit()
        flash(f"Collected {amount} from Pass Go!")
        
    return redirect(url_for('index'))

@app.route('/admin/toggle_monopoly', methods=['POST'])
@requires_permission('can_toggle_monopoly')
def admin_toggle_monopoly():
    current = get_setting('monopoly_mode')
    new_state = '0' if current == '1' else '1'
    set_setting('monopoly_mode', new_state)
    status = "ENABLED" if new_state == '1' else "DISABLED"
    flash(f"Monopoly Mode {status}")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_permission', methods=['POST'])
@requires_permission('can_manage_permissions')
def admin_update_permission():
    target_username = request.form.get('username')
    perm = request.form.get('permission')
    
    user = User.query.filter_by(username=target_username).first()
    if not user:
        flash("User not found")
        return redirect(url_for('admin_dashboard'))
        
    if user.is_root:
        flash("Cannot modify Root user permissions.")
        return redirect(url_for('admin_dashboard'))
    
    current_val = user.has_permission(perm)
    user.update_permission(perm, not current_val)
    
    # Sync legacy flags for backward compat / UI headers
    if perm == 'can_send_bank':
        user.is_banker = not current_val
    if perm == 'can_manage_permissions': # Rough approx for 'admin'
        user.is_admin = not current_val
        
    db.session.commit()
    
    flash(f"Updated {perm} for {target_username}")
    return redirect(url_for('admin_dashboard'))

# --- Admin Routes ---

@app.route('/admin')
def admin():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    user = User.query.filter_by(username=session['user']).first()
    if user and user.has_admin_access():
        return redirect(url_for('admin_dashboard'))
    
    flash("Access Denied: You do not have Administrative privileges.")
    return redirect(url_for('index'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    current_user = User.query.filter_by(username=session['user']).first()
    if not current_user or not current_user.has_admin_access():
        flash("Access Denied: Admin privileges required.")
        return redirect(url_for('index'))

    # If simple admin (not permission manager), show simple view? 
    # Or just hide the dangerous bits in the template.
    
    users = User.query.all()
    total_circulation = sum(u.balance for u in users)
    transactions = Transaction.query.order_by(Transaction.timestamp.desc()).limit(100).all()

    user_stats = []
    for u in users:
        if u.username in ['Bank', 'Free Parking']:
            continue
        count = Transaction.query.filter((Transaction.sender == u.username) | (Transaction.receiver == u.username)).count()
        user_stats.append({
            'username': u.username,
            'balance': u.balance,
            'tx_count': count,
            'is_banker': u.is_banker,
            'is_root': u.is_root,
            'permissions': u.get_permissions()
        })
    
    # Check current user perms for UI rendering
    my_perms = current_user.get_permissions()
    if current_user.is_root: my_perms = {k:True for k in DEFAULT_PERMISSIONS.keys()}

    return render_template_string(ADMIN_DASHBOARD_TEMPLATE, 
                                  user_stats=user_stats, 
                                  total_circulation=total_circulation,
                                  transactions=transactions,
                                  all_users=[u.username for u in users if u.username not in ['Bank', 'Free Parking']],
                                  monopoly_mode=get_setting('monopoly_mode')=='1',
                                  container_class='max-w-[95%] 2xl:max-w-[1800px]',
                                  free_parking_balance=User.query.filter_by(username='Free Parking').first().balance if User.query.filter_by(username='Free Parking').first() else 0,
                                  my_perms=my_perms)

@app.route('/admin/action', methods=['POST'])
def admin_action():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    user = User.query.filter_by(username=session['user']).first()
    if not user or not (user.is_admin or user.is_root):
        flash("Access Denied.")
        return redirect(url_for('index'))
    
    action = request.form.get('action') 
    
    if action == 'reset':
         if not user.has_permission('can_reset'):
             flash("Permission Denied: Reset DB")
             return redirect(url_for('admin_dashboard'))
             
         if request.form.get('confirm') == 'yes':
            db.session.query(Transaction).delete()
            db.session.query(User).delete()
            db.session.commit()
            init_bank_user()
            flash("Database Reset Complete")
         return redirect(url_for('admin_dashboard'))

    elif action == 'create_free_parking':
        if not user.has_permission('can_toggle_monopoly'):
             flash("Permission Denied")
             return redirect(url_for('admin_dashboard'))
        if not User.query.filter_by(username='Free Parking').first():
            fp = User(username='Free Parking', balance=0, color_hex='#EF4444')
            db.session.add(fp)
            db.session.commit()
            flash("Free Parking account created!")
        return redirect(url_for('admin_dashboard'))

    elif action == 'delete_user':
        if not user.has_permission('can_delete_user'):
             flash("Permission Denied: Delete User")
             return redirect(url_for('admin_dashboard'))
             
        user_to_delete = request.form.get('username')
        if user_to_delete == 'Bank':
            flash("Cannot delete the Central Bank")
        else:
            target = User.query.filter_by(username=user_to_delete).first()
            if target:
                if target.is_root:
                    flash("Cannot delete Root User")
                else:
                    db.session.delete(target)
                    db.session.commit()
                    flash(f"User '{user_to_delete}' deleted")
        return redirect(url_for('admin_dashboard'))

    elif action == 'reset_cooldown':
        if not user.has_permission('can_manage_permissions'):
            flash("Permission Denied")
            return redirect(url_for('admin_dashboard'))
        target_name = request.form.get('username')
        target = User.query.filter_by(username=target_name).first()
        if target:
            target.last_pass_go = datetime.datetime.utcnow() - datetime.timedelta(days=1)
            db.session.commit()
            flash(f"Reset Pass Go for {target_name}")
        return redirect(url_for('admin_dashboard'))

    # Transfer / Mint / Burn Logic
    sender_name = request.form.get('sender')
    receiver_name = request.form.get('receiver')
    try:
        amount = int(float(request.form.get('amount', 0)))
    except:
        amount = 0

    if amount <= 0 and action != 'set':
         if sender_name != 'SET_BALANCE':
             flash("Amount must be positive")
             return redirect(url_for('admin_dashboard'))

    # 1. SET BALANCE
    if sender_name == 'SET_BALANCE':
        if not user.has_permission('can_mint'): # Require high-level perms
             flash("Permission Denied: Set Balance")
             return redirect(url_for('admin_dashboard'))
        target = User.query.filter_by(username=receiver_name).first()
        if target:
            diff = amount - target.balance
            target.balance = amount
            if diff != 0:
                note = "Admin Set Balance"
                sender_rec = "ADMIN" if diff > 0 else target.username
                rec_rec = target.username if diff > 0 else "ADMIN"
                db.session.add(Transaction(sender=sender_rec, receiver=rec_rec, amount=abs(diff), note=note))
                db.session.commit()
                flash(f"Set balance for {receiver_name} to {amount}")
        return redirect(url_for('admin_dashboard'))

    # 2. MINT
    if sender_name == 'MINT':
        if not user.has_permission('can_mint'):
             flash("Permission Denied: Mint")
             return redirect(url_for('admin_dashboard'))
        target = User.query.filter_by(username=receiver_name).first()
        if target:
            target.balance += amount
            db.session.add(Transaction(sender="MINT", receiver=target.username, amount=amount, note="Admin Mint"))
            db.session.commit()
            flash(f"Minted {amount} for {receiver_name}")
        return redirect(url_for('admin_dashboard'))

    # 3. BURN
    if receiver_name == 'BURN':
        if not user.has_permission('can_burn'):
             flash("Permission Denied: Burn")
             return redirect(url_for('admin_dashboard'))
        target = User.query.filter_by(username=sender_name).first()
        if target:
            target.balance = max(0, target.balance - amount)
            db.session.add(Transaction(sender=target.username, receiver="BURN", amount=amount, note="Admin Burn"))
            db.session.commit()
            flash(f"Burned {amount} from {sender_name}")
        return redirect(url_for('admin_dashboard'))

    # 4. Force Transfer
    # If we are here, it's an admin "force transfer"
    if not user.has_permission('can_mint'): # Using can_mint as proxy for 'God Mode Transfer'
         flash("Permission Denied: Force Transfer")
         return redirect(url_for('admin_dashboard'))
         
    sender = User.query.filter_by(username=sender_name).first()
    receiver = User.query.filter_by(username=receiver_name).first()

    if sender and receiver:
        sender.balance -= amount
        receiver.balance += amount
        db.session.add(Transaction(sender=sender.username, receiver=receiver.username, amount=amount, note="Admin Transfer"))
        db.session.commit()
        flash(f"Transferred {amount} from {sender_name} to {receiver_name}")

    return redirect(url_for('admin_dashboard'))

# --- Templates ---

HELP_MODAL_FRAGMENT = """
<button id="help-btn" onclick="toggleHelp()" style="position: fixed; top: 15px; right: 15px; width: 35px; height: 35px; border-radius: 50%; background: #3b82f6; color: white; border: none; font-weight: bold; cursor: pointer; z-index: 1000; box-shadow: 0 2px 10px rgba(0,0,0,0.5);">?</button>
<div id="help-modal" class="hidden" onclick="if(event.target === this) toggleHelp()" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); display: flex; align-items: center; justify-content: center; z-index: 2000; padding: 20px;">
    <div style="background: #2d3748; padding: 25px; border-radius: 15px; max-width: 450px; width: 100%; max-height: 85vh; overflow-y: auto; position: relative; border: 1px solid #4a5568; box-shadow: 0 15px 35px rgba(0,0,0,0.5);">
        <button onclick="toggleHelp()" style="position: absolute; top: 10px; right: 15px; font-size: 2rem; color: #a0aec0; background: none; border: none; cursor: pointer;">&times;</button>
        <h2 style="margin-bottom: 15px; color: #3b82f6; font-size: 1.5rem; font-weight: bold;">How to Play</h2>
        
        <div style="font-size: 0.95rem; line-height: 1.5; color: #e2e8f0;">
            <p style="margin-bottom: 15px;"><strong>Objective:</strong> Use your phone to manage your virtual balance and transfer money to other players or the Bank.</p>
            <ul style="padding-left: 20px; margin-bottom: 15px;">
                <li style="margin-bottom: 8px;"><strong>Transfers:</strong> Select a recipient and amount to send funds.</li>
                <li style="margin-bottom: 8px;"><strong>Receiving:</strong> Show your QR code to others so they can pay you.</li>
                <li style="margin-bottom: 8px;"><strong>Pass Go:</strong> Click the button to collect your salary (Monopoly Mode).</li>
            </ul>
        </div>

        <hr style="border: 0; border-top: 1px solid #4a5568; margin: 20px 0;">
        <p style="font-size: 0.8em; color: #a0aec0; text-align: center;">
            This is a <strong>vibe code</strong> web app.<br>
            Visit <a href="https://serpilas.com" target="_blank" style="color: #3b82f6; text-decoration: none;">serpilas.com</a> for more.
        </p>
        <button onclick="location.reload();" style="width: 100%; padding: 10px; margin-top: 15px; background: #4a5568; color: white; border-radius: 8px; border: none; font-weight: bold; cursor: pointer;">RE-SYNC GAME</button>
    </div>
</div>
<script>function toggleHelp() { document.getElementById('help-modal').classList.toggle('hidden'); }</script>
"""

HTML_BASE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>SerPilasVirtualMoney</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #1a202c; color: #e2e8f0; font-family: sans-serif; }
        .card { background-color: #2d3748; padding: 2rem; border-radius: 0.75rem; margin-bottom: 1.5rem; }
        input, select { background-color: #4a5568; border: none; padding: 1rem; border-radius: 0.5rem; width: 100%; color: white; margin-bottom: 0.75rem; font-size: 1.125rem; }
        button { width: 100%; padding: 1rem; border-radius: 0.5rem; font-weight: bold; transition: background 0.2s; font-size: 1.125rem; }
        .btn-primary { background-color: #4299e1; color: white; }
        .btn-primary:hover { background-color: #3182ce; }
        .btn-danger { background-color: #f56565; color: white; }
        .btn-success { background-color: #48bb78; color: white; }
        .scrolling-wrapper { -webkit-overflow-scrolling: touch; }
        .scrolling-wrapper::-webkit-scrollbar { display: none; }
        .hover-row:hover { background-color: #4a5568; cursor: pointer; }
        .hidden { display: none !important; }
        /* Toggle Switch */
        .toggle-checkbox:checked {
            right: 0;
            border-color: #68D391;
        }
        .toggle-checkbox:checked + .toggle-label {
            background-color: #68D391;
        }
        /* Remove Spinners */
        input::-webkit-outer-spin-button,
        input::-webkit-inner-spin-button {
          -webkit-appearance: none;
          margin: 0;
        }
        input[type=number] {
          -moz-appearance: textfield;
        }
    </style>
</head>
<body class="p-6 {{ container_class|default('max-w-4xl') }} mx-auto pb-20">
    """ + HELP_MODAL_FRAGMENT + """
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="mb-6 p-4 bg-blue-600 rounded-lg text-center text-lg font-bold shadow-xl">
        {% for message in messages %}
          <div>{{ message }}</div>
        {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
</body>
</html>
"""

LOGIN_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="card text-center mt-10 max-w-md mx-auto">
    <h1 class="text-4xl font-bold mb-8 text-blue-400 tracking-tight">SerPilasVirtualMoney</h1>
    <p class="mb-6 text-lg text-gray-300">Enter a username to join the economy.</p>
    <form action="{{ url_for('login', **request_args) }}" method="POST">
        <input type="text" name="username" id="usernameInput" placeholder="Username" required autocomplete="off" class="text-center text-2xl h-16 rounded-xl">
        <button type="submit" class="btn-primary mt-6 py-5 text-xl rounded-xl shadow-lg">Join Economy</button>
    </form>
</div>

{% if monopoly_mode %}
<div class="card mt-8 max-w-md mx-auto">
    <h2 class="text-center text-xl font-bold text-gray-400 mb-4 uppercase tracking-widest">Select Player</h2>
    <div class="relative h-64 w-full">
        <canvas id="loginChart"></canvas>
    </div>
    <p class="text-center text-sm text-gray-500 mt-4 italic">Click a slice to pick your character</p>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    const ctx = document.getElementById('loginChart').getContext('2d');
    const chart = new Chart(ctx, {
        type: 'pie',
        data: {
            labels: {{ chart_labels | safe }},
            datasets: [{
                data: {{ chart_data | safe }},
                backgroundColor: {{ chart_colors | safe }},
                borderWidth: 2,
                borderColor: '#2d3748'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            onClick: (e, elements) => {
                if (elements.length > 0) {
                    const index = elements[0].index;
                    document.getElementById('usernameInput').value = chart.data.labels[index];
                }
            }
        }
    });
</script>
{% endif %}
""")

DASHBOARD_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="max-w-md mx-auto">
<div class="flex justify-between items-center mb-6">
    <div class="flex gap-2">
        <a href="{{ url_for('logout') }}" class="px-3 py-2 bg-gray-700 rounded-lg text-sm font-bold text-gray-400 hover:text-white transition">Logout</a>
        <a href="{{ url_for('how_to_play') }}" class="px-3 py-2 bg-green-700 rounded-lg text-sm font-black text-white hover:bg-green-600 transition shadow-lg border border-green-400">GUIDE</a>
        <a href="{{ url_for('leaderboard') }}" class="px-3 py-2 bg-yellow-600 rounded-lg text-sm font-black text-white hover:bg-yellow-500 transition shadow-lg">Ledger</a>
        {% if is_admin %}
        <a href="{{ url_for('admin') }}" class="px-3 py-2 bg-red-600 rounded-lg text-sm font-black text-white hover:bg-red-500 transition shadow-lg border border-red-400">ADMIN</a>
        {% endif %}
    </div>
    <div onclick="cycleColor()" class="text-base font-bold text-gray-400 tracking-wide cursor-pointer hover:text-white transition" id="usernameDisplay">{{ user.username }}</div>
</div>

<div class="text-center mb-12 pt-6">
    <h2 class="text-gray-500 uppercase tracking-widest text-sm font-black mb-3">Your Balance</h2>
    <div id="balanceDisplay" class="text-8xl font-black text-white tracking-tighter leading-none drop-shadow-2xl" style="color: {{ user.color_hex }}">{{ user.balance | currency }}</div>
</div>

{% if monopoly_mode %}
<form action="{{ url_for('pass_go') }}" method="POST" class="mb-8">
    <button type="submit" id="passGoBtn" class="w-full py-5 rounded-2xl bg-green-600 hover:bg-green-500 text-white font-black text-2xl shadow-xl transition-all transform active:scale-95 border-b-4 border-green-800 active:border-b-0 flex items-center justify-center gap-2">
        <span>🏁</span> <span id="passGoText">PASS GO (+$200)</span>
    </button>
</form>
{% endif %}

<div class="card relative shadow-2xl border border-gray-700/50">
    <h2 class="text-2xl font-black mb-4 text-gray-200">Transfer Funds</h2>
    <form action="{{ url_for('transfer') }}" method="POST" id="transferForm">
        {% if perms.can_act_as_banker %}
        <div class="mb-4">
            <label class="text-sm font-bold text-gray-500 uppercase tracking-wide ml-1 mb-1 block">Source Account (Banker Mode)</label>
            <select name="source" id="bankerSourceSelect" onchange="checkSource()" class="h-16 text-xl rounded-xl w-full bg-red-900/20 border border-red-500/50 text-white font-bold p-4">
                <option value="me">My Account ({{ user.balance | currency }})</option>
                <option value="bank" class="text-yellow-400">&#127974; THE BANK</option>
                <option value="parking" class="text-red-400">&#128663; Free Parking</option>
                {% for r in recents if r.username not in ['Bank', 'Free Parking'] %}
                <option value="{{ r.username }}">{{ r.username }}</option>
                {% endfor %}
            </select>
        </div>
        {% elif perms.can_send_bank %}
        <div class="mb-4">
            <label class="text-sm font-bold text-gray-500 uppercase tracking-wide ml-1 mb-1 block">From Account</label>
            <select name="source" id="bankerSourceSelect" onchange="checkSource()" class="h-16 text-xl rounded-xl w-full bg-blue-900/30 border border-blue-500/50 text-white font-bold p-4">
                <option value="me">My Personal Account ({{ user.balance | currency }})</option>
                {% if perms.can_send_bank %}<option value="bank" class="text-yellow-400">&#127974; THE BANK</option>{% endif %}
                {% if monopoly_mode %}<option value="parking" class="text-red-400">&#128663; Free Parking</option>{% endif %}
            </select>
        </div>
        {% endif %}
        
        <div class="mb-4">
            <label class="text-sm font-bold text-gray-500 uppercase tracking-wide ml-1 mb-1 block">Recipient</label>
            <div class="flex gap-3 items-end">
                <select name="recipient" id="recipientSelect" onchange="checkRecipient()" class="h-16 text-xl rounded-xl w-full bg-blue-900/30 border border-blue-500/50 text-white font-bold p-4">
                    <option value="bank" class="text-yellow-400" {{ 'selected' if send_to == 'Bank' else '' }}>&#127974; THE BANK</option>
                    <option value="parking" class="text-red-400" {{ 'selected' if send_to == 'Free Parking' else '' }}>&#128663; Free Parking</option>
                    {% for r in recents if r.username not in ['Bank', 'Free Parking'] %}
                    <option value="{{ r.username }}" {{ 'selected' if send_to == r.username else '' }}>{{ r.username }}</option>
                    {% endfor %}
                </select>
                
                {% if not monopoly_mode %}
                <button type="button" id="quickSendBtn" onclick="sendOneDollar()" class="hidden bg-green-500 hover:bg-green-600 text-white font-black p-4 rounded-xl w-28 h-16 flex items-center justify-center transition-all shadow-xl animate-bounce text-2xl border-b-4 border-green-700 active:border-b-0">
                    <span class="mr-1">&#9889;</span> 1
                </button>
                {% endif %}
            </div>
        </div>

        <div class="flex gap-2 mb-4">
            <button type="button" onclick="selectUser('bank')" class="flex-1 bg-slate-300 hover:bg-slate-200 text-slate-900 font-bold py-3 rounded-lg shadow-md flex items-center justify-center gap-2 transition active:scale-95">
                <span class="text-xl">🏛️</span> Bank
            </button>
            <button type="button" onclick="selectUser('parking')" class="flex-1 bg-red-500 hover:bg-red-400 text-white font-bold py-3 rounded-lg shadow-md flex items-center justify-center gap-2 transition active:scale-95">
                <span class="text-xl">🚗</span> Parking
            </button>
        </div>
        
        <label class="text-sm font-bold text-gray-500 uppercase tracking-wide ml-1 mb-1 block">Amount</label>
        <input type="number" name="amount" id="amountInput" step="1" min="1" placeholder="0" required class="appearance-none h-16 text-2xl rounded-xl font-black">
        
        <button type="submit" class="btn-primary mt-4 py-5 text-xl rounded-xl shadow-lg border-b-4 border-blue-700 active:border-b-0">Send Money</button>
    </form>
</div>

<script>
    fetch('{{ url_for("api_users") }}').then(res => res.json()).then(data => {
        const list = document.getElementById('user-suggestions');
        data.forEach(user => {
            const option = document.createElement('option');
            option.value = user;
            list.appendChild(option);
        });
    });

    function selectUser(username) {
        const sel = document.getElementById('recipientSelect');
        sel.value = username;
        checkRecipient();
        document.getElementById('amountInput').focus();
    }

    function checkRecipient() {
        const sel = document.getElementById('recipientSelect');
        const btn = document.getElementById('quickSendBtn');
        if (!btn) return;
        if (sel.value !== "") btn.classList.remove('hidden');
        else btn.classList.add('hidden');
    }
    
    function sendOneDollar() {
        const recipient = document.getElementById('recipientSelect').value;
        const source = document.getElementById('bankerSourceSelect') ? document.getElementById('bankerSourceSelect').value : 'me';
        
        if (recipient === source || (recipient === '{{ user.username }}' && source === 'me')) {
            alert("Cannot send money to the same account");
            return;
        }

        const formData = new FormData();
        formData.append('recipient', recipient);
        formData.append('source', source);
        formData.append('amount', '1');
        
        fetch('{{ url_for("transfer") }}', { method: 'POST', body: formData, headers: {'X-Requested-With': 'XMLHttpRequest'} })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                document.getElementById('balanceDisplay').innerText = '$' + data.new_balance.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
            } else {
                alert(data.error);
            }
        });
    }

    checkRecipient();
    let currentTxId = {{ initial_tx_id }};
    let currentFreeParkingBalance = {{ free_parking_balance if free_parking_balance is not none else 0 }};
    
    function checkSource() {
        const source = document.getElementById('bankerSourceSelect');
        if (source && source.value === 'parking') {
            document.getElementById('amountInput').value = currentFreeParkingBalance;
        } else {
             document.getElementById('amountInput').value = '';
        }
    }

    function cycleColor() {
        fetch('{{ url_for("cycle_color") }}', { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                document.getElementById('balanceDisplay').style.color = data.new_color;
                // Optionally reload or update other elements
                location.reload(); 
            }
        });
    }

    setInterval(() => {
        if (document.activeElement.tagName === 'INPUT') return;
        fetch('{{ url_for("api_status") }}').then(res => res.json()).then(data => {
            if (data.error) return; 
            document.getElementById('balanceDisplay').innerText = '$' + data.balance.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
            
            // Pass Go Cooldown Logic
            const pgBtn = document.getElementById('passGoBtn');
            const pgText = document.getElementById('passGoText');
            if (pgBtn && pgText) {
                if (data.pass_go_cooldown > 0) {
                    pgBtn.disabled = true;
                    pgText.innerText = `LOCKED (${data.pass_go_cooldown}s)`;
                } else {
                    pgBtn.disabled = false;
                    pgText.innerText = "PASS GO (+ $200)";
                }
            }

            const fpDisplay = document.getElementById('freeParkingDisplay');
            if (data.free_parking_balance !== null) {
                currentFreeParkingBalance = data.free_parking_balance; 
                if (fpDisplay) fpDisplay.innerText = '$' + data.free_parking_balance.toLocaleString(undefined, {minimumFractionDigits: 0, maximumFractionDigits: 0});
            }
            if (data.last_tx_id > currentTxId) location.reload();
        });
    }, 3000);
</script>

{% if free_parking_balance is not none %}
<div class="card text-center shadow-xl border-4 border-red-500/50 bg-red-900/20 mb-6 py-6">
    <h2 class="text-sm font-black text-red-400 uppercase tracking-widest mb-1">&#128663; Free Parking Pot</h2>
    <div id="freeParkingDisplay" class="text-5xl font-black text-white tracking-tighter">{{ free_parking_balance | currency }}</div>
</div>
{% endif %}

<div class="card text-center shadow-xl border border-gray-700/30">
    <h2 class="text-xl font-black mb-3 text-gray-300 uppercase tracking-widest">Receive Funds</h2>
    <div class="bg-white p-4 inline-block rounded-3xl shadow-inner"><img src="{{ url_for('qr_image') }}" alt="Your QR Code" class="w-56 h-56"></div>
</div>

<div class="mt-10">
    <h3 class="text-xl font-black mb-4 text-gray-400 uppercase tracking-widest">Transaction History</h3>
    <div class="space-y-3">
        {% for tx in history %}
        <div class="bg-gray-800/80 backdrop-blur-sm p-4 rounded-2xl flex justify-between items-center shadow-lg border border-gray-700/30">
            <div>
                {% if tx.sender == user.username %}
                    <span class="text-red-400 font-bold text-lg">Sent to {{ tx.receiver }}</span>
                {% else %}
                    <span class="text-green-400 font-bold text-lg">Received from {{ tx.sender }}</span>
                {% endif %}
                <div class="text-sm text-gray-600 font-medium">{{ tx.timestamp.strftime('%H:%M:%S') }}</div>
            </div>
            <div class="font-black text-xl {% if tx.sender == user.username %}text-red-400{% else %}text-green-400{% endif %}">
                {% if tx.sender == user.username %}-{% else %}+{% endif %}{{ tx.amount | currency }}
            </div>
        </div>
        {% else %}
        <p class="text-gray-600 text-center py-8 text-lg italic">No activity yet.</p>
        {% endfor %}
    </div>
</div>
</div>
""")

LEADERBOARD_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="max-w-2xl mx-auto">
    <div class="flex justify-between items-center mb-8">
        <h1 class="text-3xl font-black text-yellow-400 tracking-tight uppercase">Public Ledger</h1>
        <div class="flex gap-3">
            <a href="{{ url_for('stats') }}" class="px-6 py-3 bg-blue-600 rounded-xl text-lg font-bold text-white hover:bg-blue-500 transition">Stats</a>
            <a href="{{ url_for('index') }}" class="px-6 py-3 bg-gray-700 rounded-xl text-lg font-bold text-gray-300 hover:bg-gray-600 transition">Back</a>
        </div>
    </div>

    <div class="card mb-8 shadow-2xl border border-gray-700">
        <div class="flex flex-wrap justify-between items-center mb-6 gap-4">
            <h2 class="text-2xl font-black text-gray-200 uppercase tracking-widest">Wealth Distribution</h2>
            <div class="flex gap-6">
                <label class="inline-flex items-center cursor-pointer">
                    <input type="checkbox" id="toggleBank" checked onchange="updateChart()" class="sr-only peer">
                    <div class="relative w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                    <span class="ms-3 text-xs font-bold text-gray-400 uppercase">Include Bank</span>
                </label>
                <label class="inline-flex items-center cursor-pointer">
                    <input type="checkbox" id="toggleParking" checked onchange="updateChart()" class="sr-only peer">
                    <div class="relative w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full rtl:peer-checked:after:-translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:start-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-purple-600"></div>
                    <span class="ms-3 text-xs font-bold text-gray-400 uppercase">Include Parking</span>
                </label>
            </div>
        </div>
        <div class="relative h-80 w-full"><canvas id="wealthChart"></canvas></div>
    </div>

    <div class="card shadow-2xl border border-gray-700">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-black text-white uppercase tracking-widest">Rich List</h2>
            <div class="text-right">
                <p id="totalLabel" class="text-[10px] text-gray-500 font-bold uppercase tracking-widest">Total Game Assets</p>
                <p id="dynamicTotal" class="text-green-400 text-3xl font-black tracking-tighter">{{ total_circulation | currency }}</p>
            </div>
        </div>
        <div class="overflow-y-auto">
            <table class="w-full text-lg text-left text-gray-400">
                <thead class="text-sm text-gray-200 uppercase bg-gray-700">
                    <tr><th class="px-4 py-3">Rank</th><th class="px-4 py-3">User</th><th class="px-4 py-3 text-right">Balance</th></tr>
                </thead>
                <tbody class="divide-y divide-gray-700">
                    {% for u in users %}
                    <tr class="{% if u.username == 'Bank' %}bg-blue-900/30{% endif %} hover:bg-gray-700/50 transition">
                        <td class="px-4 py-5 font-black text-gray-500">#{{ loop.index }}</td>
                        <td class="px-4 py-5 font-black text-white text-xl">{{ u.username }}</td>
                        <td class="px-4 py-5 text-right text-green-400 font-black text-2xl">{{ u.balance | currency }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
    const ctx = document.getElementById('wealthChart').getContext('2d');
    const rawLabels = {{ chart_labels | safe }};
    const rawData = {{ chart_data | safe }};
    const rawColors = {{ chart_colors | safe }};

    const chart = new Chart(ctx, {
        type: 'pie',
        data: {
            labels: rawLabels,
            datasets: [{
                data: rawData,
                backgroundColor: rawColors,
                borderWidth: 2, borderColor: '#2d3748'
            }]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false, 
            plugins: { 
                legend: { position: 'right', labels: { color: '#9ca3af', font: { weight: 'bold' } } } 
            } 
        }
    });

    function updateChart() {
        const includeBank = document.getElementById('toggleBank').checked;
        const includeParking = document.getElementById('toggleParking').checked;

        let filteredLabels = [];
        let filteredData = [];
        let filteredColors = [];
        let total = 0;

        rawLabels.forEach((label, i) => {
            if (label === 'Bank' && !includeBank) return;
            if (label === 'Free Parking' && !includeParking) return;
            
            filteredLabels.push(label);
            filteredData.push(rawData[i]);
            filteredColors.push(rawColors[i]);
            total += rawData[i];
        });

        chart.data.labels = filteredLabels;
        chart.data.datasets[0].data = filteredData;
        chart.data.datasets[0].backgroundColor = filteredColors;
        chart.update();

        document.getElementById('dynamicTotal').innerText = '$' + total.toLocaleString();
        document.getElementById('totalLabel').innerText = (includeBank && includeParking) ? "Total Game Assets" : "Filtered Wealth";
    }
</script>
""")

ADMIN_DASHBOARD_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="flex justify-between items-center mb-10">
    <h1 class="text-5xl font-black text-red-500 uppercase tracking-tighter">Admin Terminal</h1>
    <div class="flex gap-4">
        <a href="{{ url_for('admin_help') }}" class="px-10 py-5 bg-blue-700 rounded-2xl text-xl font-black text-gray-100 hover:bg-blue-600 transition shadow-2xl border-b-4 border-blue-800 active:border-b-0">GUIDE</a>
        <a href="{{ url_for('index') }}" class="px-10 py-5 bg-gray-700 rounded-2xl text-xl font-black text-gray-300 hover:bg-gray-600 transition shadow-2xl border-b-4 border-gray-800 active:border-b-0">USER VIEW</a>
    </div>
</div>

<!-- Top Row: Stats and Toggles -->
<div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
    <div class="card border-l-8 border-green-500 flex flex-col justify-center py-6">
        <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Circulation</p>
        <p class="text-4xl font-black text-green-400 tracking-tighter">{{ total_circulation | currency }}</p>
    </div>
    <div class="card border-l-8 border-blue-500 flex flex-col justify-center py-6">
        <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Active Users</p>
        <p class="text-4xl font-black text-white tracking-tighter">{{ user_stats|length }}</p>
    </div>
    <div class="card md:col-span-2 flex items-center justify-between py-6">
        <div>
            <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">System Backup</p>
            <div class="flex gap-2 mt-2">
                <a href="{{ url_for('admin_export') }}" class="px-4 py-2 bg-green-600 hover:bg-green-500 text-white rounded-lg font-black text-xs transition uppercase shadow-lg border-b-2 border-green-800 active:border-b-0">Export JSON</a>
                <form action="{{ url_for('admin_import') }}" method="POST" enctype="multipart/form-data" class="inline">
                    <input type="file" name="file" id="importInput" class="hidden" onchange="this.form.submit()">
                    <button type="button" onclick="document.getElementById('importInput').click()" class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-black text-xs transition uppercase shadow-lg border-b-2 border-blue-800 active:border-b-0">Import JSON</button>
                </form>
            </div>
        </div>
        <div class="flex gap-4">
            {% if my_perms.can_toggle_monopoly %}
            <form action="{{ url_for('admin_toggle_monopoly') }}" method="POST">
                <button type="submit" class="px-8 py-4 rounded-xl font-black transition-all text-lg {{ 'bg-yellow-500 text-black shadow-lg shadow-yellow-500/40' if monopoly_mode else 'bg-gray-700 text-gray-400' }}">
                    {{ 'ENABLED' if monopoly_mode else 'DISABLED' }}
                </button>
            </form>
            {% endif %}
        </div>
    </div>
</div>

<!-- Middle Row: Manage Funds (Full Width) -->
{% if my_perms.can_mint or my_perms.can_burn %}
<div class="card shadow-2xl border border-gray-700/50 mb-8">
    <h2 class="text-2xl font-black mb-8 text-gray-200 uppercase tracking-widest">Global Fund Management</h2>
    <form action="{{ url_for('admin_action') }}" method="POST">
        <div class="grid grid-cols-1 md:grid-cols-4 gap-8 mb-8">
            <!-- Sender -->
            <div>
                <label class="block text-sm font-black text-gray-500 mb-3 uppercase tracking-widest ml-1">From (Sender)</label>
                <select name="sender" id="senderSelect" onchange="checkAdminSender()" class="bg-gray-700 text-white rounded-2xl p-5 h-20 w-full text-3xl font-bold border-2 border-transparent focus:border-red-500 transition shadow-inner">
                    {% if my_perms.can_mint %}
                    <option value="MINT" class="text-green-400 font-black">MINT (CREATE)</option>
                    <option value="SET_BALANCE" class="text-blue-400 font-black">SET BALANCE</option>
                    {% endif %}
                    <option value="Bank" selected>BANK</option>
                    {% if monopoly_mode %}
                    <option value="Free Parking" class="text-purple-400">FREE PARKING</option>
                    {% endif %}
                    {% for u in all_users %}
                    <option value="{{ u }}">{{ u }}</option>
                    {% endfor %}
                </select>
            </div>
            
            <!-- Receiver -->
            <div>
                <label class="block text-sm font-black text-gray-500 mb-3 uppercase tracking-widest ml-1">To (Receiver)</label>
                <select name="receiver" id="receiverSelect" class="bg-gray-700 text-white rounded-2xl p-5 h-20 w-full text-3xl font-bold border-2 border-transparent focus:border-red-500 transition shadow-inner">
                     {% if my_perms.can_burn %}
                     <option value="BURN" class="text-red-400 font-black">BURN (DESTROY)</option>
                     {% endif %}
                     <option value="Bank">BANK</option>
                     {% if monopoly_mode %}
                     <option value="Free Parking">FREE PARKING</option>
                     {% endif %}
                     {% for u in all_users %}
                     <option value="{{ u }}">{{ u }}</option>
                     {% endfor %}
                </select>
            </div>

            <!-- Amount -->
            <div>
                <label class="block text-sm font-black text-gray-500 mb-3 uppercase tracking-widest ml-1">Amount</label>
                <input type="number" name="amount" id="amountInput" placeholder="0" step="1" required class="bg-gray-700 text-white rounded-2xl p-5 h-20 w-full text-3xl font-black border-2 border-transparent focus:border-red-500 transition shadow-inner">
            </div>
        </div>
        
        <button type="submit" class="w-full bg-red-600 hover:bg-red-500 text-white h-24 text-3xl font-black rounded-3xl shadow-2xl transition-all transform active:scale-[0.99] border-b-8 border-red-800 active:border-b-0 uppercase tracking-widest">Execute Transaction</button>
    </form>
</div>
{% endif %}

<!-- Bottom Row: User List and Danger Zone -->
<div class="grid grid-cols-1 md:grid-cols-3 gap-8">
    <!-- User List (2/3 width) -->
    <div class="card md:col-span-2 shadow-2xl">
        <h2 class="text-2xl font-black mb-6 text-white uppercase tracking-widest">User Permissions</h2>
        <div class="overflow-y-auto max-h-[40rem] rounded-2xl border border-gray-700">
            <table class="w-full text-left text-gray-400">
                <thead class="text-sm text-gray-200 uppercase bg-gray-700 sticky top-0">
                    <tr>
                        <th class="px-6 py-4">User</th>
                        <th class="px-6 py-4">Bank Access</th>
                        <th class="px-6 py-4">Transfer founds of all users</th>
                        <th class="px-6 py-4">Manage Perms</th>
                        <th class="px-6 py-4">Delete Users</th>
                        <th class="px-6 py-4">Cooldown</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-700 bg-gray-800/50">
                    {% for stat in user_stats if stat.username != 'Bank' %}
                    <tr class="hover-row transition duration-150 group">
                        <td class="px-6 py-4 font-black text-white text-xl">
                            {{ stat.username }}
                            {% if stat.is_root %}<span class="text-xs text-red-400 ml-2 border border-red-400 px-2 rounded">ROOT</span>{% endif %}
                        </td>
                        <!-- Permissions Columns -->
                        {% for perm in ['can_send_bank', 'can_act_as_banker', 'can_manage_permissions', 'can_delete_user'] %}
                        <td class="px-6 py-4">
                             {% if not stat.is_root and my_perms.can_manage_permissions %}
                             <form action="{{ url_for('admin_update_permission') }}" method="POST">
                                <input type="hidden" name="username" value="{{ stat.username }}">
                                <input type="hidden" name="permission" value="{{ perm }}">
                                <button type="submit" class="w-12 h-6 rounded-full transition-colors duration-200 focus:outline-none {{ 'bg-green-500' if stat.permissions[perm] else 'bg-gray-600' }}">
                                    <span class="block w-4 h-4 rounded-full bg-white shadow transform transition-transform duration-200 {{ 'translate-x-7' if stat.permissions[perm] else 'translate-x-1' }}"></span>
                                </button>
                             </form>
                             {% else %}
                                <div class="w-12 h-6 rounded-full {{ 'bg-green-500 opacity-50' if stat.permissions[perm] else 'bg-gray-600 opacity-50' }}"></div>
                             {% endif %}
                        </td>
                        {% endfor %}
                        <!-- Reset Cooldown Column -->
                        <td class="px-6 py-4">
                            {% if my_perms.can_manage_permissions %}
                            <form action="{{ url_for('admin_action') }}" method="POST">
                                <input type="hidden" name="action" value="reset_cooldown">
                                <input type="hidden" name="username" value="{{ stat.username }}">
                                <button type="submit" class="bg-blue-600 hover:bg-blue-500 text-white text-[10px] font-bold px-2 py-1 rounded shadow">RESET CO</button>
                            </form>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <!-- Danger Zone (1/3 width) -->
    <div class="card md:col-span-1 border-4 border-red-900 bg-red-950/20 shadow-2xl flex flex-col">
        <h2 class="text-3xl font-black mb-6 text-red-500 uppercase tracking-tighter">Danger Zone</h2>
        
        {% if my_perms.can_delete_user %}
        <div class="mb-12">
            <label class="block text-sm font-black text-red-900 mb-4 uppercase tracking-widest">Terminate Account</label>
            <form action="{{ url_for('admin_action') }}" method="POST" onsubmit="return confirm('Delete this user?');">
                <input type="hidden" name="action" value="delete_user">
                <div class="space-y-4">
                    <select name="username" class="bg-gray-900 text-red-500 rounded-2xl p-5 h-20 w-full text-3xl font-black border-2 border-red-900 shadow-inner" required>
                        <option value="" disabled selected>SELECT TARGET</option>
                        {% for u in all_users if u != 'Bank' %}
                        <option value="{{ u }}">{{ u }}</option>
                        {% endfor %}
                    </select>
                    <button type="submit" class="w-full bg-red-900 hover:bg-red-800 text-white py-5 rounded-2xl font-black uppercase text-xl transition shadow-xl border-b-4 border-red-950 active:border-b-0">DELETE USER</button>
                </div>
            </form>
        </div>
        {% endif %}

        {% if my_perms.can_reset %}
        <div class="mt-auto pt-10 border-t border-red-900/50">
            <label class="block text-sm font-black text-red-900 mb-4 uppercase tracking-widest">System Override</label>
            <form action="{{ url_for('admin_action') }}" method="POST" onsubmit="return confirm('WIPE database?');">
                <input type="hidden" name="action" value="reset">
                <input type="hidden" name="confirm" value="yes">
                <button type="submit" class="w-full bg-red-600 hover:bg-red-500 text-white py-8 text-2xl font-black rounded-3xl shadow-2xl border-b-8 border-red-900 active:border-b-0 transition-all uppercase tracking-widest">NUKE DATABASE</button>
            </form>
        </div>
        {% endif %}
    </div>
</div>

<!-- Transaction Log -->
<div class="card shadow-2xl border border-gray-700/50 mt-8">
    <h2 class="text-2xl font-black mb-6 text-white uppercase tracking-widest">System Transaction Log</h2>
    <div class="overflow-y-auto max-h-[32rem] rounded-xl">
        <table class="w-full text-left text-gray-400">
            <thead class="text-sm text-gray-200 uppercase bg-gray-700 sticky top-0">
                <tr><th class="px-6 py-4">Time</th><th class="px-6 py-4">From</th><th class="px-6 py-4">To</th><th class="px-6 py-4 text-right">Amount</th><th class="px-6 py-4 text-right">Note</th></tr>
            </thead>
            <tbody class="divide-y divide-gray-700 bg-gray-800/50">
                {% for tx in transactions %}
                <tr class="hover:bg-gray-700/30 transition">
                    <td class="px-6 py-4 text-sm font-mono text-gray-500">{{ tx.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                    <td class="px-6 py-4 font-bold text-white">{{ tx.sender or 'SYSTEM' }}</td>
                    <td class="px-6 py-4 font-bold text-white">{{ tx.receiver }}</td>
                    <td class="px-6 py-4 text-right font-black {{ 'text-green-400' if tx.sender == 'MINT' else 'text-white' }}">{{ tx.amount | currency }}</td>
                    <td class="px-6 py-4 text-right text-xs text-gray-500 uppercase tracking-wide">{{ tx.note }}</td>
                </tr>
                {% else %}
                <tr><td colspan="5" class="px-6 py-8 text-center text-gray-500 italic">No transactions recorded.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>

<script>
    function fillUser(username) {
        const select = document.getElementById('receiverSelect');
        select.value = username;
        select.classList.add('ring-8', 'ring-red-500/50');
        setTimeout(() => select.classList.remove('ring-8', 'ring-red-500/50'), 500);
        document.getElementById('amountInput').focus();
    }
    function checkAdminSender() {
        const sender = document.getElementById('senderSelect').value;
        if (sender === 'Free Parking') {
            document.getElementById('amountInput').value = {{ free_parking_balance }};
        } else {
             document.getElementById('amountInput').value = '';
        }
    }
</script>
""")

STATS_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="max-w-4xl mx-auto">
    <div class="flex justify-between items-center mb-8">
        <h1 class="text-3xl font-black text-blue-400 tracking-tight uppercase">Economic Analytics</h1>
        <a href="{{ url_for('leaderboard') }}" class="px-6 py-3 bg-gray-700 rounded-xl text-lg font-bold text-gray-300 hover:bg-gray-600 transition">Back</a>
    </div>

    <!-- Stat Cards -->
    <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
        <div class="card border-l-8 border-blue-500 py-6">
            <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Game Duration</p>
            <p class="text-3xl font-black text-white tracking-tighter">{{ duration }}</p>
        </div>
        <div class="card border-l-8 border-yellow-500 py-6">
            <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Total Supply</p>
            <p class="text-3xl font-black text-yellow-400 tracking-tighter">{{ total_supply | currency }}</p>
        </div>
        <div class="card border-l-8 border-red-500 py-6">
            <p class="text-xs font-black text-gray-500 uppercase tracking-widest mb-1">Inflation Rate</p>
            <p class="text-3xl font-black text-red-400 tracking-tighter">{{ inflation_pct }}%</p>
            <p class="text-[10px] text-gray-600 uppercase mt-1">Void Money / Total Supply</p>
        </div>
    </div>
    
    <div class="mb-8 p-4 bg-gray-800 rounded-lg border border-gray-700 text-sm text-gray-400">
        <p><strong class="text-white">How is Inflation Calculated?</strong></p>
        <p>Inflation Rate = (Total Money Created from Void / Total Money in Circulation) * 100</p>
        <p class="mt-1 text-xs italic">"Void Money" includes all cash generated via <strong>Pass Go</strong> and <strong>Admin Mints</strong> that didn't exist at the start.</p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
        <!-- Inflation Details -->
        <div class="card shadow-2xl border border-gray-700">
            <h2 class="text-xl font-black mb-6 text-white uppercase tracking-widest">Currency Sources</h2>
            <div class="space-y-4">
                <div class="flex justify-between items-center">
                    <span class="text-gray-400">Pass Go Rewards</span>
                    <span class="text-green-400 font-bold">{{ pass_go_total | currency }}</span>
                </div>
                <div class="flex justify-between items-center">
                    <span class="text-gray-400">Admin Mints</span>
                    <span class="text-blue-400 font-bold">{{ mint_total | currency }}</span>
                </div>
                <div class="pt-4 border-t border-gray-700 flex justify-between items-center">
                    <span class="text-white font-black uppercase">Total "Void" Money</span>
                    <span class="text-white font-black text-xl">{{ (pass_go_total + mint_total) | currency }}</span>
                </div>
            </div>
        </div>

        <!-- Player Join Timeline -->
        <div class="card shadow-2xl border border-gray-700">
            <h2 class="text-xl font-black mb-6 text-white uppercase tracking-widest">Player Timeline</h2>
            <div class="overflow-y-auto max-h-64">
                <table class="w-full text-left text-gray-400">
                    <thead class="text-xs uppercase bg-gray-700">
                        <tr>
                            <th class="px-4 py-2">Joined</th>
                            <th class="px-4 py-2">Username</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-700">
                        {% for u in timeline_users %}
                        <tr class="hover:bg-gray-700/30 transition">
                            <td class="px-4 py-3 text-xs font-mono text-gray-500">
                                {{ u.created_at.strftime('%H:%M:%S') if u.created_at else '---' }}
                            </td>
                            <td class="px-4 py-3 font-bold text-white">{{ u.username }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>
""")

ADMIN_HELP_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="max-w-3xl mx-auto">
    <div class="flex justify-between items-center mb-8">
        <h1 class="text-3xl font-black text-blue-400 tracking-tight uppercase">Admin Guide</h1>
        <a href="{{ url_for('admin_dashboard') }}" class="px-6 py-3 bg-gray-700 rounded-xl text-lg font-bold text-gray-300 hover:bg-gray-600 transition">Back</a>
    </div>

    <div class="space-y-8">
        <div class="card shadow-xl border-l-4 border-green-500">
            <h2 class="text-xl font-black text-white mb-4 uppercase">1. Fund Management</h2>
            <div class="text-gray-400 space-y-3 leading-relaxed">
                <p><strong class="text-green-400">MINT (Create):</strong> Use this to inject new money into the game. This doesn't take from anyone; it creates value from the "Void". Use for starting bonuses or special events.</p>
                <p><strong class="text-red-400">BURN (Destroy):</strong> Use this to remove money permanently. This is used to fight inflation or as a penalty for rule-breaking.</p>
                <p><strong class="text-blue-400">SET BALANCE:</strong> A direct override. Useful if a player makes a mistake and you need to fix their wallet instantly.</p>
            </div>
        </div>

        <div class="card shadow-xl border-l-4 border-blue-500">
            <h2 class="text-xl font-black text-white mb-4 uppercase">2. Role Explanations</h2>
            <div class="text-gray-400 space-y-4">
                <div>
                    <strong class="text-blue-400 uppercase text-sm block mb-1">Bank Access</strong>
                    <p>Allows the player to use "THE BANK" as a source in the transfer screen. They can distribute the Bank's money but cannot move money between players.</p>
                </div>
                <div>
                    <strong class="text-purple-400 uppercase text-sm block mb-1">Banker Role</strong>
                    <p>Total cash flow control. This user can move money from <em class="text-white italic">anyone</em> to <em class="text-white italic">anyone</em>. They act as the physical banker of a Monopoly board.</p>
                </div>
                <div>
                    <strong class="text-red-400 uppercase text-sm block mb-1">Manage Perms</strong>
                    <p>Grants access to the Admin Terminal. These users can promote others to admins or bankers. Only the Host (ROOT) can toggle this for other admins.</p>
                </div>
            </div>
        </div>

        <div class="card shadow-xl border-l-4 border-red-600 bg-red-900/10">
            <h2 class="text-xl font-black text-red-500 mb-4 uppercase">3. Reset Logic (Danger)</h2>
            <p class="text-gray-400 leading-relaxed mb-4">Clicking <strong class="text-white">NUKE DATABASE</strong> performs a hard reset. All players are deleted, all transaction history is wiped, and the game starts from scratch.</p>
            <div class="bg-red-900/20 p-4 rounded-lg border border-red-900/50">
                <p class="text-xs text-red-400 font-bold uppercase">Pro Tip:</p>
                <p class="text-xs text-gray-500">Use "SET BALANCE" to fix individual errors instead of resetting the whole game!</p>
            </div>
        </div>
    </div>
</div>
""")

WELCOME_GUIDE_TEMPLATE = HTML_BASE.replace('{% block content %}{% endblock %}', """
<div class="max-w-md mx-auto">
    <div class="flex justify-between items-center mb-8">
        <h1 class="text-3xl font-black text-green-400 tracking-tight uppercase">How to Play</h1>
        <a href="{{ url_for('index') }}" class="px-6 py-3 bg-gray-700 rounded-xl text-lg font-bold text-gray-300 hover:bg-gray-600 transition">Back</a>
    </div>

    <div class="space-y-6">
        <div class="card border-l-4 border-blue-500 shadow-xl">
            <h2 class="text-xl font-black text-white mb-3 uppercase flex items-center gap-2">
                <span>&#128247;</span> 1. Paying Others
            </h2>
            <p class="text-gray-400 leading-relaxed">To pay a player, click the <strong class="text-white">Recipient</strong> dropdown or scan their <strong class="text-white">QR Code</strong>. Enter the amount and hit Send. It's instant!</p>
        </div>

        <div class="card border-l-4 border-yellow-500 shadow-xl">
            <h2 class="text-xl font-black text-white mb-3 uppercase flex items-center gap-2">
                <span>&#127974;</span> 2. The Bank
            </h2>
            <p class="text-gray-400 leading-relaxed">Buying property? Select <strong class="text-yellow-400">THE BANK</strong> as your recipient. Rent due? Just pick the player's name.</p>
        </div>

        <div class="card border-l-4 border-red-500 shadow-xl">
            <h2 class="text-xl font-black text-white mb-3 uppercase flex items-center gap-2">
                <span>&#128663;</span> 3. Free Parking
            </h2>
            <p class="text-gray-400 leading-relaxed">Paying a fine? Send it to <strong class="text-red-400">Free Parking</strong>. Landed on it? Use the <strong class="text-white">From Account</strong> dropdown to collect the pot!</p>
        </div>

        <div class="card bg-green-900/10 border-4 border-green-500/20 text-center py-8">
            <h2 class="text-2xl font-black text-green-400 mb-2 italic">No more paper bills.</h2>
            <p class="text-gray-500 text-sm">Instant math. Real-time leaderboard. Total transparency. Welcome to the future of the game.</p>
        </div>

        <div class="card bg-gray-700/50 border-l-4 border-pink-500 shadow-xl mt-6">
            <h2 class="text-xl font-black text-white mb-3 uppercase">Project Origins</h2>
            <p class="text-gray-400 leading-relaxed mb-4 text-sm">
                This app digitizes Monopoly banking for faster, cleaner play. It was <strong>vibe coded by Elric. To learn more or provide feedback, visit our website below.
            </p>
            <div class="text-center">
                <a href="https://serpilas.com/" target="_blank" class="text-blue-400 underline font-bold text-lg hover:text-blue-300 transition">serpilas.com</a>
            </div>
        </div>
    </div>
</div>
""")

# --- Startup ---

def print_startup_qr():
    ip = get_local_ip()
    url = f"http://{ip}:8080"
    print("\n" + "="*40)
    print(f"Server running at: {url}")
    print("Scan this QR code to connect:")
    print("="*40 + "\n")
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.print_ascii(tty=True)
    print("\n" + "="*40 + "\n")


def init_db():
    with app.app_context():
        db.create_all()
        # Explicit migration for new columns if they don't exist
        with db.engine.connect() as conn:
            try: conn.execute(text("ALTER TABLE user ADD COLUMN is_root BOOLEAN DEFAULT 0"))
            except: pass
            try: conn.execute(text("ALTER TABLE user ADD COLUMN permissions_json TEXT DEFAULT '{}'"))
            except: pass
            try: conn.execute(text("ALTER TABLE user ADD COLUMN is_banker BOOLEAN DEFAULT 0"))
            except: pass
            try: conn.execute(text("ALTER TABLE user ADD COLUMN created_at DATETIME"))
            except: pass
            try: conn.execute(text("ALTER TABLE user ADD COLUMN color_hex VARCHAR(20) DEFAULT '#3b82f6'"))
            except: pass

        if not User.query.filter_by(username='Bank').first():
            bank = User(username='Bank', balance=0, color_hex='#E2E8F0', is_banker=True)
            db.session.add(bank)
            db.session.commit()
            print("Bank user initialized.")

        # Ensure Monopoly Mode default
        if not SystemSetting.query.get('monopoly_mode'):
            db.session.add(SystemSetting(key='monopoly_mode', value='1'))
            # Ensure Free Parking exists if defaulting to Monopoly
            if not User.query.filter_by(username='Free Parking').first():
                 db.session.add(User(username='Free Parking', balance=0, color_hex='#EF4444'))
            db.session.commit()
            print("Monopoly Mode enabled by default.")

if __name__ == '__main__':
    init_db()
    print_startup_qr()
    app.run(host='0.0.0.0', port=8080, debug=False)
