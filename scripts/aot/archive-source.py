"""Archive generated Rails source without credentials, DBs or dependency caches."""
import pathlib
import tarfile

root = pathlib.Path('blog')
pathlib.Path('artifacts').mkdir(exist_ok=True)
excluded_dirs = {'log', 'tmp', 'storage', 'vendor', '.bundle', '.git', 'node_modules'}
with tarfile.open('artifacts/rails-source.tar.gz', 'w:gz') as archive:
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if any(part in excluded_dirs for part in rel.parts):
            continue
        if path.name == 'master.key' or path.suffix in {'.sqlite3', '.key'} or 'credentials' in path.name:
            continue
        if path.is_file():
            archive.add(path, arcname=str(path), recursive=False)
    # Rails expects these directories; keep them in the source archive.
    import io
    for directory in ('log', 'tmp', 'storage', 'vendor'):
        info = tarfile.TarInfo(f'blog/{directory}/.keep')
        info.size = 0
        archive.addfile(info, io.BytesIO())
