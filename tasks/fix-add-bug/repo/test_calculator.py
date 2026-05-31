import unittest

from calculator import add, multiply


class TestCalculator(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add(-1, 1), 0)

    def test_multiply(self):
        self.assertEqual(multiply(2, 3), 6)


if __name__ == "__main__":
    unittest.main()
