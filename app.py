from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
from datetime import datetime, timedelta
import bcrypt

app = Flask(__name__)
app.secret_key = 'supersecretkeychangeit'

# ---------- Работа с БД ----------
def get_db():
    db = sqlite3.connect('vpn.db')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            expires_at TEXT,
            is_active INTEGER DEFAULT 1,
            last_handshake TEXT,
            rx INTEGER DEFAULT 0,
            tx INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            type TEXT CHECK(type IN ('income','expense')) NOT NULL,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT OR IGNORE INTO settings (key, value) VALUES ('monthly_price', '500');
        INSERT OR IGNORE INTO settings (key, value) VALUES ('yearly_price', '5000');
    ''')
    db.commit()
    db.close()

# Создаём таблицы при первом запуске
init_db()

# ---------- Вспомогательные функции ----------
def get_users():
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY id DESC').fetchall()
    db.close()
    return users

def get_transactions():
    db = get_db()
    trans = db.execute('SELECT * FROM transactions ORDER BY id DESC').fetchall()
    db.close()
    return trans

def get_logs():
    db = get_db()
    logs = db.execute('SELECT * FROM logs ORDER BY id DESC LIMIT 50').fetchall()
    db.close()
    return logs

def add_log(action, details=''):
    db = get_db()
    db.execute('INSERT INTO logs (action, details) VALUES (?, ?)', (action, details))
    db.commit()
    db.close()

def get_setting(key):
    db = get_db()
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    db.close()
    return row['value'] if row else None

# ---------- Админ-пароль (хеш) ----------
# Для простоты храним хеш прямо в коде (в реальности лучше в БД или env)
ADMIN_PASSWORD_HASH = bcrypt.hashpw('admin'.encode(), bcrypt.gensalt()).decode()

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
    db = get_db()
    total_users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    active_users = db.execute('SELECT COUNT(*) FROM users WHERE is_active=1 AND (expires_at IS NULL OR expires_at > date("now"))').fetchone()[0]
    income = db.execute('SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type="income"').fetchone()[0]
    expense = db.execute('SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type="expense"').fetchone()[0]
    db.close()
    stats = {
        'total_users': total_users,
        'active_users': active_users,
        'inactive_users': total_users - active_users,
        'income': income,
        'expense': expense,
        'balance': income - expense
    }
    # Статус сервера (заглушка, но можно добавить psutil)
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
    db = get_db()
    try:
        db.execute('INSERT INTO users (username, email, expires_at) VALUES (?, ?, ?)',
                   (username, email, expires_at))
        db.commit()
        add_log(f'Добавлен пользователь {username}', f'email: {email}, срок: {expires_at}')
        flash(f'Пользователь {username} добавлен', 'success')
    except sqlite3.IntegrityError:
        flash('Пользователь с таким именем уже существует', 'danger')
    db.close()
    return redirect(url_for('users_page'))

@app.route('/toggle_user/<int:user_id>')
def toggle_user(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    db = get_db()
    user = db.execute('SELECT is_active FROM users WHERE id=?', (user_id,)).fetchone()
    if user:
        new_status = 1 if user['is_active'] == 0 else 0
        db.execute('UPDATE users SET is_active=? WHERE id=?', (new_status, user_id))
        db.commit()
        add_log(f'Изменён статус пользователя ID {user_id}', f'активен: {bool(new_status)}')
    db.close()
    return redirect(url_for('users_page'))

@app.route('/delete_user/<int:user_id>')
def delete_user(user_id):
    if not session.get('admin'):
        return redirect(url_for('login'))
    db = get_db()
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    add_log(f'Удалён пользователь ID {user_id}')
    flash('Пользователь удалён', 'success')
    db.close()
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
    db = get_db()
    db.execute('INSERT INTO transactions (amount, type, description) VALUES (?, ?, ?)',
               (amount, type_, description))
    db.commit()
    add_log(f'Добавлена транзакция {type_} на {amount}₽', description)
    flash('Транзакция добавлена', 'success')
    db.close()
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
        # Смена пароля
        new_password = request.form.get('new_password')
        if new_password:
            global ADMIN_PASSWORD_HASH
            ADMIN_PASSWORD_HASH = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
            add_log('Смена пароля администратора')
            flash('Пароль изменён', 'success')
        # Изменение тарифов
        monthly = request.form.get('monthly_price')
        yearly = request.form.get('yearly_price')
        if monthly and yearly:
            db = get_db()
            db.execute('UPDATE settings SET value=? WHERE key="monthly_price"', (monthly,))
            db.execute('UPDATE settings SET value=? WHERE key="yearly_price"', (yearly,))
            db.commit()
            db.close()
            add_log('Обновлены тарифы')
            flash('Тарифы сохранены', 'success')
        return redirect(url_for('settings_page'))
    monthly = get_setting('monthly_price') or '500'
    yearly = get_setting('yearly_price') or '5000'
    return render_template('settings.html', monthly_price=monthly, yearly_price=yearly)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)