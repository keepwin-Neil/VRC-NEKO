# YUI remote speech application extension v1

This project-local extension does not change the frozen v1.1/v1.2/v1.3 MIDI
commands or capability bits. It uses the existing log envelope with an additional
application event type. It requires the 0.5.19 host and speech-input world assets.

## Explicit speaking request

`NekoNpcSpeechInput.ReceiveSpeechRequest(session, requestSequence, action)` is a
parameterized Udon network event. Action is 0 (start), 1 (finish), or 2 (cancel).
The receiver must be the actual claimed driver and owner of the separate Manual
speech controller. It validates `NetworkCalling.CallingPlayer`, the local driver
session, the caller's current perception slot, request ordering and a 4 m range
from YUI. The driver itself cannot use remote-process loopback as its microphone.

Only one caller may hold the lease. The same active request is idempotent and
does not extend the deadline. The world cancels after 12 seconds, departure,
range loss, disable, session change or authority loss. A five-second cooldown
follows finish/cancel. Visitors read the root's replicated `NekoNpcSync` session;
they do not write or inherit the driver's local MIDI session.

## Driver log event

The actual driver emits `player.speech_input` through `EmitForced`, retaining
the existing 20-line total budget. Body fields are `slot` (0..63), `pid`
(the actual caller), `request_seq` (positive int32) and `state` (`start`,
`finish`, `cancel`). The envelope supplies session, world ID, log sequence and
time. Audio and recognized words are never included in this world log event.

## Local audio and submission

Start opens an owned Windows process-loopback worker for the unambiguous
VRChat process. It does not open the host microphone or full desktop mix.
Finish closes capture before file-only Windows offline ASR. Cancellation,
shutdown, stale/disconnected world state and identity changes invalidate late
results. Capture is limited to 12 seconds; silence, low confidence, control
characters and text beyond 144 characters do not become instructions.

The recognized request uses an internal typed queue item, never a fabricated
`player.chat_submit` log line. After a second current-player check it enters the
shared message validation, cooldown and action/chat path. Host request metadata
uses `event_type=player.speech_submit` and `input_source=microphone`; world text
uses `input_source=world_chat`. Deduplication separates the two sequence spaces,
while per-player cooldown is shared. Host model/API calls are not made by ASR.

During a lease only the driver's listening mix is adjusted: other players'
voice distance is temporarily zero and scene AudioSources are muted. Original
voice settings and scene mute flags are restored on every exit; YUI's source
restores the current global mute choice. Avatar sound settings are not rewritten
because this SDK lacks corresponding getters. Remaining avatar/VRChat UI sounds
and actual speech recognition quality require real-client testing. The caller
identity is the explicit lease holder, not a claim of automatic audio diarization.

## Primary platform references

- [VRChat player audio](https://creators.vrchat.com/worlds/udon/players/player-audio/)
- [Windows process-loopback sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)
- [Windows file-input speech recognition](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.setinputtowavefile)
