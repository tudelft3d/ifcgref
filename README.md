# IfcGref

IfcGref georeferences IFC (Industry Foundation Classes) models to a projected
coordinate reference system (CRS). Use the [hosted web application](https://ifcgref.bk.tudelft.nl)
or run it locally in your browser. Upload a model, choose a target EPSG code,
provide survey points when needed, then preview and download the georeferenced IFC file.

![IfcGref application](https://github.com/tudelft3d/ifcgref/assets/50393714/e335cd23-d063-4f86-8cdf-d9898b6a955a)

## Quick start

Use Python 3.11 to match the [desktop build workflow](.github/workflows/build-desktop.yml).
Create a virtual environment from the repository root. On systems where Python
is named `python3`, use that command to create the environment.

```bash
git clone https://github.com/tudelft3d/ifcgref.git
cd ifcgref
python -m venv .venv
```

Activate the environment on Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install the dependencies and start the Flask development server:

```bash
python -m pip install -r requirements.txt
python app.py
```

Open [http://127.0.0.1:5000/](http://127.0.0.1:5000/).

### Desktop launcher

After installing the dependencies, you can instead start the desktop launcher:

```bash
python desktop_app.py
```

It opens your browser automatically, selects an available local port, and stores
IFC files on your machine. The viewer downloads JavaScript, WebAssembly, and map
resources from external services. See the [desktop guide](desktop/README.md)
for data locations, configuration, and Windows/macOS packaging instructions.

## Workflow

1. Upload a named `.ifc` file.
2. If the model already has georeferencing, inspect its values and view it on the map.
3. To georeference a model, enter the EPSG code of the target projected CRS.
4. Choose whether to use existing site information, add survey points to it, or
   use survey points alone. Enter matching coordinates in the model and target CRS
   when prompted.
5. Calculate the transformation, inspect the results and map preview, and download
   the georeferenced IFC file.

![IfcGref georeferencing workflow](https://github.com/tudelft3d/ifcgref/assets/50393714/3d14b4c7-9652-4b77-bc5b-77bd2a736341)

## IFC compatibility

IfcGref uses `IfcMapConversion` and `IfcProjectedCRS` for IFC4-family models, and
property sets for IFC2X3. The code accepts IFC2X3 and IFC4-family schema names.
Regression tests cover IFC2X3 and IFC4; there are currently no explicit IFC4.3
test fixtures.

For reference, these release names correspond to the following version numbers:

| Version | Release |
| ------- | ------- |
| 4.3.2.0 | IFC 4.3 ADD2 |
| 4.0.2.1 | IFC4 ADD2 TC1 |
| 4.0.2.0 | IFC4 ADD2 |
| 4.0.1.0 | IFC4 ADD1 |
| 4.0.0.0 | IFC4 |
| 2.3.0.1 | IFC2x3 TC1 |
| 2.3.0.0 | IFC2x3 |

See the [buildingSMART IFC specifications](https://technical.buildingsmart.org/standards/ifc/ifc-schema-specifications/)
and [release notes](https://technical.buildingsmart.org/standards/ifc/ifc-schema-specifications/ifc-release-notes/).

### IFC2X3 georeferencing compatibility

New IFC2X3 output stores `ePSet_MapConversion` and `ePSet_ProjectedCRS` on
`IfcProject`, following the [buildingSMART Geo-referencing User Guide v2.0,
section 5.1](https://www.buildingsmart.org/wp-content/uploads/2020/02/User-Guide-for-Geo-referencing-in-IFC-v2.0.pdf).
The reader and upload detection also accept legacy `IfcSite` property sets and
case variants such as `ePset_*` and `EPset_*`. A complete project pair takes
precedence over site data. Without a project pair, the reader accepts complete
site pairs whose overlapping properties agree; it never joins partial pairs
from different hosts.

Rewriting an IFC2X3 model migrates compatible site assignments and consolidates
duplicates into one project pair. Existing optional metadata, property
descriptions, and explicit units are retained. Property sets shared with other
objects are copied so those objects keep their original values. Conflicting
existing values, types, units, or descriptions must be reconciled before a
rewrite; the writer raises `ValueError` before modifying the model. It requires
exactly one `IfcProject`.

The optional `map_unit` argument preserves an existing MapUnit when omitted, and
Scale is written as `IfcReal` without changing its value or the transform
calculations. External readers that only inspect `IfcSite` need to support project
assignments to read newly written files. Other application features involving
site placement may still require an `IfcSite`.

## Configuration

The application reads environment variables, then a `.env` file next to
`app.py` (or in the packaged resources), then a `.env` file in the working
directory. Earlier values take precedence. The `.env` file is ignored by Git,
but **all desktop build scripts include it in packages when present**. See
[Building packages](desktop/README.md#building-packages) before distributing a package.

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `IFCGREF_SECRET_KEY` | random per process | Flask session signing key. Set a stable value in production so sessions survive restarts. |
| `IFCGREF_DEBUG` | off | Set to `1`, `true`, `yes`, or `on` for local debugging with `app.py`. |
| `IFCGREF_MAX_UPLOAD_MB` | `250`; unlimited in desktop mode | Maximum upload size in MiB. An explicit value also applies in desktop mode. |
| `IFCGREF_WORKERS` | `2` | Number of background IFC processing workers. |
| `IFCGREF_UPLOAD_FOLDER` | `uploads/`; `IFCGREF_DATA_DIR/uploads/` in desktop mode | Directory for uploaded/generated IFC files, model state, and job status. |
| `IFCGREF_UPLOAD_RETENTION_DAYS` | `30` | Age limit in days for IFC inputs/outputs and model state. Use `0` to disable this cleanup. |
| `IFCGREF_JOB_RETENTION_DAYS` | `7` | Age limit in days for background job status files. Use `0` to disable this cleanup. |
| `IFCGREF_IFC_CACHE_MAX` | `4` | Maximum number of parsed IFC files held in the in-memory cache. |
| `IFCGREF_MAPTILER_KEY` | empty | API key for MapTiler layers; other map layers can be used without it. |
| `IFCGREF_HOST` | `127.0.0.1` | Host for `python app.py`. The desktop launcher always binds to `127.0.0.1`. |
| `IFCGREF_PORT` | `5000`; available port in the desktop launcher | Local server port. |
| `IFCGREF_DESKTOP` | off | Enables desktop storage and upload defaults. The desktop launcher sets this automatically. |
| `IFCGREF_DATA_DIR` | current directory in `app.py` | Base for desktop upload storage. See [desktop data locations](desktop/README.md#data-and-configuration) for launcher defaults. |
| `IFCGREF_DESKTOP_PACKAGE_DIR` | `desktop_dist/` in the working directory | Directory searched by the desktop download buttons. |
| `IFCGREF_NO_BROWSER` | off | Set to `1`, `true`, `yes`, or `on` to suppress automatic browser opening by the desktop launcher. |

Cleanup runs at application startup and checks file modification times.
The desktop launcher selects its port and data directory before loading `.env`;
supply those overrides through the process environment. The portable macOS
launcher sets its own data directory as described in the desktop guide.

## HTTP inspection endpoint

Send a multipart `POST` to `/devs` with the IFC file in the `file` field.
The response is a text report describing whether the file has georeferencing
and, when present, its map conversion values.

Install `requests` to run this example:

```bash
python -m pip install requests
```

```python
import requests

url = "http://127.0.0.1:5000/devs"
with open("model.ifc", "rb") as ifc_file:
    response = requests.post(
        url,
        files={"file": ("model.ifc", ifc_file)},
        timeout=120,
    )
response.raise_for_status()
print(response.text)
```

For the hosted service, use `https://ifcgref.bk.tudelft.nl/devs` as the URL.
Disallowed extensions or unusable filenames return HTTP 400; uploads exceeding
the configured size limit return HTTP 413. A missing `file` field or an empty
filename currently returns HTTP 200 with an error message in the response text.

## Project structure

- [app.py](app.py): Flask routes, processing jobs, and georeferencing workflow.
- [desktop_app.py](desktop_app.py): Local browser launcher.
- [desktop/](desktop/): Packaging instructions and build scripts.
- [georeference_ifc/](georeference_ifc/): IFC georeferencing readers, writers, and templates.
- [static/](static/): Styles and images.
- [templates/](templates/): HTML templates and bundled viewer scripts.
- [tests/](tests/): Regression tests.
- [requirements.txt](requirements.txt): Python dependencies.
- `uploads/`: Runtime storage for IFC files, model state, and job status; configurable as above.

## Testing

After installing the dependencies, run the regression suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

## Credits

- Flask provides the web application framework.
- IfcOpenShell reads and writes IFC models.
- pyproj handles coordinate reference systems and transformations.
- SciPy's `scipy.optimize.leastsq` fits the transformation to survey points.
- MapLibre GL, Three.js, and web-ifc/web-ifc-three power the map and IFC viewer.

## Acknowledgments

This project has received funding from the European Union's Horizon Europe
programme under Grant Agreement No.101058559 (CHEK: Change toolkit for digital building permit).

## License

IfcGref is distributed under the [MIT license](LICENSE).

## References

- [buildingSMART IFC specifications](https://technical.buildingsmart.org/standards/ifc/ifc-schema-specifications/)
- [buildingSMART Australasia: User Guide for Geo-referencing in IFC, version 2.0](https://www.buildingsmart.org/wp-content/uploads/2020/02/User-Guide-for-Geo-referencing-in-IFC-v2.0.pdf)
- [georeference-ifc](https://github.com/stijngoedertier/georeference-ifc#readme)
