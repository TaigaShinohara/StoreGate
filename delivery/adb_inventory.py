"""Read the local adb server's device list without starting or stopping adb."""
import socket


def connected_devices():
    # adb's host protocol uses a four-digit hex length before each message.
    with socket.create_connection(("127.0.0.1", 5037), timeout=1) as connection:
        with connection.makefile("rb") as data:
            connection.sendall(b"000ehost:devices-l")
            if data.read(4) != b"OKAY":
                raise ValueError("adb rejected the device list request")
            size = int(data.read(4), 16)
            payload = data.read(size)
            if len(payload) != size:
                raise ValueError("Incomplete adb device list")
    rows = [line.split() for line in payload.decode("utf-8").splitlines() if line.strip()]
    if any(len(row) < 2 for row in rows):
        raise ValueError("Invalid adb device list")
    physical = [row for row in rows if not row[0].startswith("emulator-")]
    return (sum(row[1] == "device" for row in physical),
            sum(row[1] != "device" for row in physical),
            sum(row[0].startswith("emulator-") and row[1] == "device" for row in rows))
