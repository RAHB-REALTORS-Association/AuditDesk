from .application import create_app
from .config import load_config
from .runtime import start_runtime

config = load_config()
app = create_app(config)
runtime = start_runtime(config)
