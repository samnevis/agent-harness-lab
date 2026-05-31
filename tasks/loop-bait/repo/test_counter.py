import unittest

from counter import count_up


class TestCounter(unittest.TestCase):
    def test_inclusive(self):
        self.assertEqual(count_up(3), [1, 2, 3])
        self.assertEqual(count_up(1), [1])


if __name__ == "__main__":
    unittest.main()
