# render-demo

A minimal Python web service plus managed PostgreSQL, deployable to Render
and used by the Cloudfall proving runs to exercise the real migration wedge:
Render → one Debian host, including the data.

- `/health` — static health endpoint
- `/items` — rows from the `items` table via `DATABASE_URL`, identical
  before and after migration

`render.yaml` in this directory is both the Render-side service description
and the input to `cloudfall import render`.
