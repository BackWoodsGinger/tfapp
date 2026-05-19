"""PostgreSQL helpers for migrations that add unique SlugField columns.

Django's AddField(db_index=True) followed by AlterField(unique=True, db_index=True)
creates duplicate *_like indexes on PostgreSQL. Use apply_unique_slug_column instead
of AlterField for the final unique constraint.
"""


def drop_orphan_indexes(schema_editor, index_prefix):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('i', 'I')
              AND n.nspname = 'public'
              AND c.relname LIKE %s
            """,
            [f"{index_prefix}%"],
        )
        for (indexname,) in cursor.fetchall():
            cursor.execute(f'DROP INDEX IF EXISTS public."{indexname}"')


def apply_unique_slug_column(schema_editor, table_name, column_name, index_name=None):
    if schema_editor.connection.vendor != "postgresql":
        return
    index_prefix = f"{table_name}_{column_name}"
    if index_name is None:
        index_name = f"{index_prefix}_key"
    drop_orphan_indexes(schema_editor, index_prefix)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {column_name} SET NOT NULL"
        )
        cursor.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS "{index_name}" ON {table_name} ({column_name})'
        )


def run_drop_orphan_indexes(index_prefix):
    def forward(apps, schema_editor):
        drop_orphan_indexes(schema_editor, index_prefix)

    return forward


def run_apply_unique_slug(table_name, column_name, index_name=None):
    def forward(apps, schema_editor):
        apply_unique_slug_column(schema_editor, table_name, column_name, index_name)

    return forward
