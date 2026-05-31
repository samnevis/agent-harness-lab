import unittest

from fizzbuzz import fizzbuzz


class TestFizzBuzz(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(fizzbuzz(3), "fizz")
        self.assertEqual(fizzbuzz(5), "buzz")
        self.assertEqual(fizzbuzz(2), "2")

    def test_fifteen(self):
        self.assertEqual(fizzbuzz(15), "fizzbuzz")


if __name__ == "__main__":
    unittest.main()
