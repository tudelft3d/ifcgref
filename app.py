from flask import Flask, render_template, request, redirect, url_for  # Import the redirect function
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


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def infoExt(filename , epsgCode):
    ureg = pint.UnitRegistry()
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    ifc_file = ifcopenshell.open(fn)

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


    x2= x1*coeff
    y2= y1*coeff


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
            return "Invalid EPSG code. Please enter a valid integer."
        
        # Perform CRS conversion or other processing with the EPSG code here
        message = infoExt(filename,epsg_code)
        return render_template('convert.html', filename=filename, message=message)

    return render_template('convert.html', filename=filename)

if __name__ == '__main__':
    app.run(debug=True)
