#!/bin/bash
# Hardware regression check: require the AI camera and acquire a still without saving an image.
set -euo pipefail
cameras=$(rpicam-hello --list-cameras 2>&1)
printf '%s\n' "$cameras"
if [[ "$cameras" != *imx500* ]]; then
    echo 'FAIL: IMX500 camera is not enumerated.' >&2
    exit 1
fi
rpicam-still --nopreview --timeout 1000 --output /dev/null
