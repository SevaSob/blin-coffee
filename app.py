import os
import time
import csv
import io
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, redirect, url_for, request, flash,
                   Response, jsonify)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         login_required, logout_user, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'blin-coffee-secret-key-change-me'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coffee.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join('static', 'img', 'menu')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Сначала войдите'


# =========================================================
# МОДЕЛИ
# =========================================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    show_admin_counters = db.Column(db.Boolean, default=False)  # показывать ли счётчики в каталоге
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    icon = db.Column(db.String(10), default='')
    parent_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    is_system = db.Column(db.Boolean, default=False)  # «Без категории» — системная

    parent = db.relationship('Category', remote_side=[id],
                             backref=db.backref('children', order_by='Category.sort_order'))

    @property
    def level(self):
        lvl = 0
        p = self.parent
        while p:
            lvl += 1
            p = p.parent
        return lvl

    @property
    def full_name(self):
        parts = [self.name]
        p = self.parent
        while p:
            parts.insert(0, p.name)
            p = p.parent
        return ' / '.join(parts)

    @property
    def descendant_ids(self):
        ids = [self.id]
        for child in self.children:
            ids.extend(child.descendant_ids)
        return ids

    @property
    def product_count(self):
        return Product.query.filter(Product.category_id.in_(self.descendant_ids)).count()

    def chain(self):
        out = []
        c = self
        while c:
            out.insert(0, c)
            c = c.parent
        return out


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.String(60), default='')  # fallback, если нет вариантов
    description = db.Column(db.Text, default='')
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    image_path = db.Column(db.String(200), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category = db.relationship('Category', backref='products')

    @property
    def price_display(self):
        if self.variants:
            prices = [v.price for v in self.variants]
            mn, mx = min(prices), max(prices)
            if mn == mx:
                return f"{mn} ₽"
            return f"от {mn} ₽"
        return self.price or '—'

    @property
    def price_sort(self):
        if self.variants:
            return min(v.price for v in self.variants)
        return 999999


class ProductVariant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    name = db.Column(db.String(60), nullable=False)  # «250 мл», «30/60 мл» и т.д.
    price = db.Column(db.Integer, nullable=False)
    sort_order = db.Column(db.Integer, default=0)

    product = db.relationship('Product', backref=db.backref(
        'variants', cascade='all, delete-orphan', order_by='ProductVariant.sort_order'))


class Promotion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default='')
    image_path = db.Column(db.String(200), default='')


class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'product_id', name='uq_user_product'),)
    product = db.relationship('Product')


class AuditLog(db.Model):
    """Железобетонный журнал действий. Пишется всегда, кроме полного краха БД."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(80), default='system')
    action = db.Column(db.String(80), nullable=False)   # 'product.create', 'user.delete' ...
    entity_type = db.Column(db.String(50), default='')  # 'product', 'category', 'user'
    entity_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, default='')
    ip = db.Column(db.String(45), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# =========================================================
# ХЕЛПЕРЫ
# =========================================================

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Доступ только для администратора', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def log_action(action, entity_type='', entity_id=None, details=''):
    """Журналирование действий. Обязательно вызывается в мутирующих роутах."""
    try:
        uid = current_user.id if current_user.is_authenticated else None
        uname = current_user.username if current_user.is_authenticated else 'anon'
        entry = AuditLog(
            user_id=uid, username=uname,
            action=action, entity_type=entity_type,
            entity_id=entity_id, details=details,
            ip=request.remote_addr or ''
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        # журнал не должен ронять основную операцию
        print(f'[AUDIT ERROR] {e}')


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_image(file):
    if not file or file.filename == '':
        return None
    if not allowed_file(file.filename):
        return None
    filename = secure_filename(file.filename)
    filename = f"{int(time.time())}_{filename}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)
    return '/' + path.replace('\\', '/')


def root_categories(include_system=False):
    q = Category.query.filter_by(parent_id=None)
    if not include_system:
        q = q.filter_by(is_system=False)
    return q.order_by(Category.is_system, Category.sort_order, Category.name).all()


def flat_categories(include_system=False):
    result = []
    def walk(cats, depth=0):
        for c in cats:
            result.append((c, depth))
            walk(c.children, depth + 1)
    walk(root_categories(include_system=include_system))
    return result


def get_or_create_uncategorized():
    cat = Category.query.filter_by(is_system=True).first()
    if not cat:
        cat = Category(name='Без категории', icon='📦', is_system=True, sort_order=9999)
        db.session.add(cat)
        db.session.commit()
    return cat


@app.context_processor
def inject_globals():
    return {
        'user_fav_ids': {f.product_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}
                         if current_user.is_authenticated else set(),
        'menu_categories': root_categories(include_system=False),
        'can_see_counters': current_user.is_authenticated and current_user.is_admin and current_user.show_admin_counters,
    }


# =========================================================
# ПУБЛИЧНЫЕ РОУТЫ
# =========================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/catalog')
def catalog():
    q = request.args.get('q', '').strip()
    cat_id = request.args.get('category', type=int)

    current_cat = Category.query.get(cat_id) if cat_id else None

    query = Product.query
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if current_cat:
        query = query.filter(Product.category_id.in_(current_cat.descendant_ids))

    products = query.all()
    order = {c.id: i for i, (c, _) in enumerate(flat_categories())}
    products.sort(key=lambda p: (order.get(p.category_id, 9999), p.name))

    grouped = {}
    for p in products:
        grouped.setdefault(p.category, []).append(p)

    chain = current_cat.chain() if current_cat else []

    return render_template('catalog.html',
                           grouped=grouped,
                           current_cat=current_cat,
                           chain=chain,
                           q=q)


@app.route('/product/<int:pid>')
def product_detail(pid):
    product = Product.query.get_or_404(pid)
    similar = Product.query.filter(
        Product.category_id == product.category_id,
        Product.id != product.id
    ).limit(4).all()
    chain = product.category.chain() if product.category else []
    return render_template('product.html', product=product, similar=similar, chain=chain)


@app.route('/promotions')
def promotions():
    return render_template('promotions.html', promotions=Promotion.query.all())


@app.route('/about')
def about():
    return render_template('about.html')


# =========================================================
# АВТОРИЗАЦИЯ
# =========================================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Заполните все поля', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Такой пользователь уже есть', 'danger')
            return redirect(url_for('register'))
        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        log_action('user.register', 'user', user.id, f'username={username}')
        login_user(user)
        flash('Регистрация успешна', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            log_action('user.login', 'user', user.id, f'username={username}')
            flash(f'Привет, {user.username}!', 'success')
            return redirect(url_for('admin_dashboard') if user.is_admin else url_for('index'))
        log_action('user.login_failed', 'user', None, f'username={username}')
        flash('Неверный логин или пароль', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log_action('user.logout', 'user', current_user.id)
    logout_user()
    flash('Вы вышли', 'info')
    return redirect(url_for('index'))


# =========================================================
# ИЗБРАННОЕ
# =========================================================

@app.route('/favorites')
@login_required
def favorites():
    favs = Favorite.query.filter_by(user_id=current_user.id).join(Product).all()
    return render_template('favorites.html', products=[f.product for f in favs])


@app.route('/api/favorite/<int:pid>', methods=['POST'])
@login_required
def toggle_favorite(pid):
    Product.query.get_or_404(pid)
    fav = Favorite.query.filter_by(user_id=current_user.id, product_id=pid).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
        return {'status': 'removed'}
    db.session.add(Favorite(user_id=current_user.id, product_id=pid))
    db.session.commit()
    return {'status': 'added'}


# =========================================================
# API
# =========================================================

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    if not q:
        return {'items': []}
    items = Product.query.filter(Product.name.ilike(f'%{q}%')).limit(8).all()
    return {'items': [{
        'id': p.id, 'name': p.name, 'price': p.price_display,
        'category': p.category.name if p.category else '',
        'image': p.image_path or ''
    } for p in items]}


@app.route('/api/stats/full')
@admin_required
def api_stats_full():
    cats = db.session.query(Category.name, db.func.count(Product.id))\
        .join(Product, Product.category_id == Category.id, isouter=True)\
        .group_by(Category.id).all()

    today = datetime.utcnow().date()
    days = [(today - timedelta(days=i)) for i in range(29, -1, -1)]
    regs = []
    for d in days:
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        regs.append(User.query.filter(User.created_at >= start, User.created_at < end).count())

    top_favs = db.session.query(Product.name, db.func.count(Favorite.id))\
        .join(Favorite, Favorite.product_id == Product.id)\
        .group_by(Product.id).order_by(db.func.count(Favorite.id).desc()).limit(10).all()

    return {
        'regs_labels': [d.strftime('%d.%m') for d in days], 'regs_data': regs,
        'cats_labels': [c[0] for c in cats], 'cats_data': [c[1] for c in cats],
        'favs_labels': [f[0] for f in top_favs], 'favs_data': [f[1] for f in top_favs],
    }


# =========================================================
# АДМИНКА
# =========================================================

@app.route('/admin')
@admin_required
def admin_dashboard():
    stats = {
        'users': User.query.count(),
        'products': Product.query.count(),
        'promotions': Promotion.query.count(),
        'categories': Category.query.count(),
    }
    recent_users = User.query.order_by(User.id.desc()).limit(5).all()
    recent_logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(8).all()
    return render_template('admin/dashboard.html', stats=stats,
                           recent_users=recent_users, recent_logs=recent_logs)


@app.route('/admin/stats')
@admin_required
def admin_stats():
    return render_template('admin/stats.html')


@app.route('/admin/audit')
@admin_required
def admin_audit():
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action', '').strip()
    q = AuditLog.query
    if action_filter:
        q = q.filter(AuditLog.action.ilike(f'%{action_filter}%'))
    pagination = q.order_by(AuditLog.id.desc()).paginate(page=page, per_page=50, error_out=False)
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().all()]
    return render_template('admin/audit.html', pagination=pagination,
                           logs=pagination.items, action_filter=action_filter,
                           actions=sorted(actions))


# --- КАТЕГОРИИ ---

@app.route('/admin/categories')
@admin_required
def admin_categories():
    return render_template('admin/categories.html',
                           tree=root_categories(include_system=True),
                           flat=flat_categories(include_system=False))

@app.route('/admin/categories/new', methods=['POST'])
@admin_required
def admin_category_new():
    name = request.form.get('name', '').strip()
    icon = request.form.get('icon', '').strip()
    parent_id = request.form.get('parent_id', type=int)
    if not name:
        flash('Введите название', 'danger')
        return redirect(url_for('admin_categories'))
    parent = Category.query.get(parent_id) if parent_id else None
    c = Category(name=name, icon=icon, parent_id=parent_id)
    db.session.add(c)
    db.session.commit()
    log_action('category.create', 'category', c.id,
               f'{c.full_name}' + (f' (parent: {parent.full_name})' if parent else ''))
    flash(f'Категория «{name}» создана', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:cid>/edit', methods=['POST'])
@admin_required
def admin_category_edit(cid):
    c = Category.query.get_or_404(cid)
    new_parent_id = request.form.get('parent_id', type=int)

    if new_parent_id:
        if new_parent_id == c.id or new_parent_id in c.descendant_ids:
            flash('Нельзя переместить категорию внутрь себя или своего потомка', 'danger')
            return redirect(url_for('admin_categories'))

    old = c.full_name
    c.name = request.form.get('name', '').strip() or c.name
    c.icon = request.form.get('icon', '').strip()
    c.parent_id = new_parent_id
    db.session.commit()
    log_action('category.edit', 'category', c.id, f'{old} → {c.full_name}')
    flash('Категория обновлена', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/categories/<int:cid>/delete', methods=['GET', 'POST'])
@admin_required
def admin_category_delete(cid):
    c = Category.query.get_or_404(cid)

    if c.is_system:
        flash('Системную категорию нельзя удалить', 'danger')
        return redirect(url_for('admin_categories'))

    ids = c.descendant_ids
    total_products = Product.query.filter(Product.category_id.in_(ids)).count()
    total_cats = len(ids)

    if request.method == 'GET':
        # страница-предупреждение
        return render_template('admin/category_delete_confirm.html',
                               cat=c, total_products=total_products, total_cats=total_cats)

    action = request.form.get('action')
    if action == 'cancel':
        flash('Удаление отменено', 'info')
        return redirect(url_for('admin_categories'))

    if action == 'delete_all':
        Product.query.filter(Product.category_id.in_(ids)).delete(synchronize_session=False)
        for cat in Category.query.filter(Category.id.in_(ids)).all()[::-1]:
            db.session.delete(cat)
        db.session.commit()
        log_action('category.delete_with_products', 'category', cid,
                   f'{c.full_name}: удалено категорий {total_cats}, товаров {total_products}')
        flash(f'Удалено категорий: {total_cats}, товаров: {total_products}', 'info')
        return redirect(url_for('admin_categories'))

    if action == 'move_to_system':
        sys_cat = get_or_create_uncategorized()
        Product.query.filter(Product.category_id.in_(ids)).update(
            {'category_id': sys_cat.id}, synchronize_session=False)
        for cat in Category.query.filter(Category.id.in_(ids)).all()[::-1]:
            db.session.delete(cat)
        db.session.commit()
        log_action('category.delete_move_products', 'category', cid,
                   f'{c.full_name}: товаров {total_products} → «Без категории»')
        flash(f'Товары ({total_products} шт.) перемещены в «Без категории»', 'success')
        return redirect(url_for('admin_categories'))

    flash('Неизвестное действие', 'danger')
    return redirect(url_for('admin_categories'))


# --- ТОВАРЫ ---

@app.route('/admin/products')
@admin_required
def admin_products():
    q = request.args.get('q', '').strip()
    cat_id = request.args.get('category', type=int)
    current_cat = Category.query.get(cat_id) if cat_id else None

    query = Product.query
    if q:
        query = query.filter(Product.name.ilike(f'%{q}%'))
    if current_cat:
        query = query.filter(Product.category_id.in_(current_cat.descendant_ids))

    products = query.all()
    order = {c.id: i for i, (c, _) in enumerate(flat_categories(include_system=True))}
    products.sort(key=lambda p: (order.get(p.category_id, 9999), p.name))

    return render_template('admin/products.html', products=products,
                           flat=flat_categories(include_system=True),
                           current_cat=current_cat, q=q)


@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_product_new():
    if request.method == 'POST':
        p = Product(
            name=request.form['name'],
            price=request.form.get('price', ''),
            description=request.form.get('description', ''),
            category_id=request.form.get('category_id', type=int),
        )
        img = save_image(request.files.get('image'))
        if img:
            p.image_path = img
        db.session.add(p)
        db.session.flush()
        _save_variants(p)
        db.session.commit()
        log_action('product.create', 'product', p.id, p.name)
        flash('Товар добавлен', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=None, flat=flat_categories(include_system=False))


@app.route('/admin/products/<int:pid>/edit', methods=['GET', 'POST'])
@admin_required
def admin_product_edit(pid):
    p = Product.query.get_or_404(pid)
    if request.method == 'POST':
        p.name = request.form['name']
        p.price = request.form.get('price', '')
        p.description = request.form.get('description', '')
        p.category_id = request.form.get('category_id', type=int)
        img = save_image(request.files.get('image'))
        if img:
            p.image_path = img
        _save_variants(p)
        db.session.commit()
        log_action('product.edit', 'product', p.id, p.name)
        flash('Товар обновлён', 'success')
        return redirect(url_for('admin_products'))
    return render_template('admin/product_form.html', product=p, flat=flat_categories(include_system=False))


def _save_variants(product):
    """Считывает варианты из формы: vname[], vprice[]."""
    names = request.form.getlist('vname')
    prices = request.form.getlist('vprice')
    # удаляем старые
    ProductVariant.query.filter_by(product_id=product.id).delete()
    order = 0
    for name, price in zip(names, prices):
        name = name.strip()
        price = price.strip()
        if not name or not price:
            continue
        try:
            price_int = int(price)
        except ValueError:
            continue
        db.session.add(ProductVariant(product_id=product.id, name=name,
                                       price=price_int, sort_order=order))
        order += 1


@app.route('/admin/products/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_product_delete(pid):
    p = Product.query.get_or_404(pid)
    name = p.name
    Favorite.query.filter_by(product_id=pid).delete()
    db.session.delete(p)
    db.session.commit()
    log_action('product.delete', 'product', pid, name)
    flash('Товар удалён', 'info')
    return redirect(url_for('admin_products'))


# --- АКЦИИ ---

@app.route('/admin/promotions')
@admin_required
def admin_promotions():
    return render_template('admin/promotions.html', promotions=Promotion.query.all())


@app.route('/admin/promotions/new', methods=['GET', 'POST'])
@admin_required
def admin_promotion_new():
    if request.method == 'POST':
        pr = Promotion(title=request.form['title'],
                       description=request.form.get('description', ''))
        img = save_image(request.files.get('image'))
        if img:
            pr.image_path = img
        db.session.add(pr)
        db.session.commit()
        log_action('promotion.create', 'promotion', pr.id, pr.title)
        flash('Акция добавлена', 'success')
        return redirect(url_for('admin_promotions'))
    return render_template('admin/promotion_form.html', promotion=None)


@app.route('/admin/promotions/<int:pid>/edit', methods=['GET', 'POST'])
@admin_required
def admin_promotion_edit(pid):
    pr = Promotion.query.get_or_404(pid)
    if request.method == 'POST':
        pr.title = request.form['title']
        pr.description = request.form.get('description', '')
        img = save_image(request.files.get('image'))
        if img:
            pr.image_path = img
        db.session.commit()
        log_action('promotion.edit', 'promotion', pr.id, pr.title)
        flash('Акция обновлена', 'success')
        return redirect(url_for('admin_promotions'))
    return render_template('admin/promotion_form.html', promotion=pr)


@app.route('/admin/promotions/<int:pid>/delete', methods=['POST'])
@admin_required
def admin_promotion_delete(pid):
    pr = Promotion.query.get_or_404(pid)
    title = pr.title
    db.session.delete(pr)
    db.session.commit()
    log_action('promotion.delete', 'promotion', pid, title)
    flash('Акция удалена', 'info')
    return redirect(url_for('admin_promotions'))


# --- ПОЛЬЗОВАТЕЛИ ---

@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.id).all()
    counts = {
        'total': len(users),
        'admins': sum(1 for u in users if u.is_admin),
        'regular': sum(1 for u in users if not u.is_admin),
    }
    return render_template('admin/users.html', users=users, counts=counts)


@app.route('/admin/users/<int:uid>/toggle_admin', methods=['POST'])
@admin_required
def admin_user_toggle_admin(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('Нельзя снять права с самого себя', 'danger')
        return redirect(url_for('admin_users'))
    u.is_admin = not u.is_admin
    db.session.commit()
    log_action('user.toggle_admin', 'user', u.id,
               f'{u.username}: {"выдан админ" if u.is_admin else "снят админ"}')
    flash(f'{u.username} — {"админ" if u.is_admin else "пользователь"}', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/toggle_counters', methods=['POST'])
@admin_required
def admin_user_toggle_counters(uid):
    u = User.query.get_or_404(uid)
    u.show_admin_counters = not u.show_admin_counters
    db.session.commit()
    log_action('user.toggle_counters', 'user', u.id,
               f'{u.username}: счётчики {"вкл" if u.show_admin_counters else "выкл"}')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_user_delete(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash('Нельзя удалить самого себя', 'danger')
        return redirect(url_for('admin_users'))
    uname = u.username
    Favorite.query.filter_by(user_id=u.id).delete()
    db.session.delete(u)
    db.session.commit()
    log_action('user.delete', 'user', uid, uname)
    flash('Пользователь удалён', 'info')
    return redirect(url_for('admin_users'))


# --- СМЕНА ПАРОЛЯ ---

@app.route('/admin/password', methods=['GET', 'POST'])
@admin_required
def admin_password():
    if request.method == 'POST':
        old = request.form.get('old', '')
        new = request.form.get('new', '')
        if not current_user.check_password(old):
            flash('Старый пароль неверный', 'danger')
            return redirect(url_for('admin_password'))
        if len(new) < 4:
            flash('Пароль слишком короткий', 'danger')
            return redirect(url_for('admin_password'))
        current_user.set_password(new)
        db.session.commit()
        log_action('user.change_password', 'user', current_user.id)
        flash('Пароль изменён', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('admin/password.html')


# --- ЭКСПОРТ ---

@app.route('/admin/export/catalog.csv')
@admin_required
def admin_export_catalog():
    output = io.StringIO()
    output.write('\ufeff')  # BOM для Excel
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Название', 'Категория', 'Цена (база)', 'Варианты', 'Описание'])
    for p in Product.query.order_by(Product.id).all():
        variants = '; '.join(f'{v.name}={v.price}' for v in p.variants)
        writer.writerow([p.id, p.name,
                         p.category.full_name if p.category else '',
                         p.price or '', variants, p.description or ''])
    log_action('export.catalog_csv', 'export', None, f'товаров: {Product.query.count()}')
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=catalog_{datetime.now():%Y%m%d_%H%M}.csv'}
    )

@app.route('/admin/categories/<int:cid>/move', methods=['POST'])
@admin_required
def admin_category_move(cid):
    c = Category.query.get_or_404(cid)
    new_parent_id = request.form.get('parent_id', type=int)

    if new_parent_id:
        if new_parent_id == c.id or new_parent_id in c.descendant_ids:
            flash('Нельзя переместить категорию внутрь себя', 'danger')
            return redirect(url_for('admin_categories'))
        parent = Category.query.get(new_parent_id)
        if not parent:
            flash('Родитель не найден', 'danger')
            return redirect(url_for('admin_categories'))

    old_parent = c.parent.name if c.parent else 'корень'
    c.parent_id = new_parent_id
    db.session.commit()
    new_parent_name = c.parent.name if c.parent else 'корень'
    log_action('category.move', 'category', c.id,
               f'{c.full_name}: {old_parent} → {new_parent_name}')
    flash(f'«{c.name}» перемещена в «{new_parent_name}»', 'success')
    return redirect(url_for('admin_categories'))


@app.route('/admin/products/bulk', methods=['POST'])
@admin_required
def admin_products_bulk():
    ids = request.form.getlist('ids', type=int)
    action = request.form.get('action', '')

    if not ids:
        flash('Ничего не выбрано', 'danger')
        return redirect(url_for('admin_products'))

    if action == 'delete':
        # запрещаем молчаливое удаление — только после подтверждения
        products = Product.query.filter(Product.id.in_(ids)).all()
        names = [p.name for p in products]
        # переводим на страницу подтверждения
        return render_template('admin/bulk_confirm_delete.html',
                               products=products, ids=ids)

    if action == 'move':
        target_id = request.form.get('target_category', type=int)
        if not target_id:
            flash('Выбери категорию', 'danger')
            return redirect(url_for('admin_products'))
        target = Category.query.get(target_id)
        if not target:
            flash('Категория не найдена', 'danger')
            return redirect(url_for('admin_products'))
        count = Product.query.filter(Product.id.in_(ids)).update(
            {'category_id': target.id}, synchronize_session=False)
        db.session.commit()
        log_action('product.bulk_move', 'product', None,
                   f'{count} товаров → «{target.full_name}»')
        flash(f'Перемещено товаров: {count}', 'success')
        return redirect(url_for('admin_products'))

    flash('Неизвестное действие', 'danger')
    return redirect(url_for('admin_products'))


@app.route('/admin/products/bulk/confirm_delete', methods=['POST'])
@admin_required
def admin_products_bulk_confirm_delete():
    ids = request.form.getlist('ids', type=int)
    if not ids:
        flash('Ничего не выбрано', 'danger')
        return redirect(url_for('admin_products'))
    Favorite.query.filter(Favorite.product_id.in_(ids)).delete(synchronize_session=False)
    count = Product.query.filter(Product.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    log_action('product.bulk_delete', 'product', None, f'{count} товаров')
    flash(f'Удалено товаров: {count}', 'info')
    return redirect(url_for('admin_products'))

# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        get_or_create_uncategorized()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', is_admin=True, show_admin_counters=True)
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()
            print('Админ создан: admin / admin')
    app.run(debug=True)