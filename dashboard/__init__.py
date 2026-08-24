from flask import Flask, jsonify, render_template


def create_app(db):
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/stats")
    def stats():
        return jsonify(db.stats())

    @app.route("/api/events")
    def events():
        return jsonify({"events": db.recent(60)})

    return app
