import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from webui import app
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=8766)
