import subprocess

result = subprocess.Popen(    [
        "/Users/shakim/Ifc_Envelope_Extractor_ifc4.exe",        "/Users/shakim/Desktop/venv_old/input01.json"
    ],    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL)
while result.poll() is None:    time.sleep(0.5)
if result.returncode == 0:
    print("\r", "Success")