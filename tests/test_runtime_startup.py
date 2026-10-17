import asyncio
import unittest
from unittest.mock import patch

from app.db.mongodb import connect_to_mongo


class RuntimeStartupTests(unittest.TestCase):
    @patch("app.db.mongodb.AsyncIOMotorClient")
    def test_connect_to_mongo_handles_unavailable_database(self, mock_client):
        mock_client.side_effect = Exception("mongo down")

        result = asyncio.run(connect_to_mongo())

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
