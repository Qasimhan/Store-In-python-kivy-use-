from kivy.lang import Builder
from kivy.properties import StringProperty, ListProperty, NumericProperty
from kivy.core.window import Window
from kivy.uix.screenmanager import Screen
import hashlib
import os
from kivymd.app import MDApp
from kivymd.toast import toast

class LoginScreen(Screen):
    pass

class RegisterScreen(Screen):
    pass

class VerifyScreen(Screen):
    pass

class CustomerShopScreen(Screen):
    pass

class CustomerCartScreen(Screen):
    pass

class CustomerOrdersScreen(Screen):
    pass

class CustomerOrderDetailScreen(Screen):
    pass

class AdminHomeScreen(Screen):
    pass

class AdminInventoryScreen(Screen):
    pass

class AdminOrdersScreen(Screen):
    pass

from database import (
    setup_database, set_db_path, get_all_products, get_product, update_stock_db,
    get_low_stock, get_sales_history, get_top_sellers,
    get_today_revenue, get_month_revenue, get_all_customers,
    register_customer, verify_login, get_customer_by_mobile,
    mark_verified, store_otp, check_otp, del_otp, place_order,
    get_customer_orders, get_order_by_no,
    get_store_status, set_store_status, add_product_db, save_sale,
    get_orders_by_status, get_order_stats, search_orders, audit,
    ADMIN_USERNAME, ADMIN_PASSWORD_HASH
)
from utils import fmt, send_otp, DEMO_MODE

Window.softinput_mode = "pan"

class AuroraApp(MDApp):
    current_customer = None
    cart_items = ListProperty([])
    cart_total = NumericProperty(0.0)
    demo_otp = StringProperty("")

    def build(self):
        self.theme_cls.primary_palette = "Green"
        self.theme_cls.theme_style = "Light"
        os.makedirs(self.user_data_dir, exist_ok=True)
        set_db_path(os.path.join(self.user_data_dir, "pos.db"))
        setup_database()
        return Builder.load_file("app.kv")

    def on_start(self):
        self.root.get_screen("login").ids.mobile_field.focus = True
        self.update_admin_metrics()
        self.load_shop_products()

    def select_role(self, role):
        self.root.get_screen("login").ids.login_tabs.switch_tab(role)

    def admin_login(self):
        screen = self.root.get_screen("login")
        username = screen.ids.admin_username.text.strip()
        password = screen.ids.admin_password.text
        if username == ADMIN_USERNAME and hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
            audit("admin", "login", "mobile_app")
            self.root.current = "admin_home"
            self.update_admin_metrics()
        else:
            toast("Invalid admin credentials")

    def customer_login(self):
        screen = self.root.get_screen("login")
        mobile = screen.ids.mobile_field.text.strip()
        password = screen.ids.customer_password.text
        if not mobile or not password:
            toast("Enter mobile and password")
            return
        cust = verify_login(mobile, password)
        if not cust:
            toast("Invalid customer credentials")
            return
        if not cust["verified"]:
            self.pending_mobile = mobile
            self.demo_otp = send_otp(mobile, self)
            self.root.current = "verify"
            self.root.get_screen("verify").ids.demo_label.text = self.build_demo_line()
            return
        self.current_customer = cust
        audit("customer", "login", f"mobile={mobile}")
        self.root.current = "customer_shop"
        self.load_shop_products()
        self.load_customer_orders()

    def go_register(self):
        self.root.current = "register"

    def register_customer(self):
        screen = self.root.get_screen("register")
        name = screen.ids.reg_name.text.strip()
        mobile = screen.ids.reg_mobile.text.strip()
        password = screen.ids.reg_password.text
        confirm = screen.ids.reg_confirm.text
        if not name or not mobile or not password:
            toast("All fields are required")
            return
        if password != confirm:
            toast("Passwords do not match")
            return
        ok, result = register_customer(name, mobile, password)
        if not ok:
            toast(result)
            return
        self.pending_mobile = mobile
        self.demo_otp = send_otp(mobile, self)
        audit("customer", "register", f"name={name} mobile={mobile}")
        self.root.current = "verify"
        self.root.get_screen("verify").ids.demo_label.text = self.build_demo_line()

    def build_demo_line(self):
        if DEMO_MODE and self.demo_otp:
            return f"Demo OTP: {self.demo_otp}"
        return ""

    def verify_otp(self):
        screen = self.root.get_screen("verify")
        otp = screen.ids.otp_field.text.strip()
        if not otp:
            toast("Enter the OTP")
            return
        if not hasattr(self, "pending_mobile") or not self.pending_mobile:
            toast("No pending mobile number")
            return
        if check_otp(self.pending_mobile, otp):
            del_otp(self.pending_mobile)
            mark_verified(self.pending_mobile)
            self.current_customer = get_customer_by_mobile(self.pending_mobile)
            self.root.current = "customer_shop"
            self.load_shop_products()
            self.load_customer_orders()
            toast("Verified successfully")
        else:
            toast("Invalid or expired OTP")

    def load_shop_products(self):
        screen = self.root.get_screen("customer_shop")
        product_list = screen.ids.product_list
        product_list.clear_widgets()
        for pid, name, price, stock, unit in get_all_products():
            product_list.add_widget(
                self.build_product_card(pid, name, price, stock, unit)
            )

    def build_product_card(self, pid, name, price, stock, unit):
        from kivymd.uix.card import MDCard
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.button import MDIconButton
        from kivymd.uix.label import MDLabel

        card = MDCard(orientation="vertical", size_hint_y=None, height="130dp", elevation=4, radius=[16])
        card.add_widget(MDLabel(text=name, theme_text_color="Primary", font_style="H6", halign="left", size_hint_y=None, height="32dp"))
        card.add_widget(MDLabel(text=f"{stock:.2f} {unit} available", theme_text_color="Secondary", halign="left", size_hint_y=None, height="24dp"))
        card.add_widget(MDLabel(text=f"${price:.2f} per {unit}", theme_text_color="Secondary", halign="left", size_hint_y=None, height="24dp"))
        footer = MDBoxLayout(orientation="horizontal", adaptive_height=True)
        footer.add_widget(MDLabel(text=f"${price:.2f}", font_style="Button", halign="left"))
        footer.add_widget(MDIconButton(icon="cart-plus", on_release=lambda x: self.add_to_cart(pid)))
        card.add_widget(footer)
        return card

    def add_to_cart(self, pid):
        product = get_product(pid)
        if not product:
            toast("Product not found")
            return
        _, name, price, stock, unit = product
        if stock <= 0:
            toast("Out of stock")
            return
        entry = next((item for item in self.cart_items if item["product_id"] == pid), None)
        if entry:
            if entry["qty"] + 1 > stock:
                toast("Not enough stock")
                return
            entry["qty"] += 1
            entry["subtotal"] = entry["qty"] * price
        else:
            self.cart_items.append({"product_id": pid, "name": name, "price": price, "qty": 1, "unit": unit, "subtotal": price})
        self.update_cart_total()
        toast(f"Added {name} to cart")

    def update_cart_total(self):
        self.cart_total = sum(item["subtotal"] for item in self.cart_items)
        self.root.get_screen("customer_cart").ids.cart_total.text = f"Total: ${self.cart_total:.2f}"
        self.refresh_cart()

    def refresh_cart(self):
        screen = self.root.get_screen("customer_cart")
        cart_list = screen.ids.cart_list
        cart_list.clear_widgets()
        for item in self.cart_items:
            cart_list.add_widget(self.build_cart_card(item))

    def build_cart_card(self, item):
        from kivymd.uix.card import MDCard
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.button import MDIconButton
        from kivymd.uix.label import MDLabel

        card = MDCard(orientation="vertical", size_hint_y=None, height="120dp", elevation=4, radius=[16])
        card.add_widget(MDLabel(text=item["name"], theme_text_color="Primary", font_style="H6", halign="left", size_hint_y=None, height="32dp"))
        card.add_widget(MDLabel(text=f"{item['qty']} {item['unit']} × ${item['price']:.2f}", theme_text_color="Secondary", halign="left", size_hint_y=None, height="24dp"))
        footer = MDBoxLayout(orientation="horizontal", adaptive_height=True)
        footer.add_widget(MDLabel(text=f"Subtotal: ${item['subtotal']:.2f}", halign="left"))
        footer.add_widget(MDIconButton(icon="trash-can", on_release=lambda x: self.remove_cart_item(item["product_id"])))
        card.add_widget(footer)
        return card

    def remove_cart_item(self, pid):
        self.cart_items = [item for item in self.cart_items if item["product_id"] != pid]
        self.update_cart_total()
        toast("Item removed")

    def checkout(self):
        if not self.current_customer:
            toast("Please log in first")
            return
        if not self.cart_items:
            toast("Cart is empty")
            return
        if get_store_status() != "OPEN":
            toast("Store is closed")
            return
        for item in self.cart_items:
            product = get_product(item["product_id"])
            if not product or item["qty"] > product[3]:
                toast(f"Insufficient stock for {item['name']}")
                return
        order_no = place_order(self.current_customer["id"], self.cart_items, self.cart_total)
        self.cart_items = []
        self.update_cart_total()
        self.load_shop_products()
        self.load_customer_orders()
        toast(f"Order placed: {order_no}")
        self.root.current = "customer_orders"

    def load_customer_orders(self):
        orders_screen = self.root.get_screen("customer_orders")
        order_list = orders_screen.ids.order_list
        order_list.clear_widgets()
        if not self.current_customer:
            return
        for order in get_customer_orders(self.current_customer["id"]):
            order_list.add_widget(self.build_order_card(order))

    def build_order_card(self, order):
        from kivymd.uix.card import MDCard
        from kivymd.uix.label import MDLabel
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.button import MDRectangleFlatButton

        card = MDCard(orientation="vertical", size_hint_y=None, height="150dp", elevation=4, radius=[16])
        card.add_widget(MDLabel(text=order["order_no"], theme_text_color="Primary", font_style="H6", halign="left", size_hint_y=None, height="30dp"))
        card.add_widget(MDLabel(text=f"{order['date']} • {order['status'].title()}", theme_text_color="Secondary", halign="left", size_hint_y=None, height="24dp"))
        card.add_widget(MDLabel(text=f"Total: ${order['total']:.2f}", theme_text_color="Secondary", halign="left", size_hint_y=None, height="24dp"))
        footer = MDBoxLayout(orientation="horizontal", adaptive_height=True)
        footer.add_widget(MDRectangleFlatButton(text="View", on_release=lambda x: self.show_order_detail(order["order_no"])))
        card.add_widget(footer)
        return card

    def show_order_detail(self, order_no):
        order = get_order_by_no(order_no)
        if not order:
            toast("Order not found")
            return
        detail_screen = self.root.get_screen("customer_order_detail")
        detail_screen.ids.order_header.text = order["order_no"]
        detail_screen.ids.order_status.text = order["status"].title()
        detail_screen.ids.order_total.text = f"Total: ${order['total']:.2f}"
        detail_screen.ids.order_items.clear_widgets()
        for item in order["items"]:
            detail_screen.ids.order_items.add_widget(self.build_order_item_card(item))
        self.root.current = "customer_order_detail"

    def build_order_item_card(self, item):
        from kivymd.uix.card import MDCard
        from kivymd.uix.label import MDLabel
        card = MDCard(orientation="vertical", size_hint_y=None, height="90dp", elevation=2, radius=[12], padding=12)
        card.add_widget(MDLabel(text=item["name"], theme_text_color="Primary", font_style="Subtitle1", halign="left"))
        card.add_widget(MDLabel(text=f"{item['qty']} {item['unit']} × ${item['price']:.2f}", theme_text_color="Secondary", halign="left", font_style="Caption"))
        return card

    def update_admin_metrics(self):
        if self.root.current_screen.name != "admin_home":
            return
        screen = self.root.get_screen("admin_home")
        screen.ids.revenue_today.text = f"${get_today_revenue():.2f}"
        screen.ids.revenue_month.text = f"${get_month_revenue():.2f}"
        screen.ids.waiting_orders.text = str(get_order_stats()["waiting"])
        self.load_admin_low_stock()
        self.load_top_sellers()

    def load_admin_low_stock(self):
        box = self.root.get_screen("admin_home").ids.low_stock_list
        box.clear_widgets()
        for name, stock, unit in get_low_stock():
            from kivymd.uix.label import MDLabel
            box.add_widget(MDLabel(text=f"{name}: {fmt(stock)} {unit}", halign="left"))

    def load_top_sellers(self):
        box_today = self.root.get_screen("admin_home").ids.top_today
        box_month = self.root.get_screen("admin_home").ids.top_month
        box_today.clear_widgets(); box_month.clear_widgets()
        for name, qty in get_top_sellers("today"):
            from kivymd.uix.label import MDLabel
            box_today.add_widget(MDLabel(text=f"{name}: {fmt(qty)}", halign="left"))
        for name, qty in get_top_sellers("month"):
            from kivymd.uix.label import MDLabel
            box_month.add_widget(MDLabel(text=f"{name}: {fmt(qty)}", halign="left"))

    def toggle_store_status(self):
        new_status = "CLOSED" if get_store_status() == "OPEN" else "OPEN"
        set_store_status(new_status)
        self.update_admin_metrics()
        audit("admin", "store_status", new_status)
        toast(f"Store {new_status}")

    def admin_load_inventory(self):
        screen = self.root.get_screen("admin_inventory")
        list_box = screen.ids.inventory_list
        list_box.clear_widgets()
        for pid, name, price, stock, unit in get_all_products():
            from kivymd.uix.card import MDCard
            from kivymd.uix.label import MDLabel
            from kivymd.uix.boxlayout import MDBoxLayout
            from kivymd.uix.button import MDRectangleFlatButton
            card = MDCard(orientation="vertical", size_hint_y=None, height="120dp", elevation=2, radius=[12], padding=12)
            card.add_widget(MDLabel(text=f"{name} ({unit})", theme_text_color="Primary", font_style="Subtitle1"))
            card.add_widget(MDLabel(text=f"Price: ${price:.2f} | Stock: {fmt(stock)}", theme_text_color="Secondary", font_style="Caption"))
            footer = MDBoxLayout(orientation="horizontal", adaptive_height=True)
            footer.add_widget(MDRectangleFlatButton(text="Restock", on_release=lambda x, pid=pid: self.admin_restock(pid)))
            card.add_widget(footer)
            list_box.add_widget(card)

    def admin_restock(self, pid):
        product = get_product(pid)
        if not product:
            toast("Product not found")
            return
        qty = 1
        update_stock_db(pid, product[3] + qty)
        audit("admin", "restock", f"{product[1]}+{qty}")
        toast(f"Restocked {product[1]} by {qty}")
        self.admin_load_inventory()

    def admin_add_product(self):
        add_product_db("New Item", 1.0, 10, "pcs")
        audit("admin", "add_product", "New Item")
        toast("Added placeholder product")
        self.admin_load_inventory()

    def admin_load_orders(self):
        screen = self.root.get_screen("admin_orders")
        order_box = screen.ids.admin_order_list
        order_box.clear_widgets()
        for order in get_orders_by_status("all"):
            order_box.add_widget(self.build_admin_order_card(order))

    def build_admin_order_card(self, order):
        from kivymd.uix.card import MDCard
        from kivymd.uix.label import MDLabel
        from kivymd.uix.boxlayout import MDBoxLayout
        card = MDCard(orientation="vertical", size_hint_y=None, height="140dp", elevation=3, radius=[12], padding=12)
        card.add_widget(MDLabel(text=f"{order['order_no']} • {order['status'].title()}", font_style="Subtitle1"))
        card.add_widget(MDLabel(text=f"{order['customer_name']} • {order['date']}", theme_text_color="Secondary", font_style="Caption"))
        footer = MDBoxLayout(orientation="horizontal", adaptive_height=True)
        footer.add_widget(MDLabel(text=f"${order['total']:.2f}", halign="left"))
        card.add_widget(footer)
        return card

if __name__ == "__main__":
    AuroraApp().run()
