"""
Быстрый просмотр данных из БД.
Запуск: python check_db.py
"""
from app import app, db, User, Product, Promotion, Favorite


def show_users():
    print("\n=== ПОЛЬЗОВАТЕЛИ ===")
    users = User.query.order_by(User.id).all()
    if not users:
        print("Пусто.")
        return
    print(f"{'ID':<5}{'Логин':<20}{'Админ':<8}{'Регистрация':<20}{'Хеш пароля'}")
    print("-" * 100)
    for u in users:
        created = u.created_at.strftime('%d.%m.%Y %H:%M') if u.created_at else '—'
        admin = 'да' if u.is_admin else 'нет'
        print(f"{u.id:<5}{u.username:<20}{admin:<8}{created:<20}{u.password_hash[:40]}...")


def show_stats():
    print("\n=== СТАТИСТИКА ===")
    print(f"Пользователей:  {User.query.count()}")
    print(f"  из них админов: {User.query.filter_by(is_admin=True).count()}")
    print(f"Товаров:        {Product.query.count()}")
    print(f"Акций:          {Promotion.query.count()}")
    print(f"В избранном:    {Favorite.query.count()} записей")


def show_categories():
    print("\n=== КАТЕГОРИИ ===")
    cats = db.session.query(Product.category, db.func.count(Product.id))\
        .group_by(Product.category).all()
    for name, count in cats:
        print(f"  {name:<40} {count}")


def show_favorites():
    print("\n=== ИЗБРАННОЕ (кто что лайкнул) ===")
    favs = Favorite.query.all()
    if not favs:
        print("Пусто.")
        return
    for f in favs:
        user = User.query.get(f.user_id)
        product = Product.query.get(f.product_id)
        if user and product:
            print(f"  {user.username:<20} → {product.name}")


if __name__ == '__main__':
    with app.app_context():
        show_stats()
        show_users()
        show_categories()
        show_favorites()
        print()