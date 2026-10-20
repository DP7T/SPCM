# Ideas for later (agreed with the user, not built yet)

Read together with [AGENTS.md](AGENTS.md). Each item says what the user decided. Build them
one at a time on `dev`, test on the microscope, then release as usual.

Built in 1.2.6: point / line cursors, clearer phase wording, phase-lag map, phase memory per
sample (with a start hint; the program still never writes the lock-in PHASE).

## 1. Check the saved data
The user will look at saved files (h5, CSV, PNG, info.txt) and list what should change.
Collect that list here before changing the file formats (Origin import must keep working,
`scanner.load()` must keep reading old files).

