#!/usr/bin/env python3
"""Stop both benchmark VMs, optionally after a complete external workflow.

Run this on the operator machine, after remote artifacts have been downloaded.
The optional --after command runs first; both stop attempts happen even if that
command fails or is interrupted. Stopped VMs still incur persistent disk costs.
"""

import argparse
import json
import signal
import subprocess
import sys
import time
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTANCES = ('bench-app-c3', 'bench-loadgen-c3')


def gcloud(*args):
    exe = shutil.which('gcloud.cmd') or shutil.which('gcloud') or 'gcloud'
    return subprocess.run([exe, 'compute', 'instances', *args],
                          text=True, capture_output=True, timeout=120, check=True).stdout.strip()


def stop_instances(project, zone, instances=INSTANCES, wait_seconds=300):
    """Attempt each stop independently, and confirm each VM is TERMINATED."""
    outcomes = {}
    pending = []
    requested_at = {name: time.time() for name in instances}
    for name in instances:
        try:
            status = gcloud('describe', name, f'--project={project}', f'--zone={zone}',
                            '--format=value(status)')
            if status == 'TERMINATED':
                outcomes[name] = {'status': status, 'stopped': True}
                continue
            if status != 'STOPPING':
                gcloud('stop', name, f'--project={project}', f'--zone={zone}', '--quiet')
            pending.append(name)
        except (OSError, subprocess.SubprocessError, TimeoutError) as error:
            outcomes[name] = {'stopped': False, 'error': str(error)}
    # Initiate both stops before waiting on either VM.
    for name in pending:
        try:
            deadline = time.monotonic() + wait_seconds
            while True:
                status = gcloud('describe', name, f'--project={project}', f'--zone={zone}',
                                '--format=value(status)')
                if status == 'TERMINATED':
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{name} remains {status} after {wait_seconds}s')
                time.sleep(5)
            outcomes[name] = {'status': status, 'stopped': True}
        except (OSError, subprocess.SubprocessError, TimeoutError) as error:
            outcomes[name] = {'stopped': False, 'error': str(error)}
    for name, outcome in outcomes.items():
        outcome.update(requested_at=requested_at[name], checked_at=time.time())
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--zone', required=True)
    parser.add_argument('--app-instance', default=INSTANCES[0])
    parser.add_argument('--loadgen-instance', default=INSTANCES[1])
    parser.add_argument('--wait-seconds', type=int, default=300)
    parser.add_argument('--status-file', type=Path, help='Local JSON record of both stop results')
    parser.add_argument('--verify-checksums', type=Path, help='Local artifact directory to compute/verify SHA256SUMS')
    parser.add_argument('--destroy', action='store_true', help='Execute terraform destroy in infra/terraform after successful stop')
    parser.add_argument('--terraform-dir', type=Path, default=ROOT / 'infra/terraform', help='Path to infra/terraform')
    parser.add_argument('--after', nargs=argparse.REMAINDER,
                        help='Complete workflow command, including artifact download and report; '
                             'the VMs are stopped in finally')
    args = parser.parse_args(argv)
    if not args.project.strip() or not args.zone.strip() or args.wait_seconds < 0:
        parser.error('Project, zone and a nonnegative wait are required')
    if args.app_instance == args.loadgen_instance:
        parser.error('The app and load generator must be distinct instances')
    command = args.after or []
    if command and command[0] == '--':
        command = command[1:]
    if args.after is not None and not command:
        parser.error('--after requires a command')
    workflow_status = 0
    previous_term = signal.getsignal(signal.SIGTERM)
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if command:
            workflow_status = subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        workflow_status = 130
    except (OSError, subprocess.SubprocessError) as error:
        print(f'Workflow failed: {error}', file=sys.stderr)
        workflow_status = 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        outcomes = stop_instances(args.project, args.zone,
                                  (args.app_instance, args.loadgen_instance), args.wait_seconds)
        record = {'project': args.project, 'zone': args.zone,
                  'workflow_exit_code': workflow_status, 'instances': outcomes}
        print(json.dumps(record, indent=2), flush=True)
        if args.status_file:
            args.status_file.parent.mkdir(parents=True, exist_ok=True)
            args.status_file.write_text(json.dumps(record, indent=2) + '\n')

        # Checksum verification if directory provided
        if args.verify_checksums:
            try:
                import run_gce_suite
                chk = run_gce_suite.verify_artifact_checksums(args.verify_checksums)
                print(f"Verified {chk['total_files']} files. Manifest: {chk['manifest_path']}", flush=True)
            except Exception as e:
                print(f"Checksum verification failed: {e}", file=sys.stderr)
                workflow_status = 1

        # Disk cost control reminder and optional destroy
        all_stopped = all(value['stopped'] for value in outcomes.values())
        if all_stopped:
            print(
                "\n[NOTICE] Both VMs are TERMINATED. SSD persistent disks continue to incur storage fees.\n"
                f"To destroy disks permanently: cd {args.terraform_dir} && terraform destroy -var-file=terraform.tfvars",
                flush=True
            )
            if args.destroy:
                tfvars = args.terraform_dir / 'terraform.tfvars'
                if not tfvars.exists():
                    print(f"terraform.tfvars not found in {args.terraform_dir}", file=sys.stderr)
                else:
                    tf_exe = shutil.which('terraform') or 'terraform'
                    print("Running automated terraform destroy...", flush=True)
                    res = subprocess.run([tf_exe, 'destroy', '-var-file=terraform.tfvars', '-auto-approve'],
                                         cwd=str(args.terraform_dir), check=False)
                    if res.returncode != 0:
                        workflow_status = res.returncode
    if not all(value['stopped'] for value in outcomes.values()):
        return 1
    return workflow_status


if __name__ == '__main__':
    raise SystemExit(main())
