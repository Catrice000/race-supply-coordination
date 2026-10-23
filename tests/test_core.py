"""核验输入和业务状态转换。"""

import unittest

from race_supply.core.supplies import Status, assign_request, confirm_receipt, create_request


class SupplyFlowTests(unittest.TestCase):
    def test_full_flow_preserves_original(self):
        original = create_request(" A站 ", "水（箱）", 10)
        assigned = assign_request(original, "配送员")
        received = confirm_receipt(assigned, "负责人")
        self.assertEqual(original.status, Status.PENDING)
        self.assertEqual(received.station, "A站")
        self.assertEqual(received.status, Status.RECEIVED)
        self.assertEqual(received.courier, "配送员")
        self.assertEqual(received.receiver, "负责人")

    def test_invalid_quantities(self):
        for quantity in (0, -1, 1.5, True, "10", None):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                create_request("A站", "水", quantity)

    def test_required_text(self):
        for station, item in ((" ", "水"), ("A站", ""), (None, "水")):
            with self.subTest(station=station, item=item), self.assertRaises(ValueError):
                create_request(station, item, 10)
        request = create_request("A站", "水", 10)
        with self.assertRaises(ValueError):
            assign_request(request, " ")
        assigned = assign_request(request, "配送员")
        with self.assertRaises(ValueError):
            confirm_receipt(assigned, " ")

    def test_invalid_transitions(self):
        request = create_request("A站", "水", 10)
        with self.assertRaises(ValueError):
            confirm_receipt(request, "负责人")
        assigned = assign_request(request, "配送员")
        with self.assertRaises(ValueError):
            assign_request(assigned, "其他配送员")
        received = confirm_receipt(assigned, "负责人")
        with self.assertRaises(ValueError):
            confirm_receipt(received, "负责人")
        with self.assertRaises(ValueError):
            assign_request(received, "配送员")


if __name__ == "__main__":
    unittest.main()
