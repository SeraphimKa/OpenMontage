# Setting up OpenMontage on a Mac

This guide gets OpenMontage running on your Mac. You paste two commands, and
after that you start it with a double-click.

## What to expect

- You will use **Terminal** (find it with Spotlight: press Cmd+Space, type
  "Terminal", press Return).
- Setup takes **10-20 minutes**, mostly waiting for downloads.
- macOS may show a window asking to install "Command Line Tools". Click
  **Install** and wait for it to finish; setup carries on by itself.
- You may be asked for your **Mac login password** once or twice. When you type
  it, nothing appears on screen. That is normal: type it and press Return.
- Running setup again is safe. If anything goes wrong, just run it again.

## Before you start: get your `.env` file

The account holder will give you a file called `.env` (sometimes named
`openmontage.env`). It holds **your own** key for the paid cloud tools: each
person has a separate key, so spend can be seen per person and one key can be
switched off without stopping everyone. Treat it like a password.

Only ever install OpenMontage with the command in Step 1. OpenMontage has no app
or installer download; pages that offer one are fakes and some carried malware.

- Receive it **only through a password manager share** (1Password, Bitwarden or
  similar). Never by email, Slack or text message.
- Save it to your **Downloads** folder. The commands below assume it is at
  `~/Downloads/openmontage.env`; if yours has a different name, change that part
  of the command.
- Do not send it on to anyone else.

## Step 1: install (paste once)

Open Terminal, paste this whole line, and press Return:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/SeraphimKa/OpenMontage/main/scripts/bootstrap-mac.sh)" bootstrap --env ~/Downloads/openmontage.env
```

This installs the tools OpenMontage needs, downloads OpenMontage into an
`OpenMontage` folder in your home folder, puts your `.env` in place, and ends
with a short report of what is ready.

If the report says Claude Code is not installed, paste the command it shows you
(it comes from https://code.claude.com/docs/en/setup), then type `claude` once
and follow the browser prompts to log in.

## Step 2: start OpenMontage (every time)

In Finder, open your home folder, then **OpenMontage > scripts**, and
double-click **OpenMontage.command**. Terminal opens and OpenMontage starts.

Tip: drag `OpenMontage.command` into the Dock so it is one click away.

If you prefer Terminal, this does the same thing:

```bash
~/OpenMontage/scripts/openmontage.sh
```

If macOS says the file "cannot be opened", right-click it, choose **Open**, then
**Open** again. You only need to do this once.

## Your first videos

Once it starts, paste one of these:

> Make a 45-second animated explainer about why the sky is blue

> Create an animated explainer about how CRISPR gene editing works, with AI-generated visuals

> Create a cinematic 30-second trailer for a sci-fi concept: humanity receives a warning from 1000 years in the future

OpenMontage will suggest ideas and check with you before each big decision.

## Where your videos go

Finished videos are saved in `OpenMontage/projects/<project name>/renders/`
inside your home folder.

## A note about Apple Silicon Macs

Apple Silicon Macs have no NVIDIA graphics card (CUDA), so the video models that
run on your own computer are not available. Cloud video (Seedance) works, and so
do the free voice (Piper) and transcription (Whisper) tools, which run on the
Mac's processor.

## If it fails

Setup writes everything it does to a log file:

```
~/Library/Logs/openmontage-bootstrap.log
```

To find it: in Finder, press Cmd+Shift+G, paste `~/Library/Logs`, and press
Return. **Send `openmontage-bootstrap.log` to the colleague who is helping you.**
It never contains your keys, so it is safe to share with them.

You can also simply run the Step 1 command again: it picks up where it left off.
