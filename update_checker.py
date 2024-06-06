import requests
import os
import shutil
import tempfile
import subprocess
import sys
import zipfile

GITHUB_REPO = "KnudPro1/YTToMP3"  # Replace with your GitHub repo
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

def download_latest_release():
    response = requests.get(LATEST_RELEASE_URL)
    response.raise_for_status()
    latest_release = response.json()

    latest_version = latest_release["tag_name"]
    asset = next((a for a in latest_release["assets"] if a["name"].endswith(".zip")), None)
    
    if not asset:
        print("No suitable asset found for download.")
        return False

    download_url = asset["browser_download_url"]
    download_name = asset["name"]

    with tempfile.TemporaryDirectory() as tmpdirname:
        temp_zip_path = os.path.join(tmpdirname, download_name)
        with requests.get(download_url, stream=True) as r:
            with open(temp_zip_path, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdirname)

        new_exe_path = os.path.join(tmpdirname, 'MyApp.exe')

        if os.path.exists(new_exe_path):
            shutil.move(new_exe_path, sys.argv[0])
            return True

    return False

def check_for_update():
    try:
        response = requests.get(LATEST_RELEASE_URL)
        response.raise_for_status()
        latest_release = response.json()
        
        latest_version = latest_release["tag_name"]
        current_version = get_current_version()

        if latest_version != current_version:
            print(f"New version available: {latest_version}. Current version: {current_version}")
            print("Updating...")
            if download_latest_release():
                print("Update successful. Restarting...")
                restart_application()
            else:
                print("Update failed.")
        else:
            print("Already running the latest version.")

    except Exception as e:
        print(f"Failed to check for update: {e}")

def get_current_version():
    # Define the current version of your application here.
    return "v1.0.0"

def restart_application():
    if os.name == 'nt':
        subprocess.Popen([sys.argv[0]], shell=True)
    else:
        os.execv(sys.executable, [sys.executable] + sys.argv)

    sys.exit()

if __name__ == "__main__":
    check_for_update()
