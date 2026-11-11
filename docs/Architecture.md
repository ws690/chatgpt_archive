# Architecture

The NAS runs a dedicated Chrome profile. The collector discovers visible chats, reads overlapping history windows, and checks upload cards and the Outputs panel. The native writer stages downloadable bytes, and the archive importer checks the bytes and places them next to the matching transcript.

The NAS clock starts the daily incremental run at midnight Europe/London. The watchdog checks the run status and can send QTS notifications. Archive records and the signed-in profile are separate from the Git checkout.

See [[Maintenance]].
