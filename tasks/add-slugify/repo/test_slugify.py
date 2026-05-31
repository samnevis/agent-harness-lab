import unittest

from slugify import slugify


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_symbols_and_edges(self):
        self.assertEqual(slugify("  Foo_Bar!! "), "foo-bar")

    def test_numbers(self):
        self.assertEqual(slugify("Version 2 Point 0"), "version-2-point-0")


if __name__ == "__main__":
    unittest.main()
