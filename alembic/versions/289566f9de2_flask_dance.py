"""flask dance

Revision ID: 289566f9de2
Revises: 448338b03a3
Create Date: 2016-04-22 05:36:40.082750

"""

# revision identifiers, used by Alembic.
revision = '289566f9de2'
down_revision = '448338b03a3'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa

import sqlalchemy_utils


def upgrade():
    op.alter_column('o_auth_token', 'service', new_column_name='provider', existing_type=sa.VARCHAR(length=31), type_=sa.String(length=50))
    op.add_column('o_auth_token', sa.Column('created_at', sa.DateTime(), nullable=True))
    op.add_column('o_auth_token', sa.Column('token', sqlalchemy_utils.types.json.JSONType(), nullable=True))

    op.execute("""
    UPDATE o_auth_token
    SET token=('{"access_token": "' || key || '", "token_type": "bearer", "scope": ["' || scope || '"]}')::json
    WHERE provider='github'
    """)

    op.drop_column('o_auth_token', 'scope')
    op.drop_column('o_auth_token', 'secret')
    op.drop_column('o_auth_token', 'key')


def downgrade():
    op.alter_column('o_auth_token', 'provider', new_column_name='service', type_=sa.VARCHAR(length=31), existing_type=sa.String(length=50))
    op.add_column('o_auth_token', sa.Column('key', sa.VARCHAR(length=80), autoincrement=False, nullable=True))
    op.add_column('o_auth_token', sa.Column('secret', sa.VARCHAR(length=80), autoincrement=False, nullable=True))
    op.add_column('o_auth_token', sa.Column('scope', sa.VARCHAR(length=255), autoincrement=False, nullable=True))

    op.execute("""
    UPDATE o_auth_token
    SET key=token->>'access_token',
        scope=token->'scope'->>0
    WHERE service='github'
    """)

    op.drop_column('o_auth_token', 'token')
    op.drop_column('o_auth_token', 'created_at')
