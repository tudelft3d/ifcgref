import requests

url = 'https://ifcgref.bk.tudelft.nl/devs'
file_path = './01.ifc'


data = {
    'file': ('01.ifc', open(file_path, 'rb'))
}


# Make a POST request to the /devs route with the data
response = requests.post(url, files=data)

# Print the response content
print(response.text)
