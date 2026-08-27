# COIFESP Agent Backend

FastAPI application and Harness runtime for the first COIFESP Agent prototype.

## Local development

```bat
..\scripts\setup-backend.cmd
E:\miniconda3\envs\bettafish\python.exe -m app.main
```

The setup script targets `E:\miniconda3\envs\bettafish\python.exe` by default;
set `COIFESP_PYTHON` to override it.

The default profile uses a local SQLite database so the prototype can start without
credentials. Set `DATABASE_URL` to the existing PostgreSQL instance before connecting
real crawler data.
