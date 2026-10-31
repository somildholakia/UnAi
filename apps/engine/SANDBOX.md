# Isolated Python test image

Build this image once on a machine with Docker and image-build network access:

    docker build -f apps/engine/Dockerfile.sandbox -t unai-python-test:latest .

Then configure:

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
