from flask import Flask, render_template, request, redirect, url_for, session, make_response # Import the redirect function
from werkzeug.utils import secure_filename
import os
import ifcopenshell
import ifcopenshell.geom
import georeference_ifc
import re
import pyproj
from pyproj import Transformer
import pint
import numpy as np
import math
from scipy.optimize import leastsq
import pandas as pd
import json
from shapely.geometry import Polygon, mapping
import ifcopenshell.util.placement
import subprocess
import time


app = Flask(__name__, static_url_path='/static', static_folder='static')
app.secret_key = '88746898'
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def georef(ifc_file):
    geo = False
    #check ifc version
    version = ifc_file.schema
    message = f"IFC version: {version}\n"
    # Check the file is georefed or not
    mapconversion = None
    crs = None

    if ifc_file.schema == 'IFC4' or ifc_file.schema == 'IFC4X3_ADD1' or ifc_file.schema == 'IFC4X3':
        project = ifc_file.by_type("IfcProject")[0]
        for c in (m for c in project.RepresentationContexts for m in c.HasCoordinateOperation):
            mapconversion = c
            crs = c.TargetCRS
        if mapconversion is not None:
            message += "IFC file is georeferenced.\n"
            geo = True
    return message , geo
        
def infoExt(filename , epsgCode):
    ureg = pint.UnitRegistry()
    ifc_file = fileOpener(filename)
    #check ifc version
    version = ifc_file.schema
    message = f"IFC version: {version}\n"
    ifc_site = ifc_file.by_type("IfcSite")


    #Find Longtitude and Latitude
    RLat = ifc_site[0].RefLatitude
    RLon = ifc_site[0].RefLongitude
    RElev = ifc_site[0].RefElevation
    if RLat is not None or RLon is not None:
        x0= (float(RLat[0]) + float(RLat[1])/60 + float(RLat[2]+RLat[3]/1000000)/(60*60))
        y0= (float(RLon[0]) + float(RLon[1])/60 + float(RLon[2]+RLon[3]/1000000)/(60*60))
        session['Refl'] = True
    else:
        session['Refl'] = False
        message += "RefLatitude or RefLongitude not available in the IFC file.\n"
    Refl = session.get('Refl')

    mapconversion = None
    crs = None
    if ifc_file.schema != 'IFC4' and ifc_file.schema != 'IFC4X3':
        message += "Only IFC4 is supported.\n"
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
    # IFC length unit name
    ifc_units = ifc_file.by_type("IfcUnitAssignment")[0].Units
    for ifc_unit in ifc_units:
        if ifc_unit.is_a("IfcSIUnit") and ifc_unit.UnitType == "LENGTHUNIT":
            if ifc_unit.Prefix is not None:
                ifcunit = ifc_unit.Prefix + ifc_unit.Name
            else:
                ifcunit = ifc_unit.Name
    try: 
        quantity = unitmapper(ifcunit)
        ifcmeter = quantity.to(ureg.meter).magnitude
    except:
        ifcmeter = None
    # try:
    #     if ifcunit in unit_mapping:
    #         quantity = 1 * unit_mapping[ifcunit]
    #         ifcmeter = quantity.to(ureg.meter).magnitude
    #     else:
    #         ifcmeter = None
    # except:
    #     ifcmeter = None
    try: 
        quantity = unitmapper(crsunit)
        crsmeter = quantity.to(ureg.meter).magnitude
    except:
        crsmeter = None

    # try:
    #     if crsunit in unit_mapping:
    #         quantity = 1 * unit_mapping[crsunit]
    #         crsmeter = quantity.to(ureg.meter).magnitude
    #     else:
    #         crsmeter = None
    # except:
    #     crsmeter = None

    if crsmeter is not None and ifcmeter is not None:
        coeff= ifcmeter/crsmeter
    else:
        message += "measurement error"
        return message
    if Refl:
        message += f"Longitude: {round(y0,4)}\n"
        message += f"Latitude: {round(x0,4)}\n"
        message += f"Reference Elevation: {RElev}\n"
    message += f"CRS Unit: {crsunit}\n"

    if ifcunit:
        unit_name = ifcunit
        message += f"IFC Unit: {unit_name}\n"

    else:
        message += "No length unit found in the IFC file."
        return message
    message += f"coeff: {coeff}\n"
    message += "______"
    session['coeff'] = coeff
    if Refl:
        x1,y1,z1 = transformer.transform(x0,y0,RElev)
        x2= x1*coeff
        y2= y1*coeff
        session['xt'] = x1
        session['yt'] = y1
        session['z1'] = z1
        session['Longitude'] = y0
        session['Latitude'] = x0


    return message

def unitmapper(value):
    ureg = pint.UnitRegistry()
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
    if value in unit_mapping:
            return  1 * unit_mapping[value]
    return

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
        ifc_file = fileOpener(filename)

        message, geo = georef(ifc_file)
        if geo:
            IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
            df = pd.DataFrame(list(IfcProjectedCRS.__dict__.items()), columns= ['property', 'value'])
            dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns= ['property', 'value'])
            html_table_f = df.to_html()
            html_table_g = dg.to_html()
            return render_template('result.html', filename=filename, table_f=html_table_f, table_g=html_table_g, message=message)
        
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
            return redirect(url_for('survey_points', filename=filename, message= message))
        return render_template('convert.html', filename=filename, message=message)

    return render_template('convert.html', filename=filename)

@app.route('/survey/<filename>/<message>', methods=['GET', 'POST'])
def survey_points(filename, message):
    Refl = session.get('Refl')
    if Refl:
        message += local_trans(filename)
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num < 0:
                    message += "Please enter zero or a positive integer."
                    return render_template('survey.html', filename=filename, message=message)
            except ValueError:
                message += "Please enter zero or a positive integer."
                return render_template('survey.html', filename=filename, message=message)
            session['rows'] = Num
            if Num == 0:
                return redirect(url_for('calculate', filename=filename))
        return render_template('survey.html', filename=filename, message=message, Num=Num)
    else:
        message += '\nThe IFC model has no surveyed or georeferenced attribute.\nYou need to provide at least one point in local and projected CRS.'
        message += '\n\nThe precision of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction.\n'
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num <= 0:
                    message += "Please enter a positive integer."
                    return render_template('survey.html', filename=filename, message=message)
            except ValueError:
                message += "Please enter a positive integer."
                return render_template('survey.html', filename=filename, message=message)
            session['rows'] = Num
        return render_template('survey.html', filename=filename, message=message, Num=Num)


def local_trans(filename):
    ifc_file = fileOpener(filename)
    xt = session.get('xt')
    yt = session.get('yt')
    z1 = session.get('z1')
    bx,by,bz = 0,0,0
    message = ""
    if hasattr(ifc_file.by_type("IfcSite")[0], "ObjectPlacement") and ifc_file.by_type("IfcSite")[0].ObjectPlacement.is_a("IfcLocalPlacement"):
        local_placement = ifc_file.by_type("IfcSite")[0].ObjectPlacement.RelativePlacement
        # Check if the local placement is an IfcAxis2Placement3D
        if local_placement.is_a("IfcAxis2Placement3D"):
            local_origin = local_placement.Location.Coordinates
            bx, by, bz = map(float, local_origin)
            message += "\nFirst point Local coordinates:" + str(local_origin)
        else:
                message += "Local placement is not IfcAxis2Placement3D."
    else:
            message += "IfcSite does not have a local placement."
    session['bx'] = bx
    session['by'] = by        
    session['bz'] = bz        

    message += "\nFirst point Target coordinates:" + "(" + str(xt) + ", " + str(yt) + ", " + str(z1) + ")"
    message += '\n\nThe precision of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction.\n'

    ifc_file = ifc_file.end_transaction()
    return message

@app.route('/calc/<filename>', methods=['GET', 'POST'])
def calculate(filename):
    #if request.method == 'POST':
        # Access the form data by iterating through the rows
        coeff = session.get('coeff')
        rows = session.get('rows')
        fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        ifc_file = fileOpener(filename)
        data_points = []
        Refl = session.get('Refl')
        if Refl:
            xt = session.get('xt')
            yt = session.get('yt')
            bx = session.get('bx')
            by = session.get('by')
            data_points.append({"X": bx, "Y": by, "X_prime": xt, "Y_prime": yt})
        #seperater
        if not Refl and rows == 1:
            Rotation_solution = 0
            S_solution = coeff
            if hasattr(ifc_file.by_type("IfcGeometricRepresentationContext")[0], "TrueNorth") and ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth.is_a("IfcDirection"):
                xord , xabs = ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth[0]
                xord , xabs = round(float(xord),6) , round(float(xabs),6)
            else:
                xord , xabs = 0 , 1
            Rotation_solution = math.atan2(xord,xabs)
            A = math.cos(Rotation_solution)
            B = math.sin(Rotation_solution)
            E_solution = float(request.form[f'x_prime{0}']) - (A*float(request.form[f'x{0}'])*coeff) + (B*float(request.form[f'y{0}'])*coeff)
            N_solution = float(request.form[f'y_prime{0}']) - (B*float(request.form[f'x{0}'])*coeff) - (A*float(request.form[f'y{0}'])*coeff)
            session['z1'] = float(request.form[f'z_prime{0}'])
            session['bz'] = float(request.form[f'z{0}'])

        #seperater
        else:
            if rows == 0:
                Rotation_solution = 0
                S_solution = coeff
                if hasattr(ifc_file.by_type("IfcGeometricRepresentationContext")[0], "TrueNorth") and ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth.is_a("IfcDirection"):
                    xord , xabs = ifc_file.by_type("IfcGeometricRepresentationContext")[0].TrueNorth[0]
                    xord , xabs = round(float(xord),6) , round(float(xabs),6)
                else:
                    xord , xabs = 0 , 1
                Rotation_solution = math.atan2(xord,xabs)
                A = math.cos(Rotation_solution)
                B = math.sin(Rotation_solution)
                E_solution = xt - (A*S_solution*bx) + (B*S_solution*by)
                N_solution = yt - (B*S_solution*bx) - (A*S_solution*by)
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
                if Refl:
                    initial_guess = [coeff, 0, xt, yt]
                else:
                    initial_guess = [coeff,0,0,0]

                # Perform the least squares optimization for all data points
                result, _ = leastsq(equations, initial_guess, args=(data_points,))
                S_solution, Rotation_solution, E_solution, N_solution = result

        Rotation_degrees = (180 / math.pi) * Rotation_solution
        rDeg = Rotation_degrees - (360*round(Rotation_degrees/360))

        target_epsg = "EPSG:"+str(session.get('target_epsg'))
        georeference_ifc.set_mapconversion_crs(ifc_file=ifc_file,
                                        target_crs_epsg_code=target_epsg,
                                        eastings=E_solution,
                                        northings=N_solution,
                                        orthogonal_height=(session.get('z1')-(session.get('bz')*S_solution)),
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
        # ifc_schema = ifc_file.schema
        # ifc_site = ifc_file.by_type("IfcSite")[0]
        # ifc_unit = ifc_file.by_type("IfcUnitAssignment")[0].Units
        # ifc_geom = ifc_file.by_type("IfcGeometricRepresentationContext")[0]
        # ifc_mapconv, ifc_projcrs = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
        # session['ifc_schema'] = ifc_schema
        # session['ifc_site'] = pickle.dumps(ifc_site.ObjectPlacement)
        # # session['ifc_unit'] = ifc_unit
        # # session['ifc_geom'] = ifc_geom
        # # session['ifc_mapconv'] = ifc_mapconv
        # # session['ifc_projcrs'] = ifc_projcrs
        return ifc_file
    except Exception as e:
        print("Error opening IFC file:", str(e))  # Add this line for debugging
        return None

@app.route('/show/<filename>', methods=['GET', 'POST'])
def visualize(filename):
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fn_output = re.sub('\.ifc$','_georeferenced.ifc', fn)

    ifc_file = fileOpener(filename)
    ifc_units = ifc_file.by_type("IfcUnitAssignment")[0].Units
    for ifc_unit in ifc_units:
        if ifc_unit.is_a("IfcSIUnit") and ifc_unit.UnitType == "LENGTHUNIT":
            if ifc_unit.Prefix is not None:
                ifcunit = ifc_unit.Prefix + ifc_unit.Name
            else:
                ifcunit = ifc_unit.Name

    ureg = pint.UnitRegistry()
    quantity= unitmapper(ifcunit)
    mag = (quantity.to(ureg.meter).magnitude)*1000

    if not os.path.exists(fn_output):
        fn_output = fn
        ifc_file = fileOpener(filename)
        RLat = ifc_file.by_type("IfcSite")[0].RefLatitude
        RLon = ifc_file.by_type("IfcSite")[0].RefLongitude
        RElev = ifc_file.by_type("IfcSite")[0].RefElevation

        if RLat is not None or RLon is not None:
            x0= (float(RLat[0]) + float(RLat[1])/60 + float(RLat[2]+RLat[3]/1000000)/(60*60))
            y0= (float(RLon[0]) + float(RLon[1])/60 + float(RLon[2]+RLon[3]/1000000)/(60*60))
            session['Longitude'] = y0
            session['Latitude'] = x0

            session['Refl'] = True
        else:
            session['Refl'] = False

    Refl = session.get('Refl')
    ifc_file = ifcopenshell.open(fn_output)
    IfcMapConversion, IfcProjectedCRS = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
    target = IfcProjectedCRS.Name.split(':')
    E = IfcMapConversion.Eastings
    N = IfcMapConversion.Northings
    S = IfcMapConversion.Scale
    if S is None:
        S = 1
    ortz = IfcMapConversion.OrthogonalHeight
    cos = IfcMapConversion.XAxisAbscissa
    if cos is None:
        cos = 1    
    sin = IfcMapConversion.XAxisOrdinate
    if sin is None:
        sin = 0    
    #target_epsg = "EPSG:"+str(session.get('target_epsg'))
    target_epsg = "EPSG:"+ target[1]

    transformer2 = Transformer.from_crs(target_epsg,"EPSG:4326")
    #create JSON file
    json_dict = {
    "Filepaths": {
        "Input" : ['./uploads/'+filename],
        "Output" : "./envelop/"
    },
    "voxelSize" : {
        "xy" : 1,
        "z" : 1
    },
    "Footprint elevation" : 0.15,
    "Output report" : 1,
    "LoD output" : [0.2],
    "Ignore Proxy" : 1
    }


    fnjson = re.sub('\.ifc$','.json', filename)
    json_file = open(os.path.join('envelop', fnjson), 'w+')
    json_file.write(json.dumps(json_dict, indent=2))
    json_file.close()

    # Construct the full file paths relative to the current working directory
    path1 = os.path.join(os.getcwd(), 'envelop','Env02.exe')
    path2 = os.path.join(os.getcwd(), 'envelop', fnjson)
    result = subprocess.Popen([path1 , path2],    stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    while result.poll() is None:    time.sleep(0.5)
    if result.returncode == 0:
        print("\r", "Success")

    # Load the JSON data from the file
    fncjson = re.sub('\.json$','.city.json', fnjson)
    with open(os.path.join('envelop', fncjson), 'r') as cityjson_file:
        data = json.load(cityjson_file)

    # Extract the CityObjects dictionary
    city_objects = data.get("CityObjects", {})
    gpoly = []
    # Iterate through the CityObjects
    for object_id, city_object in city_objects.items():
        attributes = city_object.get("attributes", {})
        geometry = city_object.get("geometry", [])
        coordinates = data.get("vertices", [])

        # Check if this object has the "RoofSurface" type
        for geom in geometry:
            semantics = geom.get("semantics", {})
            surfaces = semantics.get("surfaces", [])

            if any(surface.get("type") == "RoofSurface" for surface in surfaces):
                print(f"Object ID: {object_id}")
                print(f"Coordinates of RoofSurface:")
                for boundary in geom.get("boundaries", []):
                    poly = []
                    for b in boundary:
                        pg = []
                        for coordinate_id in b:
                            if 0 <= coordinate_id < len(coordinates):
                                x,y,z = coordinates[coordinate_id]
                                po = (x/mag),(y/mag),(z/mag)
                                xx = S * cos * po[0] - S * sin * po[1] + E
                                yy = S * sin * po[0] + S * cos * po[1] + N
                                zz = po[2] + ortz
                                x2,y2 = transformer2.transform(xx,yy)
                                vert = y2,x2
                                pg.append(vert)
                        pg.append(pg[0])
                        poly.append(pg)
                    gpoly.append(poly)

    geo_json_dict = {
        "type": "FeatureCollection",
        "features": []
        }
    for pol in gpoly:
        if len(pol) == 1:
                polygon = Polygon(pol[0])
        else:
            polygon = Polygon(pol[0],holes = [pol[1]])
        feature = {
            'type': 'Feature',
            'properties': {},
            'geometry': mapping(polygon)
        }
        geo_json_dict["features"].append(feature)
    fn_ = re.sub('\.ifc$','.geojson', filename)
    geo_json_file = open(os.path.join('./static/', fn_), 'w+')
    geo_json_file.write(json.dumps(geo_json_dict, indent=2))
    geo_json_file.close()
    filename = "."+ geo_json_file.name
    # if Refl:
    #     Latitude =session.get('Latitude')
    #     Longitude =session.get('Longitude')
    # else:
    Latitude =gpoly[0][0][0][1]
    Longitude =gpoly[0][0][0][0]
    return render_template('view.html', filename=filename, Latitude=Latitude, Longitude=Longitude)

@app.route('/download/<filename>', methods=['GET'])
def download(filename):
    # Define the path to the GeoJSON file
    fn = re.sub('\.ifc$','_georeferenced.ifc', filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)

    # Ensure the file exists
    if os.path.exists(file_path):
        # Set the response headers to indicate a file download
        response = make_response()
        response.headers['Content-Type'] = 'application/octet-stream'
        response.headers['Content-Disposition'] = f'attachment; filename={fn}'
        
        # Read the file content and add it to the response
        with open(file_path, 'rb') as file:
            response.data = file.read()
        
        return response
    else:
        # Return a 404 error if the file doesn't exist
        return 'File not found', 404
    
if __name__ == '__main__':
    app.run(debug=True)
