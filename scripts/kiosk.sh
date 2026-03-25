#!/bin/bash
# Espresso Bridge Kiosk Mode — Portrait via wlr-randr
#
# Launched automatically on tty1 via .bash_profile.
# Requires: cage, chromium, wlr-randr

export WLR_LIBINPUT_NO_DEVICES=1
export WLR_NO_HARDWARE_CURSORS=1
export XDG_RUNTIME_DIR=/run/user/$(id -u)

# Wait for espresso-bridge service to be ready
for i in $(seq 1 30); do
    curl -s http://localhost:8080/api/status > /dev/null 2>&1 && break
    sleep 1
done

# Rotate display to portrait after cage starts
/opt/espresso-bridge/rotate.sh &

exec cage -d -- chromium \
    --no-memcheck \
    --kiosk \
    --no-first-run \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --noerrdialogs \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --check-for-update-interval=31536000 \
    --disable-component-update \
    --suppress-message-center-popups \
    --disable-notifications \
    --autoplay-policy=no-user-gesture-required \
    http://localhost:8080
