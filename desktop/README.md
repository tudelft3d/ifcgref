# IfcGref Desktop

The desktop app processes and stores IFC files locally and opens its interface in your browser. The map viewer also downloads JavaScript, WebAssembly, and basemap resources from external services, so it needs network access.

## Local run

Install the dependencies using the [quick start](../README.md#quick-start), then run this from the repository root:

```bash
python desktop_app.py
```

The launcher binds to `127.0.0.1`, chooses a free port, and opens the browser automatically. Set `IFCGREF_PORT` in the process environment to choose a port, or `IFCGREF_NO_BROWSER=1` to disable automatic browser opening. The desktop launcher always disables Flask debug mode.

## Data and configuration

Default data directories are:

| Launch method | Data directory |
| --- | --- |
| Windows, when `LOCALAPPDATA` is set | `%LOCALAPPDATA%\IfcGref` |
| Local or native launcher without `LOCALAPPDATA`, including macOS | `~/.ifcgref` |
| Portable macOS launcher | `~/Library/Application Support/IfcGref` |

IFC uploads and generated files go in `uploads/` under the data directory. Job status and model state go in `uploads/jobs/` and `uploads/state/`. `IFCGREF_UPLOAD_FOLDER` can override the upload directory.

Existing process environment values take precedence over `.env` files. The application reads its own `.env` first, then the working directory's `.env`, filling only settings that are still unset. The desktop launcher chooses its port and data directory before those files are read: set `IFCGREF_PORT` and `IFCGREF_DATA_DIR` in the process environment when starting `desktop_app.py` or a native package. The portable macOS wrapper sets its data directory to the path in the table.

Desktop mode has no Flask upload size limit unless `IFCGREF_MAX_UPLOAD_MB` is explicitly set. See the main [configuration reference](../README.md#configuration) for other settings, including retention cleanup.

## Building packages

Run build commands from the repository root. **All three build scripts include the repository's `.env` in the package when it exists.** Keep only settings intended for distribution in that file.

The Windows and native macOS scripts install the application requirements and PyInstaller into the Python environment used for the build, then package the runtime and application.

### Windows

Run on Windows:

```powershell
.\desktop\build_windows.ps1 -Version 3.0
```

Output: `desktop_dist/IfcGref-Desktop-Windows-3.0.zip`. Extract the archive and run `IfcGref-Desktop.exe`, keeping the other extracted files alongside it.

### Native macOS

Run on a Mac:

```bash
bash ./desktop/build_macos.sh 3.0
```

Output: `desktop_dist/IfcGref-Desktop-macOS-3.0.zip`. Extract the archive and open `IfcGref-Desktop.app`.

### Portable macOS launcher

Create the launcher package from any OS:

```bash
python desktop/build_macos_portable.py 3.0
```

Output: `desktop_dist/IfcGref-Desktop-macOS-3.0.zip`. This contains application source and a launcher that prepares its Python environment on the destination Mac.

The destination Mac needs `/usr/bin/python3` with `venv` support. The launcher creates a virtual environment at `~/Library/Application Support/IfcGref/venv` when it is missing. **Every launch** runs `pip install --upgrade pip` and `pip install -r requirements.txt` before starting IfcGref. These commands can contact the configured package index on every launch; plan for network access at startup.

Native and portable macOS builds use the same ZIP filename for a given version. Building both with the same version replaces the earlier archive; use distinct version labels if keeping both.

## Serving packages

The home page offers separate Windows and Mac download buttons. Each selects the matching package with the most recent **file modification time** from `desktop_dist/` under the server's working directory. Set `IFCGREF_DESKTOP_PACKAGE_DIR` to serve packages from another directory.
