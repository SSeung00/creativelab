"""Tiny CLI for testing: POST a JSON body and, if a job comes back, wait for it.

python tools/api_wait.py /ai/mask/5000 '{"kind":"sky"}'
"""

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8002/api"


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ("POST" if data is not None else "GET"),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


if __name__ == "__main__":
    t = time.time()
    r = call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else None)
    print("->", r)
    jid = r.get("job_id")
    while jid:
        j = call(f"/jobs/{jid}")
        if j["status"] in ("done", "error", "cancelled"):
            print(j["status"], j["result"] or j["message"], f"{time.time() - t:.1f}s")
            break
        time.sleep(1)
