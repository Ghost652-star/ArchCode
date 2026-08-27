# ArchCode Agent Instructions

## GitHub operations

- For GitHub authentication and repository operations, prefer the installed GitHub CLI (`gh`) together with the existing HTTPS remote and the system credential helper.
- Do not request, print, store, or transmit GitHub tokens, SSH private keys, or SSH key passphrases.
- Do not change the repository remote to SSH or alter global Git/SSH credential settings unless the user explicitly requests it; SSH-over-443 remains an available fallback for network failures.
