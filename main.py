"""Point d'entrée local pour exécution via Uvicorn (`uvicorn main:app --reload`)."""

import uvicorn
from api.index import app

__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
