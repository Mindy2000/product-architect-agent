"""The macOS app delegates to a user-owned version directory; bundle is immutable."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from distribution import inspect,extract,DistributionError

def main():
 p=argparse.ArgumentParser();p.add_argument('package');p.add_argument('--destination');p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
 if sys.version_info[:2]!=(3,13):raise DistributionError('请先安装Python 3.13，见docs/SETUP.md。')
 manifest=inspect(args.package);dest=Path(args.destination).absolute() if args.destination else Path.home()/'Library/Application Support/ProductArchitect/versions'/manifest['version']
 if dest.is_symlink():raise DistributionError('安装目录不允许符号链接。')
 if not dest.exists():extract(args.package,dest)
 else:
  current=dest/'RELEASE_MANIFEST.json'
  if current.is_symlink() or not current.is_file():raise DistributionError('目标不是本产品版本，未覆盖。')
  value=json.loads(current.read_text())
  if value.get('product')!=manifest['product'] or value.get('version')!=manifest['version']:raise DistributionError('目标版本不一致，未覆盖。')
 if args.prepare_only:print(json.dumps({'prepared':str(dest),'version':manifest['version']}));return
 python=dest/'.venv/bin/python'
 if not python.is_file():subprocess.run([sys.executable,str(dest/'scripts/manage.py'),'install'],cwd=dest,check=True)
 subprocess.run([str(python),str(dest/'scripts/manage.py'),'serve'],cwd=dest,check=True)

if __name__=='__main__':
 try:main()
 except (DistributionError,OSError,subprocess.CalledProcessError):print('首次使用需要Python 3.13、Git及Colima/Docker。未覆盖旧项目，请查看安装说明。',file=sys.stderr);raise SystemExit(1)
