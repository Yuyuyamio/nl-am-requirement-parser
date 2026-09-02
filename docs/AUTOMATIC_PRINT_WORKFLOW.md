> 中文使用入口：[造物台使用说明](ZAOWUTAI_QUICK_START.md)。网页默认只生成文件；开启自动打印后，Bambu Studio 切片成功即上传开打。

# Automatic print workflow

`am_print_automation` is the application-facing entry point for the complete
manufacturing flow. A desktop UI or a future speech-to-text adapter supplies
one final transcript to `run_text_to_print()`; it must not invoke M1, M2,
Bambu Studio, FTPS, or MQTT modules separately.

```python
from am_print_automation import AutomationConfig, run_text_to_print

result = run_text_to_print(
    transcript,
    config=AutomationConfig(start_print=False),
    access_code_provider=secure_access_code_provider,
    event_sink=show_progress_event,
)
```

The access-code provider is called only after Bambu Studio successfully creates
the requested G-code 3MF. Its value is never written to the workflow state or
event log. A speech module only needs to produce a `str`; its audio capture and
transcription implementation can be replaced without changing the manufacturing
pipeline. The local frontend provides local faster-whisper transcription
and always keeps editable text input available.

## Pipeline

1. M1 routes and structures the natural-language requirement.
2. M2 generates, downloads, validates, repairs when necessary, normalizes, and
   exports the STL handoff.
3. Materialize the selected Bambu profiles, force native `tree(auto)` /
   `tree_hybrid` support, and run Bambu Auto Orient through the hidden CLI.
4. Slice once with native tree support. A successful Bambu CLI result and its
   requested output file are the complete printability decision; there is no
   post-slice geometry, bed-contact, toolpath, support-contact or removal gate.
5. Expose the Bambu project, G-code 3MF and STL files. Download-time path and
   hash checks protect file identity only and never decide printability.
6. Only with explicit `start_print=True`, M4 verifies FTPS upload, reads a fresh
   strict-IDLE printer state and publishes one MQTT `project_file` start command.

Every job stores `workflow_state.json` and `workflow_events.jsonl` below
`outputs/automatic_jobs/<job-id>/`. Completed deterministic stages are reused
when a job resumes. An interrupted print-start stage is never replayed
automatically because the physical outcome may be unknown.

## Local application

Start the complete local frontend and API with one command:

```powershell
am-print-ui
```

The server binds to `127.0.0.1` only and opens the application in the default
browser. The UI can create a job, display durable stage events and failures,
pause or resume at safe stage boundaries, and stop the software workflow before
printer dispatch. Provider and slicer calls are treated as atomic: a pause or
stop requested during one of those calls takes effect as soon as that call
finishes, before the next stage starts.

Voice input is transcribed locally by the MIT-licensed `faster-whisper` engine.
The browser records at most 90 seconds and sends the recording only to the
loopback application server. The server deletes its temporary audio file after
each request and returns text to the normal composer; the user can edit that
text freely before submitting it. The multilingual `base` model runs on CPU
with INT8 by default and is cached below `outputs/speech_models/` after its
first download. These defaults can be changed with `AM_STT_MODEL`,
`AM_STT_DEVICE`, and `AM_STT_COMPUTE_TYPE`.

Print dispatch is an irreversible boundary. Once the one-time MQTT start command
enters that boundary, the local UI refuses pause/stop instead of claiming that a
physical print was cancelled. A physical emergency stop remains a printer-side
operation.

The same application API remains available as a command-line entry:

```powershell
am-auto-print "打印一只10厘米高、坐着的卡通小狗" --start-print
```

The default secret source is the `BAMBU_LAN_ACCESS_CODE` environment variable.
It is never sent to the browser or written to workflow files. A packaged desktop
release can replace this adapter with Windows Credential Manager without
changing the frontend or manufacturing workflow.
