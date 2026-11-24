"""Choose one JDK for Android build tools and signed bundle inspection."""
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple


class JDK(NamedTuple):
    home: Path
    version: str
    architecture: str
    source: str
    fallback: str = ""

    def tool(self, name):
        return str(self.home / "bin" / name)

    def details(self):
        return {"home": str(self.home), "version": self.version, "architecture": self.architecture,
                "source": self.source, **({"fallback": self.fallback} if self.fallback else {})}

    def environment(self):
        path = os.pathsep.join((str(self.home / "bin"), os.environ.get("PATH", os.defpath)))
        return {**os.environ, "JAVA_HOME": str(self.home), "PATH": path}


def command(argv):
    return subprocess.run(argv, capture_output=True, timeout=15, check=False)


def candidates(target):
    if target.get("java_home"):
        yield Path(target["java_home"]), "java_home"
        return
    if os.environ.get("JAVA_HOME"):
        yield Path(os.environ["JAVA_HOME"]), "JAVA_HOME"
    mac = platform.system() == "Darwin"
    if mac:
        try:
            result = command(["/usr/libexec/java_home", "--failfast", "-a", platform.machine()])
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result and not result.returncode and result.stdout.strip():
            yield Path(os.fsdecode(result.stdout.strip())), "macOS"
    java = shutil.which("java")
    if java and (not mac or java != "/usr/bin/java"):
        yield Path(java).resolve().parent.parent, "PATH"


def resolve_jdk(target):
    attempts = []
    for home, source in candidates(target):
        home = home.expanduser().resolve()
        tools = ("java", "jarsigner", "keytool")
        missing = [name for name in tools if not os.access(home / "bin" / name, os.X_OK)
                   or not (home / "bin" / name).is_file()]
        if missing:
            attempts.append(f"{source} ({home}) に必要な実行ファイルがありません（{'、'.join(missing)}）。")
            continue
        try:
            result = command([str(home / "bin/java"), "-Duser.language=en", "-XshowSettings:properties", "-version"])
        except (OSError, subprocess.TimeoutExpired):
            attempts.append(f"{source} ({home}) のjavaを起動できません。")
            continue
        properties = dict(re.findall(rb"^\s*(java\.version|os\.arch)\s*=\s*(\S+)",
                                     result.stdout + result.stderr, re.MULTILINE))
        if not result.returncode and properties.keys() == {b"java.version", b"os.arch"}:
            return JDK(home, properties[b"java.version"].decode(errors="replace"),
                       properties[b"os.arch"].decode(errors="replace"), source, "\n".join(attempts))
        attempts.append(f"{source} ({home}) のJavaの版とアーキテクチャを確認できません。")
    hint = "利用できるJDKをプロファイルの java_home に指定するか、JAVA_HOME を確認してください。"
    raise ValueError("\n".join([*attempts, hint]))
