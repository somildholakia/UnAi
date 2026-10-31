# Isolated Python test image

Build this image once on a machine with Docker and image-build network access:

    docker build -f apps/engine/Dockerfile.sandbox -t unai-python-test:latest .

The engine and `.env.example` default to `unai-python-test:latest`. Build the image
before enabling test execution:

    docker build -f apps/engine/Dockerfile.sandbox -t unai-python-test:latest .

To use a different reviewed image, configure:

    UNAI_DOCKER_IMAGE=unai-python-test:latest

The runtime always uses --pull=never and --network=none. Tests run against a
temporary filtered copy of the selected workspace; the original project directory
is not mounted into the container. The stager copies Python source and limited
text configuration files, while excluding VCS/virtualenv/build/cache directories,
environment files, private keys, database files, symlinks and unrelated binary
assets.

The built-in image includes pytest but not arbitrary project dependencies. Extend
the image with only the dependencies your test suite needs. Do not bake credentials
or production secrets into the image. Runtime package installation is not enabled.

Only the fixed command python -m pytest -q is exposed. There is no user-supplied
shell command field. Container resource limits are configured in DockerSandbox;
timeout, file-count and staged-byte limits are configurable through the engine env.


## Test-run API

A task must first be inspected with both workspace_read and run_tests explicitly
approved. The user then makes a separate confirmation call:

    POST /api/tasks/{task_id}/run-tests

    {"confirm": "run-python-tests"}

The engine runs only python -m pytest -q; it never accepts an arbitrary command
from the model or API caller. It copies eligible Python/configuration files into a
temporary staging directory, omits secrets and generated content, and mounts only
that copy into Docker. The container has no network, a read-only root filesystem,
dropped capabilities, no-new-privileges, one CPU, 1 GB memory, bounded PIDs, and
a timeout. Tests may mutate the staging copy but not the original workspace.

Build the provided pytest-enabled image and configure UNAI_DOCKER_IMAGE as above.
Project-specific test dependencies must be deliberately added to that image.
No dependency installation happens at test runtime. If the image cannot start or
pytest is missing, the API reports unavailable rather than claiming tests passed.
The evidence ledger stores output hashes and redacted previews, not raw full logs.
