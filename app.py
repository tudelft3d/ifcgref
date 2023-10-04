from flask import Flask, render_template, request, redirect, url_for, session, render_template_string # Import the redirect function
from werkzeug.utils import secure_filename
import os
import ifcopenshell
import ifcopenshell.geom
import georeference_ifc
import re
import sys
import pyproj
from pyproj import Transformer
import pint
from pint.errors import UndefinedUnitError
import numpy as np
import math
from scipy.optimize import leastsq
import pandas as pd
import json
import os
from shapely.geometry import Polygon, mapping


app = Flask(__name__)
app.secret_key = '88746898'
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def infoExt(filename , epsgCode):
    ureg = pint.UnitRegistry()
    ifc_file = fileOpener(filename)
    #check ifc version
    version = ifc_file.schema
    message = f"IFC version: {version}\n"

    #Find Longtitude and Latitude
    RLat = ifc_file.by_type("IfcSite")[0].RefLatitude
    RLon = ifc_file.by_type("IfcSite")[0].RefLongitude
    RElev = ifc_file.by_type("IfcSite")[0].RefElevation
    x0= (float(RLat[0]) + float(RLat[1])/60 + float(RLat[2]+RLat[3]/1000000)/(60*60))
    y0= (float(RLon[0]) + float(RLon[1])/60 + float(RLon[2]+RLon[3]/1000000)/(60*60))

    # Check the file is georefed or not
    mapconversion = None
    crs = None

    if ifc_file.schema == 'IFC4':
        project = ifc_file.by_type("IfcProject")[0]
        for c in (m for c in project.RepresentationContexts for m in c.HasCoordinateOperation):
            mapconversion = c
            crs = c.TargetCRS
        if mapconversion is not None:
            message += "IFC file is georeferenced."
            return message
    bx,by,bz = 0,0,0
    # Find local origin
    if hasattr(ifc_file.by_type("IfcSite")[0], "ObjectPlacement") and ifc_file.by_type("IfcSite")[0].ObjectPlacement.is_a("IfcLocalPlacement"):
        local_placement = ifc_file.by_type("IfcSite")[0].ObjectPlacement.RelativePlacement
            # Check if the local placement is an IfcAxis2Placement3D
        if local_placement.is_a("IfcAxis2Placement3D"):
            local_origin = local_placement.Location.Coordinates
            bx,by,bz= local_origin
            message += f"Local Origin: {local_origin}\n"
        else:
                message += "Local placement is not IfcAxis2Placement3D."
                return message
    else:
            message += "IfcSite does not have a local placement."
            return message
                
    # Target CRS unit name
    try: 
        crs = pyproj.CRS.from_epsg(int(epsgCode))
    except:
        message += "CRS is not available."
        return message


    crsunit = crs.axis_info[0].unit_name

    if crs.is_projected:
        message += "CRS is projected.\n"
    else:
        message += "CRS is not projected (geographic)."
        return message
    target_epsg = "EPSG:"+str(epsgCode)
    transformer = Transformer.from_crs("EPSG:4326", target_epsg)



    x1,y1,z1 = transformer.transform(x0,y0,RElev)




    # IFC length unit name
    ifc_units = ifc_file.by_type("IfcUnitAssignment")[0].Units
    for ifc_unit in ifc_units:
        if ifc_unit.is_a("IfcSIUnit") and ifc_unit.UnitType == "LENGTHUNIT":
            if ifc_unit.Prefix is not None:
                ifcunit = ifc_unit.Prefix + ifc_unit.Name
            else:
                ifcunit = ifc_unit.Name
    # Map units to Pint unit
    unit_mapping = {
        "METRE": ureg.meter,
        "METER": ureg.meter,
        "CENTIMETRE": ureg.centimeter,
        "CENTIMETER": ureg.centimeter,
        "MILLIMETRE": ureg.millimeter,
        "MILLIMETER": ureg.millimeter,
        "INCH": ureg.inch,
        "FOOT": ureg.foot,
        "YARD": ureg.yard,
        "MILE": ureg.mile,
        "NAUTICAL_MILE": ureg.nautical_mile,
        "metre": ureg.meter,
        "meter": ureg.meter,
        "centimeter": ureg.centimeter,
        "centimetre": ureg.centimeter,
        "millimeter": ureg.millimeter,
        "millimetre": ureg.millimeter,
        "inch": ureg.inch,
        "foot": ureg.foot,
        "yard": ureg.yard,
        "mile": ureg.mile,
        "nautical_mile": ureg.nautical_mile,
        # Add more mappings as needed
    }

    try:
        if ifcunit in unit_mapping:
            quantity = 1 * unit_mapping[ifcunit]
            ifcmeter = quantity.to(ureg.meter).magnitude
        else:
            ifcmeter = None
    except:
        ifcmeter = None

    try:
        if crsunit in unit_mapping:
            quantity = 1 * unit_mapping[crsunit]
            crsmeter = quantity.to(ureg.meter).magnitude
        else:
            crsmeter = None
    except:
        crsmeter = None

    if crsmeter is not None and ifcmeter is not None:
        coeff= crsmeter/ifcmeter
    else:
        message += "measurement error"
        return message




    message += f"Longitude: {round(y0,4)}\n"
    message += f"Latitude: {round(x0,4)}\n"
    message += f"Reference Elevation: {RElev}\n"
    message += f"CRS Unit: {crsunit}\n"

    if ifcunit:
        unit_name = ifcunit
        message += f"IFC Unit: {unit_name}\n"

    else:
        message += "No length unit found in the IFC file."
    message += f"coeff: {coeff}\n"
    message += "______"

    x2= x1*coeff
    y2= y1*coeff

    session['x2'] = x2
    session['y2'] = y2
    session['z1'] = z1
    session['Longitude'] = y0
    session['Latitude'] = x0

    return message

    

@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part"
    file = request.files['file']
    if file.filename == '':
        return "No selected file"
    if file and allowed_file(file.filename):  # Check if the file extension is allowed
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        #ifc_file = ifcopenshell.open(filename)

        return redirect(url_for('convert_crs', filename=filename))  # Redirect to EPSG code input page
    else:
        return render_template('upload.html', error_message="Invalid file format. Please upload a .ifc file.")
    
   
@app.route('/convert/<filename>', methods=['GET', 'POST'])
def convert_crs(filename):
    if request.method == 'POST':
        try:
            epsg_code = int(request.form['epsg_code'])
        except ValueError:
            message = "Invalid EPSG code. Please enter a valid integer."
            return render_template('convert.html', filename=filename, message=message)
        session['target_epsg'] = epsg_code
       # Call the infoExt function and unpack the results
        message = infoExt(filename, epsg_code)

        if message.endswith("______"):
            # Pass x2, y2, and z1 to the survey_points route
            return redirect(url_for('survey_points', filename=filename))
        return render_template('convert.html', filename=filename, message=message)

    return render_template('convert.html', filename=filename)

@app.route('/survey/<filename>', methods=['GET', 'POST'])
def survey_points(filename):
    message = local_trans(filename)
    Num = []

    if request.method == 'POST':
        try:
            Num = int(request.form['Num'])
            if Num < 0:
                message = "Please enter zero or a positive integer."
                return render_template('survey.html', filename=filename, message=message)
        except ValueError:
            message = "Please enter a valid integer."
            return render_template('survey.html', filename=filename, message=message)
        session['rows'] = Num
    return render_template('survey.html', filename=filename, message=message, Num=Num)

def local_trans(filename):
    ifc_file = fileOpener(filename)
    x2 = session.get('x2')
    y2 = session.get('y2')
    z1 = session.get('z1')
    bx,by,bz = 0,0,0
    message = ""
    if hasattr(ifc_file.by_type("IfcSite")[0], "ObjectPlacement") and ifc_file.by_type("IfcSite")[0].ObjectPlacement.is_a("IfcLocalPlacement"):
        local_placement = ifc_file.by_type("IfcSite")[0].ObjectPlacement.RelativePlacement
        # Check if the local placement is an IfcAxis2Placement3D
        if local_placement.is_a("IfcAxis2Placement3D"):
            local_origin = local_placement.Location.Coordinates
            bx, by, bz = map(float, local_origin)
            message += "First point Local coordinates:" + str(local_origin)
        else:
                message += "Local placement is not IfcAxis2Placement3D."
    else:
            message += "IfcSite does not have a local placement."
    session['bx'] = bx
    session['by'] = by        
    session['bz'] = bz        

    message += "\nFirst point Target coordinates:" + "(" + str(x2) + ", " + str(y2) + ", " + str(z1) + ")"
    ifc_file = ifc_file.end_transaction()
    return message

@app.route('/calc/<filename>', methods=['GET', 'POST'])
def calculate(filename):
    if request.method == 'POST':
        # Access the form data by iterating through the rows
        rows = session.get('rows')
        x2 = session.get('x2')
        y2 = session.get('y2')
        bx = session.get('bx')
        by = session.get('by')

        data_points = []
        data_points.append({"X": bx, "Y": by, "X_prime": x2, "Y_prime": y2})
        if rows == 0:
            S_solution, Rotation_solution, E_solution, N_solution = 1, 0, x2, y2
        else:
            
            for row in range(rows):
                x = request.form[f'x{row}']
                y = request.form[f'y{row}']
                z = request.form[f'z{row}']
                x_prime = request.form[f'x_prime{row}']
                y_prime = request.form[f'y_prime{row}']
                z_prime = request.form[f'z_prime{row}']

                try:
                    x = float(x)
                    y = float(y)
                    z = float(z)
                    x_prime = float(x_prime)
                    y_prime = float(y_prime)
                    z_prime = float(z_prime)
                except ValueError:
                    message = "Invalid input. Please enter only float values."
                    Num = rows
                    return render_template('survey.html', message=message, Num=Num)

                data_points.append({"X": x, "Y": y, "X_prime": x_prime, "Y_prime": y_prime})

            def equations(variables, data_points):
                    S, Rotation, E, N = variables
                    eqs = []

                    for data in data_points:
                        X = data["X"]
                        Y = data["Y"]
                        X_prime = data["X_prime"]
                        Y_prime = data["Y_prime"]

                        eq1 = S * np.cos(Rotation) * X - S * np.sin(Rotation) * Y + E - X_prime
                        eq2 = S * np.sin(Rotation) * X + S * np.cos(Rotation) * Y + N - Y_prime
                        eqs.extend([eq1, eq2])

                    return eqs
                # Initial guess for variables [S, Rotation, E, N]
            initial_guess = [1, 0, x2, y2]

            # Perform the least squares optimization for all data points
            result, _ = leastsq(equations, initial_guess, args=(data_points,))
            S_solution, Rotation_solution, E_solution, N_solution = result
        Rotation_degrees = (180 / math.pi) * Rotation_solution
        rDeg = Rotation_degrees - (360*round(Rotation_degrees/360))

        fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        ifc_file = fileOpener(filename)
        target_epsg = "EPSG:"+str(session.get('target_epsg'))
        georeference_ifc.set_mapconversion_crs(ifc_file=ifc_file,
                                        target_crs_epsg_code=target_epsg,
                                        eastings=E_solution,
                                        northings=N_solution,
                                        orthogonal_height=(session.get('z1')-session.get('bz')),
                                        x_axis_abscissa=math.cos(Rotation_solution),
                                        x_axis_ordinate=math.sin(Rotation_solution),
                                        scale=S_solution)
        fn_output = re.sub('\.ifc$','_georeferenced.ifc', fn)
        ifc_file.write(fn_output)
        IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
        df = pd.DataFrame(list(IfcProjectedCRS.__dict__.items()), columns= ['property', 'value'])
        dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns= ['property', 'value'])
        html_table_f = df.to_html()
        html_table_g = dg.to_html()
        return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g)
    
def fileOpener(filename):
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    print("Opening IFC file:", fn)  # Add this line for debugging
    try:
        ifc_file = ifcopenshell.open(fn)
        return ifc_file
    except Exception as e:
        print("Error opening IFC file:", str(e))  # Add this line for debugging
        return None

@app.route('/show/<filename>', methods=['GET', 'POST'])
def visualize(filename):
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fn_output = re.sub('\.ifc$','_georeferenced.ifc', fn)
    ifc_file = ifcopenshell.open(fn_output)
    IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
    E = IfcMapConversion.Eastings
    N = IfcMapConversion.Northings
    S = IfcMapConversion.Scale
    ortz = IfcMapConversion.OrthogonalHeight
    cos = IfcMapConversion.XAxisAbscissa
    sin = IfcMapConversion.XAxisOrdinate
    target_epsg = "EPSG:"+str(session.get('target_epsg'))
    transformer2 = Transformer.from_crs(target_epsg,"EPSG:4326")
    #Adding IFC boundries to geojson
    Points = ifc_file.by_type("IfcPolygonalFaceSet")[0].Coordinates.CoordList
    vertlist = []
    for point in Points:
        x = S * cos * point[0] - S * sin * point[1] + E 
        y = S * sin * point[0] + S * cos * point[1] + N 
        z = point[2] + ortz
        x2,y2 = transformer2.transform(x,y)
        vert = y2,x2
        vertlist.append(vert)
    vertlist.append(vertlist[0])

    if len(vertlist) >= 3:  # A polygon needs at least 3 vertices
        polygon = Polygon(vertlist)

        geo_json_dict = {
            "type": "FeatureCollection",
            "features": []
            }
        

        feature = {
            'type': 'Feature',
            'properties': {},
            'geometry': mapping(polygon)
        }

        geo_json_dict["features"].append(feature)
        fn_ = re.sub('\.ifc$','.geojson', filename)
        geo_json_file = open(os.path.join('./MapDev/', fn_), 'w+')
        geo_json_file.write(json.dumps(geo_json_dict, indent=2))
        geo_json_file.close()
        filename = geo_json_file.name
        Latitude =session.get('Latitude')
        Longitude =session.get('Longitude')
    return render_template('Viewer.html', filename=filename, Latitude=Latitude, Longitude=Longitude)


if __name__ == '__main__':
    app.run(debug=True)
