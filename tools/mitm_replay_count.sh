#!/usr/bin/env bash
# mitm_replay_count.sh: count replayed responses served TO ONE PHONE.
#
# Written 2026-08-04.
#
# WHY THIS EXISTS
# ---------------
# The sweep proves a Tier-A cell actually used its recorded flow by counting
# "[replay]" lines in the mitmdump log before and after the cell and taking the
# delta. That log is SHARED by all three phones, so with two phones running a
# Tier-A app at the same time each cell's delta includes the other's traffic.
#
# Measured on 2026-08-04: pdfviewer cells served 5 responses each, EXCEPT the one
# overlapping pair (pixel3 03:46:01-03:50:57 and pixel9 03:46:41-03:51:40) which
# reported 8 and 10, each inflated by the other's requests.
#
# The direction of that error is the dangerous one. If phone A serves 5 and phone
# B serves ZERO, B's global delta still reads ~5 and B PASSES: an un-proxied cell
# slips through the very guard meant to catch it. Over-counting can only ever mask
# a failure, never invent one.
#
# HOW ATTRIBUTION WORKS
# ---------------------
# mitmdump writes the request line with the client address, then the replay marker
# immediately after:
#
#     <pixel6-ip>:47052: GET https://css4.pub/2015/usenix/example.pdf HTTP/2.0
#     [replay] << HTTP/2.0 200 OK 354k
#
# So: remember the client IP from the most recent request line, and attribute the
# next "[replay]" to it. Interleaving under heavy concurrency can still mis-assign
# an individual line, but it is bounded and vastly better than a global count,
# and a phone that served NOTHING reads 0 instead of borrowing its neighbour's
# traffic.
#
# Usage:
#   mitm_replay_count.sh <logfile> <client_ip>      # -> integer on stdout
#   mitm_replay_count.sh <logfile> --all            # -> "ip count" per line
set -uo pipefail

log="${1:-}"
who="${2:-}"

if [ -z "$log" ] || [ -z "$who" ]; then
    echo "usage: mitm_replay_count.sh <logfile> <client_ip|--all>" >&2
    exit 64
fi

# Missing log is not an error: the caller uses this to compute a delta, and 0 is
# the correct answer before mitmproxy has written anything.
[ -f "$log" ] || { [ "$who" = "--all" ] || echo 0; exit 0; }

if [ "$who" = "--all" ]; then
    awk '
        /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+: / { ip=$1; sub(/:[0-9]+:$/,"",ip); last=ip; next }
        /^\[replay\]/ { if (last != "") c[last]++ }
        END { for (k in c) printf "%s %d\n", k, c[k] }
    ' "$log" | sort
else
    awk -v want="$who" '
        /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+: / { ip=$1; sub(/:[0-9]+:$/,"",ip); last=ip; next }
        /^\[replay\]/ { if (last == want) n++ }
        END { print n+0 }
    ' "$log"
fi
