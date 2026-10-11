from collections import Counter

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from app.models import Base


def test_postgres_constraint_names_are_unique_with_multiple_composite_fks():
    """Duas FKs para accounts/transactions e várias UNIQUEs não podem colidir."""
    for table in Base.metadata.sorted_tables:
        counts = Counter(c.name for c in table.constraints if c.name)
        duplicates = [name for name, count in counts.items() if count > 1]
        assert not duplicates, (table.name, duplicates)
        assert str(CreateTable(table).compile(dialect=dialect()))
