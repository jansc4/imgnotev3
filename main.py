"""Entry point ImgNote v3 (pywebview)."""
import os
import webview

from backendV4.api import Api

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
PR_SET_DUMPABLE = 4


def _disable_core_dumps():
    # zrzut pamięci zawierałby hasło i odszyfrowane obrazy; RLIMIT_CORE dziedziczą też
    # procesy QtWebEngine, PR_SET_DUMPABLE (reset przy execve) chroni proces główny
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass


def main():
    _disable_core_dumps()
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
    webview.start(debug=False)


if __name__ == "__main__":
    main()
