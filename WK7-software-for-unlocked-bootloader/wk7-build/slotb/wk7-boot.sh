#!/system/bin/sh
# WK7 Linux firmware boot hook. Installed only in system_b; runs once after Android has booted.
# Everything else (USB/Wi-Fi routing, AirPlay, buttons, dashboard, moOde) lives in /data/wk7linux and is
# started by "wk7ctl start all", so it can be updated without touching system_b.
log -t wk7 "boot hook start, slot $(getprop ro.boot.slot_suffix)"
[ "$(getprop ro.boot.slot_suffix)" = "_b" ] || { log -t wk7 "not slot B, exiting"; exit 0; }

# Tell the bootloader slot B booted fine (otherwise it falls back to slot A after its retries)
bootctl mark-boot-successful && log -t wk7 "slot B marked successful"

# Stop the LG/Android pieces that conflict with our stack (runtime only; slot A is unaffected)
am force-stop com.lge.smartspeaker
stop peripheralman      # owns the MICOM UART otherwise
stop mdnsd              # clashes with avahi

# No waiting for Wi-Fi here: wk7-usbroute (inside "start all") decides cable vs Wi-Fi and brings Wi-Fi up
/data/wk7-enter.sh -c "wk7ctl start all" && log -t wk7 "wk7 services started"
