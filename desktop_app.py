from pathlib import Path
import os
import socket
import threading
import webbrowser


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def desktop_data_dir():
    appdata = os.environ.get('LOCALAPPDATA')
    if appdata:
        return Path(appdata) / 'IfcGref'
    return Path.home() / '.ifcgref'


def open_browser_when_ready(port):
    url = f'http://127.0.0.1:{port}/'
    for _ in range(60):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                webbrowser.open(url)
                return
        except OSError:
            threading.Event().wait(0.25)
    webbrowser.open(url)


def main():
    port = int(os.environ.get('IFCGREF_PORT') or find_free_port())
    data_dir = Path(os.environ.get('IFCGREF_DATA_DIR') or desktop_data_dir())
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault('IFCGREF_DESKTOP', '1')
    os.environ.setdefault('IFCGREF_DATA_DIR', str(data_dir))
    os.environ.setdefault('IFCGREF_HOST', '127.0.0.1')
    os.environ['IFCGREF_PORT'] = str(port)
    os.environ.setdefault('IFCGREF_DEBUG', '0')

    from app import app

    if os.environ.get('IFCGREF_NO_BROWSER', '').lower() not in {'1', 'true', 'yes', 'on'}:
        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
