"""A recording stand-in for a MySQL connection, so SQL can be checked without a database."""
import _path  # noqa: F401


class FakeCursor:
    def __init__(self, conn, dictionary=False):
        self.conn, self.dictionary = conn, dictionary
        self.rows, self.rowcount, self.lastrowid = [], 0, 1

    def execute(self, sql, params=()):
        sql = " ".join(str(sql).split())
        self.conn.log.append((sql, tuple(params)))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise RuntimeError("simulated database failure")
        result = self.conn.respond(sql, tuple(params))
        if isinstance(result, int):                       # an UPDATE/DELETE: how many rows it touched
            self.rows, self.rowcount = [], result
        else:
            self.rows, self.rowcount = list(result or []), len(result or [])

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)

    def _shape(self, row):
        return row if self.dictionary else tuple(row.values())

    def fetchone(self):
        return self._shape(self.rows[0]) if self.rows else None

    def fetchall(self):
        return [self._shape(r) for r in self.rows]


class FakeConn:
    """`respond(sql, params)` returns the rows (a list of dicts) or the affected-row count for a statement."""

    def __init__(self, respond=None, fail_on=None):
        self.respond = respond or (lambda sql, params: [])
        self.fail_on = fail_on
        self.log, self.commits, self.rollbacks, self.closed = [], 0, 0, False

    def cursor(self, dictionary=False):
        return FakeCursor(self, dictionary)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def statements(self, containing=""):
        return [(sql, p) for sql, p in self.log if containing in sql]
