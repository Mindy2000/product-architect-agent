"""Bind quality evidence to immutable images and the actual fixed execution inputs."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from . import runner, project, fullstack


def current(run_id):
    image = fullstack.IMAGE if fullstack.application_type(run_id)=='fullstack_crud' else runner.WORKER_IMAGE
    try:
        result = runner.run_bounded(['docker','--context',runner.DOCKER_CONTEXT,'image','inspect',
                                     '--format','{{.Id}}',image], timeout=10)
        identifier = result.stdout.strip()
        if result.returncode or not re.fullmatch(r'sha256:[0-9a-f]{64}',identifier):return None
        code = Path(__file__).parent
        inputs = {name:hashlib.sha256((code/name).read_bytes()).hexdigest()
                  for name in ('runner.py','fullstack.py','runtime_identity.py','tools.py','page_health.py')}
        inputs['seccomp'] = hashlib.sha256(runner.SECCOMP_PROFILE.read_bytes()).hexdigest()
        value = {'image_id':identifier,'framework':project.framework_hashes(run_id),'execution':inputs}
        return {'image_id':identifier,'digest':hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()}
    except (OSError,subprocess.SubprocessError,runner.OutputLimitExceeded):
        return None


def require(run_id):
    value = current(run_id)
    if value is None:raise runner.SandboxError('无法核对固定运行镜像；旧验证结果暂不可复用，请检查容器环境。')
    return value


def unchanged(run_id, expected):
    if current(run_id)!=expected:
        raise runner.SandboxError('运行镜像或固定配置已变化，本次结果未作为有效证据保存，请重新构建验证。')
