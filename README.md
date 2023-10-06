# Georeferencing IFC Files with Flask

## Overview

This Flask-based application serves the purpose of georeferencing IFC (Industry Foundation Classes) files, which are commonly used in the context of Building Information Modeling (BIM) data exchange. To accomplish georeferencing, the application leverages the **IFCMapConversion** entity in IFC4, which facilitates the updating of data and the conversion from a local Coordinate Reference System (CRS), often referred to as the engineering coordinate system, into the coordinate reference system of the underlying map (Projected CRS).

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
## Usage

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

## File Structure

- app.py: The main Flask application file.
- static/: Directory to store static files (e.g., GeoJSON output).
- templates/: HTML templates for the web interface.
- uploads/: Directory to temporarily store uploaded IFC files.

## Customization

You can customize the application by modifying the Flask routes, templates, and logic in app.py to suit your specific requirements.

## Credits

- This application uses the Flask web framework for the user interface.
- It leverages the ifcopenshell library for working with IFC files.
- Georeferencing is performed using pyproj for coordinate transformations.

## Acknowledgments

This project has received funding from the European Union’s Horizon Europe programme under Grant Agreement No.101058559 (CHEK: Change toolkit for digital building permit).


## References

BuildingSMART. IFC Specifications database. https://technical.buildingsmart.org/standards/ifc/ifc-schema-specifications/

BuildingSMART Australasia (2020). User Guide for Geo-referencing in IFC, version 2.0. https://www.buildingsmart.org/wp-content/uploads/2020/02/User-Guide-for-Geo-referencing-in-IFC-v2.0.pdf

https://github.com/stijngoedertier/georeference-ifc#readme
