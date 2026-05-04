import json
import os
import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib import request as urlrequest
from urllib.error import URLError

import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, session, url_for


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "doc"
USER_DATA_FILE = Path(os.environ.get("USER_DATA_FILE", "/tmp/user_data.json"))
APP_BASE_PATH = "/" + os.environ.get("APP_BASE_PATH", "OlympicsInTokyo").strip("/")
CLOUDBASE_ENV_ID = os.environ.get("CLOUDBASE_ENV_ID", "")
CLOUDBASE_REGION = os.environ.get("CLOUDBASE_REGION", "ap-shanghai")
CLOUDBASE_USE_DB = os.environ.get("CLOUDBASE_USE_DB", "").lower() in {"1", "true", "yes"}
USERS_COLLECTION = os.environ.get("USERS_COLLECTION", "olympics_users")
LIKES_COLLECTION = os.environ.get("LIKES_COLLECTION", "olympics_likes")

_user_data_lock = Lock()

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


def _empty_user_data():
    return {"users": {}, "likes": {}}


def _load_user_data():
    if not USER_DATA_FILE.exists():
        return _empty_user_data()

    try:
        with USER_DATA_FILE.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (json.JSONDecodeError, OSError):
        return _empty_user_data()

    data.setdefault("users", {})
    data.setdefault("likes", {})
    return data


def _save_user_data(data):
    USER_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = USER_DATA_FILE.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    tmp_file.replace(USER_DATA_FILE)


def _cloudbase_db_enabled():
    return (
        CLOUDBASE_USE_DB
        and CLOUDBASE_ENV_ID
        and os.environ.get("TENCENTCLOUD_SECRETID")
        and os.environ.get("TENCENTCLOUD_SECRETKEY")
    )


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _run_cloudbase_command(table_name, command_type, command):
    service = "tcb"
    host = "tcb.tencentcloudapi.com"
    endpoint = f"https://{host}"
    version = "2018-06-08"
    action = "RunCommands"
    timestamp = int(datetime.utcnow().timestamp())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")

    payload = {
        "EnvId": CLOUDBASE_ENV_ID,
        "MgoCommands": [
            {
                "TableName": table_name,
                "CommandType": command_type,
                "Command": json.dumps(command, ensure_ascii=False, separators=(",", ":")),
            }
        ],
    }
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    hashed_payload = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join(
        ["POST", "/", "", canonical_headers, signed_headers, hashed_payload]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    secret_id = os.environ["TENCENTCLOUD_SECRETID"]
    secret_key = os.environ["TENCENTCLOUD_SECRETKEY"]
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Region": CLOUDBASE_REGION,
        "X-TC-Timestamp": str(timestamp),
    }
    token = os.environ.get("TENCENTCLOUD_SESSIONTOKEN")
    if token:
        headers["X-TC-Token"] = token

    req = urlrequest.Request(endpoint, data=payload_json.encode("utf-8"), headers=headers, method="POST")
    with urlrequest.urlopen(req, timeout=8) as response:
        result = json.loads(response.read().decode("utf-8"))

    response_data = result.get("Response", {})
    if "Error" in response_data:
        raise RuntimeError(response_data["Error"].get("Message", "CloudBase DB command failed"))
    return response_data.get("Data") or []


def _parse_cloudbase_documents(data):
    documents = []
    for item in data:
        parsed = json.loads(item)
        if isinstance(parsed, list):
            for raw_doc in parsed:
                documents.append(json.loads(raw_doc) if isinstance(raw_doc, str) else raw_doc)
        elif isinstance(parsed, dict):
            documents.append(parsed)
    return documents


def _mongo_int(value):
    if isinstance(value, dict):
        if "$numberInt" in value:
            return int(value["$numberInt"])
        if "$numberLong" in value:
            return int(value["$numberLong"])
        if "$numberDouble" in value:
            return int(float(value["$numberDouble"]))
    return int(value)


def _cloudbase_find_one(collection, filter_query):
    data = _run_cloudbase_command(
        collection,
        "QUERY",
        {"find": collection, "filter": filter_query, "limit": 1},
    )
    documents = _parse_cloudbase_documents(data)
    return documents[0] if documents else None


def _cloudbase_find_many(collection, filter_query):
    data = _run_cloudbase_command(
        collection,
        "QUERY",
        {"find": collection, "filter": filter_query, "limit": 1000},
    )
    return _parse_cloudbase_documents(data)


def _cloudbase_insert_one(collection, document):
    _run_cloudbase_command(
        collection,
        "INSERT",
        {"insert": collection, "documents": [document]},
    )


def _cloudbase_update_one(collection, filter_query, updates, upsert=False):
    _run_cloudbase_command(
        collection,
        "UPDATE",
        {
            "update": collection,
            "updates": [
                {
                    "q": filter_query,
                    "u": {"$set": updates},
                    "upsert": upsert,
                    "multi": False,
                }
            ],
        },
    )


def _cloudbase_delete_one(collection, filter_query):
    _run_cloudbase_command(
        collection,
        "DELETE",
        {"delete": collection, "deletes": [{"q": filter_query, "limit": 1}]},
    )


def _fallback_user_password(user_name):
    data = _load_user_data()
    return data.setdefault("users", {}).get(user_name)


def _fallback_create_user(user_name, password):
    data = _load_user_data()
    data.setdefault("users", {})[user_name] = password
    _save_user_data(data)


def _fallback_liked_countries(user_name):
    data = _load_user_data()
    return list(_user_likes(data, user_name).values())


def _fallback_save_like(user_name, selected_country, like_doc):
    data = _load_user_data()
    _user_likes(data, user_name)[selected_country] = like_doc
    _save_user_data(data)


def _fallback_delete_like(user_name, selected_country):
    data = _load_user_data()
    _user_likes(data, user_name).pop(selected_country, None)
    _save_user_data(data)


def _get_user_password(user_name):
    if _cloudbase_db_enabled():
        try:
            user = _cloudbase_find_one(USERS_COLLECTION, {"UserName": user_name})
            return user.get("Password") if user else None
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            app.logger.warning("CloudBase DB user lookup failed, falling back to JSON: %s", exc)
    return _fallback_user_password(user_name)


def _create_user(user_name, password):
    if _cloudbase_db_enabled():
        try:
            _cloudbase_insert_one(
                USERS_COLLECTION,
                {
                    "UserName": user_name,
                    "Password": password,
                    "CreatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            return
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            app.logger.warning("CloudBase DB user create failed, falling back to JSON: %s", exc)
    _fallback_create_user(user_name, password)


def _get_liked_countries(user_name):
    if _cloudbase_db_enabled():
        try:
            docs = _cloudbase_find_many(LIKES_COLLECTION, {"UserName": user_name})
            return [
                {
                    "CountryName": doc["CountryName"],
                    "TimeStamp": doc["TimeStamp"],
                    "Gold": _mongo_int(doc["Gold"]),
                    "Silver": _mongo_int(doc["Silver"]),
                    "Bronze": _mongo_int(doc["Bronze"]),
                    "Total": _mongo_int(doc["Total"]),
                }
                for doc in docs
            ]
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            app.logger.warning("CloudBase DB like lookup failed, falling back to JSON: %s", exc)
    return _fallback_liked_countries(user_name)


def _save_like(user_name, selected_country, like_doc):
    if _cloudbase_db_enabled():
        try:
            db_doc = {"UserName": user_name, **like_doc}
            _cloudbase_update_one(
                LIKES_COLLECTION,
                {"UserName": user_name, "CountryName": selected_country},
                db_doc,
                upsert=True,
            )
            return
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            app.logger.warning("CloudBase DB like save failed, falling back to JSON: %s", exc)
    _fallback_save_like(user_name, selected_country, like_doc)


def _delete_like(user_name, selected_country):
    if _cloudbase_db_enabled():
        try:
            _cloudbase_delete_one(
                LIKES_COLLECTION,
                {"UserName": user_name, "CountryName": selected_country},
            )
            return
        except (RuntimeError, URLError, TimeoutError, OSError) as exc:
            app.logger.warning("CloudBase DB like delete failed, falling back to JSON: %s", exc)
    _fallback_delete_like(user_name, selected_country)


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


def _user_likes(data, username):
    return data.setdefault("likes", {}).setdefault(username, {})


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

        with _user_data_lock:
            stored_password = _get_user_password(user_name)

            if stored_password is not None and password != stored_password:
                return render_template("login.html", error_message="Invalid login password.")

            if stored_password is None:
                if password != confirm_password:
                    return render_template(
                        "login.html",
                        error_message="Passwords do not match.",
                        register_username=user_name,
                    )
                _create_user(user_name, password)

        session["username"] = user_name
        return redirect(app_url())

    return render_template("login.html")


@app.route("/check_username")
def check_username():
    user_name = request.args.get("username", "").strip()
    if not user_name:
        return jsonify({"exists": False, "valid": False})

    return jsonify({"exists": _get_user_password(user_name) is not None, "valid": True})


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

    with _user_data_lock:
        _save_like(
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
        )

    return jsonify({"status": "success"})


@app.route("/dislike_store", methods=["POST"])
def dislike_option():
    if "username" not in session:
        return jsonify({"status": "logged_in_request"})

    user_name = session["username"]
    selected_country = request.form.get("selected_dislike_country")

    with _user_data_lock:
        _delete_like(user_name, selected_country)

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
    liked_countries = _get_liked_countries(username)
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
