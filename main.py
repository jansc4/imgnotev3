"""Entry point ImgNote v3 (pywebview)."""
import os
import webview

from backendV4.api import Api

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


def main():
    api = Api()
    window = webview.create_window(
        "ImgNote v3",
        os.path.join(FRONTEND_DIR, "index.html"),
        js_api=api,
        width=1100,
        height=700,
        text_select=True,
    )
    api.set_window(window)
    webview.start(debug=True)


if __name__ == "__main__":
    main()
