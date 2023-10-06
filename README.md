# Georeferencing IFC Files with Flask

This Flask application allows you to georeference IFC (Industry Foundation Classes) files, which are commonly used for exchanging Building Information Model (BIM) data. Georeferencing involves adding geographic coordinates (latitude and longitude) to a BIM model to place it in the real world.

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

## Acknowledgments

- This application uses the Flask web framework for the user interface.
- It leverages the ifcopenshell library for working with IFC files.
- Georeferencing is performed using pyproj for coordinate transformations.
