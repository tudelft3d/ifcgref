#!/usr/bin/env python
# coding: utf-8

# In[1]:


import subprocess
import os
import time
import re
import json
filename = '00.ifc'
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
json_file.write(json.dumps(json_dict, indent=1))
json_file.close()
print(json_file)


# In[5]:


# Construct the full file paths relative to the current working directory
path1 = os.path.join(os.getcwd(), 'envelop','EnvNew.exe')
path2 = os.path.join(os.getcwd(), 'envelop', fnjson)
result = subprocess.Popen([path1 , path2])
print(path1)
print(path2)

while result.poll() is None:    time.sleep(0.5)
if result.returncode == 0:
        print("\r", "Success")


# In[ ]:




