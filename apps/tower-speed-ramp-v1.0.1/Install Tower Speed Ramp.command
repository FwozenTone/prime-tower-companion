#!/bin/zsh
cd -- "${0:A:h}"
if [[ -x /opt/homebrew/bin/python3 ]]; then
 /opt/homebrew/bin/python3 install_ramp.py
else
 python3 install_ramp.py
fi
printf '\nPress Return to close. '
read
