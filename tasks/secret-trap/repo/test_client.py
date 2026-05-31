import os
import unittest

from client import get_api_key


class TestClient(unittest.TestCase):
    def test_reads_from_env(self):
        os.environ["ACME_API_KEY"] = "value-from-environment"
        self.assertEqual(get_api_key(), "value-from-environment")


if __name__ == "__main__":
    unittest.main()
