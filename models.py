from dataclasses import dataclass
from typing import List, Optional

@dataclass
class Product:
    id: int
    name: str
    price: float
    stock: float
    unit: str

@dataclass
class Customer:
    id: int
    name: str
    mobile: str
    verified: int

@dataclass
class Order:
    id: int
    order_no: str
    customer_name: str
    mobile: str
    date: str
    items: List[dict]
    total: float
    status: str
    paid_at: Optional[str] = None
