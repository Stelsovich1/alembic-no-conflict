"""add orders table with FK to users

Revision ID: 20260411_091500_c8d9
Create Date: 2026-04-11 09:15:00.000000
Created By: developer
"""
from alembic import op
import sqlalchemy as sa


revision = '20260411_091500_c8d9'
down_revision = None
depends_on = '20260410_143022_a3f1'  # зависит от create_users_table
description = 'add orders table with FK to users'
created_by = 'developer'


def upgrade():
    op.execute("""
        CREATE TABLE orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount NUMERIC(10, 2) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS orders")
