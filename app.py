from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, jsonify # Import the redirect function
from werkzeug.utils import secure_filename
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
import os
import sys
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
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.shape
import ifcopenshell.util.unit
import time
import threading
import traceback
import secrets


def env_int(name, default, minimum=None):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_flag(name):
    return os.environ.get(name, '').lower() in {'1', 'true', 'yes', 'on'}


APP_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(APP_DIR / '.env')
load_env_file(Path.cwd() / '.env')

DESKTOP_MODE = env_flag('IFCGREF_DESKTOP')
DATA_DIR = Path(os.environ.get('IFCGREF_DATA_DIR', Path.cwd()))

app = Flask(
    __name__,
    static_url_path='/static',
    static_folder=str(APP_DIR / 'static'),
    template_folder=str(APP_DIR / 'templates'),
)
app.secret_key = os.environ.get('IFCGREF_SECRET_KEY') or secrets.token_hex(32)
default_upload_folder = str(DATA_DIR / 'uploads') if DESKTOP_MODE else 'uploads'
app.config['UPLOAD_FOLDER'] = os.environ.get('IFCGREF_UPLOAD_FOLDER', default_upload_folder)
app.config['JOB_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'jobs')
app.config['MODEL_STATE_FOLDER'] = os.path.join(app.config['UPLOAD_FOLDER'], 'state')
if DESKTOP_MODE and 'IFCGREF_MAX_UPLOAD_MB' not in os.environ:
    app.config['MAX_CONTENT_LENGTH'] = None
else:
    app.config['MAX_CONTENT_LENGTH'] = env_int('IFCGREF_MAX_UPLOAD_MB', 250, minimum=1) * 1024 * 1024
app.config['MAPTILER_KEY'] = os.environ.get('IFCGREF_MAPTILER_KEY', '').strip()
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set
UPLOAD_RETENTION_DAYS = env_int('IFCGREF_UPLOAD_RETENTION_DAYS', 30, minimum=0)
JOB_RETENTION_DAYS = env_int('IFCGREF_JOB_RETENTION_DAYS', 7, minimum=0)

Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True, exist_ok=True)
Path(app.config['JOB_FOLDER']).mkdir(parents=True, exist_ok=True)
Path(app.config['MODEL_STATE_FOLDER']).mkdir(parents=True, exist_ok=True)

job_executor = ThreadPoolExecutor(max_workers=env_int('IFCGREF_WORKERS', 2, minimum=1))
job_lock = threading.Lock()
model_state_lock = threading.Lock()
map_context_cache = {}
map_context_lock = threading.Lock()
ifc_file_cache = {}
ifc_file_cache_order = []
ifc_file_cache_lock = threading.Lock()
IFC_FILE_CACHE_MAX = int(os.environ.get('IFCGREF_IFC_CACHE_MAX', '4'))
DESKTOP_PACKAGE_DIR = Path(os.environ.get('IFCGREF_DESKTOP_PACKAGE_DIR', Path.cwd() / 'desktop_dist'))
DESKTOP_PACKAGE_PATTERNS = {
    'windows': (
        'IfcGref-Desktop-Windows*.zip',
        'IfcGref-Desktop-Windows*.exe',
        'IfcGref-Desktop-Windows*.msi',
    ),
    'mac': (
        'IfcGref-Desktop-macOS*.zip',
        'IfcGref-Desktop-Mac*.zip',
        'IfcGref-Desktop-macOS*.dmg',
        'IfcGref-Desktop-Mac*.dmg',
    ),
}


def get_desktop_package(platform='windows'):
    if not DESKTOP_PACKAGE_DIR.exists():
        return None
    patterns = DESKTOP_PACKAGE_PATTERNS.get(platform)
    if patterns is None:
        return None
    candidates = []
    for pattern in patterns:
        candidates.extend(DESKTOP_PACKAGE_DIR.glob(pattern))
    files = [path for path in candidates if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def job_path(job_id):
    safe_job_id = secure_filename(job_id)
    if safe_job_id != job_id:
        raise ValueError("Invalid job id")
    return os.path.join(app.config['JOB_FOLDER'], f"{job_id}.json")


def read_job(job_id):
    try:
        with open(job_path(job_id), 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        return None


def write_job(job):
    job['updated_at'] = utc_now()
    path = job_path(job['id'])
    tmp_path = f"{path}.tmp"
    with job_lock:
        with open(tmp_path, 'w', encoding='utf-8') as file:
            json.dump(job, file, indent=2)
        os.replace(tmp_path, path)


def update_job(job_id, **changes):
    job = read_job(job_id)
    if job is None:
        return
    job.update(changes)
    write_job(job)


def model_state_path(filename):
    safe_filename = secure_filename(filename)
    if not safe_filename:
        raise ValueError("Invalid filename")
    return os.path.join(app.config['MODEL_STATE_FOLDER'], f"{safe_filename}.json")


def _read_model_state_unlocked(filename):
    try:
        with open(model_state_path(filename), 'r', encoding='utf-8') as file:
            state = json.load(file)
            if isinstance(state, dict):
                return state
    except FileNotFoundError:
        pass
    return {'filename': filename}


def read_model_state(filename):
    with model_state_lock:
        return _read_model_state_unlocked(filename)


def write_model_state(filename, state):
    safe_filename = secure_filename(filename)
    state = dict(state)
    state['filename'] = filename
    state['updated_at'] = utc_now()
    path = model_state_path(safe_filename)
    tmp_path = f"{path}.tmp"
    with model_state_lock:
        with open(tmp_path, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2)
        os.replace(tmp_path, path)
    return state


def update_model_state(filename, **changes):
    safe_filename = secure_filename(filename)
    with model_state_lock:
        state = _read_model_state_unlocked(safe_filename)
        state.update(changes)
        state['filename'] = filename
        state['updated_at'] = utc_now()
        path = model_state_path(safe_filename)
        tmp_path = f"{path}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as file:
            json.dump(state, file, indent=2)
        os.replace(tmp_path, path)
        return state


def workflow_value(filename, key, default=None):
    state = read_model_state(filename)
    if key in state:
        return state[key]
    return session.get(key, default)


def workflow_context(filename, keys):
    state = read_model_state(filename)
    return {key: state.get(key, session.get(key)) for key in keys}


def persist_workflow_values(filename, **changes):
    for key, value in changes.items():
        session[key] = value
    return update_model_state(filename, **changes)


def ifc_cache_key(filename):
    path = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    stat = os.stat(path)
    return (path, stat.st_mtime_ns, stat.st_size)


def remember_ifc_cache_order(cache_key):
    if cache_key in ifc_file_cache_order:
        ifc_file_cache_order.remove(cache_key)
    ifc_file_cache_order.append(cache_key)
    while len(ifc_file_cache_order) > IFC_FILE_CACHE_MAX:
        oldest_key = ifc_file_cache_order.pop(0)
        ifc_file_cache.pop(oldest_key, None)


def invalidate_ifc_cache(filename):
    path = os.path.abspath(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    with ifc_file_cache_lock:
        keys_to_remove = [key for key in ifc_file_cache if key[0] == path]
        for key in keys_to_remove:
            ifc_file_cache.pop(key, None)
            if key in ifc_file_cache_order:
                ifc_file_cache_order.remove(key)


def start_job(kind, task, payload=None, *args, **kwargs):
    job_id = uuid4().hex
    job = {
        'id': job_id,
        'kind': kind,
        'status': 'queued',
        'stage': 'Queued',
        'progress': 0,
        'created_at': utc_now(),
        'updated_at': utc_now(),
        'payload': payload or {},
        'result': None,
        'error': None,
    }
    write_job(job)
    job_executor.submit(run_job, job_id, task, *args, **kwargs)
    return job_id


def run_job(job_id, task, *args, **kwargs):
    def progress(stage, percent):
        update_job(job_id, status='running', stage=stage, progress=max(0, min(100, int(percent))))

    try:
        progress('Starting', 1)
        result = task(progress, *args, **kwargs)
        update_job(job_id, status='complete', stage='Ready', progress=100, result=result)
    except Exception as exc:
        update_job(
            job_id,
            status='failed',
            stage='Failed',
            progress=100,
            error={
                'message': str(exc),
                'traceback': traceback.format_exc(),
            },
        )

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_safe_upload_filename(filename):
    safe_filename = secure_filename(filename)
    return (
        filename == safe_filename
        and filename == os.path.basename(filename)
        and allowed_file(filename)
    )


def cleanup_old_files():
    now = time.time()

    def remove_older_than(folder, max_age_days, predicate=lambda path: path.is_file()):
        if max_age_days <= 0:
            return
        cutoff = now - (max_age_days * 86400)
        root = Path(folder)
        if not root.exists():
            return
        for path in root.iterdir():
            try:
                if predicate(path) and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    remove_older_than(
        app.config['UPLOAD_FOLDER'],
        UPLOAD_RETENTION_DAYS,
        predicate=lambda path: path.is_file() and path.suffix.lower() == '.ifc',
    )
    remove_older_than(app.config['JOB_FOLDER'], JOB_RETENTION_DAYS)
    remove_older_than(app.config['MODEL_STATE_FOLDER'], UPLOAD_RETENTION_DAYS)


cleanup_old_files()


@app.errorhandler(413)
def request_entity_too_large(error):
    if app.config['MAX_CONTENT_LENGTH']:
        max_mb = app.config['MAX_CONTENT_LENGTH'] // (1024 * 1024)
        message = f"File is too large. Maximum upload size is {max_mb} MB."
    else:
        message = "File is too large for this environment."
    return render_template('upload.html', error_message=message), 413

def georef(ifc_file):
    geo = False
    #check ifc version
    version = ifc_file.schema
    message = f"IFC version: {version}\n"
    # Check the file is georefed or not
    mapconversion = None
    crs = None

    if ifc_file.schema[:4] == 'IFC4':
        project = get_ifc_project(ifc_file)
        contexts = getattr(project, 'RepresentationContexts', None) or []
        for c in (m for c in contexts for m in getattr(c, 'HasCoordinateOperation', []) or []):
            mapconversion = c
            crs = c.TargetCRS
        if mapconversion is not None:
            message += "IFC file is georeferenced.\n"
            geo = True
    if ifc_file.schema == 'IFC2X3':
        mapconversion, crs = georeference_ifc.get_mapconversion_crs(ifc_file)
        if mapconversion is not None and crs is not None:
            message += "IFC file is georeferenced.\n"
            geo = True
    return message , geo
        
def infoExt(filename , epsgCode):
    ureg = pint.UnitRegistry()
    state_updates = {'target_epsg': int(epsgCode)} if epsgCode is not None else {}
    ifc_file = fileOpener(filename)
    if ifc_file is None:
        return [], "Could not open the IFC file."
    #check ifc version
    version = ifc_file.schema
    messages = [('IFC version', version)]
    try:
        ifc_site = get_ifc_site(ifc_file)
    except ValueError as exc:
        return messages, str(exc)


    #Find Longtitude and Latitude
    RLat = ifc_site.RefLatitude
    RLon = ifc_site.RefLongitude
    RElev = ifc_site.RefElevation
    x0 = ifc_angle_to_decimal(RLat)
    y0 = ifc_angle_to_decimal(RLon)
    if has_useful_ref_coordinates(x0, y0):
        if RElev is None:
            RElev = 0
        state_updates['Refl'] = True
        if hasattr(ifc_site, "ObjectPlacement") and ifc_site.ObjectPlacement and ifc_site.ObjectPlacement.is_a("IfcLocalPlacement"):
            local_placement = ifc_site.ObjectPlacement.RelativePlacement
                # Check if the local placement is an IfcAxis2Placement3D
            if local_placement.is_a("IfcAxis2Placement3D"):
                local_origin = local_placement.Location.Coordinates
                bx,by,bz= local_origin
                messages.append(('IFC Local Origin', local_origin))
            else:
                    errorMessage = "Local placement is not IfcAxis2Placement3D."
                    return messages, errorMessage
        else:
                errorMessage = "IfcSite does not have a local placement."
                return messages, errorMessage
    else:
        state_updates['Refl'] = False
        messages.append(('RefLatitude or RefLongitude', 'Not available'))
    Refl = state_updates.get('Refl')
    crs = None
    if ifc_file.schema[:4] != 'IFC4' and ifc_file.schema != 'IFC2X3':
        errorMessage = "IFC2X3, IFC4, and newer versions are supported.\n"
        return messages, errorMessage

    # Find local origin

                
    # Target CRS unit name
    try: 
        crs = pyproj.CRS.from_epsg(int(epsgCode))
    except:
        errorMessage = "CRS is not available."
        return messages, errorMessage


    crsunit = crs.axis_info[0].unit_name

    if crs.is_projected:
        messages.append(('Target CRS Type', 'Projected'))
        messages.append(('Target CRS EPSG', epsgCode))

    else:
        errorMessage = "CRS is not projected (geographic)."
        return messages, errorMessage
    target_epsg = "EPSG:"+str(epsgCode)
    transformer = Transformer.from_crs("EPSG:4326", target_epsg)
    # IFC length unit name
    try:
        ifcunit = get_length_unit_name(ifc_file)
    except ValueError as exc:
        return messages, str(exc)
    try: 
        quantity = unitmapper(ifcunit)
        ifcmeter = quantity.to(ureg.meter).magnitude
    except:
        ifcmeter = None
    try: 
        quantity = unitmapper(crsunit)
        crsmeter = quantity.to(ureg.meter).magnitude
    except:
        crsmeter = None

    if crsmeter is not None and ifcmeter is not None:
        coeff= ifcmeter/crsmeter
    else:
        errorMessage = "IFC/Map unit error"
        return messages, errorMessage
    if Refl:
        messages.append(("Reference Longitude",y0))
        messages.append(("Reference Latitude",x0))
        messages.append(("Reference Elevation",RElev))

    messages.append(("Target CRS Unit",str.lower(crsunit)))

    state_updates['mapunit'] = str.lower(crsunit)

    if ifcunit:
        unit_name = ifcunit
        messages.append(("IFC Unit",str.lower(unit_name)))
        state_updates['ifcunit'] = str.lower(unit_name)

    else:
        errorMessage = "No length unit found in the IFC file."
        return messages, errorMessage
    messages.append(("Unit Conversion Ratio",coeff))
    errorMessage = ""
    state_updates['coeff'] = coeff
    if Refl:
        x1,y1,z1 = transformer.transform(x0,y0,RElev)
        x2= x1*coeff
        y2= y1*coeff
        state_updates.update({
            'xt': x1,
            'yt': y1,
            'zt': z1,
            'Longitude': y0,
            'Latitude': x0,
        })

    persist_workflow_values(filename, **state_updates)

    return messages, errorMessage

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


def length_unit_ratio(ifc_file, target_epsg):
    ureg = pint.UnitRegistry()
    crsunit = pyproj.CRS(target_epsg).axis_info[0].unit_name
    ifcunit = get_length_unit_name(ifc_file)

    ifcmeter = unitmapper(ifcunit).to(ureg.meter).magnitude
    crsmeter = unitmapper(crsunit).to(ureg.meter).magnitude
    return ifcmeter / crsmeter


def ifc_angle_to_decimal(value):
    if value is None or len(value) < 3:
        return None

    degrees = float(value[0])
    minutes = float(value[1])
    seconds = float(value[2])
    millionths = float(value[3]) if len(value) > 3 else 0
    sign = -1 if degrees < 0 else 1
    return sign * (abs(degrees) + minutes / 60 + (seconds + millionths / 1000000) / 3600)


def has_useful_ref_coordinates(latitude, longitude):
    return latitude is not None and longitude is not None and not (abs(latitude) < 1e-12 and abs(longitude) < 1e-12)


def first_entity(ifc_file, entity_name):
    entities = ifc_file.by_type(entity_name)
    return entities[0] if entities else None


def get_ifc_site(ifc_file):
    site = first_entity(ifc_file, "IfcSite")
    if site is None:
        raise ValueError("No IfcSite entity found in the IFC file.")
    return site


def get_ifc_project(ifc_file):
    project = first_entity(ifc_file, "IfcProject")
    if project is None:
        raise ValueError("No IfcProject entity found in the IFC file.")
    return project


def get_representation_context(ifc_file):
    context = first_entity(ifc_file, "IfcGeometricRepresentationContext")
    if context is not None:
        return context

    project = get_ifc_project(ifc_file)
    contexts = getattr(project, 'RepresentationContexts', None)
    if contexts:
        return contexts[0]
    raise ValueError("No geometric representation context found in the IFC file.")


def get_world_origin(ifc_file):
    context = get_representation_context(ifc_file)
    world_coordinate_system = getattr(context, 'WorldCoordinateSystem', None)
    location = getattr(world_coordinate_system, 'Location', None)
    coordinates = getattr(location, 'Coordinates', None)
    if coordinates is None or len(coordinates) < 2:
        return (0, 0, 0)
    if len(coordinates) == 2:
        return (float(coordinates[0]), float(coordinates[1]), 0)
    return (float(coordinates[0]), float(coordinates[1]), float(coordinates[2]))


def get_length_unit_name(ifc_file):
    unit_assignment = first_entity(ifc_file, "IfcUnitAssignment")
    if unit_assignment is None:
        raise ValueError("No IfcUnitAssignment found in the IFC file.")

    for ifc_unit in unit_assignment.Units:
        if ifc_unit.is_a("IfcSIUnit") and ifc_unit.UnitType == "LENGTHUNIT":
            if ifc_unit.Prefix is not None:
                return ifc_unit.Prefix + ifc_unit.Name
            return ifc_unit.Name
    raise ValueError("No length unit found in the IFC file.")


def get_true_north_components(ifc_file):
    context = get_representation_context(ifc_file)
    true_north = getattr(context, 'TrueNorth', None)
    if true_north is not None and true_north.is_a("IfcDirection") and len(true_north[0]) >= 2:
        return round(float(true_north[0][0]), 6), round(float(true_north[0][1]), 6)
    return 0, 1


def get_mapconversion_crs_or_error(ifc_file):
    map_conversion, projected_crs = georeference_ifc.get_mapconversion_crs(ifc_file=ifc_file)
    if map_conversion is None or projected_crs is None:
        raise ValueError("No IFC map conversion or projected CRS information found.")
    return map_conversion, projected_crs


def get_epsg_from_projected_crs(projected_crs):
    name = getattr(projected_crs, 'Name', None)
    if not name:
        raise ValueError("Projected CRS name is missing.")
    name = str(name)
    match = re.search(r'\bEPSG\s*[:/ ]\s*(\d{3,6})\b', name, re.IGNORECASE)
    if not match:
        match = re.search(r'(\d+)$', name)
    if not match:
        raise ValueError(f"Could not read EPSG code from projected CRS name: {name}")
    return int(match.group(1))


def render_georef_result(filename):
    source_filename = filename
    output_filename = re.sub(r'\.ifc$', '_georeferenced.ifc', filename)
    output_exists = os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], output_filename))
    if output_exists:
        source_filename = output_filename

    ifc_file = fileOpener(source_filename)
    if ifc_file is None:
        return render_template('upload.html', error_message="Could not open the IFC file.")

    try:
        message, geo = georef(ifc_file)
        if not geo:
            return redirect(url_for('convert_crs', filename=filename))
        IfcMapConversion, IfcProjectedCRS = get_mapconversion_crs_or_error(ifc_file)
    except ValueError as exc:
        return render_template('convert.html', filename=filename, message=str(exc))
    df = pd.DataFrame(list(IfcProjectedCRS.__dict__.items()), columns=['property', 'value'])
    dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns=['property', 'value'])
    html_table_f = df.to_html()
    html_table_g = dg.to_html()

    try:
        epsg = get_epsg_from_projected_crs(IfcProjectedCRS)
        message2 = infoExt(source_filename, epsg)
        coeff = workflow_value(source_filename, 'coeff')
    except Exception:
        coeff = None
        message2 = message

    if coeff is None:
        message = message2
    else:
        scale_error = int(coeff) != 1 and (
            IfcMapConversion.Scale is None or int(IfcMapConversion.Scale) == 1
        )
        if scale_error:
            message += "There is a conflict between Scale factor and unit conversion. (Yet to be decided by buildingSmart.)"
            persist_workflow_values(source_filename, scaleError=True)
        try:
            cache_map_context(
                source_filename, ifc_file, eff=coeff, scale_error=scale_error,
            )
        except ValueError:
            message += "\nCould not prepare the map viewer. Check the target CRS and model coordinates."
    return render_template(
        'result.html',
        filename=filename,
        table_f=html_table_f,
        table_g=html_table_g,
        message=message,
        download_available=output_exists,
    )


def inspect_uploaded_ifc(progress, filename):
    progress('Reading IFC file', 20)
    ifc_file = fileOpener(filename)
    if ifc_file is None:
        raise RuntimeError("Could not open the IFC file.")

    progress('Checking georeference data', 60)
    _, geo = georef(ifc_file)
    if geo:
        redirect_url = f"/result/{filename}"
        message = "The IFC is already georeferenced."
    else:
        redirect_url = f"/convert/{filename}"
        message = "The IFC needs CRS information before georeferencing."

    progress('Preparing next step', 90)
    return {
        'filename': filename,
        'georeferenced': geo,
        'redirect_url': redirect_url,
        'message': message,
    }


def write_georeferenced_ifc(progress, filename, form_data, context):
    progress('Opening IFC file', 10)
    coeff = context.get('coeff')
    rows = context.get('rows')
    Refl = context.get('Refl')
    target_epsg_value = context.get('target_epsg')
    map_unit = context.get('mapunit')

    if coeff is None or rows is None or target_epsg_value is None:
        raise RuntimeError("Missing georeferencing context. Please restart from the EPSG step.")

    coeff = float(coeff)
    rows = int(rows)
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    ifc_file = fileOpener(filename)
    if ifc_file is None:
        raise RuntimeError("Could not open the IFC file.")

    data_points = []
    if Refl:
        xt = float(context.get('xt'))
        yt = float(context.get('yt'))
        zt = float(context.get('zt'))
        bx = float(context.get('bx'))
        by = float(context.get('by'))
        bz = float(context.get('bz'))
        data_points.append({"X": bx, "Y": by, "Z": bz, "X_prime": xt, "Y_prime": yt, "Z_prime": zt})

    progress('Solving coordinate transform', 35)
    if not Refl and rows == 1:
        xord, xabs = get_true_north_components(ifc_file)
        Rotation_solution = math.atan2(xord, xabs)
        A = math.cos(Rotation_solution)
        B = math.sin(Rotation_solution)
        S_solution = coeff
        E_solution = float(form_data[f'x_prime{0}']) - (A * float(form_data[f'x{0}']) * coeff) + (B * float(form_data[f'y{0}']) * coeff)
        N_solution = float(form_data[f'y_prime{0}']) - (B * float(form_data[f'x{0}']) * coeff) - (A * float(form_data[f'y{0}']) * coeff)
        H_solution = float(form_data[f'z_prime{0}']) - (float(form_data[f'z{0}']) * coeff)
    else:
        if rows == 0:
            xord, xabs = get_true_north_components(ifc_file)
            Rotation_solution = math.atan2(xord, xabs)
            A = math.cos(Rotation_solution)
            B = math.sin(Rotation_solution)
            S_solution = coeff
            E_solution = xt - (A * S_solution * bx) + (B * S_solution * by)
            N_solution = yt - (B * S_solution * bx) - (A * S_solution * by)
            H_solution = zt - (S_solution * bz)
        else:
            for row in range(rows):
                try:
                    x = float(form_data[f'x{row}'])
                    y = float(form_data[f'y{row}'])
                    z = float(form_data[f'z{row}'])
                    x_prime = float(form_data[f'x_prime{row}'])
                    y_prime = float(form_data[f'y_prime{row}'])
                    z_prime = float(form_data[f'z_prime{row}'])
                except (KeyError, ValueError):
                    raise RuntimeError("Invalid input. Please enter only float values.")

                data_points.append({"X": x, "Y": y, "Z": z, "X_prime": x_prime, "Y_prime": y_prime, "Z_prime": z_prime})

            def equations(variables, data_points):
                S, Rotation, E, N, H = variables
                eqs = []
                for data in data_points:
                    X = data["X"]
                    Y = data["Y"]
                    Z = data["Z"]
                    X_prime = data["X_prime"]
                    Y_prime = data["Y_prime"]
                    Z_prime = data["Z_prime"]
                    eq1 = S * np.cos(Rotation) * X - S * np.sin(Rotation) * Y + E - X_prime
                    eq2 = S * np.sin(Rotation) * X + S * np.cos(Rotation) * Y + N - Y_prime
                    eq3 = S * Z + H - Z_prime
                    eqs.extend([eq1, eq2, eq3])
                return eqs

            if Refl:
                initial_guess = [coeff, 0, xt, yt, zt]
            else:
                xg = float(form_data[f'x_prime{0}']) - (float(form_data[f'x{0}']) * coeff)
                yg = float(form_data[f'y_prime{0}']) - (float(form_data[f'y{0}']) * coeff)
                zg = float(form_data[f'z_prime{0}']) - (float(form_data[f'z{0}']) * coeff)
                initial_guess = [coeff, 0, xg, yg, zg]

            result = leastsq(equations, initial_guess, args=(data_points,), full_output=True)
            S_solution, Rotation_solution, E_solution, N_solution, H_solution = result[0]

    progress('Writing IFC georeference data', 70)
    target_epsg = "EPSG:" + str(target_epsg_value)
    georeference_ifc.set_mapconversion_crs(
        ifc_file=ifc_file,
        target_crs_epsg_code=target_epsg,
        eastings=E_solution,
        northings=N_solution,
        orthogonal_height=H_solution,
        x_axis_abscissa=math.cos(Rotation_solution),
        x_axis_ordinate=math.sin(Rotation_solution),
        scale=S_solution,
        map_unit=map_unit,
    )

    progress('Saving georeferenced IFC', 90)
    fn_output = re.sub(r'\.ifc$', '_georeferenced.ifc', fn)
    ifc_file.write(fn_output)
    invalidate_ifc_cache(filename)
    invalidate_ifc_cache(os.path.basename(fn_output))
    output_filename = os.path.basename(fn_output)
    update_model_state(
        output_filename,
        source_filename=filename,
        target_epsg=target_epsg_value,
        coeff=coeff,
        rows=rows,
        Refl=Refl,
        xt=context.get('xt'),
        yt=context.get('yt'),
        zt=context.get('zt'),
        bx=context.get('bx'),
        by=context.get('by'),
        bz=context.get('bz'),
        output_filename=output_filename,
    )
    update_model_state(filename, output_filename=output_filename)
    return {
        'filename': filename,
        'output_filename': output_filename,
        'redirect_url': f"/result/{filename}",
        'download_url': f"/download/{filename}",
        'message': 'Georeferenced IFC is ready.',
    }


def get_viewer_origin(ifc_file):
    """Choose an absolute body vertex in metres, without changing the IFC."""
    products = [
        product for product in ifc_file.by_type('IfcProduct')
        if product.Representation and not any(product.is_a(kind) for kind in (
            'IfcAnnotation', 'IfcFeatureElement', 'IfcSpace',
        ))
    ]
    settings = ifcopenshell.geom.settings()
    settings.set('convert-back-units', False)
    for context in ifcopenshell.util.representation.get_prioritised_contexts(ifc_file):
        if context.CoordinateSpaceDimension != 3 or context.ContextType == 'Plan':
            continue
        settings.set('context-ids', [context.id()])
        for product in products:
            if not any(rep.ContextOfItems == context for rep in product.Representation.Representations):
                continue
            try:
                shape = ifcopenshell.geom.create_shape(settings, product)
                if not shape.geometry.faces:
                    continue
                vertices = ifcopenshell.util.shape.get_shape_vertices(shape, shape.geometry)
                for vertex in vertices:
                    if np.isfinite(vertex).all():
                        return tuple(float(value) for value in vertex)
            except (RuntimeError, ValueError):
                # An unsupported representation must not hide later valid bodies.
                continue
    # No usable body: retain the coordinate origin, never an arbitrary placement.
    return (0.0, 0.0, 0.0)


def build_map_context(ifc_file, model_filename, eff=None, scale_error=False):
    IfcMapConversion, IfcProjectedCRS = get_mapconversion_crs_or_error(ifc_file)
    target_epsg = "EPSG:" + str(get_epsg_from_projected_crs(IfcProjectedCRS))
    org = get_world_origin(ifc_file)
    viewer_origin = get_viewer_origin(ifc_file)
    # Geometry engines return metres; map conversion consumes project units.
    project_unit = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    anchor_x, anchor_y = (value / project_unit for value in viewer_origin[:2])
    E = IfcMapConversion.Eastings
    N = IfcMapConversion.Northings
    S = IfcMapConversion.Scale
    if S is None:
        S = 1
    cos = IfcMapConversion.XAxisAbscissa
    if cos is None:
        cos = 1
    sin = IfcMapConversion.XAxisOrdinate
    if sin is None:
        sin = 0
    Rotation_solution = math.atan2(sin, cos)
    A = math.cos(Rotation_solution)
    B = math.sin(Rotation_solution)
    transformer2 = Transformer.from_crs(target_epsg, "EPSG:4326", always_xy=True)
    if eff is None:
        eff = project_unit / pyproj.CRS(target_epsg).axis_info[0].unit_conversion_factor
    eff = float(eff)

    if scale_error:
        saver = S
        S = eff
        E = E * S
        N = N * S
        xx = S * org[0] * A - S * org[1] * B + E
        yy = S * org[0] * B + S * org[1] * A + N
        S = saver
        Snew = S
    else:
        xx = S * org[0] * A - S * org[1] * B + E
        yy = S * org[0] * B + S * org[1] * A + N
        Snew = S / eff

    # Move the map anchor by the same transform used to draw the rebased mesh.
    # Preserve the existing interpretation of legacy scale-error files.
    anchor_scale = S * eff if scale_error else S
    xx += anchor_scale * (anchor_x * A - anchor_y * B)
    yy += anchor_scale * (anchor_x * B + anchor_y * A)

    longitude, latitude = transformer2.transform(xx, yy)
    transformer3 = Transformer.from_crs(target_epsg, "EPSG:3857", always_xy=True)
    # Local IFC X/Y basis in MapLibre's normalized Mercator coordinates.
    # Sample one metre either side of the anchor, including rotation and Scale.
    # This replaces the old hardcoded projection correction on mesh geometry.
    map_units_per_metre = anchor_scale / project_unit
    circumference = 2 * math.pi * pyproj.CRS('EPSG:3857').ellipsoid.semi_major_metre
    map_axes = []
    for x, y in ((A, B), (-B, A)):
        dx, dy = x * map_units_per_metre, y * map_units_per_metre
        before = transformer3.transform(xx - dx, yy - dy)
        after = transformer3.transform(xx + dx, yy + dy)
        map_axes.append([
            (after[0] - before[0]) / (2 * circumference),
            -(after[1] - before[1]) / (2 * circumference),
        ])
    if not np.isfinite([longitude, latitude, *np.array(map_axes).flat]).all():
        raise ValueError('The model geometry is outside the target CRS projection domain.')

    maptiler_query = urlencode({'key': app.config['MAPTILER_KEY']})
    maptiler_styles = [
        {
            'id': 'maptiler-osm',
            'label': 'MapTiler OpenStreetMap',
            'style': f'https://api.maptiler.com/maps/openstreetmap/style.json?{maptiler_query}',
        },
        {
            'id': 'streets',
            'label': 'MapTiler Streets',
            'style': f'https://api.maptiler.com/maps/streets-v2/style.json?{maptiler_query}',
        },
        {
            'id': 'satellite',
            'label': 'MapTiler Satellite',
            'style': f'https://api.maptiler.com/maps/satellite/style.json?{maptiler_query}',
        },
    ]

    return {
        'filename': model_filename,
        'Latitude': latitude,
        'Longitude': longitude,
        'origin': viewer_origin,
        'Scale': Snew,
        'MapAxes': map_axes,
        'LowestLevel': 0,
        'MapTilerStyles': maptiler_styles,
    }


def cache_map_context(model_filename, ifc_file, eff=None, scale_error=False):
    model_path = os.path.join(app.config['UPLOAD_FOLDER'], model_filename)
    cache_key = (model_filename, os.path.getmtime(model_path))
    context = build_map_context(ifc_file, model_filename, eff=eff, scale_error=scale_error)
    with map_context_lock:
        map_context_cache.clear()
        map_context_cache[cache_key] = context
    return context

@app.route('/')
def index():
    windows_desktop_package = get_desktop_package('windows')
    mac_desktop_package = get_desktop_package('mac')
    return render_template(
        'upload.html',
        windows_desktop_package_available=windows_desktop_package is not None,
        windows_desktop_package_name=windows_desktop_package.name if windows_desktop_package else None,
        mac_desktop_package_available=mac_desktop_package is not None,
        mac_desktop_package_name=mac_desktop_package.name if mac_desktop_package else None,
        desktop_mode=DESKTOP_MODE,
    )


@app.route('/desktop/download')
def desktop_download_default():
    return desktop_download('windows')


@app.route('/desktop/download/<platform>')
def desktop_download(platform):
    if platform not in DESKTOP_PACKAGE_PATTERNS:
        return 'File not found', 404
    desktop_package = get_desktop_package(platform)
    if desktop_package is None:
        build_script = 'desktop/build_windows.ps1' if platform == 'windows' else 'desktop/build_macos.sh'
        return render_template(
            'upload.html',
            error_message=f"Desktop app package for {platform} is not built yet. Run {build_script} to create it.",
            windows_desktop_package_available=get_desktop_package('windows') is not None,
            windows_desktop_package_name=get_desktop_package('windows').name if get_desktop_package('windows') else None,
            mac_desktop_package_available=get_desktop_package('mac') is not None,
            mac_desktop_package_name=get_desktop_package('mac').name if get_desktop_package('mac') else None,
            desktop_mode=DESKTOP_MODE,
        ), 404
    return send_from_directory(
        DESKTOP_PACKAGE_DIR,
        desktop_package.name,
        as_attachment=True,
        download_name=desktop_package.name,
    )


@app.route('/jobs/<job_id>')
def job_status_page(job_id):
    job = read_job(job_id)
    if job is None:
        return render_template('upload.html', error_message="Job not found.")
    return render_template('job.html', job=job)


@app.route('/api/jobs/<job_id>')
def job_status_api(job_id):
    job = read_job(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


@app.route('/result/<filename>')
def result_page(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    return render_georef_result(filename)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "No file part"
    file = request.files['file']
    if file.filename == '':
        return "No selected file"
    filename = secure_filename(file.filename)
    if file and allowed_file(file.filename) and is_safe_upload_filename(filename):
        filename = f"{uuid4().hex}_{filename}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        update_model_state(filename, original_filename=file.filename, uploaded_at=utc_now())
        job_id = start_job(
            'inspect-upload',
            inspect_uploaded_ifc,
            payload={'filename': filename, 'original_filename': file.filename},
            filename=filename,
        )
        return redirect(url_for('job_status_page', job_id=job_id))
    else:
        return render_template('upload.html', error_message="Invalid filename or format. Please upload a named .ifc file."), 400

@app.route('/devs', methods=['GET', 'POST'])
def devs_upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            return "No file part"

        file = request.files['file']

        if file.filename == '':
            return "No selected file"

        filename = secure_filename(file.filename)
        if file and allowed_file(file.filename) and is_safe_upload_filename(filename):
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            update_model_state(filename, original_filename=file.filename, uploaded_at=utc_now())
            ifc_file = fileOpener(filename)
            if ifc_file is None:
                return "Could not open the IFC file.", 400

            # Check if the IFC file is georeferenced
            try:
                message, geo = georef(ifc_file)
            except ValueError as exc:
                return str(exc), 400

            if geo:
                IfcMapConversion, IfcProjectedCRS = get_mapconversion_crs_or_error(ifc_file)
                dg = pd.DataFrame(list(IfcMapConversion.__dict__.items()), columns= ['property', 'value'])
                message += "IfcMapconversion:\n\n" + dg.to_string()
                return f"Filename: {filename}\nGeoreferenced: YES\n{message}"
            else:
                message += "For georeferencing the IFC file, please visit the following address in a web browser:\nhttps://ifcgref.bk.tudelft.nl"
                return f"Filename: {filename}\nGeoreferenced: NO\n{message}"
        return "Invalid filename or format. Please upload a named .ifc file.", 400
                
@app.route('/convert/<filename>', methods=['GET', 'POST'])
def convert_crs(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    if request.method == 'POST':
        try:
            epsg_code = int(request.form.get('epsg_code', ''))
        except ValueError:
            message = "Invalid EPSG code. Please enter a valid integer."
            return render_template('convert.html', filename=filename, message=message)
        persist_workflow_values(filename, target_epsg=epsg_code)
       # Call the infoExt function and unpack the results
        messages, error = infoExt(filename, epsg_code)
        if error == "":
            # Pass x2, y2, and z1 to the survey_points route
            return redirect(url_for('survey_points', filename=filename))
        return render_template('convert.html', filename=filename, message=error)

    return render_template('convert.html', filename=filename)


def survey_point_field_names(rows):
    fields = ('x', 'y', 'z', 'x_prime', 'y_prime', 'z_prime')
    return [f'{field}{row}' for row in range(rows) for field in fields]


def validate_survey_values(form_data, rows):
    field_errors = {}
    for field_name in survey_point_field_names(rows):
        value = form_data.get(field_name, '').strip()
        try:
            number = float(value)
        except ValueError:
            field_errors[field_name] = "Enter a numeric value."
            continue
        if not math.isfinite(number):
            field_errors[field_name] = "Enter a finite numeric value."
    return field_errors


def render_survey_with_values(filename, rows, form_values=None, field_errors=None, error=""):
    epsg_code = workflow_value(filename, 'target_epsg')
    messages, context_error = infoExt(filename, epsg_code)
    ifcunit = workflow_value(filename, 'ifcunit')
    mapunit = workflow_value(filename, 'mapunit')
    Refl = workflow_value(filename, 'Refl')

    if Refl:
        messages, context_error = local_trans(filename, messages)
    else:
        context_error += '\nThe model has no surveyed or georeferenced attribute.\nYou need to provide at least one point in local and target CRS.'
        context_error += '\n\nAccuracy of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction (if availalble).\n'

    errors = [text for text in (error, context_error) if text]
    return render_template(
        'survey.html',
        filename=filename,
        messages=messages,
        Num=rows,
        ifcunit=ifcunit,
        mapunit=mapunit,
        error="\n".join(errors),
        Refl=Refl,
        form_values=form_values or {},
        field_errors=field_errors or {},
    )


@app.route('/survey/<filename>', methods=['GET', 'POST'])
def survey_points(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    epsg_code = workflow_value(filename, 'target_epsg')
    messages, error = infoExt(filename, epsg_code)
    ifcunit = workflow_value(filename, 'ifcunit')
    mapunit = workflow_value(filename, 'mapunit')
    Refl = workflow_value(filename, 'Refl')
    if request.method == 'POST':
        box_number = request.form.get('boxNumber')
        if box_number == '3':
            persist_workflow_values(filename, Refl=False)
            Refl = False
    if Refl:
        messages , error = local_trans(filename,messages)
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num < 0:
                    error += "Please enter zero or a positive integer."
                    return render_template('survey.html', filename=filename, messages=messages, error=error)
            except ValueError:
                error += "Please enter zero or a positive integer."
                return render_template('survey.html', filename=filename, messages=messages, error=error)
            persist_workflow_values(filename, rows=Num)
            if Num == 0:
                return redirect(url_for('calculate', filename=filename))
        return render_template('survey.html', filename=filename, messages=messages, Num=Num, ifcunit=ifcunit, mapunit=mapunit, error=error, Refl = Refl)
    else:
        error += '\nThe model has no surveyed or georeferenced attribute.\nYou need to provide at least one point in local and target CRS.'
        error += '\n\nAccuracy of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction (if availalble).\n'
        Num = []
        if request.method == 'POST':
            try:
                Num = int(request.form['Num'])
                if Num <= 0:
                    error += "Please enter a positive integer."
                    return render_template('survey.html', filename=filename, error=error)
            except ValueError:
                error += "Please enter a positive integer."
                return render_template('survey.html', filename=filename, error=error)
            persist_workflow_values(filename, rows=Num)
        return render_template('survey.html', filename=filename, messages=messages, Num=Num, ifcunit=ifcunit, mapunit=mapunit, Refl = Refl)


def local_trans(filename , messages):
    ifc_file = fileOpener(filename)
    xt = workflow_value(filename, 'xt')
    yt = workflow_value(filename, 'yt')
    zt = workflow_value(filename, 'zt')
    bx,by,bz = 0,0,0
    error = ""
    try:
        ifc_site = get_ifc_site(ifc_file)
    except ValueError as exc:
        return messages, str(exc)

    if hasattr(ifc_site, "ObjectPlacement") and ifc_site.ObjectPlacement and ifc_site.ObjectPlacement.is_a("IfcLocalPlacement"):
        local_placement = ifc_site.ObjectPlacement.RelativePlacement
        # Check if the local placement is an IfcAxis2Placement3D
        if local_placement.is_a("IfcAxis2Placement3D"):
            local_origin = local_placement.Location.Coordinates
            bx, by, bz = map(float, local_origin)
            messages.append(("First Point Local Coordinates",str(local_origin)))
        else:
                error += "Local placement is not IfcAxis2Placement3D."
    else:
            error += "IfcSite does not have a local placement."
    persist_workflow_values(filename, bx=bx, by=by, bz=bz)

    messages.append(("First Point Target coordinates" , ("(" + str(xt) + ", " + str(yt) + ", " + str(zt) + ")")))
    error += '\n\nAccuracy of the results improves as you provide more georeferenced points.\nWithout any additional georeferenced points, it is assumed that the model is scaled based on unit conversion and rotation is derived from TrueNorth direction (if available).\n'

    ifc_file = ifc_file.end_transaction()
    return messages, error

@app.route('/calc/<filename>', methods=['GET', 'POST'])
def calculate(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    context = workflow_context(filename, [
        'coeff',
        'rows',
        'Refl',
        'xt',
        'yt',
        'zt',
        'bx',
        'by',
        'bz',
        'target_epsg',
        'mapunit',
    ])

    if context['coeff'] is None or context['rows'] is None or context['target_epsg'] is None:
        return redirect(url_for('convert_crs', filename=filename))

    try:
        rows = int(context['rows'])
    except (TypeError, ValueError):
        return redirect(url_for('convert_crs', filename=filename))

    context['rows'] = rows
    form_data = request.form.to_dict()
    field_errors = validate_survey_values(form_data, rows)
    if field_errors:
        error = "Please correct the highlighted surveyed point values."
        return render_survey_with_values(filename, rows, form_values=form_data, field_errors=field_errors, error=error)

    job_id = start_job(
        'georeference-ifc',
        write_georeferenced_ifc,
        payload={'filename': filename},
        filename=filename,
        form_data=form_data,
        context=context,
    )
    return redirect(url_for('job_status_page', job_id=job_id))
    
def fileOpener(filename):
    cache_key = ifc_cache_key(filename)
    with ifc_file_cache_lock:
        cached_ifc_file = ifc_file_cache.get(cache_key)
        if cached_ifc_file is not None:
            remember_ifc_cache_order(cache_key)
            print("Using cached IFC file:", cache_key[0])
            return cached_ifc_file

    fn = cache_key[0]
    print("Opening IFC file:", fn)
    try:
        ifc_file = ifcopenshell.open(fn)
        with ifc_file_cache_lock:
            ifc_file_cache[cache_key] = ifc_file
            remember_ifc_cache_order(cache_key)
        return ifc_file
    except Exception as e:
        print("Error opening IFC file:", str(e))  # Add this line for debugging
        return None

@app.route('/show/<filename>', methods=['GET', 'POST'])
def visualize(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    fn = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    fn_output = re.sub(r'\.ifc$','_georeferenced.ifc', fn)
    if not os.path.exists(fn_output):
        fn_output = fn
    model_filename = os.path.basename(fn_output)
    cache_key = (model_filename, os.path.getmtime(fn_output))
    with map_context_lock:
        cached_context = map_context_cache.get(cache_key)
    if cached_context is not None:
        return render_template('view3D.html', **cached_context)

    try:
        ifc_file = ifcopenshell.open(fn_output)
        scaleError = workflow_value(model_filename, 'scaleError', workflow_value(filename, 'scaleError'))
        eff = workflow_value(model_filename, 'coeff', workflow_value(filename, 'coeff'))
        context = cache_map_context(model_filename, ifc_file, eff=eff, scale_error=scaleError)
        if scaleError:
            session.pop('scaleError', None)
            update_model_state(model_filename, scaleError=False)
            update_model_state(filename, scaleError=False)
        return render_template('view3D.html', **context)
    except Exception as exc:
        return render_template('result.html', filename=filename, message=f"Could not open map viewer: {exc}")

@app.route('/download/<filename>', methods=['GET'])
def download(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    fn = re.sub(r'\.ifc$','_georeferenced.ifc', filename)
    if not is_safe_upload_filename(fn):
        return 'File not found', 404
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], fn)

    if os.path.exists(file_path):
        response = send_from_directory(
            app.config['UPLOAD_FOLDER'],
            fn,
            as_attachment=True,
            download_name=fn,
            mimetype='application/octet-stream',
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    else:
        return 'File not found', 404
@app.route('/templates/<path:filename>')
def temp(filename):
    return send_from_directory(str(APP_DIR / 'templates'), filename)
   
@app.route('/uploads/<path:filename>')
def ups(filename):
    if not is_safe_upload_filename(filename):
        return 'File not found', 404
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename, mimetype='application/octet-stream')
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.route('/chek',  methods=['GET', 'POST'])
def chs():
    return render_template('chek.html')

if __name__ == '__main__':
    debug = os.environ.get('IFCGREF_DEBUG', '').lower() in {'1', 'true', 'yes', 'on'}
    host = os.environ.get('IFCGREF_HOST', '127.0.0.1')
    port = env_int('IFCGREF_PORT', 5000, minimum=1)
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)
