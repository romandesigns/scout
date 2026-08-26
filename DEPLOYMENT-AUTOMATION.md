# Automated main-branch releases

Every push to `main` triggers `.github/workflows/release-main.yml`.

The workflow validates release metadata and deploys the backend/PWA to the Hostinger VPS.
Releases are serialized so two pushes cannot deploy concurrently. Desktop rebuilds are
started separately with the `Install Scout desktop` workflow after a workstation runner
has been registered and brought online.

## GitHub production configuration

Create a GitHub environment named `production`. Add these environment secrets:

- `SCOUT_VPS_HOST`: the Hostinger public hostname or IP (currently `72.60.30.64`)
- `SCOUT_VPS_USER`: `wavystack`
- `SCOUT_VPS_PORT`: `22` (optional)
- `SCOUT_VPS_SSH_KEY`: the private key for a dedicated deploy identity
- `SCOUT_VPS_KNOWN_HOSTS`: a verified `known_hosts` line for the VPS

Add this environment variable if the default is not correct:

- `SCOUT_REMOTE_APP`: `/opt/apps/scout`

Generate the deploy key on the Windows workstation:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\scout_github_deploy" -C "scout-github-actions"
Get-Content "$HOME\.ssh\scout_github_deploy.pub"
```

Add the public key to `/home/wavystack/.ssh/authorized_keys` using the Hostinger console.
Store the private-key contents as `SCOUT_VPS_SSH_KEY`. Do not commit either key.

Obtain `SCOUT_VPS_KNOWN_HOSTS` from a trusted Hostinger console and compare its fingerprint
before saving it in GitHub. Do not populate this secret from an unverified network scan.
The deploy job verifies Scout through SSH against `127.0.0.1:18081`; the private Scout API
does not need to be exposed to GitHub or the public internet.

## Windows self-hosted runner

In GitHub, open **Settings → Actions → Runners → New self-hosted runner**, choose Windows
x64, and follow GitHub's registration commands on the workstation that should receive Scout.
Add the custom runner label `scout-desktop`.

Configure the runner under the same Windows account that owns the Scout desktop install.
To keep it available while no terminal is open, install the service explicitly as that
account (GitHub's runner setup will request its password):

```powershell
cd C:\actions-runner
.\svc.cmd install "$env:USERDOMAIN\$env:USERNAME"
.\svc.cmd start
```

Do not install the runner as LocalSystem: Scout's NSIS package is a per-user desktop install.
The runner account needs permission to build the repository, stop its existing Scout process,
and run the NSIS installer. Bun, Rust/Cargo, Python, PowerShell 7, and the Tauri Windows
prerequisites must be available in that account's service environment.

## Operating behavior

- Only pushes to `main` deploy automatically.
- `workflow_dispatch` permits a manual rerun from the Actions page.
- The VPS deployment preserves `.env`, `data`, and `charts` and creates a backup first.
- The deployment fails if version/health, PWA service-worker, universe, Rust bridge, startup
  drop count, or new dispatch metrics fail verification.
- Desktop installation is manual so an offline Windows runner cannot leave production
  releases queued indefinitely. Run `Install Scout desktop` after bringing the
  `scout-desktop` runner online.
