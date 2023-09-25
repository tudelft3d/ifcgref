from flask import Flask, render_template, request, redirect, url_for  # Import the redirect function
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
ALLOWED_EXTENSIONS = {'ifc'}  # Define allowed file extensions as a set

# Function to check if a filename has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        return redirect(url_for('convert_crs', filename=filename))  # Redirect to EPSG code input page
    else:
        return "Invalid file format. Please upload a .ifc file."
    
@app.route('/convert/<filename>', methods=['GET', 'POST'])
def convert_crs(filename):
    if request.method == 'POST':
        try:
            epsg_code = int(request.form['epsg_code'])
        except ValueError:
            return "Invalid EPSG code. Please enter a valid integer."
        
        # Perform CRS conversion or other processing with the EPSG code here
        
        return "CRS conversion completed successfully (EPSG code: {})".format(epsg_code)

    return render_template('convert.html', filename=filename)

if __name__ == '__main__':
    app.run(debug=True)
