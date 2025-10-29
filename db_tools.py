"""SQLite-backed tools for the e-commerce voice assistant."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite
from loguru import logger

from pipecat.services.llm_service import FunctionCallParams


class DatabaseTools:
    """Load JSON seed data into SQLite and expose query helpers for tool calls."""

    def __init__(self, *, db_path: Path, data_dir: Path) -> None:
        self._db_path = Path(db_path)
        self._data_dir = Path(data_dir)
        self._initialized = False
        self._lock = asyncio.Lock()

    async def ensure_initialized(self) -> None:
        """Populate the SQLite database from JSON files if needed."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return
            await self._initialize_database()
            self._initialized = True

    async def lookup_order(
        self,
        params: FunctionCallParams,
        order_id: str,
        email: Optional[str] = None,
    ) -> None:
        """Look up a single order by ID and optional customer email for verification."""

        await self.ensure_initialized()

        conditions: List[str] = ["o.order_id = ?"]
        query_params: List[Any] = [order_id]

        if email:
            conditions.append("LOWER(c.email) = LOWER(?)")
            query_params.append(email)

        query = self._base_order_query(where_clause=" AND ".join(conditions), limit=1)
        row = await self._fetchone(query, query_params)

        if not row:
            await params.result_callback(
                {
                    "status": "not_found",
                    "message": "No order found with the provided details.",
                }
            )
            return

        await params.result_callback({"status": "ok", "order": self._row_to_order(row)})

    async def list_orders_for_customer(
        self,
        params: FunctionCallParams,
        email: Optional[str] = None,
        customer_id: Optional[str] = None,
    ) -> None:
        """List all orders for a customer, filtered by email and/or customer ID."""

        await self.ensure_initialized()

        conditions: List[str] = []
        query_params: List[Any] = []

        if email:
            conditions.append("LOWER(c.email) = LOWER(?)")
            query_params.append(email)
        if customer_id:
            conditions.append("c.customer_id = ?")
            query_params.append(customer_id)

        if not conditions:
            await params.result_callback(
                {
                    "status": "error",
                    "message": "Provide at least an email or a customer_id to search orders.",
                }
            )
            return

        query = self._base_order_query(where_clause=" AND ".join(conditions), order_by="o.estimated_delivery DESC")
        rows = await self._fetchall(query, query_params)

        if not rows:
            await params.result_callback(
                {
                    "status": "not_found",
                    "message": "No orders found for the provided customer details.",
                }
            )
            return

        await params.result_callback(
            {
                "status": "ok",
                "orders": [self._row_to_order(row) for row in rows],
            }
        )

    async def _initialize_database(self) -> None:
        """Create tables and populate them from the JSON files."""

        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        customers = self._load_json("customers.json")
        products = self._load_json("products.json")
        orders = self._load_json("orders.json")

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email ON customers (LOWER(email));

                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    price REAL,
                    category TEXT,
                    use_case TEXT,
                    application TEXT,
                    stock_quantity INTEGER,
                    product_metadata TEXT
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    total_amount REAL NOT NULL,
                    status TEXT NOT NULL,
                    shipping_address TEXT,
                    estimated_delivery TEXT,
                    FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
                );
                """
            )

            await db.execute("DELETE FROM orders;")
            await db.execute("DELETE FROM products;")
            await db.execute("DELETE FROM customers;")

            await self._bulk_insert(
                db,
                "INSERT INTO customers(customer_id, name, email, phone) VALUES(?, ?, ?, ?);",
                ((c.get("customer_id"), c.get("name"), c.get("email"), c.get("phone")) for c in customers),
            )

            await self._bulk_insert(
                db,
                """
                INSERT INTO products(
                    product_id,
                    name,
                    description,
                    price,
                    category,
                    use_case,
                    application,
                    stock_quantity,
                    product_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    (
                        p.get("product_id"),
                        p.get("name"),
                        p.get("description"),
                        p.get("price"),
                        p.get("category"),
                        p.get("use_case"),
                        p.get("application"),
                        p.get("stock_quantity"),
                        json.dumps(p.get("product_metadata", {}), ensure_ascii=False),
                    )
                    for p in products
                ),
            )

            await self._bulk_insert(
                db,
                """
                INSERT INTO orders(
                    order_id,
                    customer_id,
                    product_id,
                    quantity,
                    total_amount,
                    status,
                    shipping_address,
                    estimated_delivery
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    (
                        o.get("order_id"),
                        o.get("customer_id"),
                        o.get("product_id"),
                        o.get("quantity"),
                        o.get("total_amount"),
                        o.get("status"),
                        o.get("shipping_address"),
                        o.get("estimated_delivery"),
                    )
                    for o in orders
                ),
            )

            await db.commit()

        logger.info("Initialized order database at %s", self._db_path)

    async def _bulk_insert(
        self,
        db: aiosqlite.Connection,
        statement: str,
        payload: Iterable[tuple[Any, ...]],
    ) -> None:
        items = list(payload)
        if not items:
            return
        await db.executemany(statement, items)

    async def _fetchone(self, query: str, params: List[Any]) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON;")
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
            await cursor.close()
        return dict(row) if row else None

    async def _fetchall(self, query: str, params: List[Any]) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON;")
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()
        return [dict(row) for row in rows]

    def _row_to_order(self, row: Dict[str, Any]) -> Dict[str, Any]:
        product_metadata_raw = row.get("product_metadata")
        product_metadata: Dict[str, Any] = {}
        if product_metadata_raw:
            try:
                product_metadata = json.loads(product_metadata_raw)
            except json.JSONDecodeError:
                product_metadata = {"raw": product_metadata_raw}

        return {
            "order_id": row.get("order_id"),
            "status": row.get("status"),
            "total_amount": row.get("total_amount"),
            "quantity": row.get("quantity"),
            "shipping_address": row.get("shipping_address"),
            "estimated_delivery": row.get("estimated_delivery"),
            "product": {
                "product_id": row.get("product_id"),
                "name": row.get("product_name"),
                "description": row.get("product_description"),
                "price": row.get("product_price"),
                "category": row.get("product_category"),
                "use_case": row.get("product_use_case"),
                "application": row.get("product_application"),
                "stock_quantity": row.get("product_stock_quantity"),
                "metadata": product_metadata,
            },
            "customer": {
                "customer_id": row.get("customer_id"),
                "name": row.get("customer_name"),
                "email": row.get("customer_email"),
                "phone": row.get("customer_phone"),
            },
        }

    def _base_order_query(
        self,
        *,
        where_clause: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> str:
        clauses = []
        if where_clause:
            clauses.append(f"WHERE {where_clause}")
        if order_by:
            clauses.append(f"ORDER BY {order_by}")
        if limit is not None:
            clauses.append(f"LIMIT {limit}")

        return (
            "\n".join(
                [
                    "SELECT",
                    "    o.order_id,",
                    "    o.status,",
                    "    o.total_amount,",
                    "    o.quantity,",
                    "    o.shipping_address,",
                    "    o.estimated_delivery,",
                    "    o.customer_id,",
                    "    c.name AS customer_name,",
                    "    c.email AS customer_email,",
                    "    c.phone AS customer_phone,",
                    "    o.product_id,",
                    "    p.name AS product_name,",
                    "    p.description AS product_description,",
                    "    p.price AS product_price,",
                    "    p.category AS product_category,",
                    "    p.use_case AS product_use_case,",
                    "    p.application AS product_application,",
                    "    p.stock_quantity AS product_stock_quantity,",
                    "    p.product_metadata",
                    "FROM orders o",
                    "JOIN customers c ON c.customer_id = o.customer_id",
                    "JOIN products p ON p.product_id = o.product_id",
                ]
                + clauses
            )
        )

    def _load_json(self, filename: str) -> List[Dict[str, Any]]:
        path = self._data_dir / filename
        if not path.exists():
            logger.warning("Seed file not found: %s", path)
            return []

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse %s: %s", path, exc)
            return []

    @property
    def tool_functions(self) -> List[Any]:
        """Return the callables that should be registered as tools."""
        return [self.lookup_order, self.list_orders_for_customer]
