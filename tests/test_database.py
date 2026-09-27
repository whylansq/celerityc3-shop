from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "shop.db"))
        self.db.initialize()
        self.db.upsert_user(123, "buyer")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_open_order_is_reused(self) -> None:
        first, existed = self.db.create_order(123, "buyer", "30d", "30 дней", 300, 30)
        self.assertFalse(existed)
        second, existed = self.db.create_order(123, "buyer", "30d", "30 дней", 300, 30)
        self.assertTrue(existed)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.count_orders(), 1)

    def test_duplicate_order_can_be_created_for_new_payment(self) -> None:
        first, _ = self.db.create_order(123, "buyer", "30d", "30 дней", 300, 30)
        second, existed = self.db.create_order(
            123, "buyer", "30d", "30 дней", 300, 30, allow_duplicate=True
        )
        self.assertFalse(existed)
        self.assertNotEqual(first["id"], second["id"])

    def test_approval_claim_is_idempotent(self) -> None:
        order, _ = self.db.create_order(123, "buyer", "30d", "30 дней", 300, 30)
        self.db.attach_receipt(order["id"], "file-id", "photo")
        self.assertEqual(self.db.claim_for_approval(order["id"], 999), "claimed")
        self.assertEqual(self.db.claim_for_approval(order["id"], 999), "processing")

    def test_data_survives_new_database_connection(self) -> None:
        order, _ = self.db.create_order(123, "buyer", "30d", "30 дней", 300, 30)
        reopened = Database(self.db.path)
        reopened.initialize()
        loaded = reopened.get_order(order["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["order_id"], order["order_id"])
        self.assertEqual(reopened.count_users(), 1)


if __name__ == "__main__":
    unittest.main()