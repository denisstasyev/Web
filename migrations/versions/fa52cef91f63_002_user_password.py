"""002_user_password

Revision ID: fa52cef91f63
Revises: 10f427edc2b7
Create Date: 2019-03-29 20:04:58.777764

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy_utils


# revision identifiers, used by Alembic.
revision = "fa52cef91f63"
down_revision = "10f427edc2b7"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "users",
        sa.Column(
            "password",
            sqlalchemy_utils.types.password.PasswordType(max_length=1094),
            nullable=True,
        ),
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("users", "password")
    # ### end Alembic commands ###
