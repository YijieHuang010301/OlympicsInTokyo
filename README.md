# Olympics In Tokyo

This project is a Flask web app for Tokyo Olympics country medal search, likes, comparison, and advanced statistics.

The app has been reorganized for Tencent CloudBase HTTP Function deployment. It no longer uses MySQL. Static Olympics data is loaded from Excel files in `doc/`, and simple user/like data is stored in `data/user_data.json`.

## Project Structure

```text
.
├── app.py                 # CloudBase/Flask entry
├── scf_bootstrap          # CloudBase HTTP function startup script
├── requirements.txt       # Python dependencies
├── src/
│   ├── OlympicsWeb.py     # Flask routes and data logic
│   ├── static/            # Images
│   └── templates/         # HTML templates
├── doc/                   # Excel data source
└── data/                  # Runtime JSON data
```

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:9000`.

## CloudBase Deploy

Install and log in to Tencent CloudBase CLI first:

```bash
npm install -g @cloudbase/cli
tcb login
```

Deploy as an HTTP function:

```bash
tcb fn deploy OlympicsInTokyo --runtime Python3.10 --dir ./ --httpFn --path /OlympicsInTokyo
```

If your CloudBase environment uses Python 3.9, use:

```bash
tcb fn deploy OlympicsInTokyo --runtime Python3.9 --dir ./ --httpFn --path /OlympicsInTokyo --force
```

For later code-only deployments, omit `--path` if the HTTP route already exists:

```bash
tcb fn deploy OlympicsInTokyo --runtime Python3.9 --dir ./ --httpFn --force
```

If the HTTP service route shows resource type `SCF` instead of `WEB_SCF`, update it:

```bash
tcb routes edit --data '{"domain":"*","routes":[{"path":"/OlympicsInTokyo","upstreamResourceType":"WEB_SCF","upstreamResourceName":"OlympicsInTokyo","enable":true,"enableAuth":false,"enablePathTransmission":false}]}'
```

Recommended environment variables:

```text
SECRET_KEY=<your-random-secret>
USER_DATA_FILE=/tmp/user_data.json
APP_BASE_PATH=OlympicsInTokyo
CLOUDBASE_USE_DB=true
CLOUDBASE_ENV_ID=<your-env-id>
```

`APP_BASE_PATH` controls the deployed URL prefix. If the access path changes from `/OlympicsInTokyo` to another path, update `APP_BASE_PATH` and the CloudBase route instead of editing templates one by one.

`USER_DATA_FILE=/tmp/user_data.json` is only used as a fallback. In CloudBase, login and liked-country data should be stored in CloudBase Database.

## Notes

- Removed `sqlalchemy`, `pymysql`, MySQL connection strings, SQL queries, triggers, and stored procedures.
- Replaced medals/country/athlete/coach queries with pandas operations over Excel files.
- Like statistics are calculated in Python when the `/like` page is rendered.
- Keep `scf_bootstrap` executable before deployment:

```bash
chmod +x scf_bootstrap
```
