import argparse
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///test.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Line(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(255))


@app.route("/", methods= ["get"])
def root_get():
    return render_template("index.html", messages=Line.query.order_by(Line.id.desc()).limit(16))


@app.route("/", methods= ["post"])
def root_post():
    db.session.add(Line(message=request.form["message"]))
    db.session.commit()
    return redirect(url_for("root_get"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser("simple flask app")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    subparsers.add_parser("initdb", help="initialize the database")
    subparsers.add_parser("run", help="run the app")
    args = parser.parse_args()

    if args.cmd == "initdb":
        db.create_all()
    elif args.cmd == "run":
        app.run()
