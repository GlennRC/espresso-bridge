#!/bin/bash
# Rotate display to portrait mode (90° CW).
# Called from kiosk.sh after cage starts.
# Requires: wlr-randr

export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/$(id -u)
sleep 5
wlr-randr --output HDMI-A-1 --transform 90
