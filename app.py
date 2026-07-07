# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime
import os
import json
import bcrypt
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

app = Flask(__name__)
app.secret_key = 'supersecretkeychangeit'

# ---------- Инициализация Firebase ----------
# Используем переменную окружения FIREBASE_CREDENTIALS (на Render) или файл (локально)
firebase_creds_json = os.environ.get('FIREBASE_CREDENTIALS')
if firebase_creds_json:
    cred_dict = json.loads(firebase_creds_json)
    cred = credentials.Certificate(cred_dict)
else:
    # Для локальной разработки (файл должен лежать рядом с app.py)
    cred = credentials.Certificate("firebase-key.json")

firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------- Админ-пароль (хеш) ----------
# Для безопасности храните реальный пароль в переменной окружения, но для демо используем 'admin'
ADMIN_PASSWORD_HASH = bcrypt.hashpw('admin'.encode(), bcrypt.gensalt()).decode()

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
    # Статус сервера — пока заглушка
    server_status = {
        'cpu': 12.5,
        'ram': 45.2,
        'disk': 67.8,
        'uptime': '5 дней 3 часа'
    }
    return render_template('dashboard.html', stats=stats, server=server_status)

@app.route('/users')
def users_page():
    if not session.get('admin'):
        return redirect(url_for('login'))
    users = get_users()
    return render_template('users.html', users=users)

@app.route('/add_user', methods=['POST'])
def add_user():
    if not session.get('admin'):
        return redirect(url_for('login'))
    username = request.form['username']
    email = request.form['email']
    expires_at = request.form.get('expires_at') or None
    # Проверка на дубликат
    existing = db.collection('users').where(filter=FieldFilter('username', '==', username)).get()
    if existing:
        flash('Пользователь с таким именем уже существует', 'danger')
        return redirect(url_for('users_page'))
    db.collection('users').add({
        'username': username,
        'email': email,
        'expires_at': expires_at,
        'is_active': True,
        'created_at': datetime.now().isoformat()
    })
    add_log(f'Добавлен пользователь {username}', f'email: {email}, срок: {expires_at}')
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
        return redirect(url_for('settings_page'))
    return render_template('settings.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)