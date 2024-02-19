import requests

url = 'https://ifcgref.bk.tudelft.nl/devs'
file_path = './01.ifc'


data = {
    'file': ('11.ifc', open(file_path, 'rb'))
}


# Make a POST request to the /devs route with the data
response = requests.post(url, files=data)

# Print the response content
print(response.text)

############



# # Set the URL of the /devs route in your Flask application
# url = "http://your-flask-app-url/devs"

# # Prepare data to be sent in the POST request
# data = {
#     'file': ('00.ifc', open(file_path, 'rb'))
# }

# # Make a POST request to the /devs route with the data
# response = requests.post(url, files=data)

# # Print the response content
# print(response.text)