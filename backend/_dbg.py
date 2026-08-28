from fastapi.testclient import TestClient
import logging; logging.disable(logging.INFO)
import app.main as m
c = TestClient(m.app, raise_server_exceptions=False)
for url in ["/api/backups/download?ticket=naoexiste", "/api/backups/recuperacao"]:
    r = c.get(url)
    print("%-40s -> %s  %s" % (url, r.status_code, r.text[:70]))
