from backend.db.connection import run_in_transaction


def update_order_record(order_id: int, fields: dict) -> None:
    """Persist changes to an order and its audit trail atomically."""
    def work(conn):
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE orders SET {cols} WHERE id = ?", (*fields.values(), order_id))
        conn.execute("INSERT INTO audit_log (entity, entity_id, action) VALUES ('order', ?, 'update')", (order_id,))
    run_in_transaction(work)


def find_order(order_id: int, conn):
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
