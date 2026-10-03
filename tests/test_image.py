"""Static checks of the Raspberry Pi image kit (image/, .github/workflows). The image itself can only be built on Linux/CI."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "image" / "stage-radio-remote"
SH = [ROOT / "image" / "stage_program.sh", STAGE / "prerun.sh", STAGE / "00-radio-remote" / "00-run.sh",
      STAGE / "00-radio-remote" / "01-run-chroot.sh", STAGE / "00-radio-remote" / "files" / "radio-remote-caddy-host.sh"]


@pytest.mark.parametrize("path", SH, ids=lambda p: p.name)
def test_image_scripts_are_lf_and_have_a_shebang(path):
    raw = path.read_bytes()
    assert b"\r" not in raw, "CRLF line endings break scripts in the image build"
    assert raw.startswith(b"#!/")


def test_pi_gen_stage_scripts_are_executable_in_git():
    # regression: pi-gen silently skips prerun.sh / NN-run*.sh that lack the executable bit (git on Windows drops it),
    # so the stage had no root filesystem and the build died with "Unable to chroot".
    import shutil
    import subprocess
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(["git", "ls-files", "--stage", "image"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    modes = {line.split("\t")[1]: line.split()[0] for line in out.splitlines()}
    for rel, mode in modes.items():
        if rel.endswith(".sh"):
            assert mode == "100755", f"{rel} must be committed executable (git update-index --chmod=+x {rel})"


def test_settings_script_does_not_pipe_the_password_through_powershell():
    # regression: `$pw | openssl passwd -6 -stdin` sends "\r\n" after the password from PowerShell, the stored hash then
    # belonged to "password<CR>" and no typed password was ever accepted (SSH "Permission denied").
    text = (ROOT / "image" / "first-boot-settings.ps1").read_text(encoding="utf-8")
    assert "New-PasswordHash" in text and "RrSha512Crypt" in text      # computed in the script itself: no OpenSSL / Git needed
    assert "| & $openssl" not in text and "openssl.exe" not in text


def test_stage_layout_matches_pi_gen():
    assert (STAGE / "EXPORT_IMAGE").read_text().strip().startswith("IMG_SUFFIX")
    assert "copy_previous" in (STAGE / "prerun.sh").read_text()
    assert (STAGE / "00-radio-remote" / "files" / "radio-remote-caddy-host.service").exists()


def test_chroot_step_uses_image_mode_and_never_enables_ptt():
    text = (STAGE / "00-radio-remote" / "01-run-chroot.sh").read_text()
    assert "install.sh --image" in text and "allow_ptt = true" not in text
    assert "radio-remote-caddy-host.service" in text


def test_installer_image_mode_enables_but_does_not_start_or_check():
    text = (ROOT / "install.sh").read_text()
    assert '--image) IMAGE=1' in text
    assert 'NOW=""' in text and "enable $NOW radio-remote.service" in text
    assert '"$IMAGE" != 1' in text and "exit 0" in text[text.index('"$IMAGE" = 1 ]; then\n  echo "Image build'):]


def test_workflow_runs_tests_stages_program_and_builds_with_pi_gen_action():
    wf = (ROOT / ".github" / "workflows" / "build-image.yml").read_text()
    assert wf.index("pytest") < wf.index("bash image/stage_program.sh") < wf.index("uses: usimd/pi-gen-action")
    assert "./image/stage-radio-remote" in wf and "stage0 stage1 stage2" in wf
    assert "password:" not in wf.split("with:")[-1].replace("# no password", "")    # never a baked-in password
    assert "enable-ssh: 1" in wf
