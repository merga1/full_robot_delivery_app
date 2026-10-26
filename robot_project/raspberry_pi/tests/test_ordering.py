"""Ordering tests — menu, tables, customer orders, kitchen board, QR page.

Run with:  cd raspberry_pi && python -m pytest -q   (no ROS needed).
"""
import time

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def wait_for(pred, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def make_item(client, name="Espresso", price=2.5, **kw):
    body = {"name": name, "price": price}
    body.update(kw)
    r = client.post("/kitchen/api/menu", json=body)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def make_table(client, name="Table 1", destination="", code=None):
    body = {"name": name, "destination": destination, "seats": 4}
    if code:
        body["code"] = code
    r = client.post("/kitchen/api/tables", json=body)
    assert r.status_code == 200, r.text
    return r.json()["table"]


def customer_session(client, table_code):
    r = client.post("/customer/session", json={"table_code": table_code})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── auth / roles ──────────────────────────────────────────────────────────────

def test_kitchen_can_log_in(client):
    r = client.post("/auth/login", json={"username": "kitchen", "password": "kitchenpw"})
    assert r.status_code == 200 and r.json()["role"] == "kitchen"


def test_kitchen_token_cannot_change_the_robot(client):
    token = client.post("/auth/login", json={"username": "kitchen",
                                             "password": "kitchenpw"}).json()["access_token"]
    h = {"Authorization": "Bearer " + token}
    assert client.get("/kitchen/api/orders", headers=h).status_code == 200
    assert client.post("/robot/stop", headers=h).status_code == 403


def test_operator_token_may_read_the_orders_but_not_change_them(client):
    token = client.post("/auth/login", json={"username": "user",
                                             "password": "userpw"}).json()["access_token"]
    h = {"Authorization": "Bearer " + token}
    assert client.get("/kitchen/api/orders", headers=h).status_code == 200
    assert client.post("/kitchen/api/orders/1/advance", headers=h).status_code == 403


def test_anonymous_cannot_reach_the_kitchen(client):
    assert client.get("/kitchen/api/orders", headers={"Authorization": ""}).status_code in (401, 403)
    r = client.get("/kitchen/api/orders", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_token_can_come_from_a_cookie(client):
    token = client.post("/auth/login", json={"username": "admin",
                                             "password": "adminpw"}).json()["access_token"]
    r = client.get("/kitchen/api/orders", cookies={"robot_token": token})
    assert r.status_code == 200


# ── menu & tables (kitchen admin) ─────────────────────────────────────────────

def test_menu_crud(client):
    item = make_item(client, "Latte", 3.25, description="espresso + milk")
    assert item["name"] == "Latte" and item["price"] == 3.25
    assert any(i["id"] == item["id"] for i in client.get("/kitchen/api/menu").json()["menu"])
    r = client.post("/kitchen/api/menu", json={"id": item["id"], "name": "Latte",
                                               "price": 3.75, "description": "espresso + milk",
                                               "category": "Drinks", "available": True})
    assert r.status_code == 200 and r.json()["item"]["price"] == 3.75
    assert client.delete(f"/kitchen/api/menu/{item['id']}").status_code == 200
    assert client.delete(f"/kitchen/api/menu/{item['id']}").status_code == 404


def test_menu_rejects_bad_prices_and_empty_names(client):
    assert client.post("/kitchen/api/menu", json={"name": "", "price": 1}).status_code == 400
    assert client.post("/kitchen/api/menu", json={"name": "x", "price": -1}).status_code == 400
    assert client.post("/kitchen/api/menu", json={"name": "x", "price": 1e9}).status_code == 400


def test_table_crud_and_generated_code(client):
    table = make_table(client, "Window")
    assert table["code"] and table["active"] is True
    assert client.get("/kitchen/api/tables").json()["tables"]
    r = client.post("/kitchen/api/tables", json={"code": table["code"], "name": "Window 2",
                                                  "destination": "", "seats": 6, "active": True})
    assert r.status_code == 200 and r.json()["table"]["name"] == "Window 2"
    assert client.delete(f"/kitchen/api/tables/{table['code']}").status_code == 200
    assert client.delete(f"/kitchen/api/tables/{table['code']}").status_code == 404


def test_table_needs_a_real_destination(client):
    r = client.post("/kitchen/api/tables", json={"name": "Ghost", "destination": "Nowhere",
                                                 "seats": 2, "active": True})
    assert r.status_code == 400 and "Nowhere" in r.json()["detail"]


# ── the customer flow ─────────────────────────────────────────────────────────

def test_customer_places_an_order_and_the_kitchen_sees_it(client):
    item = make_item(client, "Soup", 6.0)
    table = make_table(client, "T-Soup")
    token = customer_session(client, table["code"])
    h = {"Authorization": "Bearer " + token}

    r = client.post("/customer/orders", json={"items": [{"menu_id": item["id"], "qty": 2}],
                                              "note": "no onions"}, headers=h)
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["status"] == "placed" and order["total"] == 12.0
    assert order["items"][0]["name"] == "Soup" and order["items"][0]["qty"] == 2

    board = client.get("/kitchen/api/orders").json()["orders"]
    assert any(o["code"] == order["code"] for o in board)


def test_the_client_never_decides_the_price(client):
    item = make_item(client, "Water", 1.0)
    table = make_table(client, "T-Water")
    token = customer_session(client, table["code"])
    r = client.post("/customer/orders",
                    json={"items": [{"menu_id": item["id"], "qty": 3, "price": 0.01}]},
                    headers={"Authorization": "Bearer " + token})
    assert r.json()["order"]["total"] == 3.0        # server price, not the client's


def test_customer_cannot_order_a_sold_out_item(client):
    item = make_item(client, "Special", 5.0)
    client.post("/kitchen/api/menu", json={"id": item["id"], "name": "Special", "price": 5.0,
                                           "category": "General", "available": False})
    table = make_table(client, "T-Sold")
    token = customer_session(client, table["code"])
    r = client.post("/customer/orders", json={"items": [{"menu_id": item["id"], "qty": 1}]},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 400 and "sold out" in r.json()["detail"]


def test_an_empty_order_is_refused(client):
    table = make_table(client, "T-Empty")
    token = customer_session(client, table["code"])
    r = client.post("/customer/orders", json={"items": []},
                    headers={"Authorization": "Bearer " + token})
    assert r.status_code == 400 and "empty" in r.json()["detail"]


def test_the_order_needs_a_customer_token(client):
    table = make_table(client, "T-NoToken")
    # the test client carries the admin token — it is not a customer token
    assert client.post("/customer/orders", json={"items": []}).status_code == 403
    # ... and without any credentials at all it is a 401
    assert client.post("/customer/orders", json={"items": []},
                       headers={"Authorization": ""}).status_code == 401


def test_a_customer_token_only_works_for_its_own_table(client):
    a = make_table(client, "T-A")
    b = make_table(client, "T-B")
    token_a = customer_session(client, a["code"])
    token_b = customer_session(client, b["code"])
    h_a = {"Authorization": "Bearer " + token_a}
    h_b = {"Authorization": "Bearer " + token_b}

    r = client.post("/customer/orders", json={"items": [], "table_code": b["code"]}, headers=h_a)
    assert r.status_code == 400                        # empty order is refused anyway

    order = client.post("/customer/calls", json={}, headers=h_a).json()["order"]
    # the table of the token wins over the one in the body
    assert order["table_code"] == a["code"]

    # table B cannot read the order of table A
    r = client.get(f"/customer/orders/{order['code']}", headers=h_b)
    assert r.status_code == 403


def test_the_customer_tracks_the_order(client):
    table = make_table(client, "T-Track")
    token = customer_session(client, table["code"])
    h = {"Authorization": "Bearer " + token}
    order = client.post("/customer/calls", json={}, headers=h).json()["order"]
    r = client.get(f"/customer/orders/{order['code']}", headers=h)
    assert r.status_code == 200 and r.json()["order"]["code"] == order["code"]


def test_an_unknown_table_has_no_session(client):
    assert client.post("/customer/session", json={"table_code": "ZZZZZ"}).status_code == 404


# ── the kitchen moves the order along ─────────────────────────────────────────

def test_the_kitchen_walks_the_order_to_the_robot(client):
    client.post("/destinations", json={"name": "Kitchen Dock", "x": 1.0, "y": 1.0, "theta": 0})
    item = make_item(client, "Pasta", 9.0)
    table = make_table(client, "T-Pasta", destination="Kitchen Dock")
    token = customer_session(client, table["code"])
    order = client.post("/customer/orders", json={"items": [{"menu_id": item["id"], "qty": 1}]},
                        headers={"Authorization": "Bearer " + token}).json()["order"]
    oid = order["id"]

    assert client.post(f"/kitchen/api/orders/{oid}/advance").json()["order"]["status"] == "accepted"
    assert client.post(f"/kitchen/api/orders/{oid}/advance").json()["order"]["status"] == "preparing"
    assert client.post(f"/kitchen/api/orders/{oid}/advance").json()["order"]["status"] == "ready"

    r = client.post(f"/kitchen/api/orders/{oid}/advance")
    assert r.status_code == 200
    assert r.json()["order"]["status"] == "delivering"
    assert r.json()["order"]["mission_id"]

    # the mission drives the robot and the order ends up "delivered"
    assert wait_for(lambda: client.get(f"/customer/orders/{order['code']}",
                                       headers={"Authorization": "Bearer " + token}
                                       ).json()["order"]["status"] == "delivered")


def test_the_kitchen_can_cancel_an_order(client):
    table = make_table(client, "T-Cancel")
    token = customer_session(client, table["code"])
    order = client.post("/customer/calls", json={},
                        headers={"Authorization": "Bearer " + token}).json()["order"]
    r = client.post(f"/kitchen/api/orders/{order['id']}/cancel", json={"reason": "no stock"})
    assert r.status_code == 200 and r.json()["order"]["status"] == "cancelled"
    assert r.json()["order"]["error"] == "no stock"


def test_a_table_without_a_destination_cannot_be_dispatched(client):
    table = make_table(client, "T-NoDest")
    token = customer_session(client, table["code"])
    order = client.post("/customer/calls", json={},
                        headers={"Authorization": "Bearer " + token}).json()["order"]
    r = client.post(f"/kitchen/api/orders/{order['id']}/dispatch")
    assert r.status_code == 400 and "destination" in r.json()["detail"]


def test_calling_the_robot_sends_it_to_the_table(client):
    client.post("/destinations", json={"name": "Call Dock", "x": 2.0, "y": 2.0, "theta": 0})
    table = make_table(client, "T-Call", destination="Call Dock")
    token = customer_session(client, table["code"])
    r = client.post("/customer/calls", json={}, headers={"Authorization": "Bearer " + token})
    assert r.status_code == 200
    assert r.json()["dispatched"] is True
    assert r.json()["order"]["status"] == "delivering"
    assert wait_for(lambda: client.get(f"/customer/orders/{r.json()['order']['code']}",
                                       headers={"Authorization": "Bearer " + token}
                                       ).json()["order"]["status"] == "delivered")


# ── the web pages ─────────────────────────────────────────────────────────────

def test_the_order_page_is_served_for_a_table(client):
    table = make_table(client, "T-Page")
    r = client.get(f"/o/{table['code']}")
    assert r.status_code == 200
    assert table["code"] in r.text and "Menu" in r.text
    assert client.get("/o/NOPE").status_code == 404


def test_the_kitchen_page_is_served(client):
    r = client.get("/kitchen")
    assert r.status_code == 200 and "Kitchen" in r.text
    assert client.get("/kitchen/login").status_code == 200


def test_the_qr_code_is_a_png(client):
    table = make_table(client, "T-Qr")
    r = client.get(f"/qr/{table['code']}.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:4] == b"\x89PNG"


def test_the_static_assets_are_served(client):
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/order.js").status_code == 200
    assert client.get("/static/kitchen.js").status_code == 200


# ── devices (the kiosk on the robot and the admin phones) ─────────────────────

def test_devices_can_register_and_heartbeat(client):
    r = client.post("/devices/register", json={"device_id": "kiosk-1", "name": "Robot screen",
                                               "kind": "kiosk"})
    assert r.status_code == 200 and r.json()["device"]["device_id"] == "kiosk-1"
    assert client.post("/devices/heartbeat", json={"device_id": "kiosk-1"}).status_code == 200
    ids = [d["device_id"] for d in client.get("/devices").json()["devices"]]
    assert "kiosk-1" in ids
    assert client.delete("/devices/kiosk-1").status_code == 200
