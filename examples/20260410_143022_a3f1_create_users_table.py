"""create users table

Revision ID: 20260410_143022_a3f1
Create Date: 2026-04-10 14:30:22.000000
Created By: developer
"""
from alembic import op
import sqlalchemy as sa


revision = '20260410_143022_a3f1'
down_revision = None
depends_on = None
description = 'create users table'
created_by = 'developer'


def upgrade():
    op.execute("""
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS users")
