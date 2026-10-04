#!/bin/sh
# shairport-sync sessioncontrol hook: tells wk7-moode that AirPlay is playing,
# so the moOde source steps aside, and hands back when AirPlay stops.
#   sessioncontrol = {
#     run_this_before_entering_active_state = "/usr/local/bin/wk7-airplay-hook start";
#     run_this_after_exiting_active_state  = "/usr/local/bin/wk7-airplay-hook stop";
#   };
FLAG=/run/wk7/airplay-active
mkdir -p /run/wk7
case "$1" in
  start) : > "$FLAG" ;;
  stop)  rm -f "$FLAG" ;;
esac
