# FORGE

A local AI mesh. Three hubs on your Mac:

- **model-hub** — runs local LLMs (MLX, llama.cpp, bitnet)
- **verify-hub** — SMT solver (z3) for mathematical claims
- **index-hub** — searchable knowledge base with proof caching

Everything runs offline. No cloud. No accounts.

## Requirements

- macOS 14 (Sonoma) or later
- Apple Silicon (M1/M2/M3/M4)
- 30 GB free disk
- 20 minutes

## Install

Open Terminal. Paste:

    git clone https://github.com/YOUR_USER/forge.git ~/forge
    cd ~/forge && bash bootstrap.sh

Replace YOUR_USER after you push the repo.

If you already have ~/forge on disk, skip the clone and run:

    cd ~/forge && bash bootstrap.sh

## First commands

    cd ~/forge && source activate.sh
    forge ask "write a python function to reverse a list"
    forge prove "there is no integer x with x > 10 and x < 5"
    forge index-put note hello "my first memory"
    forge index-search "memory"

## Start / stop

    ~/forge/start.sh    # start the mesh
    ~/forge/stop.sh     # stop the mesh

The mesh does not start automatically. Run start.sh after every reboot.

## Uninstall

    ~/forge/stop.sh
    rm -rf ~/forge ~/.forge

## What this is not

- Not a ChatGPT replacement
- Not a cloud service
- Not multiplayer (yet)
- Not a business (yet)

## Support

File an issue at github.com/YOUR_USER/forge/issues and include the output of:

    ~/forge/doctor.sh
