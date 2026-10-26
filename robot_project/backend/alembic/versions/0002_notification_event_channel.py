"""Allow one notification per event, channel and recipient.

Revision ID: 0002_notification_event_channel
Revises: 0001_initial
"""

from alembic import op


revision = "0002_notification_event_channel"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_notifications_event_uid", "notifications", type_="unique"
    )
    op.create_unique_constraint(
        "uq_notifications_event_channel_recipient",
        "notifications",
        ["event_uid", "channel", "recipient"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_notifications_event_channel_recipient", "notifications", type_="unique"
    )
    op.create_unique_constraint(
        "uq_notifications_event_uid", "notifications", ["event_uid"]
    )
