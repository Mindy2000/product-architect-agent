"""Build real macOS DMG or Windows NSIS EXE from a verified, secret-free payload."""
import argparse,json,os,plistlib,shutil,subprocess,tempfile
from pathlib import Path
from distribution import ROOT,inspect,extract,DistributionError

def native(package,output,kind):
 package=Path(package).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True);manifest=inspect(package);version=manifest['version']
 with tempfile.TemporaryDirectory(prefix='pa-native-',dir=output) as tmp:
  stage=Path(tmp)
  if kind=='macos':
   if manifest['kind']!='macos-install':raise DistributionError('macOS需要对应安装ZIP。')
   app=stage/'Product Architect.app';resources=app/'Contents/Resources';binary=app/'Contents/MacOS';resources.mkdir(parents=True);binary.mkdir()
   shutil.copyfile(package,resources/'payload.zip')
   for name in ['distribution.py','native_bootstrap.py']:shutil.copyfile(ROOT/'scripts'/name,resources/name)
   (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.productarchitect.agent','CFBundleName':'Product Architect','CFBundleVersion':version,'CFBundleShortVersionString':version,'CFBundleExecutable':'launcher','CFBundlePackageType':'APPL','NSHighResolutionCapable':True}))
   (binary/'launcher').write_text('#!/bin/sh\nHERE="$(cd "$(dirname "$0")/../Resources" && pwd)"\nexec /usr/bin/open -a Terminal "$HERE/bootstrap.command"\n');(binary/'launcher').chmod(0o755)
   (resources/'bootstrap.command').write_text('#!/bin/zsh\nset -eu\nexport PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"\nHERE="${0:A:h}"\nif ! command -v python3.13 >/dev/null 2>&1; then\n echo "请先安装Python 3.13、Git、Colima和Docker，说明见磁盘映像中的安装说明。"\n exit 1\nfi\nexec python3.13 "$HERE/native_bootstrap.py" "$HERE/payload.zip"\n');(resources/'bootstrap.command').chmod(0o755)
   shutil.copyfile(ROOT/'docs/SETUP.md',stage/'安装说明.md');(stage/'Applications').symlink_to('/Applications',target_is_directory=True)
   target=output/f'product-architect-{version}-macos.dmg'
   if target.exists():raise DistributionError('DMG已存在，未覆盖。')
   subprocess.run(['hdiutil','create','-volname','Product Architect','-srcfolder',str(stage),'-ov','-format','UDZO',str(target)],check=True)
  else:
   if manifest['kind']!='windows-install':raise DistributionError('Windows需要对应安装ZIP。')
   compiler=shutil.which('makensis') or shutil.which('makensis.exe')
   if not compiler:raise DistributionError('请先安装免费的NSIS 3.13编译器。')
   payload=stage/'payload';extract(package,payload);target=output/f'product-architect-{version}-windows-x64.exe'
   if target.exists():raise DistributionError('EXE已存在，未覆盖。')
   def quoted(value):return str(value).replace('$','$$').replace('"','$\\"')
   # The uninstaller only removes its shortcuts; source, keys and projects remain.
   script='''Unicode true
!include "MUI2.nsh"
Name "Product Architect"
OutFile "@OUTPUT@"
InstallDir "$LOCALAPPDATA\\ProductArchitect\\versions\\@VERSION@"
RequestExecutionLevel user
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
Section "Install"
 IfFileExists "$INSTDIR\\*.*" 0 install_new
 MessageBox MB_ICONSTOP "Choose a new empty version folder. Existing code and data will not be overwritten."
 Abort
 install_new:
 SetOutPath "$INSTDIR"
 File /r "@PAYLOAD_FILES@"
 WriteUninstaller "$INSTDIR\\Uninstall.exe"
 CreateDirectory "$SMPROGRAMS\\Product Architect @VERSION@"
 CreateShortcut "$SMPROGRAMS\\Product Architect @VERSION@\\Start.lnk" "$INSTDIR\\start.cmd"
 CreateShortcut "$SMPROGRAMS\\Product Architect @VERSION@\\Configure.lnk" "$INSTDIR\\config.cmd"
 CreateShortcut "$SMPROGRAMS\\Product Architect @VERSION@\\Doctor.lnk" "$INSTDIR\\doctor.cmd"
 DetailPrint "Requires Python 3.13, Git and Docker Desktop (Linux containers). First start installs pinned Python dependencies."
SectionEnd
Section "Uninstall"
 Delete "$SMPROGRAMS\\Product Architect @VERSION@\\Start.lnk"
 Delete "$SMPROGRAMS\\Product Architect @VERSION@\\Configure.lnk"
 Delete "$SMPROGRAMS\\Product Architect @VERSION@\\Doctor.lnk"
 RMDir "$SMPROGRAMS\\Product Architect @VERSION@"
 MessageBox MB_OK "Project source, API configuration and data remain in $INSTDIR. Export data before removing the folder."
SectionEnd
'''.replace('@OUTPUT@',quoted(target)).replace('@PAYLOAD_FILES@',quoted(payload/'*')).replace('@VERSION@',version)
   source=stage/'installer.nsi';source.write_text(script,encoding='utf-8');subprocess.run([compiler,'-V2',str(source)],check=True)
 return target

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('package');p.add_argument('--kind',choices=['macos','windows'],required=True);p.add_argument('--output',default='release-artifacts');a=p.parse_args()
 print(json.dumps({'installer':str(native(a.package,a.output,a.kind))},ensure_ascii=False))
