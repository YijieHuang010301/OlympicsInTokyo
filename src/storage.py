import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib import request as urlrequest
from urllib.error import URLError


USER_DATA_FILE = Path(os.environ.get("USER_DATA_FILE", "/tmp/user_data.json"))
CLOUDBASE_ENV_ID = os.environ.get("CLOUDBASE_ENV_ID", "")
CLOUDBASE_REGION = os.environ.get("CLOUDBASE_REGION", "ap-shanghai")
CLOUDBASE_USE_DB = os.environ.get("CLOUDBASE_USE_DB", "").lower() in {"1", "true", "yes"}
USERS_COLLECTION = os.environ.get("USERS_COLLECTION", "olympics_users")
LIKES_COLLECTION = os.environ.get("LIKES_COLLECTION", "olympics_likes")

_user_data_lock = Lock()


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


def _user_likes(data, username):
    return data.setdefault("likes", {}).setdefault(username, {})


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


def _warning(logger, message, exc):
    if logger:
        logger.warning("%s: %s", message, exc)


def get_user_password(user_name, logger=None):
    with _user_data_lock:
        if _cloudbase_db_enabled():
            try:
                user = _cloudbase_find_one(USERS_COLLECTION, {"UserName": user_name})
                return user.get("Password") if user else None
            except (RuntimeError, URLError, TimeoutError, OSError) as exc:
                _warning(logger, "CloudBase DB user lookup failed, falling back to JSON", exc)
        return _fallback_user_password(user_name)


def create_user(user_name, password, logger=None):
    with _user_data_lock:
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
                _warning(logger, "CloudBase DB user create failed, falling back to JSON", exc)
        _fallback_create_user(user_name, password)


def get_liked_countries(user_name, logger=None):
    with _user_data_lock:
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
                _warning(logger, "CloudBase DB like lookup failed, falling back to JSON", exc)
        return _fallback_liked_countries(user_name)


def save_like(user_name, selected_country, like_doc, logger=None):
    with _user_data_lock:
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
                _warning(logger, "CloudBase DB like save failed, falling back to JSON", exc)
        _fallback_save_like(user_name, selected_country, like_doc)


def delete_like(user_name, selected_country, logger=None):
    with _user_data_lock:
        if _cloudbase_db_enabled():
            try:
                _cloudbase_delete_one(
                    LIKES_COLLECTION,
                    {"UserName": user_name, "CountryName": selected_country},
                )
                return
            except (RuntimeError, URLError, TimeoutError, OSError) as exc:
                _warning(logger, "CloudBase DB like delete failed, falling back to JSON", exc)
        _fallback_delete_like(user_name, selected_country)
