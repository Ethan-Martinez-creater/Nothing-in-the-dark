# COIFESP ML Worker

The worker isolates BGE-M3, Torch and GPU memory from FastAPI's web process.
It reuses the existing `E:\miniconda3\envs\bettafish` environment.

Start it from `Project`:

```bat
E:\miniconda3\envs\bettafish\python.exe -m uvicorn ml_worker.app:app --host 127.0.0.1 --port 8010
```

The model is loaded lazily on the first embedding request. On a CUDA out-of-memory
error, the worker reduces its batch size and finally falls back to CPU. Set
`EMBEDDING_WORKER_URL=http://127.0.0.1:8010` in `backend\.env` only when the
worker is intended to be available.
