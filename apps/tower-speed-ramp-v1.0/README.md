# Tower Speed Ramp — independent Orca post-processing script

This application does not change the Prime Tower Wizard, tower geometry, path order, pickup scheduling, or Orca settings. Python 3.10+ is required. Keep all three Python runtime files together. `tower_project.py` is a private snapshot of the existing project reader/auditor, so this application does not import the installed wizard.

## Installation and setup

On macOS, double-click **Install Tower Speed Ramp.command**. It installs into `~/Documents/voron/projects/Tower_Speed_Ramp_v1.0` and prints the command to paste into Orca. On Windows or Linux, copy this folder to a permanent location and use the full path of your Python executable and `orca_tower_ramp.py`.

In **Process > Others > Post Processing Scripts**, add this command LAST, preserving other scripts. Replace the project path with the actual saved, generated tower 3MF for the selected plate:

```text
"/opt/homebrew/bin/python3" "/Users/tonyo/Documents/voron/projects/Tower_Speed_Ramp_v1.0/orca_tower_ramp.py" --project "/FULL/PATH/TO/tower.3mf" --start-percent 25 --volume 5
```

Do not append a G-code filename in Orca; Orca supplies it. The interpreter path must exist on that computer. For Windows use a fully qualified `python.exe` path; for Linux use the installed Python 3 executable's full path. The script is local, requires no network, and sends no print commands.

## Workflow

1. Use the restored wizard as before to generate the tower and open the project in Orca.
2. Inspect/move the tower, then SAVE the tower 3MF at the path configured above. Keep its `.3mf.ipt.json` sidecar beside it. Saving is necessary because the audit reads the saved tower position.
3. Configure this script only in that generated tower project. Do not add it to the original reference project or a global preset used to generate reference slices. For multi-plate projects, use a separate saved project/command for the intended plate; exporting a different plate will fail the audit.
4. Slice and review in Orca, then export/send normally. The export hook applies the ramp and audits the result. The exported filename remains the one you chose in Orca; there is no separate `print_ready` workflow.
5. For another tower project, update `--project`. After any tower movement, save and reslice before exporting.

Start defaults to 25% of the original sliced feed rate. Volume defaults to 5 mm³ of deposited tower filament after each changed Tn selection. Speed increases in small volume-based steps to 100%; original travel, retraction and model feed rates are restored. Retraction repayment does not count toward the ramp. Set `--start-percent 100` to disable slowing. Choose a ramp volume no greater than the tower's available priming volume; ideally set the generator's purge volume to at least that value. An insufficient-volume audit fails without modifying G-code.

The script pre-audits, creates a temporary ramped file, audits it, then atomically replaces only the working G-code supplied by Orca. Failures return a nonzero exit and retain the original working file. Repeated application is rejected rather than slowing already-ramped moves again. No inner-first checks or reordering are included.

For a Voron/non-Bambu profile, Orca runs post-processors during export/send. Normal slicer Preview and sliced 3MF retain the original speeds. Open the exported plain G-code in Orca if you want to inspect the adjusted speeds. The original time estimate is not recalculated. Scripts configured AFTER this one could invalidate its audit, so keep this one last.

The invocation/error contract was checked against OrcaSlicer v2.4.2 `src/libslic3r/GCode/PostProcessor.cpp`. Synthetic tests cover successful replacement, failure preservation, repeat rejection, saved-project movement, extrusion/feed preservation and the existing audit. End-to-end GUI export/send and physical printing still require testing on your installation.
