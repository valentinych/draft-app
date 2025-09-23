import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('WEB_CONCURRENCY', 2))
worker_class = 'sync'
worker_connections = 1000
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 300))
keepalive = 2

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# Process naming
proc_name = 'draft_app'

# Server mechanics
preload_app = True
daemon = False
pidfile = None
tmp_upload_dir = None

# SSL
keyfile = None
certfile = None
