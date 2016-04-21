"""multiple emails for users

Revision ID: ba63ec7d4f
Revises: 553390a1723
Create Date: 2016-04-21 06:23:54.883199

"""

# revision identifiers, used by Alembic.
revision = 'ba63ec7d4f'
down_revision = '553390a1723'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table('user_email',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    )
    op.create_index(op.f('ix_user_email_email'), 'user_email', ['email'], unique=True)
    op.create_index(op.f('ix_user_email_user_id'), 'user_email', ['user_id'], unique=False)
    op.add_column('user', sa.Column('primary_email_id', sa.Integer()))
    op.create_index(op.f('ix_user_primary_email_id'), 'user', ['primary_email_id'], unique=False)
    
    op.execute("""
    INSERT INTO user_email (email, user_id)
    SELECT u.email, u.id
    FROM "user" u
    """)
    op.execute("""
    UPDATE "user" SET primary_email_id=user_email.id
    FROM "user" uj INNER JOIN user_email
    ON uj.email = user_email.email
    """)

    op.create_foreign_key(None, 'user', 'user_email', ['primary_email_id'], ['id'])
    op.alter_column('user', 'primary_email_id', nullable=False)

    op.drop_index('ix_user_email', table_name='user')
    op.drop_column('user', 'email')


def downgrade():
    op.add_column('user', sa.Column('email', sa.VARCHAR(length=255), autoincrement=False, nullable=True))
    op.create_index('ix_user_email', 'user', ['email'], unique=True)

    op.execute("""
    UPDATE "user" SET email=user_email.email
    FROM "user" uj INNER JOIN user_email
    ON uj.primary_email_id = user_email.id
    """)

    op.drop_index(op.f('ix_user_primary_email_id'), table_name='user')
    op.drop_column('user', 'primary_email_id')
    op.drop_index(op.f('ix_user_email_user_id'), table_name='user_email')
    op.drop_index(op.f('ix_user_email_email'), table_name='user_email')
    op.drop_table('user_email')
