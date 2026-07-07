# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime
import os
import json
import bcrypt
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from dateutil.relativedelta import relativedelta
import paramiko
import re

app = Flask(__name__)
app.secret_key = 'supersecretkeychangeit'

# ---------- Инициализация Firebase ----------
firebase_creds_json = os.environ.get('FIREBASE_CREDENTIALS')
if firebase_creds_json:
    cred_dict = json.loads(firebase_creds_json)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("firebase-key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------- Админ-пароль (хеш) ----------
ADMIN_PASSWORD_HASH = bcrypt.hashpw('admin'.encode(), bcrypt.gensalt()).decode()

# ---------- Работа с настройками ----------
def get_setting(key, default=None):
    doc_ref = db.collection('settings').document(key)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict().get('value', default)
    return default

def set_setting(key, value):
    db.collection('settings').document(key).set({'value': value})

# Устанавливаем цены по умолчанию, если их нет
if not get_setting('monthly_price'):
    set_setting('monthly_price', '500')
if not get_setting('yearly_price'):
    set_setting('yearly_price', '5000')

# ---------- Вспомогательные функции для работы с Firestore ----------
def get_users():
    users_ref = db.collection('users')
    docs = users_ref.stream()
    users_list = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        users_list.append(data)
    return users_list

def get_transactions():
    trans_ref = db.collection('transactions').order_by('created_at', direction=firestore.Query.DESCENDING)
    docs = trans_ref.stream()
    trans_list = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        trans_list.append(data)
    return trans_list

def get_logs(limit=50):
    logs_ref = db.collection('logs').order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)
    docs = logs_ref.stream()
    logs_list = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id
        logs_list.append(data)
    return logs_list

def add_log(action, details=''):
    db.collection('logs').add({
        'action': action,
        'details': details,
        'created_at': datetime.now().isoformat()
    })

# ---------- Мониторинг состояния сервера ----------
def get_server_status():
    """Получает состояние сервера по SSH (CPU, RAM, диск, uptime)"""
    host = os.environ.get('VPS_HOST')
    port = int(os.environ.get('VPS_PORT', 22))
    username = os.environ.get('VPS_USERNAME', 'root')
    password = os.environ.get('VPS_PASSWORD')
    private_key_path = os.environ.get('VPS_SSH_KEY_PATH')

    if not host or not password:
        return {'error': 'Сервер не настроен', 'available': False}

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        if private_key_path:
            key = paramiko.RSAKey.from_private_key_file(private_key_path)
            ssh.connect(hostname=host, port=port, username=username, pkey=key, timeout=5)
        else:
            ssh.connect(hostname=host, port=port, username=username, password=password, timeout=5)

        # CPU
        stdin, stdout, stderr = ssh.exec_command("top -bn1 | grep 'Cpu(s)'")
        cpu_line = stdout.read().decode()
        cpu_match = re.search(r'(\d+\.\d+)\s*id', cpu_line)
        cpu_usage = 100 - float(cpu_match.group(1)) if cpu_match else 0

        # RAM
        stdin, stdout, stderr = ssh.exec_command("free -m | grep Mem")
        mem_line = stdout.read().decode()
        mem_parts = mem_line.split()
        if len(mem_parts) >= 3:
            total_mem = int(mem_parts[1])
            used_mem = int(mem_parts[2])
            mem_percent = (used_mem / total_mem) * 100
        else:
            mem_percent = 0

        # Диск
        stdin, stdout, stderr = ssh.exec_command("df -h / | tail -1")
        disk_line = stdout.read().decode()
        disk_parts = disk_line.split()
        if len(disk_parts) >= 5:
            disk_percent = float(disk_parts[4].replace('%', ''))
        else:
            disk_percent = 0

        # Uptime
        stdin, stdout, stderr = ssh.exec_command("uptime -p")
        uptime_line = stdout.read().decode().strip()
        if not uptime_line:
            uptime_line = "неизвестно"

        ssh.close()

        return {
            'cpu': round(cpu_usage, 1),
            'ram': round(mem_percent, 1),
            'disk': round(disk_percent, 1),
            'uptime': uptime_line,
            'available': True,
            'error': None
        }

    except Exception as e:
        return {
            'cpu': 0,
            'ram': 0,
            'disk': 0,
            'uptime': '—',
            'available': False,
            'error': str(e)
        }

# ---------- Маршруты ----------
@app.route('/')
def index():
    if not session.get('admin'):
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode()):
            session['admin'] = True
            add_log('Вход администратора', f'IP: {request.remote_addr}')
            flash('Добро пожаловать!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Неверный пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('admin', None)
    add_log('Выход администратора')
    flash('Вы вышли', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if not session.get('admin'):
        return redirect(url_for('login'))
    users = get_users()
    total_users = len(users)
    active_users = sum(1 for u in users if u.get('is_active', False))
    income = sum(t.get('amount', 0) for t in get_transactions() if t.get('type') == 'income')
    expense = sum(t.get('amount', 0) for t in get_transactions() if t.get('type') == 'expense')
    stats = {
        'total_users': total_users,
        'active_users': active_users,
        'inactive_users': total_users - active_users,
        'income': income,
        'expense': expense,
        'balance': income - expense
    }
    server_status = get_server_status()
    return render_template('dashboard.html', stats=stats, server=server_status)

@app.route('/users')
def users_page():
    if not session.get('admin'):
        return redirect(url_for('login'))
    users = get_users()
    now = datetime.now()
    for user in users:
        if user.get('expires_at'):
            try:
                exp_date = datetime.fromisoformat(user['expires_at'])
                days_left = (exp_date - now).days
                user['days_left'] = days_left if days_left > 0 else 0
            except:
                user['days_left'] = None
        else:
            user['days_left'] = None
    monthly_price = get_setting('monthly_price', '500')
    yearly_price = get_setting('yearly_price', '5000')
    return render_template('users.html', users=users, now=now, monthly_price=monthly_price, yearly_price=yearly_price)

@app.route('/add_user', methods=['POST'])
def add_user():
    if not session.get('admin'):
        return redirect(url_for('login'))
    username = request.form['username']
    email = request.form['email']
    expires_at = request.form.get('expires_at') or None
    tariff = request.form.get('tariff', 'month')
    amount_str = request.form.get('amount')

    if tariff == 'month':
        amount = float(get_setting('monthly_price', '500'))
        if not expires_at:
            new_date = datetime.now() + relativedelta(months=1)
            expires_at = new_date.isoformat()
    elif tariff == 'year':
        amount = float(get_setting('yearly_price', '5000'))
        if not expires_at:
            new_date = datetime.now() + relativedelta(years=1)
            expires_at = new_date.isoformat()
    else:
        if amount_str and amount_str.strip():
            amount = float(amount_str)
        else:
            amount = float(get_setting('monthly_price', '500'))
            if not expires_at:
                new_date = datetime.now() + relativedelta(months=1)
                expires_at = new_date.isoformat()

    existing = db.collection('users').where(filter=FieldFilter('username', '==', username)).get()
    if existing:
        flash('Пользователь с таким именем уже существует', 'danger')
        return redirect(url_for('users_page'))

    user_ref = db.collection('users').add({
        'username': username,
        'email': email,
        'expires_at': expires_at,
        'is_active': True,
        'created_at': datetime.now().isoformat()
    })

    if amount > 0:
        db.collection('transactions').add({
            'amount': amount,
            'type': 'income',
            'description': f'Оплата за пользователя {username} ({tariff})',
            'created_at': datetime.now().isoformat(),
            'user_id': user_ref[1].id
        })
        add_log(f'Добавлен пользователь {username} с оплатой {amount}₽', f'тариф: {tariff}, срок: {expires_at}')
    else:
        add_log(f'Добавлен пользователь {username} (без оплаты)', f'email: {email}, срок: {expires_at}')

    flash(f'Пользователь {username} добавлен', 'success')
    return redirect(url_for('users_page'))

@app.route('/toggle_user/<user_id>')
def toggle_user(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    user_ref = db.collection('users').document(user_id)
    user = user_ref.get()
    if user.exists:
        current_status = user.to_dict().get('is_active', False)
        user_ref.update({'is_active': not current_status})
        add_log(f'Изменён статус пользователя ID {user_id}', f'активен: {not current_status}')
    return redirect(url_for('users_page'))

@app.route('/delete_user/<user_id>')
def delete_user(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    db.collection('users').document(user_id).delete()
    add_log(f'Удалён пользователь ID {user_id}')
    flash('Пользователь удалён', 'success')
    return redirect(url_for('users_page'))

@app.route('/renew_user/<user_id>', methods=['POST'])
def renew_user(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    monthly_price = float(get_setting('monthly_price', '500'))
    user_ref = db.collection('users').document(user_id)
    user = user_ref.get()
    if not user.exists:
        flash('Пользователь не найден', 'danger')
        return redirect(url_for('users_page'))
    user_data = user.to_dict()
    current_expires = user_data.get('expires_at')
    if current_expires:
        try:
            old_date = datetime.fromisoformat(current_expires)
            new_date = old_date + relativedelta(months=1)
        except:
            new_date = datetime.now() + relativedelta(months=1)
    else:
        new_date = datetime.now() + relativedelta(months=1)
    new_expires = new_date.isoformat()
    user_ref.update({'expires_at': new_expires})
    db.collection('transactions').add({
        'amount': monthly_price,
        'type': 'income',
        'description': f'Продление подписки для {user_data.get("username")} на 1 месяц',
        'created_at': datetime.now().isoformat(),
        'user_id': user_id
    })
    add_log(f'Продлена подписка пользователя {user_data.get("username")}', f'новый срок: {new_expires}, сумма: {monthly_price}')
    flash(f'Подписка пользователя {user_data.get("username")} продлена на месяц', 'success')
    return redirect(url_for('users_page'))

@app.route('/finances')
def finances_page():
    if not session.get('admin'):
        return redirect(url_for('login'))
    transactions = get_transactions()
    return render_template('finances.html', transactions=transactions)

@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if not session.get('admin'):
        return redirect(url_for('login'))
    amount = float(request.form['amount'])
    type_ = request.form['type']
    description = request.form['description']
    db.collection('transactions').add({
        'amount': amount,
        'type': type_,
        'description': description,
        'created_at': datetime.now().isoformat()
    })
    add_log(f'Добавлена транзакция {type_} на {amount}₽', description)
    flash('Транзакция добавлена', 'success')
    return redirect(url_for('finances_page'))

@app.route('/logs')
def logs_page():
    if not session.get('admin'):
        return redirect(url_for('login'))
    logs = get_logs()
    return render_template('logs.html', logs=logs)

@app.route('/settings', methods=['GET', 'POST'])
def settings_page():
    if not session.get('admin'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        if new_password:
            global ADMIN_PASSWORD_HASH
            ADMIN_PASSWORD_HASH = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            add_log('Смена пароля администратора')
            flash('Пароль изменён', 'success')
        monthly_price = request.form.get('monthly_price')
        yearly_price = request.form.get('yearly_price')
        if monthly_price:
            set_setting('monthly_price', monthly_price)
        if yearly_price:
            set_setting('yearly_price', yearly_price)
        flash('Тарифы сохранены', 'success')
        return redirect(url_for('settings_page'))
    monthly_price = get_setting('monthly_price', '500')
    yearly_price = get_setting('yearly_price', '5000')
    return render_template('settings.html', monthly_price=monthly_price, yearly_price=yearly_price)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)