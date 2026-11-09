# Resumable binary transfers (Linux, v0.4.0)

OpenRemote v0.4.0 adds **bounded, resumable binary file transfer** in authorized
workspaces, so files larger than the 64 KiB text-tool limit can be copied with
integrity verification.

## Upload workflow

Prepare the bytes on the sender and calculate the SHA-256 hash of the *entire*
payload. Then use the private ChatGPT MCP plugin on the destination device:

1. `begin_upload(path, total_size, sha256, workspace, device_id)` returns an
   `upload_id` and starting offset 0. To replace an existing regular file,
   additionally supply `expected_existing_sha256` (the current target hash).
2. Base64-encode a chunk of at most **131,072 raw bytes**. Call
   `upload_chunk(upload_id, offset, data_base64, device_id)`. Each call must
   use the next exact byte offset; mismatches are rejected.
3. After interruption or reconnect, call `upload_status(upload_id, device_id)`
   to recover `received` and resume from that offset.
4. When received equals total_size, call `finish_upload(upload_id, device_id)`.
   The server hashes the temporary file and refuses a wrong digest or
   unexpected overwrite. Files are committed atomically within the same
   filesystem.
5. `cancel_upload(upload_id, device_id)` removes an unfinished session's
   temporary file.

Upload roles: operator or admin. A workspace must grant `access=write`.
Active uploads are staged in a temporary file **inside the target workspace**.
Session metadata is held in the target's private state directory.

## Download workflow

1. `binary_file_info(path, workspace, device_id)` returns size and SHA-256.
2. Repeatedly use `read_binary_chunk(path, workspace, offset, max_bytes,
   device_id)`, decoding `data_base64` and appending the returned bytes.
3. Continue with `next_offset` until `eof=true`, then verify the SHA-256
   against the original metadata. Recheck if source files may have changed.

Downloads require viewer or higher. Relative paths remain inside the approved
workspace; symlink traversal is rejected.

## Limits and security

- Maximum size for one transfer: **1 GiB**.
- Maximum raw chunk: **128 KiB**; small chunks are safer for the ChatGPT context.
- Up to **8 pending uploads** at once, expiring after **24 hours**.
- A new upload cannot overwrite an existing file by default. Replacements
  require the expected current hash and same-directory atomic rename.
- All private metadata is stored outside exported workspaces and does not
  contain file bytes. Upload bytes live temporarily at the destination.
- As with text replacement, expected-hash checks provide optimistic conflict
  detection; unrelated filesystem writers can still race. OpenRemote is not
  a fully isolated, multi-user file server.

**Performance:** These operations send base64 through MCP, so moving hundreds
of megabytes via ChatGPT is possible in chunks but inefficient and can consume
many requests/tokens. If both machines already trust each other over SSH,
use a direct verified **SFTP/rsync/scp** transfer under the owner's OS
permissions for large backups or projects. Do not print binary payloads in
chat or copy private credentials into the model context.

See [SECURITY](SECURITY.md).
