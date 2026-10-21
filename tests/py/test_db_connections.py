"""The web server opens a database connection per request, on many threads at once. The MySQL connector's C extension
is not safe for that (concurrent first connections fail with error 2059 and can segfault the whole server), so the
connections the server makes must use the pure-Python connector."""
import os
import unittest
from unittest import mock

import _path  # noqa: F401
import api
import screener


class WebServerConnections(unittest.TestCase):
    def connect_kwargs(self, opener):
        with mock.patch("mysql.connector.connect") as connect:
            opener()
        self.assertEqual(connect.call_count, 1)
        return connect.call_args.kwargs

    def test_api_connections_use_the_pure_python_connector(self):
        self.assertIs(self.connect_kwargs(api.get_db)["use_pure"], True)

    def test_screener_connections_use_the_pure_python_connector(self):
        self.assertIs(self.connect_kwargs(screener.get_db_connection)["use_pure"], True)

    def test_the_rest_of_the_connection_settings_are_unchanged(self):
        env = {"DB_HOST": "db.example.test", "DB_PORT": "3307", "DB_USER": "u", "DB_PASSWORD": "p", "DB_NAME": "n", "DB_SSL": "true"}
        with mock.patch.dict(os.environ, env):
            kwargs = self.connect_kwargs(api.get_db)
        self.assertEqual({k: kwargs[k] for k in ("host", "port", "user", "password", "database")},
                         {"host": "db.example.test", "port": 3307, "user": "u", "password": "p", "database": "n"})
        self.assertIs(kwargs["ssl_disabled"], False)


if __name__ == "__main__":
    unittest.main()
