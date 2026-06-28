"""
╔══════════════════════════════════════════════════════╗
║       AURORA GROCERY POS  —  main.py                ║
║  Run:  python main.py                               ║
║                                                      ║
║  • Shows a unified Login screen first               ║
║  • Admin  → full Tkinter dashboard                  ║
║  • Customer → Tkinter window that opens the         ║
║               customer web portal in browser        ║
║  • Flask web server starts automatically in         ║
║    background (port 5000) for customer panel        ║
╚══════════════════════════════════════════════════════╝
"""

import os
import sys
import json
import random
import hashlib
import secrets
import sqlite3
import threading
import traceback
import webbrowser
import flask
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime
from functools import wraps

# ── Flask imports ──────────────────────────────────────────────
from flask import (
    Flask, render_template_string, request,
    redirect, url_for, session, jsonify, flash
)

# ── Twilio (optional — demo mode if not configured) ────────────
try:
    from twilio.rest import Client as TwilioClient
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════
# 0.  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DB_FILE         = os.path.join(BASE_DIR, "pos.db")
RECEIPTS_FOLDER = os.path.join(BASE_DIR, "receipts")

ADMIN_USERNAME      = "admin"
ADMIN_PASSWORD      = "admin123"          # ← change this
ADMIN_PASSWORD_HASH = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()

FLASK_PORT          = 5000
FLASK_SECRET        = "aurora-pos-secret-2024"

TWILIO_SID   = os.environ.get("TWILIO_ACCOUNT_SID",  "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN",    "")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE",          "")
DEMO_MODE    = not (TWILIO_AVAILABLE and TWILIO_SID and TWILIO_TOKEN and TWILIO_PHONE)

LOW_STOCK_THRESHOLD = 1
TOP_SELLERS_LIMIT   = 5

# ── Design tokens (shared by all Tkinter windows) ──────────────
C_BG      = "#F0F4F0"
C_HEADER  = "#1E6B45"
C_HTEXT   = "#FFFFFF"
C_CARD    = "#FFFFFF"
C_SIDEBAR = "#FAFCFB"
C_GREEN   = "#2A8A5A"
C_GRNDK   = "#1E6B45"
C_TEXT    = "#12211A"
C_SUB     = "#5A7265"
C_WARN    = "#C0392B"
C_BORDER  = "#D6E5DA"
C_GOLD    = "#C08B30"

FH  = ("Segoe UI", 17, "bold")   # header
FS  = ("Segoe UI", 12, "bold")   # section
FSB = ("Segoe UI",  9, "bold")   # sidebar
FB  = ("Segoe UI", 10)           # body
FSM = ("Segoe UI",  9)           # small
FR  = ("Consolas", 10)           # receipt mono
FT  = ("Segoe UI", 15, "bold")   # total

# ═══════════════════════════════════════════════════════════════
# 1.  DATABASE LAYER
# ═══════════════════════════════════════════════════════════════

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
            date  TEXT NOT NULL, items TEXT NOT NULL, total REAL NOT NULL
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
            key TEXT PRIMARY KEY, value TEXT NOT NULL
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
            [("Sugar",1.20,25,"kg"),("Rice",2.50,40,"kg"),("Cola",1.50,20,"pcs"),
             ("Milk",1.80,15,"litre"),("Flour",0.90,30,"kg"),("Oil",3.00,15,"litre"),
             ("Tea",2.20,10,"pcs"),("Bread",1.10,8,"pcs"),("Salt",0.50,20,"kg"),
             ("Eggs",2.80,50,"pcs")]
        )
        conn.commit()
    cur.execute("SELECT value FROM settings WHERE key='store_status'")
    if not cur.fetchone():
        cur.execute("INSERT INTO settings(key,value) VALUES('store_status','OPEN')")
        conn.commit()
    conn.close()

# ── Helpers ────────────────────────────────────────────────────
def fmt(qty):
    return str(int(qty)) if qty == int(qty) else f"{qty:.2f}"

def hp(pw): return hashlib.sha256(pw.encode()).hexdigest()

def audit(actor, action, detail=""):
    c = get_db()
    c.execute("INSERT INTO audit_log(timestamp,actor,action,detail) VALUES(?,?,?,?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, action, detail))
    c.commit(); c.close()

# ── Products ───────────────────────────────────────────────────
def get_all_products():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,name,price,stock,unit FROM products ORDER BY name")
    r=cur.fetchall(); c.close(); return r

def get_product(pid):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,name,price,stock,unit FROM products WHERE id=?",(pid,))
    r=cur.fetchone(); c.close(); return r

def add_product_db(name,price,stock,unit):
    c=get_db(); c.execute("INSERT INTO products(name,price,stock,unit) VALUES(?,?,?,?)",(name,price,stock,unit))
    c.commit(); c.close()

def update_stock_db(pid,stock):
    c=get_db(); c.execute("UPDATE products SET stock=? WHERE id=?",(stock,pid)); c.commit(); c.close()

def product_exists(name):
    c=get_db(); cur=c.cursor(); cur.execute("SELECT id FROM products WHERE name=?",(name,))
    r=cur.fetchone(); c.close(); return r is not None

def get_low_stock():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT name,stock,unit FROM products WHERE stock<=? ORDER BY stock",(LOW_STOCK_THRESHOLD,))
    r=cur.fetchall(); c.close(); return r

# ── Store settings ─────────────────────────────────────────────
def get_store_status():
    c=get_db(); cur=c.cursor(); cur.execute("SELECT value FROM settings WHERE key='store_status'")
    r=cur.fetchone(); c.close(); return r[0] if r else "OPEN"

def set_store_status(s):
    c=get_db()
    c.execute("INSERT INTO settings(key,value) VALUES('store_status',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(s,))
    c.commit(); c.close()

# ── Sales ──────────────────────────────────────────────────────
def save_sale(line_items, total):
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary=", ".join(f"{li['name']} x{fmt(li['qty'])}{li['unit']}" for li in line_items)
    c=get_db(); cur=c.cursor()
    cur.execute("INSERT INTO sales(date,items,total) VALUES(?,?,?)",(now,summary,total))
    sid=cur.lastrowid
    for li in line_items:
        cur.execute("INSERT INTO sale_items(sale_id,product_name,quantity,subtotal) VALUES(?,?,?,?)",
                    (sid,li["name"],li["qty"],li["subtotal"]))
    c.commit(); c.close()

def get_sales_history():
    c=get_db(); cur=c.cursor(); cur.execute("SELECT date,items,total FROM sales ORDER BY id DESC")
    r=cur.fetchall(); c.close(); return r

def get_top_sellers(period):
    fil = "date(sales.date)=date('now','localtime')" if period=="today" \
          else "strftime('%Y-%m',sales.date)=strftime('%Y-%m','now','localtime')"
    q = f"""SELECT si.product_name,SUM(si.quantity) AS tq FROM sale_items si
            JOIN sales ON si.sale_id=sales.id WHERE {fil}
            GROUP BY si.product_name ORDER BY tq DESC LIMIT ?"""
    c=get_db(); cur=c.cursor(); cur.execute(q,(TOP_SELLERS_LIMIT,)); r=cur.fetchall(); c.close(); return r

def get_today_revenue():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE date(date)=date('now','localtime')")
    r=cur.fetchone()[0]; c.close(); return r

def get_month_revenue():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE strftime('%Y-%m',date)=strftime('%Y-%m','now','localtime')")
    r=cur.fetchone()[0]; c.close(); return r

# ── Customers ──────────────────────────────────────────────────
def register_customer(name,mobile,password):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id FROM customers WHERE mobile=?",(mobile,))
    if cur.fetchone(): c.close(); return False,"Mobile already registered."
    try:
        cur.execute("INSERT INTO customers(name,mobile,password_hash,verified,created_at) VALUES(?,?,?,0,?)",
                    (name,mobile,hp(password),datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        c.commit(); cid=cur.lastrowid; c.close(); return True,cid
    except Exception as e:
        c.close(); return False,str(e)

def verify_login(mobile,password):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,name,mobile,verified FROM customers WHERE mobile=? AND password_hash=?",(mobile,hp(password)))
    r=cur.fetchone(); c.close()
    return {"id":r[0],"name":r[1],"mobile":r[2],"verified":r[3]} if r else None

def get_customer_by_mobile(mobile):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,name,mobile,verified FROM customers WHERE mobile=?",(mobile,))
    r=cur.fetchone(); c.close()
    return {"id":r[0],"name":r[1],"mobile":r[2],"verified":r[3]} if r else None

def mark_verified(mobile):
    c=get_db(); c.execute("UPDATE customers SET verified=1 WHERE mobile=?",(mobile,)); c.commit(); c.close()

def get_all_customers():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,name,mobile,verified,created_at FROM customers ORDER BY id DESC")
    r=cur.fetchall(); c.close(); return r

# ── OTP ────────────────────────────────────────────────────────
def store_otp(mobile,otp):
    from datetime import timedelta
    exp=(datetime.now()+timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    c=get_db()
    c.execute("INSERT INTO otp_store(mobile,otp_hash,expires_at) VALUES(?,?,?) ON CONFLICT(mobile) DO UPDATE SET otp_hash=excluded.otp_hash,expires_at=excluded.expires_at",
              (mobile,hp(otp),exp))
    c.commit(); c.close()

def check_otp(mobile,otp):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT otp_hash,expires_at FROM otp_store WHERE mobile=?",(mobile,))
    r=cur.fetchone(); c.close()
    if not r: return False
    oh,exp=r
    if datetime.now().strftime("%Y-%m-%d %H:%M:%S")>exp: return False
    return oh==hp(otp)

def del_otp(mobile):
    c=get_db(); c.execute("DELETE FROM otp_store WHERE mobile=?",(mobile,)); c.commit(); c.close()

# ── Sessions ───────────────────────────────────────────────────
def create_session(cid):
    from datetime import timedelta
    token=secrets.token_hex(32)
    exp=(datetime.now()+timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    c=get_db(); c.execute("INSERT INTO customer_sessions(token,customer_id,expires_at) VALUES(?,?,?)",(token,cid,exp))
    c.commit(); c.close(); return token

def get_session(token):
    if not token: return None
    c=get_db(); cur=c.cursor()
    cur.execute("""SELECT c.id,c.name,c.mobile,c.verified FROM customer_sessions cs
                   JOIN customers c ON cs.customer_id=c.id
                   WHERE cs.token=? AND cs.expires_at>datetime('now','localtime')""",(token,))
    r=cur.fetchone(); c.close()
    return {"id":r[0],"name":r[1],"mobile":r[2],"verified":r[3]} if r else None

def del_session(token):
    c=get_db(); c.execute("DELETE FROM customer_sessions WHERE token=?",(token,)); c.commit(); c.close()

# ── Orders ─────────────────────────────────────────────────────
def place_order(cid, line_items, total):
    ts=datetime.now().strftime("%Y%m%d%H%M%S"); rnd=secrets.token_hex(2).upper()
    order_no=f"ORD-{ts}-{rnd}"
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c=get_db()
    c.execute("INSERT INTO orders(order_no,customer_id,date,items_json,total,status) VALUES(?,?,?,?,?,?)",
              (order_no,cid,now,json.dumps(line_items),total,"waiting"))
    c.commit(); c.close()
    for li in line_items:
        p=get_product(li["product_id"])
        if p: update_stock_db(li["product_id"],max(0,p[3]-li["qty"]))
    return order_no

def get_orders_by_status(status):
    c=get_db(); cur=c.cursor()
    if status=="all":
        cur.execute("""SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status
                       FROM orders o JOIN customers cu ON o.customer_id=cu.id ORDER BY o.id DESC""")
    else:
        cur.execute("""SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status
                       FROM orders o JOIN customers cu ON o.customer_id=cu.id
                       WHERE o.status=? ORDER BY o.id DESC""",(status,))
    rows=cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"customer_name":r[2],"mobile":r[3],"date":r[4],
             "items":json.loads(r[5]),"total":r[6],"status":r[7]} for r in rows]

def get_customer_orders(cid):
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT id,order_no,date,items_json,total,status FROM orders WHERE customer_id=? ORDER BY id DESC",(cid,))
    rows=cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"date":r[2],"items":json.loads(r[3]),"total":r[4],"status":r[5]} for r in rows]

def get_order_by_no(order_no):
    c=get_db(); cur=c.cursor()
    cur.execute("""SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status,o.paid_at
                   FROM orders o JOIN customers cu ON o.customer_id=cu.id WHERE o.order_no=?""",(order_no,))
    r=cur.fetchone(); c.close()
    return {"id":r[0],"order_no":r[1],"customer_name":r[2],"mobile":r[3],"date":r[4],
            "items":json.loads(r[5]),"total":r[6],"status":r[7],"paid_at":r[8]} if r else None

def mark_order_paid(order_no):
    now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c=get_db(); c.execute("UPDATE orders SET status='completed',paid_at=? WHERE order_no=?",(now,order_no))
    c.commit(); c.close()

def search_orders(q):
    like=f"%{q}%"; c=get_db(); cur=c.cursor()
    cur.execute("""SELECT o.id,o.order_no,cu.name,cu.mobile,o.date,o.items_json,o.total,o.status
                   FROM orders o JOIN customers cu ON o.customer_id=cu.id
                   WHERE o.order_no LIKE ? OR cu.name LIKE ? OR cu.mobile LIKE ?
                   ORDER BY o.id DESC""",(like,like,like))
    rows=cur.fetchall(); c.close()
    return [{"id":r[0],"order_no":r[1],"customer_name":r[2],"mobile":r[3],"date":r[4],
             "items":json.loads(r[5]),"total":r[6],"status":r[7]} for r in rows]

def get_order_stats():
    c=get_db(); cur=c.cursor()
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='waiting'"); w=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM customers"); cu=cur.fetchone()[0]
    c.close(); return {"waiting":w,"total_customers":cu}

# ═══════════════════════════════════════════════════════════════
# 2.  OTP SENDER
# ═══════════════════════════════════════════════════════════════

def send_otp(mobile):
    otp=str(random.randint(100000,999999))
    store_otp(mobile,otp)
    if DEMO_MODE:
        session['demo_otp'] = otp
        print(f"\n{'='*44}\n  [DEMO OTP]  Mobile: {mobile}   OTP: {otp}\n{'='*44}\n")
    else:
        try:
            TwilioClient(TWILIO_SID,TWILIO_TOKEN).messages.create(
                body=f"Aurora Grocery OTP: {otp}  (valid 10 min)",
                from_=TWILIO_PHONE, to=mobile)
        except Exception as e:
            print(f"[Twilio error] {e}")
    return otp

# ═══════════════════════════════════════════════════════════════
# 3.  FLASK — CUSTOMER WEB PORTAL
# ═══════════════════════════════════════════════════════════════

flask_app = Flask(__name__)
flask_app.secret_key = FLASK_SECRET

@flask_app.route('/manifest.json')
def manifest():
    return {
        "name": "Aurora Grocery",
        "short_name": "Aurora",
        "start_url": "/shop",
        "display": "standalone",
        "background_color": "#F0F4F0",
        "theme_color": "#1E6B45",
        "icons": [
            {"src": "https://via.placeholder.com/192.png?text=A", "sizes": "192x192", "type": "image/png"},
            {"src": "https://via.placeholder.com/512.png?text=A", "sizes": "512x512", "type": "image/png"}
        ]
    }

@flask_app.route('/sw.js')
def service_worker():
    js = """self.addEventListener('install', event => { event.waitUntil(caches.open('aurora-cache-v1').then(cache => cache.addAll(['/','/login','/shop','/cart','/my-orders']))); });
self.addEventListener('fetch', event => { event.respondWith(fetch(event.request).catch(() => caches.match(event.request))); });"""
    return flask_app.response_class(js, mimetype='application/javascript')

# ─── Auth decorator ────────────────────────────────────────────
def customer_required(f):
    @wraps(f)
    def w(*a,**kw):
        cust=get_session(session.get("token"))
        if not cust: return redirect(url_for("web_login"))
        if not cust["verified"]: return redirect(url_for("web_verify"))
        return f(*a,customer=cust,**kw)
    return w

# ─── HTML templates (inline — no external folder needed) ───────

BASE_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1E6B45">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Aurora Grocery">
<link rel="manifest" href="/manifest.json">
<title>{% block title %}Aurora Grocery{% endblock %}</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--g:#1E6B45;--gm:#2A8A5A;--gl:#EAF4EE;--gold:#C08B30;--goldl:#FDF4E3;
  --bg:#F0F4F0;--card:#fff;--text:#12211A;--sub:#5A7265;--border:#D6E5DA;
  --warn:#C0392B;--warnl:#FDECEA;--infl:#E3F0FD;--r:12px;--sh:0 2px 12px rgba(30,107,69,.10)}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:15px;line-height:1.6}
nav{background:var(--g);color:#fff;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:58px;
  position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.2)}
.brand{font-family:'Space Grotesk',sans-serif;font-size:1.2rem;color:#fff;text-decoration:none;display:flex;align-items:center;gap:8px}
.brand span{color:#A8D8B4}
.nav-links{display:flex;align-items:center;gap:4px}
.nav-links a{color:rgba(255,255,255,.85);text-decoration:none;font-size:.88rem;font-weight:600;padding:6px 12px;border-radius:8px;transition:background .15s}
.nav-links a:hover,.nav-links a.act{background:rgba(255,255,255,.18);color:#fff}
.badge{background:var(--gold);color:#fff;border-radius:99px;font-size:.72rem;font-weight:700;padding:1px 7px;margin-left:3px}
.page{max-width:1080px;margin:0 auto;padding:28px 18px}
.page-sm{max-width:460px;margin:0 auto;padding:44px 18px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);box-shadow:var(--sh)}
.card-body{padding:26px}
.form-group{margin-bottom:16px}
label{display:block;font-size:.83rem;font-weight:700;color:var(--sub);margin-bottom:5px}
input{width:100%;border:1.5px solid var(--border);border-radius:8px;padding:10px 13px;font-size:.93rem;
  font-family:inherit;outline:none;transition:border-color .15s,box-shadow .15s;background:#fff;color:var(--text)}
input:focus{border-color:var(--gm);box-shadow:0 0 0 3px rgba(42,138,90,.15)}
.btn{display:inline-flex;align-items:center;gap:6px;padding:10px 22px;border-radius:8px;font-size:.9rem;
  font-weight:700;font-family:inherit;border:none;cursor:pointer;transition:filter .15s;text-decoration:none}
.btn:hover{filter:brightness(1.08)}
.btn-g{background:var(--g);color:#fff}
.btn-gold{background:var(--gold);color:#fff}
.btn-out{background:transparent;border:1.5px solid var(--border);color:var(--text)}
.btn-warn{background:var(--warn);color:#fff}
.btn-sm{padding:6px 13px;font-size:.81rem}
.btn-full{width:100%;justify-content:center}
.alert{padding:11px 15px;border-radius:8px;font-size:.88rem;margin-bottom:10px;border-left:4px solid}
.alert-error{background:var(--warnl);border-color:var(--warn);color:#7a1a1a}
.alert-success{background:var(--gl);border-color:var(--g);color:#0f3d22}
.alert-info{background:var(--infl);border-color:#1565C0;color:#0d3c77}
.sbadge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:.73rem;font-weight:700;text-transform:uppercase}
.sb-wait{background:var(--goldl);color:var(--gold)}
.sb-comp{background:var(--gl);color:var(--g)}
.txt-c{text-align:center}.txt-r{text-align:right}
.muted{color:var(--sub);font-size:.86rem}
.mt-8{margin-top:8px}.mt-16{margin-top:16px}.mt-24{margin-top:24px}.mb-16{margin-bottom:16px}
.flex{display:flex}.gap-8{gap:8px}.gap-12{gap:12px}.ac{align-items:center}.jb{justify-content:space-between}
hr.div{border:none;border-top:1px solid var(--border);margin:18px 0}
.auth-top{text-align:center;margin-bottom:26px}
.auth-top .ic{font-size:2.6rem;display:block;margin-bottom:5px}
.auth-top h1{font-family:'Space Grotesk',sans-serif;font-size:1.45rem;color:var(--g)}
.auth-top p{color:var(--sub);font-size:.9rem}
.ptitle{font-family:'Space Grotesk',sans-serif;font-size:1.65rem;font-weight:700;margin-bottom:4px}
.psub{color:var(--sub);margin-bottom:22px;font-size:.92rem}
</style>
{% block css %}{% endblock %}
</head><body>
<div id="pwa-install" style="display:none;position:fixed;left:16px;right:16px;bottom:16px;background:#fff;border:1px solid #d6e5da;border-radius:14px;box-shadow:0 14px 30px rgba(0,0,0,.12);padding:14px 18px;z-index:999;display:flex;justify-content:space-between;align-items:center;gap:12px;font-family:'Plus Jakarta Sans',sans-serif;">
  <div><strong>Install Aurora Grocery</strong><div style="font-size:.88rem;color:#5a7265;margin-top:4px">Use it like a mobile app from your home screen.</div></div>
  <button id="install-btn" style="background:#1E6B45;color:#fff;border:none;border-radius:10px;padding:10px 14px;font-weight:700;cursor:pointer">Install</button>
</div>
{% if nav %}
<nav>
  <a class="brand" href="/shop">🛒 Aurora <span>Grocery</span></a>
  <div class="nav-links">
    <a href="/shop" {% if req=='shop' %}class="act"{% endif %}>Shop</a>
    <a href="/cart" {% if req=='cart' %}class="act"{% endif %}>Cart <span class="badge" id="cc">{{cc}}</span></a>
    <a href="/my-orders" {% if req=='orders' %}class="act"{% endif %}>My Orders</a>
    <a href="/logout" style="color:rgba(255,255,255,.55)">Logout</a>
  </div>
</nav>
{% endif %}
{% if msgs %}
<div style="max-width:1080px;margin:14px auto 0;padding:0 18px">
  {% for cat,msg in msgs %}<div class="alert alert-{{cat}}">{{msg}}</div>{% endfor %}
</div>
{% endif %}
{% block body %}{% endblock %}
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
  }
  let deferredPrompt;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferredPrompt = e;
    document.getElementById('pwa-install').style.display = 'flex';
  });
  document.getElementById('install-btn')?.addEventListener('click', async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const choice = await deferredPrompt.userChoice;
    deferredPrompt = null;
    document.getElementById('pwa-install').style.display = 'none';
  });
</script>
</body></html>"""

LOGIN_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","Login — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","")\
    .replace("{% block body %}{% endblock %}","""
<div class="page-sm">
  <div class="auth-top"><span class="ic">🛒</span><h1>Aurora Grocery</h1><p>Sign in to your account</p></div>
  <div class="card"><div class="card-body">
    <form method="POST">
      <div class="form-group"><label>Mobile Number</label>
        <input type="tel" name="mobile" placeholder="+923001234567" required autofocus></div>
      <div class="form-group"><label>Password</label>
        <input type="password" name="password" placeholder="Your password" required></div>
      <button type="submit" class="btn btn-g btn-full mt-8">Sign In</button>
    </form>
    <hr class="div">
    <p class="txt-c muted">New customer? <a href="/register" style="color:var(--g);font-weight:700">Create account</a></p>
  </div></div>
</div>""")

REGISTER_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","Register — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","")\
    .replace("{% block body %}{% endblock %}","""
<div class="page-sm">
  <div class="auth-top"><span class="ic">🛒</span><h1>Create Account</h1><p>Register to start shopping</p></div>
  <div class="card"><div class="card-body">
    <form method="POST">
      <div class="form-group"><label>Full Name</label>
        <input type="text" name="name" placeholder="Your name" required autofocus></div>
      <div class="form-group"><label>Mobile Number <span style="font-weight:400;color:var(--sub)">(OTP sent here)</span></label>
        <input type="tel" name="mobile" placeholder="+923001234567" required></div>
      <div class="form-group"><label>Password</label>
        <input type="password" name="password" placeholder="Min. 6 characters" required></div>
      <div class="form-group"><label>Confirm Password</label>
        <input type="password" name="confirm" placeholder="Repeat password" required></div>
      <button type="submit" class="btn btn-g btn-full mt-8">Create Account & Send OTP</button>
    </form>
    <hr class="div">
    <p class="txt-c muted">Already registered? <a href="/login" style="color:var(--g);font-weight:700">Sign in</a></p>
  </div></div>
</div>""")

VERIFY_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verify OTP</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700&family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--g:#1E6B45;--bg:#F0F4F0;--border:#D6E5DA;--sub:#5A7265;--warn:#C0392B;--warnl:#FDECEA;--infl:#E3F0FD}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:#12211A;min-height:100vh;font-size:15px}
.page-sm{max-width:460px;margin:0 auto;padding:44px 18px}
.auth-top{text-align:center;margin-bottom:26px}
.auth-top .ic{font-size:2.6rem;display:block;margin-bottom:5px}
.auth-top h1{font-family:'Space Grotesk',sans-serif;font-size:1.45rem;color:var(--g)}
.auth-top p{color:var(--sub);font-size:.9rem}
.card{background:#fff;border:1px solid var(--border);border-radius:12px;box-shadow:0 2px 12px rgba(30,107,69,.10)}
.card-body{padding:26px}
.form-group{margin-bottom:16px}
label{display:block;font-size:.83rem;font-weight:700;color:var(--sub);margin-bottom:5px}
input{width:100%;border:1.5px solid var(--border);border-radius:8px;padding:10px 13px;font-size:.93rem;
  font-family:inherit;outline:none;transition:border-color .15s;background:#fff}
input:focus{border-color:#2A8A5A;box-shadow:0 0 0 3px rgba(42,138,90,.15)}
.btn{display:inline-flex;align-items:center;justify-content:center;width:100%;padding:10px 22px;border-radius:8px;
  font-size:.9rem;font-weight:700;font-family:inherit;border:none;cursor:pointer;text-decoration:none;margin-top:8px}
.btn-g{background:var(--g);color:#fff}
.btn-out{background:transparent;border:1.5px solid var(--border);color:#12211A;width:auto;padding:6px 16px;font-size:.83rem;margin-top:0}
.alert{padding:11px 15px;border-radius:8px;font-size:.88rem;margin-bottom:12px;border-left:4px solid}
.alert-error{background:var(--warnl);border-color:var(--warn);color:#7a1a1a}
.alert-info{background:var(--infl);border-color:#1565C0;color:#0d3c77}
hr{border:none;border-top:1px solid var(--border);margin:18px 0}
.muted{color:var(--sub);font-size:.86rem;text-align:center}
</style></head><body>
<div class="page-sm">
  <div class="auth-top"><span class="ic">📱</span><h1>Verify Your Number</h1>
    <p>6-digit code sent to <strong>{{mobile}}</strong></p></div>
  {% if demo %}<div class="alert alert-info"><strong>Demo Mode:</strong> Your OTP is <strong>{{demo_otp}}</strong>. Enter it here to continue.</div>{% endif %}
  {% for cat,msg in msgs %}<div class="alert alert-{{cat}}">{{msg}}</div>{% endfor %}
  <div class="card"><div class="card-body">
    <form method="POST">
      <input type="hidden" name="mobile" value="{{mobile}}">
      <div class="form-group"><label>OTP Code</label>
        <input type="text" name="otp" placeholder="123456" maxlength="6"
          style="font-size:1.5rem;letter-spacing:10px;text-align:center" required autofocus></div>
      <button type="submit" class="btn btn-g">Verify & Continue</button>
    </form>
    <hr>
    <form method="POST" action="/resend-otp" style="text-align:center">
      <input type="hidden" name="mobile" value="{{mobile}}">
      <p class="muted mb-16" style="margin-bottom:10px">Didn't receive the code?</p>
      <button type="submit" class="btn btn-out">Resend OTP</button>
    </form>
  </div></div>
</div></body></html>"""

SHOP_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","Shop — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","""
<style>
.hero{background:linear-gradient(135deg,var(--g) 0%,var(--gm) 100%);border-radius:12px;padding:24px 28px;
  color:#fff;margin-bottom:22px;display:flex;align-items:center;justify-content:space-between}
.hero h2{font-family:'Space Grotesk',sans-serif;font-size:1.35rem}
.hero p{opacity:.8;font-size:.9rem;margin-top:3px}
.spill{padding:7px 16px;border-radius:99px;font-weight:700;font-size:.83rem}
.s-open{background:rgba(255,255,255,.2);color:#fff}
.s-closed{background:#C0392B;color:#fff}
.search-wrap{position:relative;margin-bottom:20px}
.search-wrap input{padding-left:42px;height:44px;font-size:.97rem;border-radius:10px}
.search-wrap .si{position:absolute;left:13px;top:50%;transform:translateY(-50%);font-size:1.05rem;color:var(--sub)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px}
.pc{background:#fff;border:1px solid var(--border);border-radius:12px;padding:18px;
  transition:box-shadow .15s,transform .15s;position:relative}
.pc:hover{box-shadow:0 6px 22px rgba(30,107,69,.14);transform:translateY(-2px)}
.pname{font-weight:700;font-size:.97rem;margin-bottom:3px}
.pprice{color:var(--g);font-weight:700;font-size:1.08rem}
.punit{color:var(--sub);font-size:.8rem}
.pstock{font-size:.78rem;color:var(--sub);margin-top:3px}
.pstock.low{color:var(--warn);font-weight:600}
.qadd{display:flex;gap:6px;margin-top:12px;align-items:center}
.qadd input{width:74px;padding:6px 8px;text-align:center;border-radius:7px;font-size:.88rem;
  border:1.5px solid var(--border)}
.qadd button{flex:1;padding:8px 0;background:var(--g);color:#fff;border:none;border-radius:7px;
  font-weight:700;cursor:pointer;font-size:.83rem;transition:background .15s}
.qadd button:hover{background:var(--gm)}
.qadd button:disabled{background:var(--border);color:var(--sub);cursor:not-allowed}
.noq{text-align:center;padding:50px 20px;color:var(--sub)}
.noq .ni{font-size:2.3rem;display:block;margin-bottom:10px}
</style>""")\
    .replace("{% block body %}{% endblock %}","""
<div class="page">
  <div class="hero">
    <div><h2>Welcome, {{cname}}! 👋</h2><p>Browse fresh products and add to your cart.</p></div>
    <div class="spill {{'s-open' if sopen else 's-closed'}}">{{'🟢 Store Open' if sopen else '🔴 Store Closed'}}</div>
  </div>
  {% if not sopen %}<div class="alert alert-error mb-16">Store is currently <strong>closed</strong>. You can browse but cannot place orders.</div>{% endif %}
  <div class="search-wrap"><span class="si">🔍</span><input type="text" id="si" placeholder="Search products…"></div>
  <div class="grid" id="pg">
    {% for pid,name,price,stock,unit in products %}
    <div class="pc" data-n="{{name|lower}}">
      <div class="pname">{{name}}</div>
      <div class="pprice">${{'%.2f'|format(price)}} <span class="punit">/ {{unit}}</span></div>
      <div class="pstock {{'low' if stock<=1 else ''}}">
        {{'❌ Out of stock' if stock<=0 else ('⚠️ Low: '+fq(stock)+' '+unit if stock<=1 else 'Stock: '+fq(stock)+' '+unit)}}
      </div>
      {% if sopen and stock>0 %}
      <form method="POST" action="/cart/add">
        <input type="hidden" name="product_id" value="{{pid}}">
        <div class="qadd">
          <input type="number" name="qty" value="1" min="0.01" max="{{stock}}" step="0.01">
          <button type="submit">+ Add</button>
        </div>
      </form>
      {% else %}
      <div class="qadd"><button disabled style="flex:1">Unavailable</button></div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  <div class="noq" id="nq" style="display:none"><span class="ni">🔍</span><p>No products match.</p></div>
</div>
<script>
const si=document.getElementById('si'),cards=document.querySelectorAll('.pc'),nq=document.getElementById('nq');
si.addEventListener('input',()=>{const q=si.value.toLowerCase().trim();let v=0;
  cards.forEach(c=>{const m=!q||c.dataset.n.includes(q);c.style.display=m?'':'none';if(m)v++;});
  nq.style.display=v===0?'':'none';});
</script>""")

CART_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","Cart — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","""
<style>
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:.79rem;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:.4px;
  padding:11px 15px;border-bottom:1px solid var(--border)}
td{padding:13px 15px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
.qf{display:flex;gap:5px;align-items:center}
.qf input{width:68px;padding:5px 7px;text-align:center;font-size:.88rem;border-radius:7px;border:1.5px solid var(--border)}
.qf button{padding:5px 11px;background:var(--gl);color:var(--g);border:1.5px solid var(--border);
  border-radius:7px;font-weight:700;cursor:pointer;font-size:.8rem}
.rb{background:none;border:none;color:var(--warn);cursor:pointer;font-size:.95rem;padding:3px 7px;border-radius:6px}
.rb:hover{background:var(--warnl)}
.summ{max-width:360px;margin-left:auto;margin-top:22px}
.sr{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid var(--border);font-size:.91rem;color:var(--sub)}
.sr.tot{font-size:1.18rem;font-weight:700;color:var(--text);border-bottom:none;padding-top:13px}
.sr.tot span:last-child{color:var(--g)}
.empty{text-align:center;padding:55px;color:var(--sub)}
.empty .ni{font-size:2.8rem;display:block;margin-bottom:10px}
</style>""")\
    .replace("{% block body %}{% endblock %}","""
<div class="page">
  <div class="flex ac jb mb-16">
    <div><div class="ptitle">Your Cart</div><div class="psub">{{items|length}} item(s)</div></div>
    <a href="/shop" class="btn btn-out btn-sm">← Continue Shopping</a>
  </div>
  {% if items %}
  <div class="card">
    <table><thead><tr><th>Product</th><th>Price</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
    <tbody>
    {% for it in items %}
    <tr>
      <td><strong>{{it.name}}</strong></td>
      <td>${{'%.2f'|format(it.price)}}/{{it.unit}}</td>
      <td><form method="POST" action="/cart/update" class="qf">
        <input type="hidden" name="product_id" value="{{it.product_id}}">
        <input type="number" name="qty" value="{{fq(it.qty)}}" min="0.01" step="0.01">
        <button type="submit">Update</button></form></td>
      <td><strong>${{'%.2f'|format(it.subtotal)}}</strong></td>
      <td><form method="POST" action="/cart/remove">
        <input type="hidden" name="product_id" value="{{it.product_id}}">
        <button class="rb" type="submit">🗑</button></form></td>
    </tr>
    {% endfor %}
    </tbody></table>
  </div>
  <div class="summ card" style="padding:18px 22px">
    <div class="sr"><span>Subtotal</span><span>${{'%.2f'|format(total)}}</span></div>
    <div class="sr"><span>Payment</span><span>Pay at counter</span></div>
    <div class="sr tot"><span>Total</span><span>${{'%.2f'|format(total)}}</span></div>
    <form method="POST" action="/checkout" style="margin-top:14px">
      <button type="submit" class="btn btn-g btn-full">Place Order →</button>
    </form>
    <p class="muted txt-c mt-8" style="font-size:.8rem">Pay when you collect at the counter.</p>
  </div>
  {% else %}
  <div class="card"><div class="empty"><span class="ni">🛒</span>
    <p>Your cart is empty.</p><a href="/shop" class="btn btn-g mt-16">Browse Products</a>
  </div></div>
  {% endif %}
</div>""")

ORDERS_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","My Orders — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","""
<style>
.oc{background:#fff;border:1px solid var(--border);border-radius:12px;padding:18px 22px;margin-bottom:12px;
  display:flex;align-items:center;gap:18px;transition:box-shadow .15s;text-decoration:none;color:inherit}
.oc:hover{box-shadow:0 4px 18px rgba(30,107,69,.12)}
.ono{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:.93rem;color:var(--g)}
.odt{color:var(--sub);font-size:.8rem;margin-top:2px}
.empty{text-align:center;padding:55px;color:var(--sub)}
.empty .ni{font-size:2.6rem;display:block;margin-bottom:10px}
</style>""")\
    .replace("{% block body %}{% endblock %}","""
<div class="page">
  <div class="ptitle">My Orders</div><div class="psub">All your past and current orders</div>
  {% if orders %}
    {% for o in orders %}
    <a href="/my-orders/{{o.order_no}}" class="oc">
      <div><div class="ono">{{o.order_no}}</div><div class="odt">{{o.date}}</div>
        <div style="margin-top:5px"><span class="sbadge {{'sb-wait' if o.status=='waiting' else 'sb-comp'}}">{{o.status|title}}</span></div></div>
      <div style="margin-left:auto;text-align:right">
        <div style="font-size:1.1rem;font-weight:700">${{'%.2f'|format(o.total)}}</div>
        <div class="muted">{{o['items']|length}} item(s)</div>
      </div>
    </a>
    {% endfor %}
  {% else %}
  <div class="card"><div class="empty"><span class="ni">📋</span>
    <p>No orders yet.</p><a href="/shop" class="btn btn-g mt-16">Go to Shop</a>
  </div></div>
  {% endif %}
</div>""")

ORDER_DETAIL_HTML = BASE_HTML.replace("{% block title %}Aurora Grocery{% endblock %}","Order Detail — Aurora Grocery")\
    .replace("{% block css %}{% endblock %}","""
<style>
.rec{max-width:500px;margin:0 auto}
.rh{background:linear-gradient(135deg,var(--g),var(--gm));color:#fff;text-align:center;padding:26px;
  border-radius:12px 12px 0 0}
.rh .sn{font-family:'Space Grotesk',sans-serif;font-size:1.3rem;font-weight:700;margin-bottom:3px}
.rh .on{font-size:.93rem;opacity:.85;background:rgba(255,255,255,.15);
  display:inline-block;padding:3px 15px;border-radius:99px;margin-top:6px;letter-spacing:.8px}
.rb2{padding:22px}
.mr{display:flex;justify-content:space-between;font-size:.87rem;padding:6px 0;
  border-bottom:1px solid var(--border);color:var(--sub)}
.mr span:last-child{color:#12211A;font-weight:600}
.ir{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px dashed var(--border);font-size:.91rem}
.ir:last-child{border-bottom:none}
.iname{font-weight:600}.iqty{color:var(--sub);font-size:.81rem;margin-top:1px}
.isub{font-weight:700;color:var(--g)}
.tb{background:var(--gl);border-radius:10px;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;margin-top:14px}
.tb .lbl{font-weight:600;color:var(--sub)}.tb .amt{font-size:1.45rem;font-weight:700;color:var(--g)}
.stamp{border:3px solid var(--g);color:var(--g);border-radius:7px;padding:5px 22px;
  font-family:'Space Grotesk',sans-serif;font-size:1.05rem;font-weight:700;transform:rotate(-6deg);
  display:inline-block;margin-bottom:10px;letter-spacing:2px}
.wn{background:var(--goldl);border:1px solid var(--gold);border-radius:10px;padding:14px;
  font-size:.88rem;color:#7a5800;margin-top:14px;text-align:center}
</style>""")\
    .replace("{% block body %}{% endblock %}","""
<div class="page">
  <div style="margin-bottom:14px"><a href="/my-orders" class="btn btn-out btn-sm">← All Orders</a></div>
  <div class="rec card">
    <div class="rh">
      <div class="sn">🛒 Aurora Grocery</div>
      <div class="on">{{o.order_no}}</div>
      {% if o.status=='completed' %}<div style="margin-top:10px"><span class="stamp">✓ PAID</span></div>{% endif %}
    </div>
    <div class="rb2">
      <div class="mr"><span>Customer</span><span>{{o.customer_name}}</span></div>
      <div class="mr"><span>Mobile</span><span>{{o.mobile}}</span></div>
      <div class="mr"><span>Date</span><span>{{o.date}}</span></div>
      <div class="mr"><span>Status</span><span><span class="sbadge {{'sb-wait' if o.status=='waiting' else 'sb-comp'}}">{{o.status|title}}</span></span></div>
      {% if o.paid_at %}<div class="mr"><span>Paid At</span><span>{{o.paid_at}}</span></div>{% endif %}
      <div style="margin:16px 0 10px;font-weight:700;font-size:.85rem;color:var(--sub);text-transform:uppercase">Items</div>
      {% for it in o['items'] %}
      <div class="ir">
        <div><div class="iname">{{it.name}}</div>
          <div class="iqty">{{fq(it.qty)}} {{it.unit}} × ${{ '%.2f'|format(it.price) }}</div></div>
        <div class="isub">${{'%.2f'|format(it.subtotal)}}</div>
      </div>
      {% endfor %}
      <div class="tb"><span class="lbl">Total Amount</span><span class="amt">${{'%.2f'|format(o.total)}}</span></div>
      {% if o.status=='waiting' %}
      <div class="wn">⏳ <strong>Order is waiting.</strong><br>
        Show order number <strong>{{o.order_no}}</strong> at the counter. Pay & collect your groceries.</div>
      {% endif %}
      <div style="margin-top:16px;display:flex;gap:8px">
        <a href="/shop" class="btn btn-out btn-sm">Continue Shopping</a>
        <button onclick="window.print()" class="btn btn-g btn-sm">🖨 Print</button>
      </div>
    </div>
  </div>
</div>""")

def render_base(template, **ctx):
    msgs = [(c,m) for c,m in session.get("_flashes",[]) if True]
    session["_flashes"] = []
    ctx.setdefault("nav", False)
    ctx.setdefault("msgs", msgs)
    ctx.setdefault("cc", len(session.get("cart", {})))
    ctx.setdefault("req", "")
    ctx.setdefault("fq", fmt)
    return render_template_string(template, **ctx)

def fl(msg, cat="info"):
    flashes = session.get("_flashes", [])
    flashes.append((cat, msg))
    session["_flashes"] = flashes

# ─── Flask Routes ──────────────────────────────────────────────

@flask_app.route("/")
def web_index():
    cust = get_session(session.get("token"))
    if cust and cust["verified"]: return redirect(url_for("web_shop"))
    return redirect(url_for("web_login"))

@flask_app.route("/register", methods=["GET","POST"])
def web_register():
    if request.method=="POST":
        name=request.form.get("name","").strip()
        mobile=request.form.get("mobile","").strip()
        pw=request.form.get("password","")
        cf=request.form.get("confirm","")
        if not all([name,mobile,pw]):
            fl("All fields required.","error")
        elif pw!=cf:
            fl("Passwords do not match.","error")
        elif len(pw)<6:
            fl("Password must be at least 6 characters.","error")
        else:
            ok,res=register_customer(name,mobile,pw)
            if not ok:
                fl(res,"error")
            else:
                otp=send_otp(mobile)
                session["pending_mobile"]=mobile
                msg=f"OTP sent to {mobile}."+(f" [DEMO: {otp}]" if DEMO_MODE else "")
                fl(msg,"info")
                audit("customer","register",f"name={name} mobile={mobile}")
                return redirect(url_for("web_verify"))
    return render_base(REGISTER_HTML)

@flask_app.route("/login", methods=["GET","POST"])
def web_login():
    if request.method=="POST":
        mobile=request.form.get("mobile","").strip()
        pw=request.form.get("password","")
        cust=verify_login(mobile,pw)
        if not cust:
            fl("Incorrect mobile or password.","error")
        elif not cust["verified"]:
            session["pending_mobile"]=mobile
            otp=send_otp(mobile)
            fl("Please verify your number."+(f" [DEMO OTP: {otp}]" if DEMO_MODE else ""),"info")
            return redirect(url_for("web_verify"))
        else:
            session["token"]=create_session(cust["id"])
            audit("customer","login",f"mobile={mobile}")
            return redirect(url_for("web_shop"))
    return render_base(LOGIN_HTML)

@flask_app.route("/verify-otp", methods=["GET","POST"])
def web_verify():
    mobile=session.get("pending_mobile") or request.form.get("mobile","")
    demo_otp = session.get('demo_otp') if DEMO_MODE else None
    msgs=[]
    if not mobile: return redirect(url_for("web_register"))
    if request.method=="POST":
        otp=request.form.get("otp","").strip()
        if check_otp(mobile,otp):
            del_otp(mobile)
            mark_verified(mobile)
            c=get_customer_by_mobile(mobile)
            session["token"]=create_session(c["id"])
            session.pop("pending_mobile",None)
            session.pop('demo_otp', None)
            fl(f"Welcome, {c['name']}! You're verified.","success")
            return redirect(url_for("web_shop"))
        else:
            msgs=[("error","Invalid or expired OTP.")]
    return render_template_string(VERIFY_HTML,mobile=mobile,demo=DEMO_MODE,demo_otp=demo_otp,msgs=msgs)

@flask_app.route("/resend-otp", methods=["POST"])
def web_resend():
    mobile=session.get("pending_mobile") or request.form.get("mobile","")
    if mobile:
        otp=send_otp(mobile)
        fl("OTP resent."+(f" [DEMO: {otp}]" if DEMO_MODE else ""),"info")
    return redirect(url_for("web_verify"))

@flask_app.route("/logout")
def web_logout():
    tok=session.pop("token",None)
    if tok: del_session(tok)
    return redirect(url_for("web_login"))

@flask_app.route("/shop")
@customer_required
def web_shop(customer):
    prods=get_all_products()
    sopen=get_store_status()=="OPEN"
    return render_base(SHOP_HTML,nav=True,req="shop",
                       cname=customer["name"],products=prods,sopen=sopen)

@flask_app.route("/cart")
@customer_required
def web_cart(customer):
    cart=session.get("cart",{}); items=[]; total=0.0
    for pid_s,qty in cart.items():
        p=get_product(int(pid_s))
        if p:
            sub=p[2]*qty; total+=sub
            items.append({"product_id":p[0],"name":p[1],"price":p[2],"qty":qty,"unit":p[4],"subtotal":sub})
    return render_base(CART_HTML,nav=True,req="cart",items=items,total=total)

@flask_app.route("/cart/add", methods=["POST"])
@customer_required
def web_cart_add(customer):
    pid=int(request.form.get("product_id")); qty=float(request.form.get("qty",1))
    p=get_product(pid)
    if not p: fl("Product not found.","error"); return redirect(url_for("web_shop"))
    if qty<=0 or qty>p[3]: fl(f"Invalid qty. Available: {fmt(p[3])} {p[4]}","error"); return redirect(url_for("web_shop"))
    cart=session.get("cart",{}); pkey=str(pid); cart[pkey]=cart.get(pkey,0)+qty
    session["cart"]=cart; fl(f"Added {fmt(qty)} {p[4]} of {p[1]}.","success")
    return redirect(url_for("web_shop"))

@flask_app.route("/cart/remove", methods=["POST"])
@customer_required
def web_cart_remove(customer):
    cart=session.get("cart",{}); cart.pop(str(request.form.get("product_id")),None)
    session["cart"]=cart; return redirect(url_for("web_cart"))

@flask_app.route("/cart/update", methods=["POST"])
@customer_required
def web_cart_update(customer):
    pid=str(request.form.get("product_id")); qty=float(request.form.get("qty",1))
    cart=session.get("cart",{})
    if qty<=0: cart.pop(pid,None)
    else: cart[pid]=qty
    session["cart"]=cart; return redirect(url_for("web_cart"))

@flask_app.route("/checkout", methods=["POST"])
@customer_required
def web_checkout(customer):
    try:
        cart=session.get("cart",{})
        if not cart: fl("Cart is empty.","error"); return redirect(url_for("web_shop"))
        if get_store_status()!="OPEN": fl("Store is closed.","error"); return redirect(url_for("web_shop"))
        line_items=[]; total=0.0
        for pid_s,qty in cart.items():
            p=get_product(int(pid_s))
            if not p: continue
            if qty>p[3]: fl(f"Only {fmt(p[3])} {p[4]} of {p[1]} left.","error"); return redirect(url_for("web_cart"))
            sub=p[2]*qty; total+=sub
            line_items.append({"product_id":p[0],"name":p[1],"qty":qty,"unit":p[4],"price":p[2],"subtotal":sub})
        order_no=place_order(customer["id"],line_items,total)
        session["cart"]={}
        audit("customer","place_order",f"order={order_no} total={total:.2f}")
        fl(f"Order placed! Your number: {order_no}","success")
        return redirect(url_for("web_order_detail",order_no=order_no))
    except Exception as e:
        print("[ERROR] web_checkout failed:")
        traceback.print_exc()
        fl("Unable to place order. Please try again.","error")
        return redirect(url_for("web_cart"))

@flask_app.route("/my-orders")
@customer_required
def web_my_orders(customer):
    orders=get_customer_orders(customer["id"])
    return render_base(ORDERS_HTML,nav=True,req="orders",orders=orders)

@flask_app.route("/my-orders/<order_no>")
@customer_required
def web_order_detail(customer,order_no):
    order=get_order_by_no(order_no)
    if not order: fl("Order not found.","error"); return redirect(url_for("web_my_orders"))
    mine=[o["order_no"] for o in get_customer_orders(customer["id"])]
    if order_no not in mine: fl("Access denied.","error"); return redirect(url_for("web_my_orders"))
    return render_base(ORDER_DETAIL_HTML,nav=True,req="orders",o=order)

def run_flask():
    import logging
    log=logging.getLogger("werkzeug"); log.setLevel(logging.ERROR)
    flask_app.run(port=FLASK_PORT, debug=False, use_reloader=False)

# ═══════════════════════════════════════════════════════════════
# 4.  TKINTER — SHARED LOGIN SCREEN
# ═══════════════════════════════════════════════════════════════

root = tk.Tk()
root.title("Aurora Grocery POS")
root.geometry("480x560")
root.resizable(False, False)
root.configure(bg=C_BG)

# Center window
root.update_idletasks()
sw=root.winfo_screenwidth(); sh=root.winfo_screenheight()
root.geometry(f"480x560+{(sw-480)//2}+{(sh-560)//2}")

style = ttk.Style(root)
style.theme_use("clam")
style.configure("Accent.TButton", background=C_GREEN, foreground="white",
                font=("Segoe UI",11,"bold"), padding=12, borderwidth=0)
style.map("Accent.TButton", background=[("active",C_GRNDK)])
style.configure("Gold.TButton", background=C_GOLD, foreground="white",
                font=("Segoe UI",11,"bold"), padding=12, borderwidth=0)
style.configure("Outline.TButton", background=C_CARD, foreground=C_TEXT,
                font=FB, padding=9, borderwidth=1)
style.map("Outline.TButton", background=[("active",C_BORDER)])
style.configure("TEntry", padding=7, font=FB)


def build_login_screen():
    """The unified login screen shown at startup."""
    for w in root.winfo_children():
        w.destroy()

    # ── Header band ──────────────────────────────
    hdr = tk.Frame(root, bg=C_HEADER, height=120)
    hdr.pack(fill="x")
    hdr.pack_propagate(False)
    tk.Label(hdr, text="🛒", bg=C_HEADER, font=("Segoe UI",32)).pack(pady=(18,2))
    tk.Label(hdr, text="Aurora Grocery POS",
             bg=C_HEADER, fg=C_HTEXT, font=("Segoe UI",15,"bold")).pack()

    # ── Role selector tabs ────────────────────────
    tab_frame = tk.Frame(root, bg=C_BG)
    tab_frame.pack(fill="x", padx=30, pady=(24,0))

    role_var = tk.StringVar(value="admin")

    def make_role_btn(parent, label, value, icon):
        btn = tk.Button(
            parent, text=f"{icon}\n{label}",
            font=("Segoe UI",10,"bold"), borderwidth=0,
            cursor="hand2", relief="flat",
            command=lambda: [role_var.set(value), update_tabs()]
        )
        btn.pack(side="left", expand=True, fill="x", padx=4)
        return btn

    admin_btn = make_role_btn(tab_frame, "Admin / Shopkeeper", "admin", "🔐")
    cust_btn  = make_role_btn(tab_frame, "Customer Portal", "customer", "🛍")

    def update_tabs():
        v = role_var.get()
        admin_btn.config(
            bg=C_GRNDK if v=="admin" else C_BORDER,
            fg="white" if v=="admin" else C_SUB
        )
        cust_btn.config(
            bg=C_GOLD if v=="customer" else C_BORDER,
            fg="white" if v=="customer" else C_SUB
        )
        # Show/hide username field
        if v=="admin":
            uname_frame.pack(fill="x", pady=(0,14))
        else:
            uname_frame.pack_forget()

        login_btn.config(
            text="🔐  Login as Admin" if v=="admin" else "🛍  Open Customer Portal",
            style="Accent.TButton" if v=="admin" else "Gold.TButton"
        )
        title_lbl.config(text="Admin Login" if v=="admin" else "Customer Portal")
        hint_lbl.config(
            text="Use your shopkeeper credentials." if v=="admin"
            else "Opens the customer web portal in your browser.\nCustomers register/login there."
        )

    # ── Login card ────────────────────────────────
    card = tk.Frame(root, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
    card.pack(fill="x", padx=30, pady=(14,0))
    inner = tk.Frame(card, bg=C_CARD)
    inner.pack(fill="x", padx=24, pady=20)

    title_lbl = tk.Label(inner, text="Admin Login", bg=C_CARD, fg=C_TEXT, font=FS)
    title_lbl.pack(anchor="w", pady=(0,14))

    # Username (admin only)
    uname_frame = tk.Frame(inner, bg=C_CARD)
    tk.Label(uname_frame, text="Username", bg=C_CARD, fg=C_SUB, font=FSM).pack(anchor="w")
    username_entry = ttk.Entry(uname_frame, width=32)
    username_entry.pack(fill="x", pady=(3,0))
    username_entry.insert(0, ADMIN_USERNAME)
    uname_frame.pack(fill="x", pady=(0,14))

    # Password
    tk.Label(inner, text="Password", bg=C_CARD, fg=C_SUB, font=FSM).pack(anchor="w")
    password_entry = ttk.Entry(inner, width=32, show="★")
    password_entry.pack(fill="x", pady=(3,0))
    password_entry.focus()

    err_lbl = tk.Label(inner, text="", bg=C_CARD, fg=C_WARN, font=FSM)
    err_lbl.pack(anchor="w", pady=(6,0))

    hint_lbl = tk.Label(inner, text="Use your shopkeeper credentials.",
                        bg=C_CARD, fg=C_SUB, font=("Segoe UI",8),
                        wraplength=380, justify="left")
    hint_lbl.pack(anchor="w", pady=(4,0))

    login_btn = ttk.Button(inner, text="🔐  Login as Admin",
                           style="Accent.TButton", command=lambda: do_login())
    login_btn.pack(fill="x", pady=(16,0))

    hint2 = tk.Label(root, text="Customer web portal runs at  http://localhost:5000",
                     bg=C_BG, fg=C_SUB, font=("Segoe UI",8))
    hint2.pack(pady=(12,0))

    # ── Demo mode badge ───────────────────────────
    if DEMO_MODE:
        demo_lbl = tk.Label(root,
            text="⚠  Demo Mode — OTPs print to console  (set Twilio env vars for real SMS)",
            bg="#FDF4E3", fg=C_GOLD, font=("Segoe UI",8,"bold"), pady=4)
        demo_lbl.pack(fill="x", padx=30, pady=(8,0))

    def do_login():
        role = role_var.get()

        if role == "admin":
            h = hashlib.sha256(password_entry.get().encode()).hexdigest()
            if username_entry.get().strip()==ADMIN_USERNAME and h==ADMIN_PASSWORD_HASH:
                audit("admin","login","")
                build_admin_dashboard()
            else:
                err_lbl.config(text="Incorrect username or password.")
                password_entry.delete(0, tk.END)
                password_entry.focus()

        else:  # customer
            # Just open browser — web portal handles auth
            webbrowser.open(f"http://localhost:{FLASK_PORT}/login")
            err_lbl.config(text="")
            messagebox.showinfo(
                "Customer Portal",
                f"Customer portal opened in your browser.\n\n"
                f"URL: http://localhost:{FLASK_PORT}\n\n"
                f"Customers can register, browse, and place orders there.",
                parent=root
            )

    root.bind("<Return>", lambda e: do_login())


# ═══════════════════════════════════════════════════════════════
# 5.  TKINTER — ADMIN DASHBOARD
# ═══════════════════════════════════════════════════════════════

def build_admin_dashboard():
    """Tear down the login screen and build the full admin UI."""
    for w in root.winfo_children():
        w.destroy()
    root.geometry("1340x740")
    root.resizable(True, True)
    root.minsize(1100,620)
    root.title("Aurora Grocery — Admin Dashboard")

    # re-apply styles for larger window
    style.configure("Treeview", background=C_CARD, fieldbackground=C_CARD,
                    foreground=C_TEXT, rowheight=28, font=FB, borderwidth=0)
    style.configure("Treeview.Heading", background=C_CARD, foreground=C_SUB,
                    font=("Segoe UI",9,"bold"), borderwidth=0)
    style.map("Treeview", background=[("selected",C_GREEN)], foreground=[("selected","white")])
    style.configure("TNotebook", background=C_BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=[13,8], font=("Segoe UI",10,"bold"),
                    background=C_BORDER, foreground=C_SUB)
    style.map("TNotebook.Tab", background=[("selected",C_CARD)], foreground=[("selected",C_GREEN)])

    cart      = {}
    cart_names= {}

    # ── Header ───────────────────────────────────
    hdr = tk.Frame(root, bg=C_HEADER, height=56)
    hdr.pack(side="top", fill="x")
    tk.Label(hdr, text="🛒  Aurora Grocery — Admin",
             bg=C_HEADER, fg=C_HTEXT, font=FH).pack(side="left", padx=18, pady=10)

    clock_lbl = tk.Label(hdr, bg=C_HEADER, fg="#A8D8B4", font=FB)
    clock_lbl.pack(side="right", padx=18)

    ss_lbl = tk.Label(hdr, bg=C_HEADER, font=("Segoe UI",10,"bold"))
    ss_lbl.pack(side="right", padx=(0,12))

    ss_btn = tk.Button(hdr, bg="#2D5A3D", fg="white", activebackground="#3A7A52",
                       font=("Segoe UI",9,"bold"), borderwidth=0, padx=12, pady=4)
    ss_btn.pack(side="right", padx=(0,6))

    order_alert = tk.Label(hdr, bg=C_GOLD, fg="white", font=("Segoe UI",9,"bold"), padx=12, pady=4)

    logout_btn = tk.Button(hdr, text="⬅ Logout", bg="#2D5A3D", fg="white",
                           activebackground="#3A7A52", font=("Segoe UI",9,"bold"),
                           borderwidth=0, padx=12, pady=4,
                           command=lambda: [root.geometry("480x560"), build_login_screen()])
    logout_btn.pack(side="right", padx=(0,6))

    def upd_ss():
        s = get_store_status()
        if s=="OPEN":
            ss_lbl.config(text="🟢 STORE OPEN", fg="#66BB6A")
            ss_btn.config(text="Close Store")
        else:
            ss_lbl.config(text="🔴 STORE CLOSED", fg="#EF9A9A")
            ss_btn.config(text="Open Store")

    def toggle_ss():
        new = "CLOSED" if get_store_status()=="OPEN" else "OPEN"
        set_store_status(new); upd_ss(); audit("admin","store_status",new)

    ss_btn.config(command=toggle_ss)

    def tick():
        clock_lbl.config(text=datetime.now().strftime("%a, %d %b %Y   %I:%M:%S %p"))
        root.after(1000, tick)

    # ── Body ─────────────────────────────────────
    body = tk.Frame(root, bg=C_BG)
    body.pack(fill="both", expand=True, padx=13, pady=13)

    # Sidebar
    sb = tk.Frame(body, bg=C_SIDEBAR, highlightbackground=C_BORDER, highlightthickness=1, width=218)
    sb.pack(side="left", fill="y", padx=(0,11)); sb.pack_propagate(False)

    def sb_sec(title, icon, h=4):
        w=tk.Frame(sb,bg=C_SIDEBAR); w.pack(fill="x",padx=11,pady=(11,3))
        tk.Label(w,text=f"{icon}  {title}",bg=C_SIDEBAR,fg=C_TEXT,font=FSB).pack(anchor="w")
        lb=tk.Listbox(w,bg=C_CARD,fg=C_TEXT,font=FSM,borderwidth=0,
                      highlightthickness=1,highlightbackground=C_BORDER,height=h,activestyle="none")
        lb.pack(fill="x",pady=(4,0)); return lb

    sf = tk.Frame(sb,bg=C_SIDEBAR); sf.pack(fill="x",padx=11,pady=(13,0))
    tk.Label(sf,text="📊  Today's Revenue",bg=C_SIDEBAR,fg=C_TEXT,font=FSB).pack(anchor="w")
    rev_today=tk.Label(sf,text="$0.00",bg=C_SIDEBAR,fg=C_GREEN,font=("Segoe UI",15,"bold"))
    rev_today.pack(anchor="w",pady=(1,0))
    tk.Label(sf,text="This Month",bg=C_SIDEBAR,fg=C_SUB,font=FSM).pack(anchor="w",pady=(7,0))
    rev_month=tk.Label(sf,text="$0.00",bg=C_SIDEBAR,fg=C_GOLD,font=("Segoe UI",12,"bold"))
    rev_month.pack(anchor="w",pady=(1,0))
    tk.Label(sf,text="Waiting Orders",bg=C_SIDEBAR,fg=C_SUB,font=FSM).pack(anchor="w",pady=(7,0))
    wait_lbl=tk.Label(sf,text="0",bg=C_SIDEBAR,fg=C_WARN,font=("Segoe UI",20,"bold"))
    wait_lbl.pack(anchor="w")

    ls_box=sb_sec("Low Stock","⚠",4)
    td_box=sb_sec("Top Sellers Today","🔥",4)
    mo_box=sb_sec("Top Sellers Month","📅",4)

    # Notebook
    nb = ttk.Notebook(body); nb.pack(side="left",fill="both",expand=True)
    def mtab(lbl):
        f=tk.Frame(nb,bg=C_CARD); nb.add(f,text=f"  {lbl}  "); return f
    t_pos    = mtab("🛒 Counter Sale")
    t_orders = mtab("📋 Online Orders")
    t_hist   = mtab("🧾 Sales History")
    t_cust   = mtab("👤 Customers")
    t_inv    = mtab("📦 Inventory")

    # ── Tab 1: Counter Sale ───────────────────────
    tp = tk.Frame(t_pos,bg=C_CARD); tp.pack(fill="x",padx=17,pady=(13,5))
    tk.Label(tp,text="Products",bg=C_CARD,fg=C_TEXT,font=FS).pack(side="left")
    sv=tk.StringVar()
    se=ttk.Entry(tp,textvariable=sv,width=21); se.pack(side="right")
    tk.Label(tp,text="🔍",bg=C_CARD,font=FB).pack(side="right",padx=(0,3))

    pn=tk.Frame(t_pos,bg=C_CARD); pn.pack(fill="both",expand=True,padx=17,pady=(0,13))
    pc=("name","price","stock")
    pt=ttk.Treeview(pn,columns=pc,show="headings",selectmode="browse")
    for col,hdr_t,w in [("name","PRODUCT",200),("price","PRICE/UNIT",115),("stock","STOCK",115)]:
        pt.heading(col,text=hdr_t); pt.column(col,anchor="center" if col!="name" else "w",width=w)
    pt.tag_configure("low",foreground=C_WARN); pt.pack(side="left",fill="both",expand=True)

    cp=tk.Frame(pn,bg=C_CARD,width=285); cp.pack(side="right",fill="y",padx=(13,0)); cp.pack_propagate(False)
    tk.Label(cp,text="Current Sale",bg=C_CARD,fg=C_TEXT,font=FS).pack(anchor="w",pady=(0,7))
    cl=tk.Listbox(cp,bg=C_CARD,fg=C_TEXT,font=FR,borderwidth=0,
                  highlightthickness=1,highlightbackground=C_BORDER,selectbackground=C_GREEN)
    cl.pack(fill="both",expand=True)
    tr=tk.Frame(cp,bg=C_CARD); tr.pack(fill="x",pady=(7,3))
    tk.Label(tr,text="TOTAL",bg=C_CARD,fg=C_SUB,font=FSM).pack(side="left")
    total_lbl=tk.Label(tr,text="$0.00",bg=C_CARD,fg=C_GREEN,font=FT); total_lbl.pack(side="right")
    cf2=tk.Frame(cp,bg=C_CARD); cf2.pack(fill="x",pady=(3,0))
    ptb=tk.Frame(t_pos,bg=C_CARD); ptb.pack(fill="x",padx=17,pady=(0,13))

    # ── Tab 2: Online Orders ──────────────────────
    ot=tk.Frame(t_orders,bg=C_CARD); ot.pack(fill="x",padx=17,pady=(13,7))
    tk.Label(ot,text="Online Orders Queue",bg=C_CARD,fg=C_TEXT,font=FS).pack(side="left")
    tk.Label(ot,text="⟳ auto-refresh 5s",bg=C_CARD,fg=C_SUB,font=FSM).pack(side="left",padx=8)
    sov=tk.StringVar()
    ttk.Entry(ot,textvariable=sov,width=23).pack(side="right")
    tk.Label(ot,text="🔍",bg=C_CARD,font=FSM).pack(side="right",padx=(0,3))
    fv=tk.StringVar(value="waiting")
    ff=tk.Frame(t_orders,bg=C_CARD); ff.pack(fill="x",padx=17,pady=(0,7))

    oc=("order_no","customer","mobile","date","items","total","status")
    ordt=ttk.Treeview(t_orders,columns=oc,show="headings",selectmode="browse")
    for col,hdr_t,w in [("order_no","ORDER NO",160),("customer","CUSTOMER",130),("mobile","MOBILE",120),
                        ("date","DATE/TIME",148),("items","ITEMS",60),("total","TOTAL",85),("status","STATUS",85)]:
        ordt.heading(col,text=hdr_t); ordt.column(col,anchor="center" if col!="order_no" else "w",width=w)
    ordt.tag_configure("waiting",foreground=C_GOLD)
    ordt.tag_configure("completed",foreground=C_GREEN)
    ordt.pack(fill="both",expand=True,padx=17,pady=(0,7))
    ordtb=tk.Frame(t_orders,bg=C_CARD); ordtb.pack(fill="x",padx=17,pady=(0,13))

    for lbl,val in [("Waiting","waiting"),("Completed","completed"),("All","all")]:
        tk.Radiobutton(ff,text=lbl,variable=fv,value=val,bg=C_CARD,fg=C_TEXT,font=FB,
                       selectcolor=C_CARD,activebackground=C_CARD,
                       command=lambda: rf_orders()).pack(side="left",padx=(0,14))

    # ── Tab 3: History ────────────────────────────
    hc=("date","items","total")
    ht=ttk.Treeview(t_hist,columns=hc,show="headings")
    ht.heading("date",text="DATE"); ht.column("date",width=155)
    ht.heading("items",text="ITEMS"); ht.column("items",width=360)
    ht.heading("total",text="TOTAL"); ht.column("total",width=90,anchor="center")
    ht.pack(fill="both",expand=True,padx=17,pady=17)

    # ── Tab 4: Customers ──────────────────────────
    cc2=("id","name","mobile","verified","joined")
    ct=ttk.Treeview(t_cust,columns=cc2,show="headings")
    for col,hdr_t,w in [("id","ID",50),("name","NAME",180),("mobile","MOBILE",140),
                        ("verified","VERIFIED",90),("joined","JOINED",160)]:
        ct.heading(col,text=hdr_t); ct.column(col,anchor="center" if col!="name" else "w",width=w)
    ct.tag_configure("unverified",foreground=C_WARN)
    ct.pack(fill="both",expand=True,padx=17,pady=17)

    # ── Tab 5: Inventory ──────────────────────────
    iv=("id","name","price","stock","unit")
    invt=ttk.Treeview(t_inv,columns=iv,show="headings")
    for col,hdr_t,w in [("id","ID",50),("name","PRODUCT",200),("price","PRICE",110),
                        ("stock","STOCK",130),("unit","UNIT",80)]:
        invt.heading(col,text=hdr_t); invt.column(col,anchor="center" if col!="name" else "w",width=w)
    invt.tag_configure("low",foreground=C_WARN)
    invt.pack(fill="both",expand=True,padx=17,pady=(14,7))
    invtb=tk.Frame(t_inv,bg=C_CARD); invtb.pack(fill="x",padx=17,pady=(0,13))

    # ── Receipt generator ─────────────────────────
    def gen_receipt(line_items, total, order_no=None, cname=None, paid=False):
        os.makedirs(RECEIPTS_FOLDER, exist_ok=True)
        now=datetime.now(); fn=os.path.join(RECEIPTS_FOLDER,f"receipt_{now.strftime('%Y%m%d_%H%M%S')}.txt")
        lines=["==============================","       AURORA GROCERY","==============================",
               f"Date: {now.strftime('%Y-%m-%d %H:%M:%S')}"]
        if order_no: lines.append(f"Order: {order_no}")
        if cname:    lines.append(f"Customer: {cname}")
        if paid:     lines.append("*** PAID ***")
        lines.append("------------------------------")
        for li in line_items:
            lines.append(f"{li['name']:<14} {fmt(li['qty'])} {li['unit']:>8}  ${li['subtotal']:.2f}")
        lines+=["------------------------------",f"TOTAL: ${total:.2f}","==============================","  Thank you for shopping!"]
        open(fn,"w").write("\n".join(lines)); return fn

    # ── Refresh functions ─────────────────────────
    def rf_products():
        q=sv.get().lower().strip()
        for r in pt.get_children(): pt.delete(r)
        for pid,name,price,stock,unit in get_all_products():
            if q and q not in name.lower(): continue
            tags=("low",) if stock<=LOW_STOCK_THRESHOLD else ()
            pt.insert("","end",iid=str(pid),
                      values=(name,f"${price:.2f}/{unit}",f"{fmt(stock)} {unit}"),tags=tags)

    def rf_cart():
        cl.delete(0,tk.END); total=0
        ap={p[0]:p for p in get_all_products()}
        for pid,qty in cart.items():
            if pid not in ap: continue
            _,name,price,stock,unit=ap[pid]; sub=price*qty; total+=sub
            cl.insert(tk.END,f"{name[:12]:<12} {fmt(qty)}{unit:<6}  ${sub:>7.2f}")
        total_lbl.config(text=f"${total:.2f}"); return total

    def rf_orders():
        for r in ordt.get_children(): ordt.delete(r)
        q=sov.get().strip()
        rows=search_orders(q) if q else get_orders_by_status(fv.get())
        for o in rows:
            ordt.insert("","end",iid=o["order_no"],
                        values=(o["order_no"],o["customer_name"][:17],o["mobile"],o["date"],
                                f"{len(o['items'])} item(s)",f"${o['total']:.2f}",o["status"].upper()),
                        tags=(o["status"],))

    def rf_history():
        for r in ht.get_children(): ht.delete(r)
        for date,items,total in get_sales_history():
            ht.insert("","end",values=(date,items,f"${total:.2f}"))

    def rf_customers():
        for r in ct.get_children(): ct.delete(r)
        for cid,name,mobile,verified,created in get_all_customers():
            ct.insert("","end",values=(cid,name,mobile,"✓ Yes" if verified else "✗ No",created),
                      tags=() if verified else ("unverified",))

    def rf_inventory():
        for r in invt.get_children(): invt.delete(r)
        for pid,name,price,stock,unit in get_all_products():
            tags=("low",) if stock<=LOW_STOCK_THRESHOLD else ()
            invt.insert("","end",iid=str(pid),
                        values=(pid,name,f"${price:.2f}",f"{fmt(stock)} {unit}",unit),tags=tags)

    def rf_dashboard():
        rev_today.config(text=f"${get_today_revenue():.2f}")
        rev_month.config(text=f"${get_month_revenue():.2f}")
        st=get_order_stats(); wait_lbl.config(text=str(st["waiting"]))
        if st["waiting"]>0:
            order_alert.config(text=f"  ⚡ {st['waiting']} ORDER(S) WAITING  ")
            order_alert.pack(side="right",padx=(0,6))
        else:
            order_alert.pack_forget()
        ls_box.delete(0,tk.END)
        lows=get_low_stock()
        if not lows: ls_box.insert(tk.END,"  All stocked ✓")
        else:
            for name,stock,unit in lows:
                ls_box.insert(tk.END,f"  {name} — {fmt(stock)} {unit}")
                ls_box.itemconfig(tk.END,foreground=C_WARN)
        for box,period in [(td_box,"today"),(mo_box,"month")]:
            box.delete(0,tk.END)
            rows=get_top_sellers(period)
            if not rows: box.insert(tk.END,"  No sales yet")
            else:
                for i,(name,q) in enumerate(rows,1): box.insert(tk.END,f"  {i}. {name} ({fmt(q)})")

    def full_rf():
        rf_products(); rf_dashboard(); rf_orders()
        rf_history(); rf_inventory(); rf_customers()

    def poll():
        rf_orders(); rf_dashboard(); root.after(5000,poll)

    # ── Actions ───────────────────────────────────
    def add_to_cart():
        sel=pt.selection()
        if not sel: messagebox.showwarning("No selection","Select a product first."); return
        pid=int(sel[0]); ap={p[0]:p for p in get_all_products()}; _,name,price,stock,unit=ap[pid]
        qty=simpledialog.askfloat("Quantity",f"How much '{name}' ({unit})?\n(decimals OK)",minvalue=0.01)
        if qty is None: return
        if qty>stock: messagebox.showerror("Low stock",f"Only {fmt(stock)} {unit} in stock."); return
        cart[pid]=cart.get(pid,0)+qty; cart_names[pid]=name; rf_cart()

    def remove_from_cart():
        sel=cl.curselection()
        if not sel: messagebox.showwarning("No selection","Click a cart item first."); return
        pid=list(cart.keys())[sel[0]]; del cart[pid]; del cart_names[pid]; rf_cart()

    def checkout():
        if not cart: messagebox.showinfo("Empty","Add products first."); return
        total=rf_cart(); ap={p[0]:p for p in get_all_products()}; line_items=[]
        for pid,qty in cart.items():
            _,name,price,stock,unit=ap[pid]; sub=price*qty
            line_items.append({"name":name,"qty":qty,"unit":unit,"subtotal":sub})
            update_stock_db(pid,stock-qty)
        save_sale(line_items,total)
        fn=gen_receipt(line_items,total)
        messagebox.showinfo("Sale complete",f"Total: ${total:.2f}\nReceipt saved: {fn}")
        cart.clear(); cart_names.clear(); rf_cart(); full_rf()
        audit("admin","counter_sale",f"total={total:.2f}")

    def view_order():
        sel=ordt.selection()
        if not sel: messagebox.showwarning("No selection","Select an order first."); return
        order_no=sel[0]; order=get_order_by_no(order_no)
        if not order: return
        dw=tk.Toplevel(root); dw.title(f"Order {order_no}"); dw.geometry("500x500")
        dw.configure(bg=C_BG)
        tk.Label(dw,text=f"Order: {order_no}",bg=C_BG,fg=C_TEXT,font=FS).pack(anchor="w",padx=20,pady=(16,4))
        inf=tk.Frame(dw,bg=C_BG); inf.pack(fill="x",padx=20,pady=(0,8))
        for k,v in [("Customer",order["customer_name"]),("Mobile",order["mobile"]),
                    ("Date",order["date"]),("Status",order["status"].upper()),("Total",f"${order['total']:.2f}")]:
            rw=tk.Frame(inf,bg=C_BG); rw.pack(fill="x",pady=2)
            tk.Label(rw,text=f"{k}:",bg=C_BG,fg=C_SUB,font=FSM,width=11,anchor="w").pack(side="left")
            tk.Label(rw,text=v,bg=C_BG,fg=C_TEXT,font=("Segoe UI",10,"bold")).pack(side="left")
        tk.Label(dw,text="Items",bg=C_BG,fg=C_TEXT,font=FSB).pack(anchor="w",padx=20,pady=(6,3))
        lb2=tk.Listbox(dw,bg=C_CARD,fg=C_TEXT,font=FR,borderwidth=0,
                       highlightthickness=1,highlightbackground=C_BORDER,height=7)
        lb2.pack(fill="x",padx=20)
        for it in order["items"]:
            lb2.insert(tk.END,f"  {it['name']:<16} {fmt(it['qty'])} {it['unit']:>8}  ${it['subtotal']:.2f}")
        bf=tk.Frame(dw,bg=C_BG); bf.pack(fill="x",padx=20,pady=14)
        if order["status"]=="waiting":
            def do_paid():
                if messagebox.askyesno("Confirm",f"Mark {order_no} as PAID?",parent=dw):
                    mark_order_paid(order_no)
                    fn=gen_receipt(order["items"],order["total"],order_no=order_no,
                                   cname=order["customer_name"],paid=True)
                    audit("admin","mark_paid",f"order={order_no}")
                    messagebox.showinfo("Paid",f"Order marked paid.\nReceipt: {fn}",parent=dw)
                    dw.destroy(); full_rf()
            ttk.Button(bf,text="✓ Mark as Paid",style="Gold.TButton",command=do_paid).pack(side="left")
        ttk.Button(bf,text="Close",style="Outline.TButton",command=dw.destroy).pack(side="right")

    def add_product_action():
        name=simpledialog.askstring("Product Name","Enter product name:")
        if not name: return
        if product_exists(name): messagebox.showerror("Error","Product already exists."); return
        unit=simpledialog.askstring("Unit","Enter unit (kg, litre, pcs):",initialvalue="pcs")
        unit=(unit or "pcs").strip() or "pcs"
        price=simpledialog.askfloat("Price",f"Price per {unit}:")
        stock=simpledialog.askfloat("Stock",f"Starting stock ({unit}):")
        if price is None or stock is None: return
        add_product_db(name,price,stock,unit)
        audit("admin","add_product",f"name={name}"); full_rf()

    def restock_action():
        sel=invt.selection()
        if not sel: messagebox.showwarning("No selection","Select a product."); return
        pid=int(sel[0]); p=get_product(pid)
        if not p: return
        _,name,price,stock,unit=p
        qty=simpledialog.askfloat("Restock",f"Add stock for {name}?\nCurrent: {fmt(stock)} {unit}",minvalue=0.01)
        if qty is None: return
        update_stock_db(pid,stock+qty); audit("admin","restock",f"{name}+{qty}"); full_rf()

    # ── Wire buttons ──────────────────────────────
    ttk.Button(ptb,text="Add to Cart",style="Accent.TButton",command=add_to_cart).pack(side="left")
    ttk.Button(ptb,text="Remove Item",style="Outline.TButton",command=remove_from_cart).pack(side="left",padx=6)
    ttk.Button(cf2,text="💳 Checkout",style="Accent.TButton",command=checkout).pack(fill="x",pady=(5,0))
    ttk.Button(ordtb,text="View / Mark Paid",style="Gold.TButton",command=view_order).pack(side="left")
    ttk.Button(ordtb,text="Refresh Now",style="Outline.TButton",
               command=lambda:(rf_orders(),rf_dashboard())).pack(side="left",padx=7)
    ttk.Button(invtb,text="Add Product",style="Accent.TButton",command=add_product_action).pack(side="left")
    ttk.Button(invtb,text="Add Stock",style="Outline.TButton",command=restock_action).pack(side="left",padx=7)

    sv.trace_add("write",lambda *a: rf_products())
    sov.trace_add("write",lambda *a: rf_orders())

    def on_tab(event):
        t=nb.index(nb.select())
        if t==1: rf_orders()
        elif t==2: rf_history()
        elif t==3: rf_customers()
        elif t==4: rf_inventory()
    nb.bind("<<NotebookTabChanged>>",on_tab)

    upd_ss(); tick(); full_rf(); poll()

# ═══════════════════════════════════════════════════════════════
# 6.  ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    setup_database()

    # Start Flask in background thread
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    print(f"\n{'='*50}")
    print("  Aurora Grocery POS  —  Starting...")
    print(f"  Customer Web Portal: http://localhost:{FLASK_PORT}")
    if DEMO_MODE:
        print("  ⚠  Demo Mode: OTPs will print here in console")
    print(f"{'='*50}\n")

    build_login_screen()
    root.mainloop()