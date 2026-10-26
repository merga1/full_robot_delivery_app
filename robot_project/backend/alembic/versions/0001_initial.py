"""initial schema: users, catalogue, tables, orders, robots, missions,
notifications, audit trail, outbox and idempotency records.

The tables are created in dependency order.  Three foreign keys form cycles
(``orders`` <-> ``robot_missions`` and ``robots`` <-> ``robot_missions``),
so they are added after every table exists.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_created_at"), "users", ["created_at"], unique=False)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    # ── 2. customers ──────────────────────────────────────────────────────────
    op.create_table(
        "customers",
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(length=80), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("anonymized", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_customers_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
        sa.UniqueConstraint("public_token", name="uq_customers_public_token"),
    )
    op.create_index(op.f("ix_customers_created_at"), "customers", ["created_at"], unique=False)
    op.create_index(op.f("ix_customers_user_id"), "customers", ["user_id"], unique=False)

    # ── 3. menu_items ─────────────────────────────────────────────────────────
    op.create_table(
        "menu_items",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("image_url", sa.String(length=300), nullable=True),
        sa.Column("preparation_note", sa.String(length=280), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("external_key", sa.String(length=80), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_menu_items")),
    )
    op.create_index(op.f("ix_menu_items_available"), "menu_items", ["available"], unique=False)
    op.create_index(op.f("ix_menu_items_category"), "menu_items", ["category"], unique=False)
    op.create_index(op.f("ix_menu_items_created_at"), "menu_items", ["created_at"], unique=False)
    op.create_index(op.f("ix_menu_items_external_key"), "menu_items", ["external_key"], unique=False)
    op.create_index(op.f("ix_menu_items_name"), "menu_items", ["name"], unique=True)

    # ── 4. restaurant_tables ──────────────────────────────────────────────────
    op.create_table(
        "restaurant_tables",
        sa.Column("table_number", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("destination_id", sa.String(length=80), nullable=True),
        sa.Column("map_identifier", sa.String(length=120), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=280), nullable=True),
        sa.Column("public_code", sa.String(length=16), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_tables")),
    )
    op.create_index(op.f("ix_restaurant_tables_created_at"), "restaurant_tables", ["created_at"], unique=False)
    op.create_index(op.f("ix_restaurant_tables_destination_id"), "restaurant_tables", ["destination_id"], unique=False)
    op.create_index(op.f("ix_restaurant_tables_is_active"), "restaurant_tables", ["is_active"], unique=False)
    op.create_index(op.f("ix_restaurant_tables_public_code"), "restaurant_tables", ["public_code"], unique=True)
    op.create_index(op.f("ix_restaurant_tables_table_number"), "restaurant_tables", ["table_number"], unique=True)

    # ── 5. robots ─────────────────────────────────────────────────────────────
    # ``current_mission_id`` -> robot_missions.id is a cycle and is added later.
    op.create_table(
        "robots",
        sa.Column("robot_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=280), nullable=True),
        sa.Column("connection_status", sa.String(length=16), nullable=False),
        sa.Column("availability", sa.String(length=16), nullable=False),
        sa.Column("fault_code", sa.String(length=120), nullable=True),
        sa.Column("fault_detail", sa.Text(), nullable=True),
        sa.Column("battery_percent", sa.Float(), nullable=True),
        sa.Column("battery_voltage", sa.Float(), nullable=True),
        sa.Column("home_destination_id", sa.String(length=80), nullable=True),
        sa.Column("current_mission_id", sa.Integer(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pose_x", sa.Float(), nullable=True),
        sa.Column("pose_y", sa.Float(), nullable=True),
        sa.Column("pose_yaw", sa.Float(), nullable=True),
        sa.Column("pose_frame", sa.String(length=32), nullable=True),
        sa.Column("gateway_version", sa.String(length=40), nullable=True),
        sa.Column("ros_distro", sa.String(length=40), nullable=True),
        sa.Column("navigation_action", sa.String(length=80), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_robots")),
    )
    op.create_index(op.f("ix_robots_availability"), "robots", ["availability"], unique=False)
    op.create_index(op.f("ix_robots_connection_status"), "robots", ["connection_status"], unique=False)
    op.create_index(op.f("ix_robots_created_at"), "robots", ["created_at"], unique=False)
    op.create_index(op.f("ix_robots_current_mission_id"), "robots", ["current_mission_id"], unique=False)
    op.create_index(op.f("ix_robots_last_heartbeat_at"), "robots", ["last_heartbeat_at"], unique=False)
    op.create_index(op.f("ix_robots_robot_id"), "robots", ["robot_id"], unique=True)

    # ── 6. robot_missions ─────────────────────────────────────────────────────
    # ``order_id`` -> orders.id is a cycle and is added later.
    op.create_table(
        "robot_missions",
        sa.Column("mission_uid", sa.String(length=64), nullable=False),
        sa.Column("robot_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("destination_id", sa.String(length=80), nullable=False),
        sa.Column("destination_label", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("mission_kind", sa.String(length=24), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=40), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("distance_remaining", sa.Float(), nullable=True),
        sa.Column("dispatch_command_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"],
            name=op.f("fk_robot_missions_requested_by_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["robot_id"], ["robots.id"],
            name=op.f("fk_robot_missions_robot_id_robots"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_robot_missions")),
        sa.UniqueConstraint("mission_uid", name="uq_robot_missions_mission_uid"),
    )
    op.create_index(op.f("ix_robot_missions_created_at"), "robot_missions", ["created_at"], unique=False)
    op.create_index(op.f("ix_robot_missions_dispatch_command_id"), "robot_missions", ["dispatch_command_id"], unique=False)
    op.create_index(op.f("ix_robot_missions_mission_uid"), "robot_missions", ["mission_uid"], unique=False)
    op.create_index(op.f("ix_robot_missions_order_id"), "robot_missions", ["order_id"], unique=False)
    op.create_index(op.f("ix_robot_missions_robot_id"), "robot_missions", ["robot_id"], unique=False)
    op.create_index(op.f("ix_robot_missions_status"), "robot_missions", ["status"], unique=False)

    # ── 7. orders ─────────────────────────────────────────────────────────────
    # ``mission_id`` -> robot_missions.id is a cycle and is added later.
    op.create_table(
        "orders",
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("tracking_token", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("table_id", sa.Integer(), nullable=True),
        sa.Column("table_label", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payment_status", sa.String(length=16), nullable=False),
        sa.Column("subtotal", sa.Float(), nullable=False),
        sa.Column("tax", sa.Float(), nullable=False),
        sa.Column("fees", sa.Float(), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("special_instructions", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=True),
        sa.Column("assigned_robot_id", sa.Integer(), nullable=True),
        sa.Column("loaded_by_id", sa.Integer(), nullable=True),
        sa.Column("accepted_by_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=400), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["accepted_by_id"], ["users.id"],
            name=op.f("fk_orders_accepted_by_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_robot_id"], ["robots.id"],
            name=op.f("fk_orders_assigned_robot_id_robots"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"],
            name=op.f("fk_orders_customer_id_customers"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["loaded_by_id"], ["users.id"],
            name=op.f("fk_orders_loaded_by_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["table_id"], ["restaurant_tables.id"],
            name=op.f("fk_orders_table_id_restaurant_tables"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        sa.UniqueConstraint("code", name="uq_orders_code"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        sa.UniqueConstraint("mission_id", name=op.f("uq_orders_mission_id")),
        sa.UniqueConstraint("tracking_token", name="uq_orders_tracking_token"),
    )
    op.create_index(op.f("ix_orders_assigned_robot_id"), "orders", ["assigned_robot_id"], unique=False)
    op.create_index(op.f("ix_orders_code"), "orders", ["code"], unique=False)
    op.create_index(op.f("ix_orders_created_at"), "orders", ["created_at"], unique=False)
    op.create_index(op.f("ix_orders_customer_id"), "orders", ["customer_id"], unique=False)
    op.create_index(op.f("ix_orders_idempotency_key"), "orders", ["idempotency_key"], unique=False)
    op.create_index(op.f("ix_orders_kind"), "orders", ["kind"], unique=False)
    op.create_index(op.f("ix_orders_payment_status"), "orders", ["payment_status"], unique=False)
    op.create_index(op.f("ix_orders_source"), "orders", ["source"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)
    op.create_index(op.f("ix_orders_table_id"), "orders", ["table_id"], unique=False)

    # ── 8. order_items ────────────────────────────────────────────────────────
    op.create_table(
        "order_items",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("menu_item_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Float(), nullable=False),
        sa.Column("customizations", sa.Text(), nullable=False),
        sa.Column("preparation_note", sa.String(length=280), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["menu_item_id"], ["menu_items.id"],
            name=op.f("fk_order_items_menu_item_id_menu_items"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(op.f("ix_order_items_menu_item_id"), "order_items", ["menu_item_id"], unique=False)
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)

    # ── 9. order_status_history ───────────────────────────────────────────────
    op.create_table(
        "order_status_history",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=400), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"],
            name=op.f("fk_order_status_history_actor_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"],
            name=op.f("fk_order_status_history_order_id_orders"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_status_history")),
    )
    op.create_index(op.f("ix_order_status_history_created_at"), "order_status_history", ["created_at"], unique=False)
    op.create_index(op.f("ix_order_status_history_order_id"), "order_status_history", ["order_id"], unique=False)

    # ── 10. mission_events ────────────────────────────────────────────────────
    op.create_table(
        "mission_events",
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("event_uid", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["robot_missions.id"],
            name=op.f("fk_mission_events_mission_id_robot_missions"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_events")),
    )
    op.create_index(op.f("ix_mission_events_created_at"), "mission_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_mission_events_event_type"), "mission_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_mission_events_event_uid"), "mission_events", ["event_uid"], unique=True)
    op.create_index(op.f("ix_mission_events_mission_id"), "mission_events", ["mission_id"], unique=False)

    # ── 11. notifications ──────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("event_uid", sa.String(length=80), nullable=False),
        sa.Column("notification_type", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("delivery_status", sa.String(length=16), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=80), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("event_uid", name="uq_notifications_event_uid"),
    )
    op.create_index(op.f("ix_notifications_channel"), "notifications", ["channel"], unique=False)
    op.create_index(op.f("ix_notifications_created_at"), "notifications", ["created_at"], unique=False)
    op.create_index(op.f("ix_notifications_delivery_status"), "notifications", ["delivery_status"], unique=False)
    op.create_index(op.f("ix_notifications_event_uid"), "notifications", ["event_uid"], unique=False)
    op.create_index(op.f("ix_notifications_notification_type"), "notifications", ["notification_type"], unique=False)
    op.create_index(op.f("ix_notifications_recipient"), "notifications", ["recipient"], unique=False)

    # ── 12. device_tokens ─────────────────────────────────────────────────────
    op.create_table(
        "device_tokens",
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("roles_json", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_device_tokens_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_tokens")),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index(op.f("ix_device_tokens_created_at"), "device_tokens", ["created_at"], unique=False)
    op.create_index(op.f("ix_device_tokens_token"), "device_tokens", ["token"], unique=False)
    op.create_index(op.f("ix_device_tokens_user_id"), "device_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_device_tokens_username"), "device_tokens", ["username"], unique=False)

    # ── 13. audit_logs ────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=48), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_actor"), "audit_logs", ["actor"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_logs_request_id"), "audit_logs", ["request_id"], unique=False)

    # ── 14. outbox_events ─────────────────────────────────────────────────────
    op.create_table(
        "outbox_events",
        sa.Column("event_uid", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("event_uid", name="uq_outbox_events_event_uid"),
    )
    op.create_index(op.f("ix_outbox_events_channel"), "outbox_events", ["channel"], unique=False)
    op.create_index(op.f("ix_outbox_events_correlation_id"), "outbox_events", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_outbox_events_created_at"), "outbox_events", ["created_at"], unique=False)
    op.create_index(op.f("ix_outbox_events_event_type"), "outbox_events", ["event_type"], unique=False)
    op.create_index(op.f("ix_outbox_events_event_uid"), "outbox_events", ["event_uid"], unique=False)

    # ── 15. idempotency_records ───────────────────────────────────────────────
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint("scope", "key", name="uq_idempotency_records_scope_key"),
    )
    op.create_index(op.f("ix_idempotency_records_created_at"), "idempotency_records", ["created_at"], unique=False)

    # ── deferred (cyclic) foreign keys ────────────────────────────────────────
    op.create_foreign_key(
        op.f("fk_orders_mission_id_robot_missions"), "orders", "robot_missions",
        ["mission_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_robot_missions_order_id_orders"), "robot_missions", "orders",
        ["order_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_robots_current_mission_id_robot_missions"), "robots", "robot_missions",
        ["current_mission_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    # Drop the cyclic foreign keys first, then the tables in reverse order.
    op.drop_constraint(op.f("fk_robots_current_mission_id_robot_missions"), "robots", type_="foreignkey")
    op.drop_constraint(op.f("fk_robot_missions_order_id_orders"), "robot_missions", type_="foreignkey")
    op.drop_constraint(op.f("fk_orders_mission_id_robot_missions"), "orders", type_="foreignkey")

    op.drop_index(op.f("ix_idempotency_records_created_at"), table_name="idempotency_records")
    op.drop_table("idempotency_records")

    op.drop_index(op.f("ix_outbox_events_event_uid"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_event_type"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_created_at"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_correlation_id"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_channel"), table_name="outbox_events")
    op.drop_table("outbox_events")

    op.drop_index(op.f("ix_audit_logs_request_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_device_tokens_username"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_user_id"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_token"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_created_at"), table_name="device_tokens")
    op.drop_table("device_tokens")

    op.drop_index(op.f("ix_notifications_recipient"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_notification_type"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_event_uid"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_delivery_status"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_channel"), table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_mission_events_mission_id"), table_name="mission_events")
    op.drop_index(op.f("ix_mission_events_event_uid"), table_name="mission_events")
    op.drop_index(op.f("ix_mission_events_event_type"), table_name="mission_events")
    op.drop_index(op.f("ix_mission_events_created_at"), table_name="mission_events")
    op.drop_table("mission_events")

    op.drop_index(op.f("ix_order_status_history_order_id"), table_name="order_status_history")
    op.drop_index(op.f("ix_order_status_history_created_at"), table_name="order_status_history")
    op.drop_table("order_status_history")

    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_menu_item_id"), table_name="order_items")
    op.drop_table("order_items")

    op.drop_index(op.f("ix_orders_table_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_source"), table_name="orders")
    op.drop_index(op.f("ix_orders_payment_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_kind"), table_name="orders")
    op.drop_index(op.f("ix_orders_idempotency_key"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_created_at"), table_name="orders")
    op.drop_index(op.f("ix_orders_code"), table_name="orders")
    op.drop_index(op.f("ix_orders_assigned_robot_id"), table_name="orders")
    op.drop_table("orders")

    op.drop_index(op.f("ix_robot_missions_status"), table_name="robot_missions")
    op.drop_index(op.f("ix_robot_missions_robot_id"), table_name="robot_missions")
    op.drop_index(op.f("ix_robot_missions_order_id"), table_name="robot_missions")
    op.drop_index(op.f("ix_robot_missions_mission_uid"), table_name="robot_missions")
    op.drop_index(op.f("ix_robot_missions_dispatch_command_id"), table_name="robot_missions")
    op.drop_index(op.f("ix_robot_missions_created_at"), table_name="robot_missions")
    op.drop_table("robot_missions")

    op.drop_index(op.f("ix_robots_robot_id"), table_name="robots")
    op.drop_index(op.f("ix_robots_last_heartbeat_at"), table_name="robots")
    op.drop_index(op.f("ix_robots_current_mission_id"), table_name="robots")
    op.drop_index(op.f("ix_robots_created_at"), table_name="robots")
    op.drop_index(op.f("ix_robots_connection_status"), table_name="robots")
    op.drop_index(op.f("ix_robots_availability"), table_name="robots")
    op.drop_table("robots")

    op.drop_index(op.f("ix_restaurant_tables_table_number"), table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_public_code"), table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_is_active"), table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_destination_id"), table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_created_at"), table_name="restaurant_tables")
    op.drop_table("restaurant_tables")

    op.drop_index(op.f("ix_menu_items_name"), table_name="menu_items")
    op.drop_index(op.f("ix_menu_items_external_key"), table_name="menu_items")
    op.drop_index(op.f("ix_menu_items_created_at"), table_name="menu_items")
    op.drop_index(op.f("ix_menu_items_category"), table_name="menu_items")
    op.drop_index(op.f("ix_menu_items_available"), table_name="menu_items")
    op.drop_table("menu_items")

    op.drop_index(op.f("ix_customers_user_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_created_at"), table_name="customers")
    op.drop_table("customers")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_created_at"), table_name="users")
    op.drop_table("users")
