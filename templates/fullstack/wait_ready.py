"""Fixed readiness probe for the generated application's loopback API."""
import time
from urllib.request import urlopen
for _ in range(100):
    try:
        with urlopen('http://127.0.0.1:8001/api/health',timeout=1) as response:
            if response.status==200:break
    except Exception:pass
    time.sleep(0.2)
else:raise SystemExit('业务API启动未完成，停止后续交互检查')
