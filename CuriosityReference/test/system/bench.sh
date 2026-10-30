#!/bin/bash
# Bench control for the FramDriver system test (Pi side). No test logic here.
# usage: bench.sh gds-start <archive-dir> | gds-stop | rec-start <archive-dir> | rec-stop | cmd <mnemonic> [--arguments ...] -l <dir> | flash <elf> | reset
set -u
D=/home/devin/fram-test/fvp
V=/home/devin/samd-venv/bin
DICT="--dictionary $D/TopTopologyDictionary.json"
Z="--zmq-transport ipc:///tmp/fprime-fvp-in ipc:///tmp/fprime-fvp-out"
# number of top-level processes whose command line matches (a GDS / recorder forks children with the same command line)
roots() { for p in $(pgrep -f "$1"); do pp=$(ps -o ppid= -p "$p" | tr -d " "); pgrep -f "$1" | grep -qx "$pp" || echo "$p"; done | wc -l; }
OOCD="openocd -f interface/cmsis-dap.cfg -c \"adapter serial MCHP3313053000002254\" -c \"transport select swd\" -f target/at91samdXX.cfg"
case "$1" in
  gds-start)
    mkdir -p "$2"
    nohup $V/fprime-gds -n $DICT --packet-set-name Main \
      --communication-selection uart --uart-device /dev/samd21-curiosity --uart-baud 115200 \
      --framing-selection fprime --gui none --logs "$2" $Z > "$2/gds.out" 2>&1 &
    sleep 12; roots "fprime-gds -n" ;;              # prints the number of GDS instances (expect 1)
  gds-stop) pkill -f "zmq-transport ipc:///tmp/fprime-fvp" ; sleep 2 ;;
  rec-start)
    mkdir -p "$2"
    # one recorder only: `fprime-cli events` logs channels, events and commands it sees; a second one duplicates every row
    nohup $V/fprime-cli events $DICT $Z --logs "$2" > /dev/null 2>&1 &
    sleep 3; roots "fprime-cli events" ;;            # prints the number of recorders (expect 1)
  rec-stop) pkill -f "fprime-cli events" ;;
  cmd) shift; timeout 20 $V/fprime-cli command-send "$@" $DICT $Z ;;
  flash) eval timeout 120 $OOCD -c "\"program $2 verify reset exit\"" 2>&1 ;;
  reset) eval timeout 60 $OOCD -c "\"init\"" -c "\"reset run\"" -c "\"exit\"" ;;
  *) echo "unknown: $1"; exit 2 ;;
esac
