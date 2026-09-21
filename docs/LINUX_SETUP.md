# Setting up OpenMontage on Linux

This guide gets OpenMontage running on a Linux laptop. It takes about 20 minutes,
mostly waiting for downloads. Running any step again is safe.

Only ever download OpenMontage from the address in Step 2. OpenMontage has no
installer, no `.exe` and no app download; pages that offer one are fakes and
some have carried malware.

## Before you start: get your own key

Each person uses **their own** Ark key on the team's BytePlus account, so spend
can be seen per person and one key can be switched off without stopping
everyone. Ask the account holder to create yours. Receive it **only through a
password manager share** (1Password, Bitwarden or similar), never by email or
chat, and do not pass it on.

## Step 1: install the tools

You need git, ffmpeg, make, Python 3.10 or newer and Node.js 22 or newer.

Ubuntu or Debian:

```bash
sudo apt update && sudo apt install -y git ffmpeg make python3 python3-venv curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

Arch:

```bash
sudo pacman -S --needed git ffmpeg make python nodejs npm curl
```

Check the two versions that matter. Python must say 3.10 or higher and Node 22
or higher:

```bash
python3 --version && node --version
```

## Step 2: download OpenMontage and set it up

```bash
git clone https://github.com/SeraphimKa/OpenMontage.git ~/OpenMontage
cd ~/OpenMontage
make setup
```

`make setup` creates the project's own Python environment in `.venv`, installs
what it needs, and creates a file called `.env` for your key.

## Step 3: put your key in

Open `~/OpenMontage/.env` in a text editor, find the line `ARK_API_KEY=` and
paste your key straight after the `=`, with no spaces and no quotes. Leave the
`ARK_BASE_URL=` line below it exactly as it is. Save the file.

## Step 4: install Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Then type `claude` once and follow the browser prompts to log in. Official
instructions: https://code.claude.com/docs/en/setup

## Start OpenMontage (every time)

```bash
~/OpenMontage/scripts/openmontage.sh
```

Always start it this way. The script switches on the project's Python
environment first; started any other way, tools can fail with confusing
messages such as an image being "unreadable or corrupt".

## Check it works, for free

Inside OpenMontage, type:

> Run `make preflight` and tell me which tools are available.

`seedance_ark` and `seedream_ark` should both be listed as available. Nothing is
spent by checking.

## Your first video

> /open-montage

and give it your brief. It shows you the plan and the price before anything is
spent, and asks before each big decision.

## Where your videos go

`~/OpenMontage/projects/<project name>/renders/`

## Getting updates

When a colleague says there is a new version:

```bash
cd ~/OpenMontage && git pull && make setup
```

## If it fails

Copy the last 30 lines shown in the terminal and send them to the colleague who
is helping you. Check first that your key is not among them.
