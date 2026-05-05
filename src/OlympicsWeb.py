import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, session

from src.storage import (
    create_user,
    delete_like,
    get_liked_countries,
    get_user_password,
    save_like,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "doc"
APP_BASE_PATH = "/" + os.environ.get("APP_BASE_PATH", "OlympicsInTokyo").strip("/")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "team18stage4")


def app_url(path=""):
    path = str(path or "").strip("/")
    return APP_BASE_PATH if not path else f"{APP_BASE_PATH}/{path}"


@app.context_processor
def inject_app_helpers():
    return {"app_url": app_url, "app_base_path": APP_BASE_PATH}


def _read_excel(filename):
    return pd.read_excel(DATA_DIR / filename).fillna("")


def _load_dataset():
    countries = _read_excel("Medals.xlsx").rename(columns={"Team/NOC": "CountryName"})
    countries["CountryName"] = countries["CountryName"].astype(str)

    athletes = _read_excel("Athletes.xlsx").rename(columns={"NOC": "CountryName"})
    coaches = _read_excel("Coaches.xlsx").rename(columns={"NOC": "CountryName"})

    return {
        "countries": countries,
        "athletes": athletes,
        "coaches": coaches,
    }


DATASET = _load_dataset()


def _country_rows():
    return DATASET["countries"]


def _athlete_rows():
    return DATASET["athletes"]


def _coach_rows():
    return DATASET["coaches"]


def _country_names():
    return sorted(_country_rows()["CountryName"].dropna().astype(str).tolist())


def _country_record(country_name):
    rows = _country_rows()[_country_rows()["CountryName"] == country_name]
    if rows.empty:
        return None
    return rows.iloc[0]


def get_country_medals_info(countryName):
    country_info = _country_record(countryName)
    if country_info is None:
        return {"gold": 0, "silver": 0, "bronze": 0, "total": 0}

    return {
        "gold": int(country_info["Gold"]),
        "silver": int(country_info["Silver"]),
        "bronze": int(country_info["Bronze"]),
        "total": int(country_info["Total"]),
    }


def get_country_disciplines(countryName):
    rows = _athlete_rows()[_athlete_rows()["CountryName"] == countryName]
    return {"num_disciplines": int(rows["Discipline"].nunique())}


def get_country_people(countryName):
    coaches = _coach_rows()[_coach_rows()["CountryName"] == countryName]
    athletes = _athlete_rows()[_athlete_rows()["CountryName"] == countryName]
    return {
        "total_coaches": int(len(coaches)),
        "total_athletes": int(len(athletes)),
    }


def _like_stats(liked_countries):
    if not liked_countries:
        return {}

    gold = [item["Gold"] for item in liked_countries]
    silver = [item["Silver"] for item in liked_countries]
    bronze = [item["Bronze"] for item in liked_countries]
    total = [item["Total"] for item in liked_countries]

    return {
        "Avg_Gold": round(sum(gold) / len(gold), 2),
        "Avg_Silver": round(sum(silver) / len(silver), 2),
        "Avg_Bronze": round(sum(bronze) / len(bronze), 2),
        "Avg_Total": round(sum(total) / len(total), 2),
        "Total_Liked": len(liked_countries),
    }


@app.route("/", methods=["GET", "POST"])
def index():
    user_logged_in = "username" in session
    username = session.get("username", "")
    return render_template("index.html", user_logged_in=user_logged_in, user_name=username)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_name = request.form["username"].strip()
        password = request.form["password"]
        confirm_password = request.form.get("confirm_password", "")

        if not user_name:
            return render_template("login.html", error_message="Invalid username.")

        stored_password = get_user_password(user_name, app.logger)

        if stored_password is not None and password != stored_password:
            return render_template("login.html", error_message="Invalid login password.")

        if stored_password is None:
            if password != confirm_password:
                return render_template(
                    "login.html",
                    error_message="Passwords do not match.",
                    register_username=user_name,
                )
            create_user(user_name, password, app.logger)

        session["username"] = user_name
        return redirect(app_url())

    return render_template("login.html")


@app.route("/check_username")
def check_username():
    user_name = request.args.get("username", "").strip()
    if not user_name:
        return jsonify({"exists": False, "valid": False})

    return jsonify({"exists": get_user_password(user_name, app.logger) is not None, "valid": True})


@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(app_url())


@app.route("/search", methods=["GET", "POST"])
def search():
    if request.method == "POST":
        selected_country = request.form.get("selected_option", "")
        country_info = _country_record(selected_country)
        if country_info is None:
            return jsonify({"error": "Country not found"}), 404

        disciplines = (
            _athlete_rows()[_athlete_rows()["CountryName"] == selected_country]
            .groupby("Discipline")
            .size()
            .reset_index(name="count")
            .to_dict("records")
        )

        return jsonify(
            {
                "display1": f"Total medals: {int(country_info['Total'])}",
                "display2": f"Gold: {int(country_info['Gold'])}",
                "display3": f"Silver: {int(country_info['Silver'])}",
                "display4": f"Bronze: {int(country_info['Bronze'])}",
                "Discipline_info": disciplines,
            }
        )

    return render_template("search.html")


@app.route("/get_dropdown_options")
def get_dropdown_options():
    user_input = request.args.get("input", "").strip().lower()
    options = _country_names()

    if user_input:
        options = [country for country in options if user_input in country.lower()]

    return jsonify({"options": options})


@app.route("/compare", methods=["GET", "POST"])
def compare():
    first_country = session.get("first_selected_country")
    countries = _country_names()
    methods = ["Medals Information", "Coach & Athlete", "Disciplines"]

    if request.method == "POST":
        second_country = request.form.get("second_country")
        selected_method = request.form.get("selected_method")
        selected_countries = [first_country, second_country]

        if selected_method == "Medals Information":
            first_info = get_country_medals_info(first_country)
            second_info = get_country_medals_info(second_country)
        elif selected_method == "Coach & Athlete":
            first_info = get_country_people(first_country)
            second_info = get_country_people(second_country)
        elif selected_method == "Disciplines":
            first_info = get_country_disciplines(first_country)
            second_info = get_country_disciplines(second_country)
        else:
            first_info = {}
            second_info = {}

        return jsonify(
            {
                "countries": selected_countries,
                "first_country_info": first_info,
                "second_country_info": second_info,
            }
        )

    return render_template(
        "compare.html",
        countries=countries,
        first_country=first_country,
        methods=methods,
    )


@app.route("/like_store", methods=["POST"])
def like_option():
    if "username" not in session:
        return jsonify({"status": "logged_in_request"})

    user_name = session["username"]
    selected_country = request.form.get("selected_like_country")
    medals_info = get_country_medals_info(selected_country)
    curr_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_like(
        user_name,
        selected_country,
        {
            "CountryName": selected_country,
            "TimeStamp": curr_timestamp,
            "Gold": medals_info["gold"],
            "Silver": medals_info["silver"],
            "Bronze": medals_info["bronze"],
            "Total": medals_info["total"],
        },
        app.logger,
    )

    return jsonify({"status": "success"})


@app.route("/dislike_store", methods=["POST"])
def dislike_option():
    if "username" not in session:
        return jsonify({"status": "logged_in_request"})

    user_name = session["username"]
    selected_country = request.form.get("selected_dislike_country")

    delete_like(user_name, selected_country, app.logger)

    return jsonify({"status": "success"})


@app.route("/first_country_compare_store", methods=["POST"])
def like_store():
    session["first_selected_country"] = request.form.get("first_selected_country")
    return jsonify({"status": "success"})


@app.route("/like", methods=["GET"])
def like():
    if "username" not in session:
        return redirect(app_url("login"))

    username = session["username"]
    liked_countries = get_liked_countries(username, app.logger)
    like_stats = _like_stats(liked_countries)

    return render_template(
        "like.html",
        username=username,
        liked_countries=liked_countries,
        like_stats=like_stats,
    )


@app.route("/advInfo", methods=["GET"])
def advInfo():
    coach_info_df = (
        _athlete_rows()
        .merge(_coach_rows(), on=["CountryName", "Discipline"], suffixes=("_athlete", "_coach"))
        .groupby("Name_coach")
        .size()
        .reset_index(name="athlete_count")
        .sort_values(["athlete_count", "Name_coach"], ascending=[False, True])
    )
    coach_info = list(coach_info_df.itertuples(index=False, name=None))

    country_info_df = (
        _athlete_rows()
        .groupby("CountryName")
        .size()
        .reset_index(name="total_athlete")
        .merge(_country_rows()[["CountryName", "Total"]], on="CountryName", how="inner")
        .sort_values(["Total", "total_athlete"], ascending=[False, False])
    )
    country_info = list(country_info_df[["total_athlete", "CountryName", "Total"]].itertuples(index=False, name=None))

    return render_template("advInfo.html", coach_info=coach_info, country_info=country_info)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 9000)), debug=True)
