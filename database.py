import os
import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
from models import Product, Order, Customer

def hp(password):
    return hashlib.sha256(password.encode()).hexdigest()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "pos.db")
RECEIPTS_FOLDER = os.path.join(BASE_DIR, "receipts")
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
ADMIN_PASSWORD_HASH = hp(ADMIN_PASSWORD)
LOW_STOCK_THRESHOLD = 1
TOP_SELLERS_LIMIT = 5


def set_db_path(path):
    global DB_FILE
    DB_FILE = path


def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def setup_database():
    conn = get_db(); cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT UNIQUE NOT NULL,
            price REAL NOT NULL,
            stock REAL NOT NULL,
            unit  TEXT NOT NULL DEFAULT 'pcs'
        );
        CREATE TABLE IF NOT EXISTS sales (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            date  TEXT NOT NULL,
            items TEXT NOT NULL,
            total REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id      INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity     REAL NOT NULL,
            subtotal     REAL NOT NULL,
            FOREIGN KEY (sale_id) REFERENCES sales(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            mobile        TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            verified      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS otp_store (
            mobile     TEXT PRIMARY KEY,
            otp_hash   TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS customer_sessions (
            token       TEXT PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            expires_at  TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no    TEXT UNIQUE NOT NULL,
            customer_id INTEGER NOT NULL,
            date        TEXT NOT NULL,
            items_json  TEXT NOT NULL,
            total       REAL NOT NULL,
            status      TEXT NOT NULL DEFAULT 'waiting',
            paid_at     TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            actor     TEXT NOT NULL,
            action    TEXT NOT NULL,
            detail    TEXT
        );
    """)
    conn.commit()
    try:
        cur.execute("ALTER TABLE products ADD COLUMN unit TEXT NOT NULL DEFAULT 'pcs'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO products (name,price,stock,unit) VALUES (?,?,?,?)",
            [
                ("Sugar",1.20,25,"kg"),("Rice",2.50,40,"kg"),("Cola",1.50,20,"pcs"),
                ("Milk",1.80,15,"litre"),("Flour",0.90,30,"kg"),("Oil",3.00,15,"litre"),
                ("Tea",2.20,10,"pcs"),("Bread",1.10,8,"pcs"),("Salt",0.50,20,"kg"),
                ("Eggs",2.80,50,"pcs")
            ]
        )
        conn.commit()
    cur.execute("SELECT value FROM settings WHERE key='store_status'")
    if not cur.fetchone():
        cur.execute("INSERT INTO settings(key,value) VALUES('store_status','OPEN')")
        conn.commit()
    conn.close()


def audit(actor, action, detail=""):
    c = get_db()
    c.execute("INSERT INTO audit_log(timestamp,actor,action,detail) VALUES(?,?,?,?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, action, detail))
    c.commit(); c.close()


def get_all_products():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,name,price,stock,unit FROM products ORDER BY name")
    rows = cur.fetchall(); c.close()
    return rows


def get_product(pid):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,name,price,stock,unit FROM products WHERE id=?",(pid,))
    row = cur.fetchone(); c.close(); return row


def add_product_db(name, price, stock, unit):
    c = get_db(); c.execute("INSERT INTO products(name,price,stock,unit) VALUES(?,?,?,?)",(name,price,stock,unit))
    c.commit(); c.close()


def update_stock_db(pid, stock):
    c = get_db(); c.execute("UPDATE products SET stock=? WHERE id=?",(stock,pid)); c.commit(); c.close()


def product_exists(name):
    c = get_db(); cur = c.cursor(); cur.execute("SELECT id FROM products WHERE name=?",(name,))
    found = cur.fetchone() is not None; c.close(); return found


def get_low_stock():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT name,stock,unit FROM products WHERE stock<=? ORDER BY stock",(LOW_STOCK_THRESHOLD,))
    rows = cur.fetchall(); c.close(); return rows


def get_store_status():
    c = get_db(); cur = c.cursor(); cur.execute("SELECT value FROM settings WHERE key='store_status'")
    row = cur.fetchone(); c.close(); return row[0] if row else "OPEN"


def set_store_status(value):
    c = get_db(); c.execute("INSERT INTO settings(key,value) VALUES('store_status',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(value,))
    c.commit(); c.close()


def save_sale(line_items, total):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary = ", ".join(f"{li['name']} x{li['qty']}{li['unit']}" for li in line_items)
    c = get_db(); cur = c.cursor()
    cur.execute("INSERT INTO sales(date,items,total) VALUES(?,?,?)",(now,summary,total))
    sid = cur.lastrowid
    for li in line_items:
        cur.execute("INSERT INTO sale_items(sale_id,product_name,quantity,subtotal) VALUES(?,?,?,?)",
                    (sid,li["name"],li["qty"],li["subtotal"]))
    c.commit(); c.close()


def get_sales_history():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT date,items,total FROM sales ORDER BY id DESC")
    rows = cur.fetchall(); c.close(); return rows


def get_top_sellers(period):
    fil = "date(sales.date)=date('now','localtime')" if period == "today" else "strftime('%Y-%m',sales.date)=strftime('%Y-%m','now','localtime')"
    q = f"""SELECT si.product_name,SUM(si.quantity) AS tq FROM sale_items si
            JOIN sales ON si.sale_id=sales.id WHERE {fil}
            GROUP BY si.product_name ORDER BY tq DESC LIMIT ?"""
    c = get_db(); cur = c.cursor(); cur.execute(q,(TOP_SELLERS_LIMIT,)); rows = cur.fetchall(); c.close(); return rows


def get_today_revenue():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE date(date)=date('now','localtime')")
    total = cur.fetchone()[0]; c.close(); return total


def get_month_revenue():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE strftime('%Y-%m',date)=strftime('%Y-%m','now','localtime')")
    total = cur.fetchone()[0]; c.close(); return total


def register_customer(name, mobile, password):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id FROM customers WHERE mobile=?",(mobile,))
    if cur.fetchone(): c.close(); return False, "Mobile already registered."
    cur.execute("INSERT INTO customers(name,mobile,password_hash,verified,created_at) VALUES(?,?,?,0,?)",
                (name,mobile,hp(password),datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit(); cid = cur.lastrowid; c.close(); return True, cid


def verify_login(mobile, password):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,name,mobile,verified FROM customers WHERE mobile=? AND password_hash=?",(mobile,hp(password)))
    row = cur.fetchone(); c.close()
    return {"id":row[0],"name":row[1],"mobile":row[2],"verified":row[3]} if row else None


def get_customer_by_mobile(mobile):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,name,mobile,verified FROM customers WHERE mobile=?",(mobile,))
    row = cur.fetchone(); c.close()
    return {"id":row[0],"name":row[1],"mobile":row[2],"verified":row[3]} if row else None


def mark_verified(mobile):
    c = get_db(); c.execute("UPDATE customers SET verified=1 WHERE mobile=?",(mobile,)); c.commit(); c.close()


def get_all_customers():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,name,mobile,verified,created_at FROM customers ORDER BY id DESC")
    rows = cur.fetchall(); c.close(); return rows


def store_otp(mobile, otp):
    exp = (datetime.now() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    c = get_db()
    c.execute("INSERT INTO otp_store(mobile,otp_hash,expires_at) VALUES(?,?,?) ON CONFLICT(mobile) DO UPDATE SET otp_hash=excluded.otp_hash,expires_at=excluded.expires_at",
              (mobile, hp(otp), exp))
    c.commit(); c.close()


def check_otp(mobile, otp):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT otp_hash,expires_at FROM otp_store WHERE mobile=?",(mobile,))
    row = cur.fetchone(); c.close()
    if not row: return False
    otp_hash, expires_at = row
    if datetime.now().strftime("%Y-%m-%d %H:%M:%S") > expires_at: return False
    return otp_hash == hp(otp)


def del_otp(mobile):
    c = get_db(); c.execute("DELETE FROM otp_store WHERE mobile=?",(mobile,)); c.commit(); c.close()


def place_order(customer_id, items, total):
    order_no = f"ORD{int(datetime.now().timestamp())}"
    date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c = get_db(); cur = c.cursor()
    cur.execute(
        "INSERT INTO orders(order_no,customer_id,date,items_json,total,status) VALUES(?,?,?,?,?,'waiting')",
        (order_no, customer_id, date, json.dumps(items), total)
    )
    for item in items:
        cur.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ?",
            (item["qty"], item["product_id"])
        )
    c.commit(); c.close()
    return order_no


def get_customer_orders(customer_id):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT order_no,date,total,status FROM orders WHERE customer_id=? ORDER BY id DESC",(customer_id,))
    rows = cur.fetchall(); c.close()
    return [
        {"order_no": row[0], "date": row[1], "total": row[2], "status": row[3]}
        for row in rows
    ]


def get_order_by_no(order_no):
    c = get_db(); cur = c.cursor()
    cur.execute(
        "SELECT order_no,customer_id,date,items_json,total,status,paid_at FROM orders WHERE order_no=?",
        (order_no,)
    )
    row = cur.fetchone(); c.close()
    if not row:
        return None
    return {
        "order_no": row[0],
        "customer_id": row[1],
        "date": row[2],
        "items": json.loads(row[3]),
        "total": row[4],
        "status": row[5],
        "paid_at": row[6],
    }


def get_order_stats():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    rows = cur.fetchall(); c.close()
    stats = {"waiting": 0, "paid": 0, "canceled": 0}
    stats.update({row[0]: row[1] for row in rows})
    return stats


def get_orders_by_status(status):
    c = get_db(); cur = c.cursor()
    if status == "all":
        cur.execute(
            "SELECT o.order_no,c.name,o.date,o.total,o.status FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.id DESC"
        )
    else:
        cur.execute(
            "SELECT o.order_no,c.name,o.date,o.total,o.status FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.status=? ORDER BY o.id DESC",
            (status,)
        )
    rows = cur.fetchall(); c.close()
    return [
        {"order_no": row[0], "customer_name": row[1], "date": row[2], "total": row[3], "status": row[4]}
        for row in rows
    ]


def search_orders(keyword):
    c = get_db(); cur = c.cursor()
    like_term = f"%{keyword}%"
    cur.execute(
        "SELECT o.order_no,c.name,o.date,o.total,o.status FROM orders o JOIN customers c ON c.id=o.customer_id "
        "WHERE o.order_no LIKE ? OR c.name LIKE ? ORDER BY o.id DESC",
        (like_term, like_term)
    )
    rows = cur.fetchall(); c.close()
    return [
        {"order_no": row[0], "customer_name": row[1], "date": row[2], "total": row[3], "status": row[4]}
        for row in rows
    ]


def place_order(cid, line_items, total):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    order_no = f"ORD-{ts}-{secrets.token_hex(2).upper()}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c = get_db()
    c.execute("INSERT INTO orders(order_no,customer_id,date,items_json,total,status) VALUES(?,?,?,?,?,?)",
              (order_no,cid,now,json.dumps(line_items),total,"waiting"))
    c.commit(); c.close()
    for item in line_items:
        product = get_product(item["product_id"])
        if product:
            update_stock_db(item["product_id"], max(0, product[3] - item["qty"]))
    return order_no


def get_customer_orders(cid):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT id,order_no,date,items_json,total,status FROM orders WHERE customer_id=? ORDER BY id DESC",(cid,))
    rows = cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"date":r[2],"items":json.loads(r[3]),"total":r[4],"status":r[5]} for r in rows]


def get_order_by_no(order_no):
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status,o.paid_at FROM orders o JOIN customers cu ON o.customer_id=cu.id WHERE o.order_no=?",(order_no,))
    row = cur.fetchone(); c.close()
    return {"id":row[0],"order_no":row[1],"customer_name":row[2],"mobile":row[3],"date":row[4],"items":json.loads(row[5]),"total":row[6],"status":row[7],"paid_at":row[8]} if row else None


def mark_order_paid(order_no):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c = get_db(); c.execute("UPDATE orders SET status='completed',paid_at=? WHERE order_no=?",(now,order_no)); c.commit(); c.close()


def get_orders_by_status(status):
    c = get_db(); cur = c.cursor()
    if status == "all":
        cur.execute("SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status FROM orders o JOIN customers cu ON o.customer_id=cu.id ORDER BY o.id DESC")
    else:
        cur.execute("SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status FROM orders o JOIN customers cu ON o.customer_id=cu.id WHERE o.status=? ORDER BY o.id DESC",(status,))
    rows = cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"customer_name":r[2],"mobile":r[3],"date":r[4],"items":json.loads(r[5]),"total":r[6],"status":r[7]} for r in rows]


def search_orders(q):
    like = f"%{q}%"
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status FROM orders o JOIN customers cu ON o.customer_id=cu.id WHERE o.order_no LIKE ? OR cu.name LIKE ? OR cu.mobile LIKE ? ORDER BY o.id DESC",(like,like,like))
    rows = cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"customer_name":r[2],"mobile":r[3],"date":r[4],"items":json.loads(r[5]),"total":r[6],"status":r[7]} for r in rows]


def get_order_stats():
    c = get_db(); cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='waiting'")
    waiting = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM customers")
    total_customers = cur.fetchone()[0]
    c.close()
    return {"waiting": waiting, "total_customers": total_customers}
