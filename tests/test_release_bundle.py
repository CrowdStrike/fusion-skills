"""release.sh --bundle checks the fields the OpenAI plugin portal requires."""

import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INTERFACE = {
    "composerIcon": "./assets/logo.png",
    "logo": "./assets/logo.png",
    "supportURL": "https://example.com/support",
}


def _bundle(tmp_path, interface):
    """Build a minimal plugin repo in tmp_path and run release.sh --bundle on it."""
    shutil.copy2(REPO / "release.sh", tmp_path / "release.sh")
    for d in (".codex-plugin", "assets", "skills/example", "use-cases", "common", "scripts"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / ".codex-plugin" / "plugin.json").write_text(json.dumps({"interface": interface}))
    shutil.copy2(REPO / "assets" / "crowdstrike-logo.png", tmp_path / "assets" / "logo.png")
    (tmp_path / "skills" / "example" / "SKILL.md").write_text("# Example\n")
    for f in ("use-cases/example.md", "common/README.md", "scripts/README.md",
              "requirements.txt", "LICENSE", "README.md"):
        (tmp_path / f).write_text("x\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "Test bundle")
    return subprocess.run(["bash", "release.sh", "--bundle", "HEAD"],
                          cwd=tmp_path, capture_output=True, text=True, check=False)


def test_bundle_passes_with_the_required_fields(tmp_path):
    result = _bundle(tmp_path, INTERFACE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "interface.supportURL is https://example.com/support" in result.stdout


def test_bundle_fails_without_a_support_url(tmp_path):
    """The portal rejects an upload with no support URL ("support URL is required")."""
    interface = {k: v for k, v in INTERFACE.items() if k != "supportURL"}
    result = _bundle(tmp_path, interface)
    assert result.returncode != 0
    assert "interface.supportURL missing" in result.stdout + result.stderr


def test_repo_manifest_has_a_support_url():
    manifest = json.loads((REPO / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["interface"]["supportURL"].startswith("https://")
