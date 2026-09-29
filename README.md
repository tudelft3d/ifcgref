
# IfcGref Web-Based Application

![ifcgref-release](https://github.com/tudelft3d/ifcgref/assets/50393714/e335cd23-d063-4f86-8cdf-d9898b6a955a)


## Overview

This Flask-based application serves the purpose of georeferencing IFC (Industry Foundation Classes) files, which are commonly used in the context of Building Information Modeling (BIM) data exchange. To accomplish georeferencing, the application leverages the **IFCMapConversion** entity in IFC4, which facilitates the updating of data and the conversion from a local Coordinate Reference System (CRS), often referred to as the engineering coordinate system, into the coordinate reference system of the underlying map (Projected CRS). It's accessible at https://ifcgref.bk.tudelft.nl.



## Prerequisites

Before running the application, make sure you have the following prerequisites installed on your system:

- Python 3
- Flask
- ifcopenshell
- pyproj
- pint
- numpy
- scipy
- pandas
- shapely

You can install these dependencies using pip:

```bash
pip install Flask ifcopenshell pyproj pint numpy scipy pandas shapely
```

## Supported IFC versions


Coordinate operations become accessible starting from IFC 4. For earlier versions like the widely utilized IFC2x3, the utilization of Property sets (Pset) is employed to enable georeferencing. The table below outlines the supported versions: 

| Version | Name |
| -------- | ------- |
| 4.3.2.0 | IFC 4.3 ADD2 |
| 4.0.2.0 | IFC4 ADD2 TC1 |
| 4.0.2.1 | IFC4 ADD2 |
| 4.0.2.0	| IFC4 ADD1 |
| 4.0.0.0 | IFC4 |
| 2.3.0.1 | IFC2x3 TC1 |
| 2.3.0.0 | IFC2x3 |


## Usage

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

The optional `map_unit` argument still preserves an existing MapUnit when
omitted, and Scale is written as `IfcReal` without changing its value or the
transform calculations. External readers that only inspect `IfcSite` need to
support project assignments to read newly written files. Other application
features involving site placement may still require an `IfcSite`.

### Running the application

1. Clone this repository or download the application files to your local machine.

2. Navigate to the project directory in your terminal.

3. Run the Flask application:

```bash
python app.py
```
This will start the Flask development server.

4. Access the application in your web browser by going to http://localhost:5000/.
5. Follow the on-screen instructions to upload an IFC file and specify the target EPSG code.
6. The application will georeference the IFC file and provide details about the process.
7. You can then visualize the georeferenced IFC file on the map and download it.

## Configuration

The application reads deployment settings from environment variables:

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `IFCGREF_SECRET_KEY` | random per process | Flask session signing key. Set this in production so sessions survive restarts. |
| `IFCGREF_DEBUG` | off | Set to `1`, `true`, `yes`, or `on` only for local debugging. |
| `IFCGREF_MAX_UPLOAD_MB` | `250` | Maximum IFC upload size in megabytes. |
| `IFCGREF_WORKERS` | `2` | Number of background IFC processing workers. |
| `IFCGREF_UPLOAD_RETENTION_DAYS` | `30` | Number of days to keep uploaded IFC files and model state. Use `0` to disable cleanup. |
| `IFCGREF_JOB_RETENTION_DAYS` | `7` | Number of days to keep background job status files. Use `0` to disable cleanup. |
| `IFCGREF_MAPTILER_KEY` | empty | Optional MapTiler API key used by the MapTiler layers. |
| `IFCGREF_HOST` | `127.0.0.1` | Host used when running `python app.py`. |
| `IFCGREF_PORT` | `5000` | Port used when running `python app.py`. |
| `IFCGREF_DESKTOP` | off | Enables local desktop mode. The desktop launcher sets this automatically. |
| `IFCGREF_DATA_DIR` | current directory | Desktop/local data directory. The desktop launcher defaults to `%LOCALAPPDATA%\IfcGref`. |

For local development you can also put these values in a `.env` file next to `app.py`. The `.env` file is ignored by git and should be used for local keys such as `IFCGREF_MAPTILER_KEY`.

## Desktop App

IfcGref can also be distributed as a desktop app that still runs in the user's browser. The desktop launcher starts the Flask tool on `127.0.0.1`, opens the browser automatically, and stores uploads locally on the user's machine. In desktop mode there is no Flask upload size limit unless `IFCGREF_MAX_UPLOAD_MB` is explicitly set.

To run it locally:

```bash
python desktop_app.py
```

To build a Windows package:

```powershell
.\desktop\build_windows.ps1 -Version 3.0
```

To build a native macOS package, run this on a Mac:

```bash
bash ./desktop/build_macos.sh 3.0
```

To build the portable macOS launcher package from any OS:

```bash
python desktop/build_macos_portable.py 3.0
```

The build scripts create platform zips in `desktop_dist/`. The home page has separate Windows and Mac download buttons; each button serves the newest package for that platform from `desktop_dist/`.

## File Structure

- app.py: The main Flask application file.
- desktop_app.py: Local desktop-browser launcher.
- desktop/: Desktop packaging instructions and scripts.
- static/: Directory to store static files (e.g., GeoJSON output).
- templates/: HTML templates for the web interface.
- uploads/: Directory to temporarily store uploaded IFC files.
- envelop/: Directory to EnvelopExtractor exe files and temporary store shell produced files.

## Workflow

![Screenshot 2024-02-26 at 17 28 20 (2)](https://github.com/tudelft3d/ifcgref/assets/50393714/3d14b4c7-9652-4b77-bc5b-77bd2a736341)

## HTTP request

For streamlined handling of incoming IFC files by developers, whether they are georeferenced or not, a specialized section called "devs" is available at https://ifcgref.bk.tudelft.nl/devs. Developers can engage with this section by submitting an HTTP request containing the IFC file, and in response, the server provides them with a corresponding response.

Sample of HTTP request from the devs section using a python script:

```bash
import requests

url = 'https://ifcgref.bk.tudelft.nl/devs'
file_path = './00.ifc'


data = {
    'file': ('00.ifc', open(file_path, 'rb'))
}


# Make a POST request to the /devs route with the data
response = requests.post(url, files=data)

# Print the response content
print(response.text)
```


## Credits

- This application uses the Flask web framework for the user interface.
- It leverages the ifcopenshell library for working with IFC files.
- Georeferencing is performed using pyproj for coordinate transformations.
- The optimization is performed using SciPy and in particular scipy.optimize.least_squares function.
- For the vizualization feature IfcEnvelopeExtractor is used for generating the roof-print of the 3D BIM model.



## Acknowledgments

This project has received funding from the European Union’s Horizon Europe programme under Grant Agreement No.101058559 (CHEK: Change toolkit for digital building permit).


## References

BuildingSMART. IFC Specifications database. https://technical.buildingsmart.org/standards/ifc/ifc-schema-specifications/

BuildingSMART Australasia (2020). User Guide for Geo-referencing in IFC, version 2.0. https://www.buildingsmart.org/wp-content/uploads/2020/02/User-Guide-for-Geo-referencing-in-IFC-v2.0.pdf

https://github.com/stijngoedertier/georeference-ifc#readme
