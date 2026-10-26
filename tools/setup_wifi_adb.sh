#!/usr/bin/env bash
# setup_wifi_adb.sh: one-shot per-Pixel WiFi ADB setup.
#
# Usage:
#   ./setup_wifi_adb.sh <usb-serial>
#
# Example:
#   ./setup_wifi_adb.sh <pixel9-serial>      # Pixel 9
#   ./setup_wifi_adb.sh <pixel3-serial>      # Pixel 3
#   ./setup_wifi_adb.sh <pixel6-serial>      # Pixel 6
#
# Prerequisites (the script verifies these and aborts cleanly on miss):
#   1. Device connected via USB AND showing in `adb devices` as "device" (authorized).
#   2. Device's Wi-Fi enabled and on the same network as the laptop.
#   3. Host laptop on the same network as the device.
#
# What it does:
#   1. Reads the device's wlan0 IPv4 address via `adb -s <serial> shell ip -4 addr show wlan0`.
#   2. Switches the device's ADB into TCP mode on port 5555 (`adb tcpip 5555`).
#   3. Connects the host to <ip>:5555 (`adb connect <ip>:5555`).
#   4. Verifies the new TCP entry shows in `adb devices` as "device".
#   5. Prints the lines to copy into device_state.yaml, a hand-kept per-phone registry.
#
# After this script succeeds, the device is reachable both via USB (<serial>) and
# WiFi (<ip>:5555). Subsequent AndroidRunner runs can target either by changing the
# device alias in their JSON config + devices.json mapping.
#
# Caveats:
#   - `adb tcpip 5555` is volatile: reset on phone reboot OR `adb usb` OR after a
#     long USB disconnect. Re-run this script per session if needed.
#   - The device IP can drift if the AP doesn't have a static DHCP reservation.
#     For long-term sweeps, reserve the IP in the router's DHCP table.

set -eu

if [ "${1:-}" = "" ]; then
    echo "Usage: $0 <usb-serial>" >&2
    echo "Available serials right now:" >&2
    adb devices | awk 'NR>1 && $2=="device" && $1 !~ /:/ {print "  " $1}' >&2
    exit 2
fi

SERIAL="$1"
ADB="${ADB:-adb}"

echo ">>> 1/5: verify USB-ADB connection for $SERIAL"
if ! $ADB devices | grep -q "^${SERIAL}.*device$"; then
    echo "ERROR: $SERIAL is not in 'adb devices' as 'device'." >&2
    echo "       Either it's not plugged in, or 'unauthorized' (accept the host fingerprint on the phone)." >&2
    $ADB devices >&2
    exit 1
fi
echo "    OK"

echo ">>> 2/5: read wlan0 IPv4 for $SERIAL"
IP=$($ADB -s "$SERIAL" shell ip -4 addr show wlan0 2>/dev/null \
    | awk '/inet / {split($2,a,"/"); print a[1]; exit}')
if [ -z "$IP" ]; then
    echo "ERROR: could not parse wlan0 IPv4 address. Is WiFi connected?" >&2
    $ADB -s "$SERIAL" shell ip -4 addr show wlan0 >&2 || true
    exit 1
fi
echo "    wlan0 IP = $IP"

echo ">>> 3/5: switch ADB on $SERIAL to TCP mode on port 5555"
$ADB -s "$SERIAL" tcpip 5555 >/dev/null
sleep 2
echo "    OK"

echo ">>> 4/5: connect host to $IP:5555"
# Disconnect any stale entry first; adb-connect is idempotent but a stale entry
# can otherwise confuse downstream `adb -s` calls.
$ADB disconnect "$IP:5555" >/dev/null 2>&1 || true
sleep 1
CONNECT_OUT=$($ADB connect "$IP:5555" 2>&1)
echo "    $CONNECT_OUT"
if ! echo "$CONNECT_OUT" | grep -qE "(connected|already connected)"; then
    echo "ERROR: failed to TCP-connect to $IP:5555" >&2
    exit 1
fi

echo ">>> 5/5: verify TCP-ADB entry"
sleep 1
if ! $ADB devices | grep -qE "^${IP}:5555.*device$"; then
    echo "ERROR: $IP:5555 not in 'adb devices' as 'device' after connect" >&2
    $ADB devices >&2
    exit 1
fi
echo "    OK"

echo ""
echo "============================================"
echo "WiFi ADB ready for $SERIAL (IP: $IP)."
echo ""
# device_state.yaml is a hand-kept registry of each phone's USB serial, Wi-Fi ADB
# address and hub port, filled from the lines below. It is not part of this
# package and no script here reads it: the sweep scripts keep serials and
# addresses in their own tables (WIFI_ADDR, SERIAL_OF), which a replicator edits
# instead, and tools/ykush_calibrate.py maps phones to hub ports.
echo "Add to your device registry (device_state.yaml):"
echo "  - serial:    $SERIAL"
echo "    wifi_addr: $IP:5555"
echo "    hub:       <hub-id from \`sudo uhubctl\`, e.g. 3-6.1>"
echo "    port:      <port number, e.g. 3>"
echo ""
echo "Add to android-runner/devices.json (if not already present):"
echo "  \"<friendly-name>-W\": \"$IP:5555\","
echo ""
echo "Test it with:"
echo "  $ADB -s $IP:5555 shell getprop ro.product.model"
echo "============================================"
