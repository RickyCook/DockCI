"""multiple user emails

Revision ID: 448338b03a3
Revises: 553390a1723
Create Date: 2016-04-26 06:05:45.005053

"""

# revision identifiers, used by Alembic.
revision = '448338b03a3'
down_revision = '553390a1723'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('user_email',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_email_email'), 'user_email', ['email'], unique=True)
    op.create_index(op.f('ix_user_email_user_id'), 'user_email', ['user_id'], unique=False)
    op.alter_column('user', 'email',
               existing_type=sa.VARCHAR(length=255),
               nullable=False)

    op.execute("""
    INSERT INTO user_email (email, user_id)
    SELECT u.email, u.id
    FROM "user" u
    """)

    op.create_foreign_key(None, 'user', 'user_email', ['email'], ['email'])


def downgrade():
    op.drop_constraint(None, 'user', type_='foreignkey')
    op.alter_column('user', 'email',
               existing_type=sa.VARCHAR(length=255),
               nullable=True)
    op.drop_index(op.f('ix_user_email_user_id'), table_name='user_email')
    op.drop_index(op.f('ix_user_email_email'), table_name='user_email')
    op.drop_table('user_email')
