import webview
import threading
import uvicorn
from main import app

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == '__main__':
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    webview.create_window(
        title='AGS Solutions Dashboard',
        url='http://127.0.0.1:8000/dashboard',
        width=1280,
        height=800,
        text_select=False
    )

    webview.start()
