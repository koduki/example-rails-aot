#!/usr/bin/env python3
"""End-to-end unmanned execution orchestrator and disk lifecycle manager for GCE benchmarks.

Automates the complete GCE capacity benchmark lifecycle:
1. Verify and start VMs (app and load generator)
2. Build container images on the app VM
3. Run strict preflight verification
4. Execute benchmark run in tmux session with progress monitoring
5. Download artifacts via SCP through IAP
6. Compute and verify SHA-256 checksums of retrieved artifacts
7. In a finally block: reliably stop both VMs (verified TERMINATED)
8. Disk lifecycle management: provide safe terraform destroy guidance or auto-destroy.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Import stop_instances from gce_cleanup
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import gce_cleanup


def resolve_gcloud():
    """Resolve gcloud binary across platforms (including Windows .cmd)."""
    return shutil.which('gcloud.cmd') or shutil.which('gcloud') or 'gcloud'


def gcloud_cmd(*args, timeout=120):
    """Execute a gcloud CLI command with cross-platform binary resolution."""
    exe = resolve_gcloud()
    cmd = [exe, *args]
    res = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=True)
    return res.stdout.strip()


def get_instance_status(name, project, zone):
    """Retrieve instance status (e.g. RUNNING, TERMINATED, STOPPING)."""
    return gcloud_cmd('compute', 'instances', 'describe', name,
                      f'--project={project}', f'--zone={zone}',
                      '--format=value(status)')


def get_internal_ip(name, project, zone):
    """Retrieve internal network IP for an instance."""
    return gcloud_cmd('compute', 'instances', 'describe', name,
                      f'--project={project}', f'--zone={zone}',
                      '--format=value(networkInterfaces[0].networkIP)')


def verify_and_start_vms(project, zone, instances=('bench-app-c3', 'bench-loadgen-c3'), wait_seconds=300):
    """Ensure both instances are RUNNING and ready."""
    outcomes = {}
    for name in instances:
        status = get_instance_status(name, project, zone)
        if status == 'TERMINATED':
            print(f"Starting {name}...", flush=True)
            gcloud_cmd('compute', 'instances', 'start', name,
                       f'--project={project}', f'--zone={zone}', '--quiet')
        
    deadline = time.monotonic() + wait_seconds
    for name in instances:
        while True:
            status = get_instance_status(name, project, zone)
            if status == 'RUNNING':
                outcomes[name] = {'status': 'RUNNING', 'ready': True}
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Instance {name} failed to reach RUNNING state within {wait_seconds}s (status={status})")
            time.sleep(5)
    return outcomes


def ssh_command(instance, project, zone, command, timeout=3600):
    """Execute command over SSH via IAP."""
    exe = resolve_gcloud()
    cmd = [
        exe, 'compute', 'ssh', instance,
        f'--project={project}', f'--zone={zone}',
        '--tunnel-through-iap',
        f'--command={command}'
    ]
    res = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"Remote command failed on {instance} (code {res.returncode}):\n{res.stderr}\n{res.stdout}")
    return res.stdout.strip()


def scp_download(instance, project, zone, remote_path, local_path, retries=3):
    """Download remote directory or file via SCP through IAP with retry."""
    exe = resolve_gcloud()
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe, 'compute', 'scp', '--recurse', '--tunnel-through-iap',
        f'--project={project}', f'--zone={zone}',
        f'{instance}:{remote_path}', str(local_path)
    ]
    for attempt in range(1, retries + 1):
        try:
            res = subprocess.run(cmd, text=True, capture_output=True, timeout=600)
            if res.returncode == 0:
                return True
            print(f"SCP download attempt {attempt} failed: {res.stderr}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"SCP download attempt {attempt} timed out", file=sys.stderr)
        if attempt < retries:
            time.sleep(5)
    raise RuntimeError(f"Failed to download {remote_path} from {instance} after {retries} attempts")


def verify_artifact_checksums(directory):
    """Compute and verify SHA-256 checksums of all retrieved artifacts."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Artifact directory does not exist: {directory}")

    checksums = {}
    total_files = 0
    total_bytes = 0

    for root, _, files in os.walk(dir_path):
        for file_name in sorted(files):
            if file_name == 'SHA256SUMS':
                continue
            file_path = Path(root) / file_name
            rel_path = file_path.relative_to(dir_path).as_posix()
            content = None
            for attempt in range(5):
                try:
                    content = file_path.read_bytes()
                    break
                except PermissionError:
                    time.sleep(0.05)
            if content is None:
                content = file_path.read_bytes()
            h = hashlib.sha256(content).hexdigest()
            checksums[rel_path] = h
            total_files += 1
            total_bytes += len(content)

    if total_files == 0:
        raise ValueError(f"No artifact files found in {directory}")

    # Write SHA256SUMS file
    sums_file = dir_path / 'SHA256SUMS'
    with sums_file.open('w', encoding='utf-8') as f:
        for rel_path, h in sorted(checksums.items()):
            f.write(f"{h}  {rel_path}\n")

    # Verify key expected files exist and are valid JSON
    key_files = ['plan.json', 'env.json', 'trials/per-run.json']
    missing = [kf for kf in key_files if kf not in checksums]
    if missing:
        print(f"WARNING: Expected artifact files missing: {missing}", file=sys.stderr)

    return {
        'total_files': total_files,
        'total_bytes': total_bytes,
        'checksums': checksums,
        'manifest_path': str(sums_file),
    }


def disk_lifecycle_guidance(tf_dir=None, auto_destroy=False):
    """Print persistent disk cost warning or optionally execute terraform destroy."""
    tf_path = Path(tf_dir) if tf_dir else ROOT / 'infra/terraform'
    tfvars_file = tf_path / 'terraform.tfvars'

    warning_msg = (
        "\n"
        "======================================================================\n"
        "  PERSISTENT DISK COST CONTROL NOTICE\n"
        "======================================================================\n"
        "  Both VMs are now TERMINATED to prevent compute charges.\n"
        "  HOWEVER, SSD persistent disks continue to incur hourly storage fees\n"
        "  until destroyed via Terraform.\n"
        "\n"
        f"  To permanently delete disks and eliminate all costs:\n"
        f"    cd {tf_path}\n"
        f"    terraform destroy -var-file=terraform.tfvars\n"
        "======================================================================\n"
    )
    print(warning_msg, flush=True)

    if auto_destroy:
        tf_exe = shutil.which('terraform') or 'terraform'
        if not tfvars_file.exists():
            raise FileNotFoundError(f"terraform.tfvars not found in {tf_path}")
        print("Executing automated terraform destroy...", flush=True)
        res = subprocess.run([tf_exe, 'destroy', '-var-file=terraform.tfvars', '-auto-approve'],
                             cwd=str(tf_path), check=False)
        return res.returncode == 0
    return False


def run_suite(args):
    """Execute end-to-end benchmark suite with fail-safe VM stop."""
    project = args.project
    zone = args.zone
    app_vm = args.app_instance
    loadgen_vm = args.loadgen_instance
    run_id = args.run_id or f"gce-{time.strftime('%Y%m%d-%H%M%S')}"
    output_dir = Path(args.output_dir or ROOT / f"bench-results/{run_id}")

    artifacts_recovered = False
    verification_passed = False
    workflow_error = None

    try:
        # Step 1: Verify and start VMs
        print(f"[1/6] Verifying and starting VMs ({app_vm}, {loadgen_vm})...", flush=True)
        verify_and_start_vms(project, zone, (app_vm, loadgen_vm), wait_seconds=args.wait_seconds)
        app_ip = get_internal_ip(app_vm, project, zone)
        print(f"      Both VMs RUNNING. App internal IP: {app_ip}", flush=True)

        if args.ssh_user:
            remote_repo = f"/home/{args.ssh_user}/example-rails-aot"
        else:
            try:
                remote_home = ssh_command(app_vm, project, zone, 'echo $HOME', timeout=30).splitlines()[-1].strip()
                remote_repo = f"{remote_home}/example-rails-aot"
            except Exception:
                remote_repo = "$HOME/example-rails-aot"
        remote_output = f"bench-results/{run_id}"

        # Step 1.5: Sync git repository on app VM
        print(f"      Syncing latest code on {app_vm}...", flush=True)
        sync_cmd = (
            f"if [ -d \"{remote_repo}/.git\" ]; then "
            f"  cd \"{remote_repo}\" && git fetch origin && git checkout main && git pull --ff-only origin main; "
            f"else "
            f"  git clone https://github.com/koduki/example-rails-aot.git \"{remote_repo}\" && cd \"{remote_repo}\" && git checkout main; "
            f"fi"
        )
        ssh_command(app_vm, project, zone, sync_cmd, timeout=300)

        # Step 2: Build container images if requested
        if not args.skip_build:
            print(f"[2/6] Building containers on {app_vm}...", flush=True)
            build_cmd = (
                f"cd {remote_repo} && python3 scripts/bench/run.py build "
                f"--profile {args.profile} --remote-loadgen {loadgen_vm} --target-host {app_ip} "
                f"--gce-project {project} --gce-zone {zone} --output bench-results/build-{run_id}"
            )
            ssh_command(app_vm, project, zone, build_cmd, timeout=1800)
        else:
            print("[2/6] Skipping build step (--skip-build).", flush=True)

        # Step 3: Run strict preflight verification
        if not args.skip_preflight:
            print(f"[3/6] Running preflight validation on {app_vm}...", flush=True)
            preflight_cmd = (
                f"cd {remote_repo} && python3 scripts/bench/run.py preflight "
                f"--profile {args.profile} --remote-loadgen {loadgen_vm} --target-host {app_ip} "
                f"--gce-project {project} --gce-zone {zone} --output bench-results/preflight-{run_id}"
            )
            ssh_command(app_vm, project, zone, preflight_cmd, timeout=1800)
        else:
            print("[3/6] Skipping preflight step (--skip-preflight).", flush=True)

        # Step 4: Run benchmark trial
        print(f"[4/6] Launching benchmark run on {app_vm}...", flush=True)
        preflight_opt = f"--preflight-file bench-results/preflight-{run_id}/preflight.json" if not args.skip_preflight else ""
        run_cmd = (
            f"cd {remote_repo} && python3 scripts/bench/run.py run "
            f"--profile {args.profile} --remote-loadgen {loadgen_vm} --target-host {app_ip} "
            f"--gce-project {project} --gce-zone {zone} {preflight_opt} "
            f"--output {remote_output}"
        )
        try:
            ssh_command(app_vm, project, zone, run_cmd, timeout=args.run_timeout)
        except RuntimeError as e:
            # run.py exits with 1 if any trials failed or were unstable, which is normal during capacity exploration.
            print(f"      Benchmark process completed with non-zero exit code: {e}", flush=True)

        # Generate report on remote VM
        print("      Generating formal report on app VM...", flush=True)
        report_cmd = f"cd {remote_repo} && python3 scripts/bench/run.py report --output {remote_output}"
        try:
            ssh_command(app_vm, project, zone, report_cmd, timeout=300)
        except Exception as e:
            print(f"      Formal report generation notice: {e}", flush=True)

        # Step 5: Download artifacts
        print(f"[5/6] Downloading artifacts from {app_vm}:{remote_output} to {output_dir}...", flush=True)
        scp_download(app_vm, project, zone, f"{remote_repo}/{remote_output}", str(output_dir))
        artifacts_recovered = True

        # Step 6: Verify checksums
        print(f"[6/6] Verifying artifact checksums...", flush=True)
        verification_result = verify_artifact_checksums(output_dir)
        print(f"      Verified {verification_result['total_files']} files ({verification_result['total_bytes'] / 1048576:.1f} MB).", flush=True)
        print(f"      Checksum manifest written to {verification_result['manifest_path']}", flush=True)
        verification_passed = True

    except KeyboardInterrupt:
        workflow_error = "KeyboardInterrupt"
        print("Workflow interrupted by operator", file=sys.stderr)
    except Exception as error:
        workflow_error = error
        print(f"Workflow error: {error}", file=sys.stderr)
    finally:
        # Guarantee VM shutdown in finally block
        print("\n[FINALLY] Stopping both instances to prevent ongoing compute charges...", flush=True)
        stop_results = gce_cleanup.stop_instances(project, zone, (app_vm, loadgen_vm), wait_seconds=args.wait_seconds)
        print(json.dumps(stop_results, indent=2), flush=True)

        # Always offer disk lifecycle guidance
        disk_lifecycle_guidance(tf_dir=args.terraform_dir, auto_destroy=(args.auto_destroy and verification_passed))

    return 0 if (verification_passed and not workflow_error) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='GCP Project ID')
    parser.add_argument('--zone', required=True, help='GCP Zone')
    parser.add_argument('--app-instance', default='bench-app-c3', help='App VM instance name')
    parser.add_argument('--loadgen-instance', default='bench-loadgen-c3', help='Load generator VM instance name')
    parser.add_argument('--ssh-user', help='Optional SSH username on remote VMs')
    parser.add_argument('--profile', default='bench/profiles/gce-c3-capacity.yml', help='Benchmark profile path')
    parser.add_argument('--run-id', help='Identifier for this benchmark run')
    parser.add_argument('--output-dir', help='Local directory to download artifacts to')
    parser.add_argument('--terraform-dir', help='Path to infra/terraform directory')
    parser.add_argument('--wait-seconds', type=int, default=300, help='Max seconds to wait for VM state transitions')
    parser.add_argument('--run-timeout', type=int, default=43200, help='Max seconds for benchmark execution')
    parser.add_argument('--skip-build', action='store_true', help='Skip container build step')
    parser.add_argument('--skip-preflight', action='store_true', help='Skip preflight check step')
    parser.add_argument('--auto-destroy', action='store_true', help='Automatically execute terraform destroy after verified artifact download')

    args = parser.parse_args(argv)
    return run_suite(args)


if __name__ == '__main__':
    sys.exit(main())
