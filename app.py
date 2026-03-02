from flask import (Flask, render_template, request, redirect, url_for,
                   flash, session, Response, jsonify, make_response)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os, json, time, queue, threading, uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'caya-kaffee-secret-change-me')
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///caya.db')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['QR_FOLDER'] = os.path.join('static', 'qrcodes')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# ── SSE broadcast queue ────────────────────────────────────────────────────────
_sse_listeners = []
_sse_lock = threading.Lock()

def sse_push(event, data):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_listeners:
            try:
                q.put_nowait(msg)
            except:
                dead.append(q)
        for q in dead:
            _sse_listeners.remove(q)

# ── Models ─────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role          = db.Column(db.String(20), default='staff')  # 'admin' or 'staff'

class Category(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False)
    icon     = db.Column(db.String(10), default='☕')
    order    = db.Column(db.Integer, default=0)
    products = db.relationship('Product', backref='category', lazy=True,
                               cascade='all, delete-orphan')

class Product(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default='')
    price       = db.Column(db.Float, nullable=False)
    image       = db.Column(db.String(300), default='')
    available   = db.Column(db.Boolean, default=True)
    order       = db.Column(db.Integer, default=0)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)

class Table(db.Model):
    id     = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False)
    label  = db.Column(db.String(50), default='')
    orders = db.relationship('Order', backref='table', lazy=True)

class Customer(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    cookie_id  = db.Column(db.String(64), unique=True, nullable=False)
    first_name = db.Column(db.String(80), default='')
    last_name  = db.Column(db.String(80), default='')
    phone      = db.Column(db.String(30), default='')
    gender     = db.Column(db.String(10), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    orders     = db.relationship('Order', backref='customer', lazy=True)

class Order(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    table_id    = db.Column(db.Integer, db.ForeignKey('table.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    status      = db.Column(db.String(20), default='new')  # new / done
    note        = db.Column(db.Text, default='')
    rating      = db.Column(db.Integer, nullable=True)   # 1-5
    comment     = db.Column(db.Text, default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    items       = db.relationship('OrderItem', backref='order', lazy=True,
                                  cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id':         self.id,
            'table':      self.table.number if self.table else '?',
            'status':     self.status,
            'note':       self.note,
            'created_at': self.created_at.strftime('%H:%M'),
            'customer':   f"{self.customer.first_name} {self.customer.last_name}".strip() if self.customer else 'Anonyme',
            'items':      [{'name': i.product.name, 'qty': i.quantity, 'price': i.unit_price}
                           for i in self.items]
        }

class OrderItem(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    order_id   = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    quantity   = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, nullable=False)
    product    = db.relationship('Product')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── Helpers ────────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_image(file):
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{int(time.time())}{ext}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return filename
    return ''

def delete_file(folder, filename):
    if filename:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            os.remove(path)

def get_or_create_customer():
    """Get customer from cookie or return None."""
    cid = request.cookies.get('caya_cid')
    if cid:
        return Customer.query.filter_by(cookie_id=cid).first()
    return None

def generate_qr(table_number, base_url):
    try:
        import qrcode
        url = f"{base_url}/table/{table_number}"
        img = qrcode.make(url)
        filename = f"table_{table_number}.png"
        path = os.path.join(app.config['QR_FOLDER'], filename)
        img.save(path)
        return filename
    except Exception as e:
        print(f"QR generation error: {e}")
        return None

# ── Auth ───────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard') if current_user.role == 'admin' else url_for('staff_orders'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user)
            if user.role == 'admin':
                return redirect(url_for('dashboard'))
            return redirect(url_for('staff_orders'))
        flash('Identifiants incorrects.', 'error')
    return render_template('admin/login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ── Customer Flow ──────────────────────────────────────────────────────────────

@app.route('/table/<int:table_number>')
def table_entry(table_number):
    table = Table.query.filter_by(number=table_number).first_or_404()
    customer = get_or_create_customer()

    # Find last unrated order for this customer
    last_order = None
    if customer:
        last_order = (Order.query
                      .filter_by(customer_id=customer.id, rating=None)
                      .filter(Order.status == 'done')
                      .order_by(Order.created_at.desc())
                      .first())

    # If returning customer with no pending rating → go straight to menu
    if customer and not last_order:
        return redirect(url_for('menu', table_number=table_number))

    return render_template('customer/entry.html',
                           table=table,
                           customer=customer,
                           last_order=last_order)

@app.route('/table/<int:table_number>/save-profile', methods=['POST'])
def save_profile(table_number):
    table = Table.query.filter_by(number=table_number).first_or_404()
    cid = request.cookies.get('caya_cid')
    customer = None
    if cid:
        customer = Customer.query.filter_by(cookie_id=cid).first()

    if not customer:
        cid = str(uuid.uuid4())
        customer = Customer(cookie_id=cid)
        db.session.add(customer)

    customer.first_name = request.form.get('first_name', '').strip()
    customer.last_name  = request.form.get('last_name', '').strip()
    customer.phone      = request.form.get('phone', '').strip()
    customer.gender     = request.form.get('gender', '').strip()

    # Handle rating of last order
    last_order_id = request.form.get('last_order_id')
    if last_order_id:
        order = Order.query.get(int(last_order_id))
        if order and order.customer_id == customer.id:
            try:
                order.rating = int(request.form.get('rating', 0)) or None
            except:
                pass
            order.comment = request.form.get('comment', '').strip()

    db.session.commit()

    resp = make_response(redirect(url_for('menu', table_number=table_number)))
    resp.set_cookie('caya_cid', cid, max_age=60*60*24*365*2)  # 2 years
    return resp

@app.route('/menu/<int:table_number>')
def menu(table_number):
    table = Table.query.filter_by(number=table_number).first_or_404()
    categories = Category.query.order_by(Category.order).all()
    customer = get_or_create_customer()
    return render_template('customer/menu.html',
                           table=table,
                           categories=categories,
                           customer=customer)

@app.route('/table/<int:table_number>/order', methods=['POST'])
def place_order(table_number):
    table = Table.query.filter_by(number=table_number).first_or_404()
    customer = get_or_create_customer()

    try:
        data = request.get_json()
        items = data.get('items', [])
        note  = data.get('note', '').strip()

        if not items:
            return jsonify({'success': False, 'error': 'Panier vide'}), 400

        order = Order(
            table_id    = table.id,
            customer_id = customer.id if customer else None,
            note        = note
        )
        db.session.add(order)
        db.session.flush()

        for item in items:
            product = Product.query.get(item['product_id'])
            if not product or not product.available:
                continue
            oi = OrderItem(
                order_id   = order.id,
                product_id = product.id,
                quantity   = int(item['quantity']),
                unit_price = product.price
            )
            db.session.add(oi)

        db.session.commit()
        sse_push('new_order', order.to_dict())
        return jsonify({'success': True, 'order_id': order.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

# ── Staff Orders Page ──────────────────────────────────────────────────────────

@app.route('/staff')
@login_required
def staff_orders():
    orders = (Order.query
              .filter_by(status='new')
              .order_by(Order.created_at.asc())
              .all())
    return render_template('staff/orders.html', orders=orders)

@app.route('/staff/order/<int:order_id>/done', methods=['POST'])
@login_required
def mark_done(order_id):
    order = Order.query.get_or_404(order_id)
    order.status = 'done'
    db.session.commit()
    sse_push('order_done', {'id': order_id})
    return jsonify({'success': True})

@app.route('/staff/stream')
@login_required
def staff_stream():
    def event_stream(q):
        # Send a heartbeat first
        yield "event: ping\ndata: ok\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
                yield msg
            except:
                yield "event: ping\ndata: ok\n\n"

    q = queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_listeners.append(q)

    return Response(event_stream(q),
                    mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no'
                    })

# ── Admin Dashboard ────────────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Accès réservé aux administrateurs.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin')
@login_required
@admin_required
def dashboard():
    cat_count     = Category.query.count()
    product_count = Product.query.count()
    table_count   = Table.query.count()
    order_count   = Order.query.count()
    new_orders    = Order.query.filter_by(status='new').count()
    return render_template('admin/dashboard.html',
                           cat_count=cat_count,
                           product_count=product_count,
                           table_count=table_count,
                           order_count=order_count,
                           new_orders=new_orders)

# ── Admin Categories CRUD ──────────────────────────────────────────────────────

@app.route('/admin/categories')
@login_required
@admin_required
def categories():
    cats = Category.query.order_by(Category.order).all()
    return render_template('admin/categories.html', categories=cats)

@app.route('/admin/categories/add', methods=['POST'])
@login_required
@admin_required
def add_category():
    name = request.form.get('name', '').strip()
    icon = request.form.get('icon', '☕').strip()
    if name:
        max_order = db.session.query(db.func.max(Category.order)).scalar() or 0
        db.session.add(Category(name=name, icon=icon, order=max_order + 1))
        db.session.commit()
        flash(f'Catégorie "{name}" ajoutée.', 'success')
    return redirect(url_for('categories'))

@app.route('/admin/categories/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_category(id):
    cat = Category.query.get_or_404(id)
    if request.method == 'POST':
        cat.name = request.form.get('name', cat.name).strip()
        cat.icon = request.form.get('icon', cat.icon).strip()
        db.session.commit()
        flash('Catégorie mise à jour.', 'success')
        return redirect(url_for('categories'))
    return render_template('admin/edit_category.html', category=cat)

@app.route('/admin/categories/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_category(id):
    cat = Category.query.get_or_404(id)
    for p in cat.products:
        delete_file(app.config['UPLOAD_FOLDER'], p.image)
    db.session.delete(cat)
    db.session.commit()
    flash('Catégorie supprimée.', 'success')
    return redirect(url_for('categories'))

# ── Admin Products CRUD ────────────────────────────────────────────────────────

@app.route('/admin/products')
@login_required
@admin_required
def products():
    cat_id     = request.args.get('category_id', type=int)
    categories = Category.query.order_by(Category.order).all()
    prods      = (Product.query.filter_by(category_id=cat_id) if cat_id
                  else Product.query).order_by(Product.order).all()
    return render_template('admin/products.html',
                           products=prods, categories=categories, selected_cat=cat_id)

@app.route('/admin/products/add', methods=['GET', 'POST'])
@login_required
@admin_required
def add_product():
    categories = Category.query.order_by(Category.order).all()
    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        cat_id = request.form.get('category_id', type=int)
        try:
            price = float(request.form.get('price', 0))
        except:
            price = 0.0
        if name and cat_id:
            max_order = db.session.query(db.func.max(Product.order)).scalar() or 0
            image = save_image(request.files.get('image'))
            db.session.add(Product(
                name        = name,
                description = request.form.get('description', '').strip(),
                price       = price,
                category_id = cat_id,
                available   = request.form.get('available') == 'on',
                image       = image,
                order       = max_order + 1
            ))
            db.session.commit()
            flash(f'Produit "{name}" ajouté.', 'success')
            return redirect(url_for('products'))
        flash('Nom et catégorie requis.', 'error')
    return render_template('admin/edit_product.html', product=None, categories=categories)

@app.route('/admin/products/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_product(id):
    prod       = Product.query.get_or_404(id)
    categories = Category.query.order_by(Category.order).all()
    if request.method == 'POST':
        prod.name        = request.form.get('name', prod.name).strip()
        prod.description = request.form.get('description', '').strip()
        prod.category_id = request.form.get('category_id', prod.category_id, type=int)
        prod.available   = request.form.get('available') == 'on'
        try:
            prod.price = float(request.form.get('price', prod.price))
        except:
            pass
        f = request.files.get('image')
        if f and f.filename:
            delete_file(app.config['UPLOAD_FOLDER'], prod.image)
            prod.image = save_image(f)
        if request.form.get('remove_image') == '1':
            delete_file(app.config['UPLOAD_FOLDER'], prod.image)
            prod.image = ''
        db.session.commit()
        flash('Produit mis à jour.', 'success')
        return redirect(url_for('products'))
    return render_template('admin/edit_product.html', product=prod, categories=categories)

@app.route('/admin/products/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_product(id):
    prod = Product.query.get_or_404(id)
    delete_file(app.config['UPLOAD_FOLDER'], prod.image)
    db.session.delete(prod)
    db.session.commit()
    flash('Produit supprimé.', 'success')
    return redirect(url_for('products'))

@app.route('/admin/products/toggle/<int:id>', methods=['POST'])
@login_required
@admin_required
def toggle_product(id):
    prod           = Product.query.get_or_404(id)
    prod.available = not prod.available
    db.session.commit()
    return redirect(request.referrer or url_for('products'))

# ── Admin Tables ───────────────────────────────────────────────────────────────

@app.route('/admin/tables')
@login_required
@admin_required
def tables():
    tabs = Table.query.order_by(Table.number).all()
    return render_template('admin/tables.html', tables=tabs)

@app.route('/admin/tables/add', methods=['POST'])
@login_required
@admin_required
def add_table():
    number = request.form.get('number', type=int)
    label  = request.form.get('label', '').strip()
    if number:
        if Table.query.filter_by(number=number).first():
            flash(f'La table {number} existe déjà.', 'error')
            return redirect(url_for('tables'))
        table = Table(number=number, label=label)
        db.session.add(table)
        db.session.commit()
        base_url = request.host_url.rstrip('/')
        generate_qr(number, base_url)
        flash(f'Table {number} ajoutée avec QR code.', 'success')
    return redirect(url_for('tables'))

@app.route('/admin/tables/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_table(id):
    table = Table.query.get_or_404(id)
    delete_file(app.config['QR_FOLDER'], f"table_{table.number}.png")
    db.session.delete(table)
    db.session.commit()
    flash(f'Table {table.number} supprimée.', 'success')
    return redirect(url_for('tables'))

@app.route('/admin/tables/regen-qr/<int:id>', methods=['POST'])
@login_required
@admin_required
def regen_qr(id):
    table    = Table.query.get_or_404(id)
    base_url = request.host_url.rstrip('/')
    generate_qr(table.number, base_url)
    flash(f'QR code table {table.number} régénéré.', 'success')
    return redirect(url_for('tables'))

# ── Admin Orders History ───────────────────────────────────────────────────────

@app.route('/admin/orders')
@login_required
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).limit(200).all()
    return render_template('admin/orders.html', orders=orders)

# ── Admin Staff Users ──────────────────────────────────────────────────────────

@app.route('/admin/staff')
@login_required
@admin_required
def staff_list():
    users = User.query.filter_by(role='staff').all()
    return render_template('admin/staff.html', users=users)

@app.route('/admin/staff/add', methods=['POST'])
@login_required
@admin_required
def add_staff():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if username and password:
        if User.query.filter_by(username=username).first():
            flash('Ce nom d\'utilisateur existe déjà.', 'error')
        else:
            db.session.add(User(
                username      = username,
                password_hash = generate_password_hash(password),
                role          = 'staff'
            ))
            db.session.commit()
            flash(f'Compte staff "{username}" créé.', 'success')
    return redirect(url_for('staff_list'))

@app.route('/admin/staff/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_staff(id):
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    flash('Compte supprimé.', 'success')
    return redirect(url_for('staff_list'))

# ── Init ───────────────────────────────────────────────────────────────────────

# def init_db():
#     with app.app_context():
#         db.create_all()
#         if not User.query.first():
#             db.session.add(User(
#                 username      = 'admin',
#                 password_hash = generate_password_hash('admin123'),
#                 role          = 'admin'
#             ))
#             db.session.commit()
#             print("✅ Admin créé: admin / admin123")

# if __name__ == '__main__':
#     os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
#     os.makedirs(app.config['QR_FOLDER'], exist_ok=True)
#     init_db()
#     app.run(debug=True, threaded=True)


# ── Init (Render + Gunicorn safe) ─────────────────────────────────────────────

def initialize_database():
    with app.app_context():
        db.create_all()

        # Créer admin seulement s'il n'existe pas
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            db.session.add(User(
                username='admin',
                password_hash=generate_password_hash('admin123'),
                role='admin'
            ))
            db.session.commit()
            print("✅ Admin créé: admin / admin123")

# Crée les dossiers nécessaires (important sur Render)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['QR_FOLDER'], exist_ok=True)

# Initialise la base au démarrage (fonctionne avec Gunicorn)
initialize_database()