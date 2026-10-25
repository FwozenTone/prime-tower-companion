#!/bin/zsh
cd -- "${0:A:h}"
if [[ -x /opt/homebrew/bin/python3 ]]; then
 /opt/homebrew/bin/python3 install_wizard.py
else
 python3 install_wizard.py
fi
printf '\nPress Return to close. '
read
