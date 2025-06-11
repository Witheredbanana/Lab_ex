from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import hashlib
from werkzeug.utils import secure_filename
import csv
from io import StringIO, TextIOWrapper
from sqlalchemy import text
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import tempfile
from functools import wraps

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///library.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Создаем директорию для загрузок, если она не существует
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def save_cover(file):
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        with open(file_path, 'rb') as f:
            md5_hash = hashlib.md5(f.read()).hexdigest()
        
        cover = Cover(
            filename=filename,
            mime_type=file.content_type,
            md5_hash=md5_hash
        )
        db.session.add(cover)
        db.session.commit()
        
        return cover
    return None

# Модели данных
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=False)
    visits = db.relationship('BookVisit', backref='user', lazy=True)
    reviews = db.relationship('Review', backref='user', lazy=True)

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    users = db.relationship('User', backref='role', lazy=True)

class Book(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    brief_description = db.Column(db.Text, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    publisher = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(200), nullable=False)
    pages = db.Column(db.Integer, nullable=False)
    cover_id = db.Column(db.Integer, db.ForeignKey('cover.id'))
    genres = db.relationship('Genre', secondary='book_genre', backref='books')
    visits = db.relationship('BookVisit', backref='book', lazy=True)
    reviews = db.relationship('Review', backref='book', lazy=True)

class Genre(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class BookGenre(db.Model):
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), primary_key=True)
    genre_id = db.Column(db.Integer, db.ForeignKey('genre.id'), primary_key=True)

class Cover(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    md5_hash = db.Column(db.String(32), nullable=False)
    book = db.relationship('Book', backref='cover', uselist=False)

class BookVisit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    visit_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ip_address = db.Column(db.String(50))

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    book_id = db.Column(db.Integer, db.ForeignKey('book.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

def init_db():
    db.create_all()
    
    # Удаляем поле status_id из таблицы review, если оно существует
    with db.engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE review DROP COLUMN status_id"))
            conn.commit()
        except Exception as e:
            conn.rollback()
    
    if Role.query.count() == 0:
        roles = [
            Role(name='Администратор', description='Полный доступ к системе, включая создание и удаление книг'),
            Role(name='Модератор', description='Может редактировать данные книг и модерировать рецензии'),
            Role(name='Пользователь', description='Может оставлять рецензии')
        ]
        db.session.add_all(roles)
        db.session.commit()

    if User.query.filter_by(login='admin').first() is None:
        admin_role = Role.query.filter_by(name='Администратор').first()
        admin = User(
            login='admin',
            password_hash=generate_password_hash('admin'),
            last_name='Администратор',
            first_name='Системный',
            role_id=admin_role.id
        )
        db.session.add(admin)
        db.session.commit()

    if User.query.filter_by(login='moderator').first() is None:
        moderator_role = Role.query.filter_by(name='Модератор').first()
        if moderator_role:
            moderator = User(
                login='moderator',
                password_hash=generate_password_hash('moderator'),
                last_name='Модератор',
                first_name='Тестовый',
                role_id=moderator_role.id
            )
            db.session.add(moderator)
            db.session.commit()

    if User.query.filter_by(login='test').first() is None:
        user_role = Role.query.filter_by(name='Пользователь').first()
        test_user = User(
            login='test',
            password_hash=generate_password_hash('test'),
            last_name='Тестовый',
            first_name='Пользователь',
            role_id=user_role.id
        )
        db.session.add(test_user)
        db.session.commit()

    if Genre.query.count() == 0:
        genres = [
            Genre(name='Фантастика'),
            Genre(name='Детектив'),
            Genre(name='Роман'),
            Genre(name='Поэзия'),
            Genre(name='Драма'),
            Genre(name='Комедия'),
            Genre(name='Приключения'),
            Genre(name='Исторический'),
            Genre(name='Научно-популярный'),
            Genre(name='Учебная литература')
        ]
        db.session.add_all(genres)
        db.session.commit()

    # Добавляем тестовые книги, если их нет
    if Book.query.count() == 0:
        # Получаем жанры
        fantasy = Genre.query.filter_by(name='Фантастика').first()
        detective = Genre.query.filter_by(name='Детектив').first()
        novel = Genre.query.filter_by(name='Роман').first()
        poetry = Genre.query.filter_by(name='Поэзия').first()

        # Создаем обложки для книг
        covers = {
            'master': Cover(
                filename='master.jpg',
                mime_type='image/jpeg',
                md5_hash='master_hash'
            ),
            'crime': Cover(
                filename='crime.jpg',
                mime_type='image/jpeg',
                md5_hash='crime_hash'
            ),
            'onegin': Cover(
                filename='onegin.jpg',
                mime_type='image/jpeg',
                md5_hash='onegin_hash'
            ),
            'war': Cover(
                filename='war.jpg',
                mime_type='image/jpeg',
                md5_hash='war_hash'
            ),
            'potter': Cover(
                filename='potter.jpg',
                mime_type='image/jpeg',
                md5_hash='potter_hash'
            )
        }
        db.session.add_all(covers.values())
        db.session.commit()

        # Создаем книги
        books = [
            Book(
                title='Мастер и Маргарита',
                author='Михаил Булгаков',
                publisher='АСТ',
                year=1967,
                pages=480,
                brief_description='Роман «Мастер и Маргарита» — самое известное произведение Михаила Булгакова. В нем переплетаются реальность и фантастика, история и современность, любовь и ненависть, добро и зло.',
                genres=[novel],
                cover=covers['master']
            ),
            Book(
                title='Преступление и наказание',
                author='Федор Достоевский',
                publisher='Эксмо',
                year=1866,
                pages=672,
                brief_description='Роман «Преступление и наказание» — одно из самых известных произведений Федора Достоевского. В нем исследуются темы морали, совести и искупления.',
                genres=[novel, detective],
                cover=covers['crime']
            ),
            Book(
                title='Евгений Онегин',
                author='Александр Пушкин',
                publisher='Азбука',
                year=1833,
                pages=320,
                brief_description='Роман в стихах «Евгений Онегин» — самое известное произведение Александра Пушкина. В нем отражена жизнь русского общества первой трети XIX века.',
                genres=[poetry, novel],
                cover=covers['onegin']
            ),
            Book(
                title='Война и мир',
                author='Лев Толстой',
                publisher='АСТ',
                year=1869,
                pages=1225,
                brief_description='Роман-эпопея «Война и мир» — одно из самых значительных произведений мировой литературы. В нем отражена жизнь русского общества в эпоху наполеоновских войн.',
                genres=[novel],
                cover=covers['war']
            ),
            Book(
                title='Гарри Поттер и философский камень',
                author='Джоан Роулинг',
                publisher='Росмэн',
                year=1997,
                pages=432,
                brief_description='Первый роман в серии книг о юном волшебнике Гарри Поттере. История о мальчике, который узнает, что он волшебник, и отправляется в школу магии Хогвартс.',
                genres=[fantasy],
                cover=covers['potter']
            )
        ]
        db.session.add_all(books)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def record_book_visit(book_id):
    today = datetime.now().date()
    user_id = current_user.id if current_user.is_authenticated else None
    ip_address = request.remote_addr

    # Проверяем количество посещений за день
    if user_id:
        visits_today = BookVisit.query.filter(
            BookVisit.book_id == book_id,
            BookVisit.user_id == user_id,
            db.func.date(BookVisit.visit_date) == today
        ).count()

        if visits_today < 10:  # Ограничение в 10 посещений в день
            visit = BookVisit(
                book_id=book_id,
                user_id=user_id,
                ip_address=ip_address
            )
            db.session.add(visit)
            db.session.commit()
    else:
        # Для неаутентифицированных пользователей
        visit = BookVisit(
            book_id=book_id,
            ip_address=ip_address
        )
        db.session.add(visit)
        db.session.commit()

@app.route('/')
def index():
    # Получаем популярные книги за последние 3 месяца
    three_months_ago = datetime.now() - timedelta(days=90)
    popular_books = db.session.query(
        Book,
        db.func.count(BookVisit.id).label('visit_count'),
        db.func.avg(Review.rating).label('avg_rating'),
        db.func.count(db.distinct(Review.id)).label('review_count')  # Используем distinct для уникальных рецензий
    ).join(BookVisit).outerjoin(Review).filter(
        BookVisit.visit_date >= three_months_ago
    ).group_by(Book.id).order_by(db.desc('visit_count')).limit(5).all()

    # Получаем недавно просмотренные книги
    recent_visits = []
    if current_user.is_authenticated:
        recent_visits = BookVisit.query.filter_by(user_id=current_user.id).order_by(BookVisit.visit_date.desc()).limit(5).all()
    else:
        recent_visits = BookVisit.query.filter_by(ip_address=request.remote_addr).order_by(BookVisit.visit_date.desc()).limit(5).all()

    # Получаем все книги с рейтингом и количеством рецензий
    books = db.session.query(
        Book,
        db.func.avg(Review.rating).label('avg_rating'),
        db.func.count(db.distinct(Review.id)).label('review_count')  # Используем distinct для уникальных рецензий
    ).outerjoin(Review).group_by(Book.id).order_by(Book.year.desc()).paginate(page=1, per_page=10)

    return render_template('index.html', books=books, popular_books=popular_books, recent_visits=recent_visits)

@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = Book.query.get_or_404(book_id)
    record_book_visit(book_id)
    return render_template('view_book.html', book=book)

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role.name != 'Администратор':
            flash('У вас нет прав для выполнения этого действия')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def moderator_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role.name not in ['Администратор', 'Модератор']:
            flash('У вас нет прав для выполнения этого действия')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/statistics')
@login_required
@moderator_required
def statistics():
    # Получаем параметры фильтрации и пагинации
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    book_page = request.args.get('book_page', 1, type=int)
    user_page = request.args.get('user_page', 1, type=int)
    per_page = 10

    # Базовый запрос для статистики просмотров
    book_stats_query = db.session.query(
        Book,
        db.func.count(BookVisit.id).label('visit_count'),
        db.func.avg(Review.rating).label('avg_rating'),
        db.func.count(db.distinct(Review.id)).label('review_count')  # Используем distinct для уникальных рецензий
    ).join(BookVisit).outerjoin(Review).filter(
        BookVisit.user_id.isnot(None)  # Только аутентифицированные пользователи
    )

    # Применяем фильтры по дате
    if date_from:
        book_stats_query = book_stats_query.filter(BookVisit.visit_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        book_stats_query = book_stats_query.filter(BookVisit.visit_date <= datetime.strptime(date_to, '%Y-%m-%d'))

    # Получаем статистику просмотров с пагинацией
    book_stats = book_stats_query.group_by(Book.id).order_by(db.desc('visit_count')).paginate(page=book_page, per_page=per_page)

    # Базовый запрос для журнала действий пользователей
    user_actions_query = db.session.query(
        BookVisit,
        Book,
        User
    ).join(Book).outerjoin(User)

    # Применяем фильтры по дате
    if date_from:
        user_actions_query = user_actions_query.filter(BookVisit.visit_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        user_actions_query = user_actions_query.filter(BookVisit.visit_date <= datetime.strptime(date_to, '%Y-%m-%d'))

    # Получаем журнал действий пользователей с пагинацией
    user_actions = user_actions_query.order_by(BookVisit.visit_date.desc()).paginate(page=user_page, per_page=per_page)

    return render_template('statistics.html',
                         book_stats=book_stats,
                         user_actions=user_actions,
                         date_from=date_from,
                         date_to=date_to)

@app.route('/export/user-actions')
@login_required
@moderator_required
def export_user_actions():
    # Получаем параметры фильтрации
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    # Базовый запрос
    actions_query = db.session.query(
        BookVisit,
        Book,
        User
    ).join(Book).outerjoin(User)

    # Применяем фильтры по дате
    if date_from:
        actions_query = actions_query.filter(BookVisit.visit_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        actions_query = actions_query.filter(BookVisit.visit_date <= datetime.strptime(date_to, '%Y-%m-%d'))

    # Получаем все действия
    actions = actions_query.order_by(BookVisit.visit_date.desc()).all()

    # Создаем временный файл
    temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8-sig', suffix='.csv')
    
    # Записываем данные в CSV
    writer = csv.writer(temp_file)
    writer.writerow(['№', 'ФИО пользователя', 'Название книги', 'Автор', 'Дата и время просмотра', 'IP-адрес'])

    for i, (visit, book, user) in enumerate(actions, 1):
        user_name = f"{user.last_name} {user.first_name}" if user else "Неаутентифицированный пользователь"
        writer.writerow([
            i,
            user_name,
            book.title,
            book.author,
            visit.visit_date.strftime('%d.%m.%Y %H:%M'),
            visit.ip_address
        ])

    temp_file.close()

    # Отправляем файл
    return send_file(
        temp_file.name,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'user_actions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/export/book-stats')
@login_required
@moderator_required
def export_book_stats():
    # Получаем параметры фильтрации
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    # Базовый запрос для статистики просмотров
    book_stats_query = db.session.query(
        Book,
        db.func.count(BookVisit.id).label('visit_count'),
        db.func.avg(Review.rating).label('avg_rating'),
        db.func.count(db.distinct(Review.id)).label('review_count')  # Используем distinct для уникальных рецензий
    ).join(BookVisit).outerjoin(Review).filter(
        BookVisit.user_id.isnot(None)
    )

    # Применяем фильтры по дате
    if date_from:
        book_stats_query = book_stats_query.filter(BookVisit.visit_date >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        book_stats_query = book_stats_query.filter(BookVisit.visit_date <= datetime.strptime(date_to, '%Y-%m-%d'))

    # Получаем статистику
    book_stats = book_stats_query.group_by(Book.id).order_by(db.desc('visit_count')).all()

    # Создаем временный файл
    temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8-sig', suffix='.csv')
    
    # Записываем данные в CSV
    writer = csv.writer(temp_file)
    writer.writerow(['№', 'Название книги', 'Автор', 'Год издания', 'Количество просмотров', 'Средний рейтинг', 'Количество рецензий'])

    for i, (book, visit_count, avg_rating, review_count) in enumerate(book_stats, 1):
        writer.writerow([
            i,
            book.title,
            book.author,
            book.year,
            visit_count,
            f"{avg_rating:.1f}" if avg_rating else "Нет оценок",
            review_count
        ])

    temp_file.close()

    # Отправляем файл
    return send_file(
        temp_file.name,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'book_stats_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

@app.route('/book/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
@moderator_required
def edit_book(book_id):
    book = Book.query.get_or_404(book_id)
    
    if request.method == 'POST':
        book.title = request.form['title']
        book.author = request.form['author']
        book.publisher = request.form['publisher']
        book.year = int(request.form['year'])
        book.pages = int(request.form['pages'])
        book.brief_description = request.form['description']
        
        # Обработка обложки
        if 'cover' in request.files:
            file = request.files['cover']
            if file and file.filename:
                cover = save_cover(file)
                if cover:
                    book.cover = cover
        
        # Обработка жанров
        book.genres = []
        for genre_id in request.form.getlist('genres'):
            genre = Genre.query.get(genre_id)
            if genre:
                book.genres.append(genre)
        
        db.session.commit()
        flash('Книга успешно обновлена')
        return redirect(url_for('view_book', book_id=book.id))
    
    genres = Genre.query.all()
    return render_template('edit_book.html', book=book, genres=genres)

@app.route('/book/<int:book_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_book(book_id):
    book = Book.query.get_or_404(book_id)
    
    # Удаляем обложку, если она есть
    if book.cover:
        cover_path = os.path.join(app.config['UPLOAD_FOLDER'], book.cover.filename)
        if os.path.exists(cover_path):
            os.remove(cover_path)
        db.session.delete(book.cover)
    
    # Удаляем книгу
    db.session.delete(book)
    db.session.commit()
    
    flash('Книга успешно удалена')
    return redirect(url_for('index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы успешно вышли из аккаунта')
    return redirect(url_for('index'))

@app.route('/book/<int:book_id>/add_review', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = Book.query.get_or_404(book_id)
    
    if request.method == 'POST':
        print("POST request received")
        print("Form data:", request.form)
        
        try:
            rating = int(request.form['rating'])
            text = request.form['text']
            
            print(f"Rating: {rating}")
            print(f"Text: {text}")
            
            review = Review(
                book_id=book.id,
                user_id=current_user.id,
                rating=rating,
                text=text
            )
            
            db.session.add(review)
            db.session.commit()
            
            flash('Рецензия успешно добавлена')
            return redirect(url_for('view_book', book_id=book.id))
        except Exception as e:
            print("Error:", str(e))
            flash('Произошла ошибка при добавлении рецензии')
            return render_template('add_review.html', book=book)
    
    return render_template('add_review.html', book=book)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        login = request.form['login']
        password = request.form['password']
        print(f"Попытка входа: логин={login}")
        
        user = User.query.filter_by(login=login).first()
        if user:
            print(f"Пользователь найден: {user.login}, роль: {user.role.name}")
            print(f"Хэш пароля в базе: {user.password_hash}")
            print(f"Проверяем пароль: {password}")
            if check_password_hash(user.password_hash, password):
                print("Пароль верный, выполняем вход")
                login_user(user)
                flash('Вы успешно вошли в систему')
                return redirect(url_for('index'))
            else:
                print("Неверный пароль")
        else:
            print("Пользователь не найден")
        
        flash('Неверный логин или пароль')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        login = request.form['login']
        password = request.form['password']
        last_name = request.form['last_name']
        first_name = request.form['first_name']
        middle_name = request.form.get('middle_name')
        
        if User.query.filter_by(login=login).first():
            flash('Пользователь с таким логином уже существует')
            return render_template('register.html')
        
        user_role = Role.query.filter_by(name='Пользователь').first()
        user = User(
            login=login,
            password_hash=generate_password_hash(password),
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            role_id=user_role.id
        )
        
        db.session.add(user)
        db.session.commit()
        
        flash('Регистрация успешно завершена')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# Инициализация базы данных при запуске
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port) 
