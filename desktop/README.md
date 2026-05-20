# IfcGref Desktop

The desktop app runs the same IfcGref Flask tool on `127.0.0.1` and opens it in the user's browser. Uploaded IFC files are stored on the user's own machine instead of being sent to the hosted server.

## Local Run

```powershell
python desktop_app.py
```

Desktop mode sets:

- `IFCGREF_DESKTOP=1`
- `IFCGREF_DATA_DIR=%LOCALAPPDATA%\IfcGref`
- no Flask upload size limit unless `IFCGREF_MAX_UPLOAD_MB` is explicitly set

## Build Windows Package

```powershell
.\desktop\build_windows.ps1 -Version 3.0
```

The build creates:

```text
desktop_dist\IfcGref-Desktop-Windows-3.0.zip
```

## Build macOS Package

Preferred native build, run on a Mac:

```bash
bash ./desktop/build_macos.sh 3.0
```

Portable launcher build, can be created from any OS:

```bash
python desktop/build_macos_portable.py 3.0
```

The build creates:

```text
desktop_dist/IfcGref-Desktop-macOS-3.0.zip
```

The portable macOS launcher creates a local virtual environment on first launch and installs the Python requirements inside `~/Library/Application Support/IfcGref`. Files stay local.

The hosted home page has separate Windows and Mac buttons. Each button downloads the newest package for that platform found in `desktop_dist`.
