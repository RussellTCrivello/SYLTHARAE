"""
Database exceptions for error handling
"""


class QueryError(Exception):
    """Raised when a database query fails"""
    pass


class TransactionError(Exception):
    """Raised when a database transaction fails"""
    pass


class TransactionAbortedError(QueryError):
    """The current transaction is aborted and cannot run further statements.

    PostgreSQL marks a transaction as failed as soon as any statement in it
    raises an error; every later statement in that transaction is rejected
    with ``current transaction is aborted, commands ignored until end of
    transaction block``.  This exception lets the transaction *owner* tell
    that state apart from an ordinary statement failure:

    * an ordinary failure can be contained by a savepoint, so the rest of
      the transaction keeps its work;
    * an aborted transaction must be rolled back as a whole - continuing to
      use the ids handed out earlier in it produces foreign-key violations
      against rows that no longer exist.

    It subclasses :class:`QueryError` so existing ``except QueryError`` call
    sites keep working.
    """
    pass
