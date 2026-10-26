#!/usr/bin/env python3
"""
pi_beacon.py — lets the tablet app find this Pi on the Wi-Fi.

Protocol (matches flutter_app/lib/services/pi_discovery_service.dart):
  * the app broadcasts  {"discover": "robot-control"}  to UDP port 9999
  * the Pi answers      {"service":"robot-control","user":...,"host":...,
                         "ip":...,"api_port":8000,"reply":true}
  * the Pi also announces itself every 5 s (reply=false)
  * optional: mDNS service  _robotcontrol._tcp.local.  (needs `zeroconf`)

Run:  python3 pi_beacon.py        (or enable robot-beacon.service)
"""

import getpass
import json
import os
import socket
import threading
import time

PORT = 9999
API_PORT = int(os.getenv("ROBOT_API_PORT", os.getenv("PORT", "8000")))
USER = os.getenv("ROBOT_PI_USER") or getpass.getuser()
HOST = socket.gethostname()


def local_ip_towards(addr: str = "8.8.8.8") -> str:
    """IP of the interface that would be used to reach `addr` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((addr, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def payload(ip: str, reply: bool) -> bytes:
    return json.dumps({"service": "robot-control", "user": USER, "host": HOST,
                       "ip": ip, "api_port": API_PORT, "reply": reply}).encode()


def start_mdns():
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("[beacon] zeroconf not installed — mDNS disabled (UDP discovery still works)")
        return None
    ip = local_ip_towards()
    info = ServiceInfo(
        "_robotcontrol._tcp.local.", f"{USER}-{HOST}._robotcontrol._tcp.local.",
        addresses=[socket.inet_aton(ip)], port=API_PORT,
        properties={"user": USER, "host": HOST, "api_port": str(API_PORT)},
        server=f"{HOST}.local.")
    zc = Zeroconf()
    zc.register_service(info)
    print(f"[beacon] mDNS registered {info.name} on {ip}")
    return zc


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", PORT))
    print(f"[beacon] listening on UDP {PORT} as {USER}@{HOST}, API port {API_PORT}")
    start_mdns()

    def announce() -> None:
        while True:
            try:
                sock.sendto(payload(local_ip_towards(), False), ("255.255.255.255", PORT))
            except OSError:
                pass
            time.sleep(5)

    threading.Thread(target=announce, daemon=True).start()

    while True:
        data, addr = sock.recvfrom(2048)
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(msg, dict) and msg.get("discover") == "robot-control":
            sock.sendto(payload(local_ip_towards(addr[0]), True), addr)


if __name__ == "__main__":
    main()
