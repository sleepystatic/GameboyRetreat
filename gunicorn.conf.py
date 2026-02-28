import os

# Bind to the port Render provides (defaults to 10000)
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# 2 workers: if one gets stuck or killed, the other keeps serving requests
workers = 2

# Restart a worker after 1000 requests to prevent memory leaks
max_requests = 1000
max_requests_jitter = 100

# Kill a worker that takes longer than 120s to handle a request
timeout = 120

# Log to stdout so Render captures it
accesslog = "-"
errorlog = "-"
loglevel = "info"
