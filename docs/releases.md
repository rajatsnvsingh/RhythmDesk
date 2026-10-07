# Releases and Docker images

## Publish a release

1. On the GitHub repository, open **Releases → Draft a new release**.
2. Choose a new version tag, such as `v0.1.0`, targeting `main`.
3. Use a title such as `Rhythm Desk v0.1.0 — Beta` and describe features,
   known limitations, installation requirements and any migration instructions.
4. For the initial beta, select **Set as a pre-release**.
5. Publish the release, then watch **Actions → Release Docker image**.

The workflow builds the exact tagged source, not later changes on `main`, and
publishes one multi-platform image for Linux AMD64 and ARM64:

```text
ghcr.io/rajatsnvsingh/rhythmdesk:v0.1.0
```

Successful builds report the image digest in the workflow summary. Releases marked
as pre-releases do not update `latest`. Stable releases do. Use version tags or,
for immutable deployments, the reported digest. Do not move or reuse release tags.
This workflow packages the application; it does not run application tests.

GitHub supplies its short-lived `GITHUB_TOKEN` to the workflow. No registry password
or personal token needs to be committed. After the first publication, open the
container package's settings and set its visibility to **Public** if anonymous
pulls are intended; repository visibility and package visibility are distinct.

## Install a prebuilt image

Download the release's source archive for Compose, `.env.example`, setup scripts
and documentation. Follow [Deployment](deployment.md) to prepare the state volume,
host folders and permissions first. The image includes the application and tools,
not your token, music, settings or database. All three services use the same image
but keep their separate identities and mount restrictions.

Set the release image in your private `.env`:

```dotenv
CURATOR_IMAGE=ghcr.io/rajatsnvsingh/rhythmdesk:v0.1.0
```

Then, for an ordinary Compose installation:

```sh
docker compose pull
docker compose up -d --no-build
```

Do not use `install.sh` for the prebuilt route: it deliberately requests a local
build. Existing protected/restricted deployments retain administrator-pinned image
and Dockerfile settings; changing a project `.env` does not switch those deployments.
Do not replace a live protected configuration with the public template blindly.

## Offline image archive

Once the versioned image is published, create a transferable archive on a machine
with Docker (the pull selects that machine's platform):

```sh
docker pull ghcr.io/rajatsnvsingh/rhythmdesk:v0.1.0
docker save -o rhythmdesk-v0.1.0.tar ghcr.io/rajatsnvsingh/rhythmdesk:v0.1.0
# On the destination machine:
docker load -i rhythmdesk-v0.1.0.tar
```

An offline image archive is platform-specific. It does not replace Compose or
permission setup and does not contain persistent app data.
