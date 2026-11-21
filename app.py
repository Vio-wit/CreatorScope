from creatorscope import create_app
import os


app = create_app()


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("CREATORSCOPE_PORT", "8765")),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
