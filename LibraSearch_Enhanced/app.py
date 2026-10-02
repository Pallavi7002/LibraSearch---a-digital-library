from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
import sqlite3
import time
import re
import heapq
import random
import string
from pathlib import Path
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "library.db"

app = Flask(__name__)
app.secret_key = "libra-search-demo-secret-change-me"
app.config["JSON_SORT_KEYS"] = False

# ---------------------------------------------------------------------
# DAA SEARCH ENGINE
# ---------------------------------------------------------------------

STOP_WORDS = {"the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "with", "by", "is"}

def normalize(text):
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()

def tokenize(text):
    return [t for t in normalize(text).split() if t and t not in STOP_WORDS]

def resource_text(r):
    return " ".join([
        r.get("title", ""),
        r.get("author", ""),
        r.get("description", ""),
        r.get("keywords", ""),
        r.get("category", ""),
        r.get("publisher", ""),
        r.get("resource_type", "")
    ])

def build_inverted_index(resources):
    index = {}
    for r in resources:
        terms = set(tokenize(resource_text(r)))
        for term in terms:
            index.setdefault(term, set()).add(r["id"])
    return index

def build_hash_index(resources):
    # Exact normalized title/author lookup.
    return {
        normalize(r["title"]): r["id"] for r in resources
    }

def linear_search(resources, query):
    q = normalize(query)
    if not q:
        return []
    return [r for r in resources if q in normalize(resource_text(r))]

def hash_search(resources, hash_index, query):
    q = normalize(query)
    rid = hash_index.get(q)
    if rid is None:
        return []
    return [r for r in resources if r["id"] == rid]

def inverted_search(resources, inverted_index, query, match_all=False):
    terms = tokenize(query)
    if not terms:
        return []
    postings = [inverted_index.get(t, set()) for t in terms]
    if not postings:
        return []
    ids = set.intersection(*postings) if match_all else set.union(*postings)
    return [r for r in resources if r["id"] in ids]

def relevance_score(r, query):
    terms = tokenize(query)
    if not terms:
        return 0
    title = normalize(r["title"])
    author = normalize(r["author"])
    keywords = set(tokenize(r["keywords"]))
    desc = normalize(r["description"])
    category = normalize(r["category"])
    publisher = normalize(r["publisher"])
    qnorm = normalize(query)
    score = 0

    if qnorm and qnorm in title:
        score += 50
        if qnorm == title:
            score += 20
    if qnorm and qnorm in author:
        score += 20
    score += 15 * sum(1 for t in terms if t in keywords)
    score += 5 * sum(1 for t in terms if t in desc)
    score += 10 * sum(1 for t in terms if t in category)
    score += 5 * sum(1 for t in terms if t in publisher)
    score += 5 * sum(1 for t in terms if t in title)
    return score

def merge_sort(items, key=lambda x: x):
    if len(items) <= 1:
        return items[:]
    mid = len(items) // 2
    left = merge_sort(items[:mid], key)
    right = merge_sort(items[mid:], key)
    out, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if key(left[i]) <= key(right[j]):
            out.append(left[i]); i += 1
        else:
            out.append(right[j]); j += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out

def top_k(items, k, score_fn):
    if k <= 0:
        return []
    # Min-heap of (score, unique_id, item), retaining K best.
    heap = []
    for item in items:
        score = score_fn(item)
        entry = (score, item["id"], item)
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry[:2] > heap[0][:2]:
            heapq.heapreplace(heap, entry)
    return [x[2] for x in sorted(heap, key=lambda z: (z[0], z[1]), reverse=True)]

def levenshtein(a, b):
    a, b = normalize(a), normalize(b)
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (ca != cb)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]

def suggest_terms(query, resources, limit=3):
    q = normalize(query)
    if not q:
        return []
    vocab = set()
    for r in resources:
        vocab.update(tokenize(resource_text(r)))
    scored = []
    for word in vocab:
        d = levenshtein(q, word)
        if d <= max(1, min(3, len(q) // 3)):
            scored.append((d, word))
    return [w for _, w in sorted(scored)[:limit]]

def jaccard_similarity(a_terms, b_terms):
    a, b = set(a_terms), set(b_terms)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

# ---------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS resources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        description TEXT NOT NULL,
        keywords TEXT NOT NULL,
        category TEXT NOT NULL,
        publication_year INTEGER NOT NULL,
        resource_type TEXT NOT NULL,
        publisher TEXT NOT NULL,
        isbn TEXT,
        price REAL NOT NULL DEFAULT 0,
        cover TEXT,
        url TEXT,
        rating REAL DEFAULT 4.5,
        views INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS wishlist (
        user_id INTEGER,
        resource_id INTEGER,
        PRIMARY KEY(user_id, resource_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(resource_id) REFERENCES resources(id)
    );

    CREATE TABLE IF NOT EXISTS cart (
        user_id INTEGER,
        resource_id INTEGER,
        PRIMARY KEY(user_id, resource_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(resource_id) REFERENCES resources(id)
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        total REAL NOT NULL,
        status TEXT NOT NULL,
        transaction_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS order_items (
        order_id INTEGER,
        resource_id INTEGER,
        price REAL NOT NULL,
        PRIMARY KEY(order_id, resource_id),
        FOREIGN KEY(order_id) REFERENCES orders(id),
        FOREIGN KEY(resource_id) REFERENCES resources(id)
    );

    CREATE TABLE IF NOT EXISTS search_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        query TEXT NOT NULL,
        algorithm TEXT NOT NULL,
        duration_ms REAL NOT NULL,
        result_count INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS reading_progress (
        user_id INTEGER,
        resource_id INTEGER,
        progress INTEGER DEFAULT 0,
        PRIMARY KEY(user_id, resource_id)
    );

    CREATE TABLE IF NOT EXISTS reading_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        resource_id INTEGER,
        minutes INTEGER NOT NULL DEFAULT 10,
        read_date TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(resource_id) REFERENCES resources(id)
    );
    """)
    conn.commit()

    # Seed admin and demo user.
    if not cur.execute("SELECT 1 FROM users WHERE email=?", ("admin@librasearch.local",)).fetchone():
        cur.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                    ("LibraSearch Admin", "admin@librasearch.local",
                     generate_password_hash("Admin@123"), "admin"))
    if not cur.execute("SELECT 1 FROM users WHERE email=?", ("demo@librasearch.local",)).fetchone():
        cur.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
                    ("Demo Student", "demo@librasearch.local",
                     generate_password_hash("Demo@123"), "user"))

    count = cur.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    if count == 0:
        seed_resources(cur, 120)
    else:
        # Upgrade existing installations with the curated Classics shelf without
        # disturbing the user's existing resources, wishlist, or purchases.
        seed_resources(cur, 0)
    conn.commit()
    conn.close()

def seed_resources(cur, n=120):
    # A visually rich mix of academic resources and a curated Classics shelf.
    topics = [
        ("Data Structures and Algorithms", "Computer Science", "Book", "Dr. Ananya Rao"),
        ("Python Programming", "Programming", "Book", "R. K. Sharma"),
        ("Machine Learning Fundamentals", "Artificial Intelligence", "Book", "Neha Kapoor"),
        ("Deep Learning with Neural Networks", "Artificial Intelligence", "Research Paper", "Arjun Mehta"),
        ("Database Systems", "Database Systems", "Book", "Priya Nair"),
        ("Operating Systems Concepts", "Operating Systems", "Book", "S. Iyer"),
        ("Computer Networks", "Computer Networks", "Book", "K. Srinivas"),
        ("Modern Web Development", "Web Development", "Tutorial", "Aditi Menon"),
        ("Cybersecurity Essentials", "Cybersecurity", "Reference", "Vikram Das"),
        ("Data Science Handbook", "Data Science", "Book", "Meera Joshi"),
        ("Natural Language Processing", "Artificial Intelligence", "Journal", "Rahul Verma"),
        ("Competitive Programming", "Algorithms", "Book", "Ishaan Gupta"),
    ]
    adjectives = ["Practical", "Advanced", "Modern", "Applied", "Foundations of", "Introduction to", "Essential", "Efficient"]
    for i in range(n):
        base, category, rtype, author = topics[i % len(topics)]
        title = f"{adjectives[i % len(adjectives)]} {base}" if i % 3 else base
        if i >= len(topics):
            title += f" — Volume {i // len(topics) + 1}"
        year = 2016 + (i % 11)
        keywords = ", ".join(sorted(set(tokenize(base + " " + category + " python algorithms data"))))
        desc = (f"A digital learning resource covering {base.lower()}, practical concepts, "
                f"examples, and applications for students and researchers.")
        price = round(99 + (i % 10) * 37, 2)
        cover = f"https://placehold.co/600x800?text=LibraSearch+{i+1}"
        cur.execute("""INSERT INTO resources
            (title,author,description,keywords,category,publication_year,resource_type,publisher,isbn,price,cover,url,rating,views)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, author, desc, keywords, category, year, rtype,
             "Libra Academic Press", f"978-81-{10000+i:06d}", price, cover, "#", 4.1 + (i % 9)/10, i*3))

    classics = [
        ("Romeo and Juliet", "William Shakespeare", "1564", "Classic Literature", "9780743477116"),
        ("Hamlet", "William Shakespeare", "1603", "Classic Literature", "9780743477123"),
        ("Macbeth", "William Shakespeare", "1606", "Classic Literature", "9780743477109"),
        ("Pride and Prejudice", "Jane Austen", "1813", "Classic Literature", "9780141439518"),
        ("Emma", "Jane Austen", "1815", "Classic Literature", "9780141439587"),
        ("Sense and Sensibility", "Jane Austen", "1811", "Classic Literature", "9780141439662"),
        ("Great Expectations", "Charles Dickens", "1861", "Classic Literature", "9780141439563"),
        ("A Tale of Two Cities", "Charles Dickens", "1859", "Classic Literature", "9780141439600"),
        ("Oliver Twist", "Charles Dickens", "1838", "Classic Literature", "9780141439747"),
        ("Anna Karenina", "Leo Tolstoy", "1878", "Classic Literature", "9780143035008"),
        ("War and Peace", "Leo Tolstoy", "1869", "Classic Literature", "9780199232765"),
        ("The Death of Ivan Ilyich", "Leo Tolstoy", "1886", "Classic Literature", "9780143035138"),
        ("The Adventures of Tom Sawyer", "Mark Twain", "1876", "Classic Literature", "9780143039563"),
        ("Adventures of Huckleberry Finn", "Mark Twain", "1884", "Classic Literature", "9780142437170"),
        ("The Prince and the Pauper", "Mark Twain", "1881", "Classic Literature", "9780486411100"),
        ("Harry Potter and the Philosopher's Stone", "J. K. Rowling", "1997", "Fantasy", "9780747532699"),
        ("Harry Potter and the Chamber of Secrets", "J. K. Rowling", "1998", "Fantasy", "9780747549604"),
        ("Harry Potter and the Prisoner of Azkaban", "J. K. Rowling", "1999", "Fantasy", "9780747546290"),
        ("Harry Potter and the Goblet of Fire", "J. K. Rowling", "2000", "Fantasy", "9780747546245"),
    ]
    for j, (title, author, year, category, isbn) in enumerate(classics, start=1):
        if cur.execute("SELECT 1 FROM resources WHERE title=? AND author=?", (title, author)).fetchone():
            continue
        cover = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
        keywords = ", ".join(sorted(set(tokenize(title + " " + author + " classic literature novel book"))))
        desc = f"A curated classic by {author}, presented in the LibraSearch visual classics shelf."
        cur.execute("""INSERT INTO resources
            (title,author,description,keywords,category,publication_year,resource_type,publisher,isbn,price,cover,url,rating,views)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, author, desc, keywords, category, int(year), "Book",
             "LibraSearch Classics Shelf", isbn, 0, cover, "#", 4.6 + (j % 4)/10, 900 - j*17))

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None

@app.context_processor
def inject_globals():
    user = current_user()
    cart_count = 0
    wishlist_count = 0
    if user:
        conn = db()
        cart_count = conn.execute("SELECT COUNT(*) FROM cart WHERE user_id=?", (user["id"],)).fetchone()[0]
        wishlist_count = conn.execute("SELECT COUNT(*) FROM wishlist WHERE user_id=?", (user["id"],)).fetchone()[0]
        conn.close()
    return {"current_user": user, "cart_count": cart_count, "wishlist_count": wishlist_count}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped

def all_resources():
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY id DESC").fetchall()]
    conn.close()
    return rows

def run_search(query, algorithm="inverted", match_all=False, k=20):
    resources = all_resources()
    start = time.perf_counter()
    inv = build_inverted_index(resources)
    hidx = build_hash_index(resources)

    if algorithm == "linear":
        candidates = linear_search(resources, query)
    elif algorithm == "hash":
        candidates = hash_search(resources, hidx, query)
    else:
        candidates = inverted_search(resources, inv, query, match_all)

    for r in candidates:
        r["score"] = relevance_score(r, query)

    # Merge Sort gives an explicit DAA sorting step.
    ranked = merge_sort(candidates, key=lambda r: (-r["score"], -r["rating"], r["title"].lower()))
    ranked = ranked[:k]
    elapsed = (time.perf_counter() - start) * 1000
    return ranked, elapsed, len(resources), len(inv)

# ---------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------

@app.route("/")
def index():
    conn = db()
    popular = [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY views DESC, rating DESC LIMIT 8")]
    categories = conn.execute("SELECT category, COUNT(*) count FROM resources GROUP BY category ORDER BY count DESC").fetchall()
    featured_authors = []
    for author in ["William Shakespeare", "Jane Austen", "Charles Dickens", "Leo Tolstoy", "Mark Twain", "J. K. Rowling"]:
        row = conn.execute("SELECT * FROM resources WHERE author=? ORDER BY views DESC LIMIT 1", (author,)).fetchone()
        if row:
            featured_authors.append(dict(row))
    total_resources = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    conn.close()
    return render_template("index.html", popular=popular, categories=categories,
                           featured_authors=featured_authors, total_resources=total_resources)

@app.route("/library")
def library():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    rtype = request.args.get("type", "").strip()
    year = request.args.get("year", "").strip()
    algorithm = request.args.get("algorithm", "inverted")
    match_all = request.args.get("match_all") == "1"

    if q:
        results, elapsed, total, terms = run_search(q, algorithm, match_all)
        suggestions = suggest_terms(q, all_resources())
    else:
        conn = db()
        query = "SELECT * FROM resources WHERE 1=1"
        params = []
        if category:
            query += " AND category=?"; params.append(category)
        if rtype:
            query += " AND resource_type=?"; params.append(rtype)
        if year.isdigit():
            query += " AND publication_year=?"; params.append(int(year))
        query += " ORDER BY views DESC LIMIT 50"
        results = [dict(r) for r in conn.execute(query, params)]
        conn.close()
        elapsed, total, terms, suggestions = 0, len(results), 0, []

    # Apply metadata filters after search for simplicity.
    if category:
        results = [r for r in results if r["category"] == category]
    if rtype:
        results = [r for r in results if r["resource_type"] == rtype]
    if year.isdigit():
        results = [r for r in results if r["publication_year"] == int(year)]

    if q and session.get("user_id"):
        conn = db()
        conn.execute("""INSERT INTO search_history(user_id,query,algorithm,duration_ms,result_count)
                        VALUES(?,?,?,?,?)""", (session["user_id"], q, algorithm, elapsed, len(results)))
        conn.commit(); conn.close()

    conn = db()
    categories = [r[0] for r in conn.execute("SELECT DISTINCT category FROM resources ORDER BY category")]
    types = [r[0] for r in conn.execute("SELECT DISTINCT resource_type FROM resources ORDER BY resource_type")]
    conn.close()
    return render_template("library.html", results=results, q=q, category=category, rtype=rtype,
                           year=year, algorithm=algorithm, elapsed=elapsed, total=total,
                           terms=terms, suggestions=suggestions, categories=categories, types=types)

@app.route("/resource/<int:rid>")
def resource(rid):
    conn = db()
    row = conn.execute("SELECT * FROM resources WHERE id=?", (rid,)).fetchone()
    if not row:
        conn.close(); abort(404)
    r = dict(row)
    conn.execute("UPDATE resources SET views=views+1 WHERE id=?", (rid,))
    conn.commit()
    others = [dict(x) for x in conn.execute(
        "SELECT * FROM resources WHERE id<>? LIMIT 200", (rid,)).fetchall()]
    conn.close()

    target = set(tokenize(r["keywords"] + " " + r["category"]))
    related = []
    for item in others:
        score = jaccard_similarity(target, set(tokenize(item["keywords"] + " " + item["category"])))
        if score > 0:
            item["similarity"] = round(score * 100, 1)
            related.append(item)
    related.sort(key=lambda x: x["similarity"], reverse=True)
    return render_template("resource.html", resource=r, related=related[:6])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        conn = db()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            flash("Welcome back!", "success")
            return redirect(request.args.get("next") or url_for("index"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 6:
            flash("Enter a name, valid email, and password of at least 6 characters.", "danger")
            return render_template("signup.html")
        conn = db()
        try:
            cur = conn.execute("INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                               (name, email, generate_password_hash(password)))
            conn.commit()
            session["user_id"] = cur.lastrowid
            flash("Account created successfully.", "success")
            return redirect(url_for("index"))
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "danger")
        finally:
            conn.close()
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/wishlist")
@login_required
def wishlist():
    conn = db()
    items = [dict(r) for r in conn.execute("""
        SELECT r.* FROM resources r JOIN wishlist w ON r.id=w.resource_id
        WHERE w.user_id=? ORDER BY r.title
    """, (session["user_id"],))]
    conn.close()
    return render_template("wishlist.html", items=items)

@app.post("/wishlist/toggle/<int:rid>")
@login_required
def wishlist_toggle(rid):
    conn = db()
    exists = conn.execute("SELECT 1 FROM wishlist WHERE user_id=? AND resource_id=?",
                          (session["user_id"], rid)).fetchone()
    if exists:
        conn.execute("DELETE FROM wishlist WHERE user_id=? AND resource_id=?", (session["user_id"], rid))
        msg = "Removed from wishlist."
    else:
        conn.execute("INSERT INTO wishlist(user_id,resource_id) VALUES(?,?)", (session["user_id"], rid))
        msg = "Added to wishlist."
    conn.commit(); conn.close()
    flash(msg, "success")
    return redirect(request.referrer or url_for("library"))

@app.route("/cart")
@login_required
def cart():
    conn = db()
    items = [dict(r) for r in conn.execute("""
        SELECT r.* FROM resources r JOIN cart c ON r.id=c.resource_id
        WHERE c.user_id=? ORDER BY c.rowid DESC
    """, (session["user_id"],))]
    conn.close()
    total = round(sum(x["price"] for x in items), 2)
    return render_template("cart.html", items=items, total=total)

@app.post("/cart/add/<int:rid>")
@login_required
def cart_add(rid):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO cart(user_id,resource_id) VALUES(?,?)",
                 (session["user_id"], rid))
    conn.commit(); conn.close()
    flash("Resource added to cart.", "success")
    return redirect(request.referrer or url_for("cart"))

@app.post("/cart/remove/<int:rid>")
@login_required
def cart_remove(rid):
    conn = db()
    conn.execute("DELETE FROM cart WHERE user_id=? AND resource_id=?", (session["user_id"], rid))
    conn.commit(); conn.close()
    flash("Removed from cart.", "info")
    return redirect(url_for("cart"))

@app.post("/checkout")
@login_required
def checkout():
    conn = db()
    items = [dict(r) for r in conn.execute("""
        SELECT r.* FROM resources r JOIN cart c ON r.id=c.resource_id WHERE c.user_id=?
    """, (session["user_id"],))]
    if not items:
        conn.close()
        flash("Your cart is empty.", "warning")
        return redirect(url_for("cart"))
    total = round(sum(x["price"] for x in items), 2)
    tx = "TXN-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    cur = conn.execute("INSERT INTO orders(user_id,total,status,transaction_id) VALUES(?,?,?,?)",
                       (session["user_id"], total, "SUCCESS", tx))
    oid = cur.lastrowid
    for item in items:
        conn.execute("INSERT INTO order_items(order_id,resource_id,price) VALUES(?,?,?)",
                     (oid, item["id"], item["price"]))
    conn.execute("DELETE FROM cart WHERE user_id=?", (session["user_id"],))
    conn.commit(); conn.close()
    flash(f"Demo payment successful. Transaction {tx}", "success")
    return redirect(url_for("my_library"))

@app.route("/my-library")
@login_required
def my_library():
    conn = db()
    items = [dict(r) for r in conn.execute("""
        SELECT r.*, o.created_at purchase_date
        FROM resources r
        JOIN order_items oi ON r.id=oi.resource_id
        JOIN orders o ON o.id=oi.order_id
        WHERE o.user_id=?
        GROUP BY r.id
        ORDER BY o.created_at DESC
    """, (session["user_id"],))]
    conn.close()
    return render_template("my_library.html", items=items)

@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    conn = db()
    searches = conn.execute("SELECT COUNT(*) FROM search_history WHERE user_id=?", (uid,)).fetchone()[0]
    wishlist_n = conn.execute("SELECT COUNT(*) FROM wishlist WHERE user_id=?", (uid,)).fetchone()[0]
    orders = conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (uid,)).fetchone()[0]
    recent = [dict(x) for x in conn.execute(
        "SELECT * FROM search_history WHERE user_id=? ORDER BY id DESC LIMIT 8", (uid,)).fetchall()]
    cats = conn.execute("""
        SELECT r.category, COUNT(*) c FROM search_history sh
        JOIN resources r ON instr(',' || lower(r.keywords) || ',', ',' || lower(sh.query) || ',') > 0
        WHERE sh.user_id=? GROUP BY r.category ORDER BY c DESC LIMIT 5
    """, (uid,)).fetchall()

    # Daily reading records for the last 7 days.
    from datetime import date, timedelta
    today = date.today()
    daily = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        row = conn.execute(
            "SELECT COALESCE(SUM(minutes),0) minutes, COUNT(*) sessions FROM reading_log WHERE user_id=? AND read_date=?",
            (uid, day.isoformat())).fetchone()
        daily.append({"date": day.isoformat(), "label": day.strftime("%a"), "minutes": int(row["minutes"]), "sessions": int(row["sessions"])})

    # Consecutive-day streak ending today (or yesterday if today has not been logged yet).
    logged = {r["read_date"] for r in conn.execute(
        "SELECT DISTINCT read_date FROM reading_log WHERE user_id=?", (uid,)).fetchall()}
    streak = 0
    cursor = today
    if cursor.isoformat() not in logged:
        cursor -= timedelta(days=1)
    while cursor.isoformat() in logged:
        streak += 1
        cursor -= timedelta(days=1)

    total_minutes = conn.execute("SELECT COALESCE(SUM(minutes),0) FROM reading_log WHERE user_id=?", (uid,)).fetchone()[0]
    reading_sessions = conn.execute("SELECT COUNT(*) FROM reading_log WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    return render_template("dashboard.html", searches=searches, wishlist_n=wishlist_n,
                           orders=orders, recent=recent, cats=cats, daily=daily,
                           streak=streak, total_minutes=total_minutes, reading_sessions=reading_sessions)

@app.post("/reading/log/<int:rid>")
@login_required
def log_reading(rid):
    minutes = max(1, min(int(request.form.get("minutes") or 15), 240))
    conn = db()
    exists = conn.execute("SELECT 1 FROM resources WHERE id=?", (rid,)).fetchone()
    if not exists:
        conn.close()
        abort(404)
    from datetime import date
    conn.execute("INSERT INTO reading_log(user_id,resource_id,minutes,read_date) VALUES(?,?,?,?)",
                 (session["user_id"], rid, minutes, date.today().isoformat()))
    conn.commit(); conn.close()
    flash(f"Reading session recorded: {minutes} minutes. Keep the streak going!", "success")
    return redirect(request.referrer or url_for("dashboard"))

@app.route("/algorithm-lab")
def algorithm_lab():
    return render_template("algorithm_lab.html")

@app.post("/api/benchmark")
def benchmark():
    payload = request.get_json(silent=True) or {}
    query = payload.get("query", "machine learning")
    sizes = payload.get("sizes", [100, 500, 1000, 2000])
    sizes = [int(x) for x in sizes][:6]
    base = all_resources()
    out = []
    for size in sizes:
        data = (base * ((size + len(base)-1)//len(base)))[:size]
        inv = build_inverted_index(data)
        hidx = build_hash_index(data)
        row = {"size": size}
        for name in ("linear", "hash", "inverted"):
            t0 = time.perf_counter()
            if name == "linear":
                linear_search(data, query)
            elif name == "hash":
                hash_search(data, hidx, query)
            else:
                inverted_search(data, inv, query)
            row[name] = round((time.perf_counter() - t0) * 1000, 4)
        out.append(row)
    return jsonify({"query": query, "results": out})

@app.route("/admin")
@admin_required
def admin():
    conn = db()
    stats = {
        "resources": conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "searches": conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0],
        "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
    }
    resources = [dict(r) for r in conn.execute("SELECT * FROM resources ORDER BY id DESC LIMIT 30")]
    conn.close()
    return render_template("admin.html", stats=stats, resources=resources)

@app.route("/admin/resource/new", methods=["GET", "POST"])
@app.route("/admin/resource/<int:rid>/edit", methods=["GET", "POST"])
@admin_required
def admin_resource(rid=None):
    conn = db()
    resource_row = conn.execute("SELECT * FROM resources WHERE id=?", (rid,)).fetchone() if rid else None
    if request.method == "POST":
        fields = [
            request.form.get("title","").strip(),
            request.form.get("author","").strip(),
            request.form.get("description","").strip(),
            request.form.get("keywords","").strip(),
            request.form.get("category","").strip(),
            int(request.form.get("publication_year") or 2026),
            request.form.get("resource_type","Book"),
            request.form.get("publisher","").strip(),
            request.form.get("isbn","").strip(),
            float(request.form.get("price") or 0),
            request.form.get("cover","").strip(),
            request.form.get("url","#").strip(),
        ]
        if rid:
            conn.execute("""UPDATE resources SET title=?,author=?,description=?,keywords=?,category=?,
                publication_year=?,resource_type=?,publisher=?,isbn=?,price=?,cover=?,url=? WHERE id=?""",
                (*fields, rid))
        else:
            conn.execute("""INSERT INTO resources
                (title,author,description,keywords,category,publication_year,resource_type,publisher,isbn,price,cover,url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", fields)
        conn.commit(); conn.close()
        flash("Resource saved.", "success")
        return redirect(url_for("admin"))
    conn.close()
    return render_template("admin_resource.html", resource=dict(resource_row) if resource_row else None)

@app.post("/admin/resource/<int:rid>/delete")
@admin_required
def admin_delete(rid):
    conn = db()
    for table in ("wishlist", "cart", "order_items"):
        conn.execute(f"DELETE FROM {table} WHERE resource_id=?", (rid,))
    conn.execute("DELETE FROM resources WHERE id=?", (rid,))
    conn.commit(); conn.close()
    flash("Resource deleted.", "info")
    return redirect(url_for("admin"))

@app.post("/admin/rebuild-index")
@admin_required
def rebuild_index():
    resources = all_resources()
    idx = build_inverted_index(resources)
    session["index_terms"] = len(idx)
    flash(f"Index rebuilt: {len(resources)} resources, {len(idx)} unique terms.", "success")
    return redirect(url_for("admin"))

@app.errorhandler(403)
def forbidden(e):
    return render_template("404.html", code=403, message="You do not have permission to access this page."), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", code=404, message="The requested page was not found."), 404

init_db()

if __name__ == "__main__":
    app.run(debug=True)
