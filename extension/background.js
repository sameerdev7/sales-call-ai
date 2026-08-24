let socket = null;
let activeTabId = null;

let recording = false;
let currentMeetingId = null;


function connectWebSocket() {

    console.log("[BG] Connecting to backend...");


    socket =new WebSocket("ws://127.0.0.1:8000/ws");
    socket.onopen = () => {
        console.log(
            "[BG] Connected to backend"
        );
    };

    socket.onmessage = (event) => {
        try {
            const message =JSON.parse(event.data);

            console.log("[BG] Backend:",message);

            if (message.type === "summary" || message.type === "snapshot") {
                if (activeTabId === null) {
                    return;
                }
                chrome.tabs.sendMessage(activeTabId, message);
            }


            if (message.type === "meeting_started") {
                currentMeetingId = message.meeting_id;

                console.log("[BG] Meeting ID:", currentMeetingId);

                if (activeTabId !== null) {
                    chrome.tabs.sendMessage(activeTabId, message);
                }
            }


            if (message.type === "final_processing_started") {

                if (activeTabId !== null) {
                    chrome.tabs.sendMessage(activeTabId, message);
                }
            }


            if (message.type === "final_processing_complete") {
                if (activeTabId !== null) {
                    chrome.tabs.sendMessage(activeTabId, message);
                }
            }

        } catch (error) {
            console.error("[BG] Parse error:", error);
        }
    };


    socket.onclose = () => {

        console.log("[BG] Disconnected");

        socket = null;

        setTimeout(connectWebSocket, 2000);
    };


    socket.onerror = (error) => {
        console.error("[BG] WebSocket error:", error);
    };
}

connectWebSocket();

// Offscreen Document 

async function ensureOffscreenDocument() {

    const offscreenUrl = chrome.runtime.getURL("offscreen.html");

    const existingContexts = await chrome.runtime.getContexts({
                contextTypes: [
                    "OFFSCREEN_DOCUMENT"
                ],
                documentUrls: [
                    offscreenUrl
                ]
            });


    if (existingContexts.length > 0) {
        return;
    }


    await chrome.offscreen.createDocument({
        url: "offscreen.html",
        reasons: [
            "USER_MEDIA"
        ],
        justification:
            "Record Google Meet audio locally for post-call transcription."
    });

}


// Start Audio Capture
async function startAudioRecording() {
    if (recording) {
        console.log("[AUDIO] Already recording");
        return;
    }
    if (activeTabId === null) {
        console.error("[AUDIO] No active Meet tab");
        return;
    }

    try {
        await ensureOffscreenDocument();
        const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: activeTabId });
        console.log("[AUDIO] Stream ID acquired");

        recording = true;

        chrome.runtime.sendMessage({
            type:
                "audio_recording_state",

            recording:
                true
        });


        await chrome.runtime.sendMessage({
            type:
                "start_audio_recording",

            streamId:
                streamId
        });
        console.log("[AUDIO] Recording requested");


    } catch (error) {
        recording = false;
        console.error("[AUDIO] Capture failed:", error);

        if (activeTabId !== null) {
            chrome.tabs.sendMessage(
                activeTabId,
                {
                    type: "audio_recording_error",
                    error: error.message
                }
            );
        }
    }
}

// Stop Audio Capture
async function stopAudioRecording() {
    if (!recording) {
        console.log("[AUDIO] Not recording");
        return;
    }
    recording = false;
    chrome.runtime.sendMessage({
        type: "audio_recording_state",
        recording: false
    });

    await chrome.runtime.sendMessage({
        type: "stop_audio_recording"
    });

    console.log("[AUDIO] Stop requested");
}

// Extension Messages
chrome.runtime.onMessage.addListener(
    (message, sender) => {
        // Transcript 
        if (message.type === "transcript") {
            if (sender.tab && sender.tab.id !== undefined) {
                activeTabId = sender.tab.id;
            }
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(
                    JSON.stringify({
                        type: "transcript",
                        speaker: message.speaker,
                        text: message.text,
                        timestamp: message.timestamp
                    })
                );
            }
        }

        if (message.type === "snapshot_interval") {
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                        type: "snapshot_interval",
                        minutes: message.minutes
                    })
                );
            }
        }

        // start audio 
        if (message.type === "start_audio_recording") {
            if (sender.tab && sender.tab.id !== undefined) {
                activeTabId = sender.tab.id;
            }
            startAudioRecording();
        }

        // stop audio 
        if (message.type === "stop_audio_recording") {
            stopAudioRecording();
        }
 
        // audio started
        if (message.type === "audio_recording_started") {
            recording = true;
            if (activeTabId !== null) {
                chrome.tabs.sendMessage(
                    activeTabId,
                    message
                );
            }
        }
        
        // audio error
        if (message.type === "audio_recording_error") {
            recording = false;

            if (activeTabId !== null) {
                chrome.tabs.sendMessage(
                    activeTabId,
                    message
                );
            }
        }

        // audio complete 
        if (message.type === "audio_recording_complete") {
            recording = false;

            console.log("[AUDIO] Recording complete");

            uploadAudio(message.audioBuffer);
        }

        if (message.type === "meet_session_started") {
            if (sender.tab && sender.tab.id !== undefined) {
                activeTabId = sender.tab.id;

                console.log("[MEET] Session started.", activeTabId);

                startAudioRecording();
            }
        }

        if (message.type === "meet_session_ended") {
            console.log("[MEET] Session ended");
            stopAudioRecording();
        }

    }
);

// Upload Audio to Backend

async function uploadAudio(audioBuffer) {
    if (!currentMeetingId) {
        console.error("[AUDIO] No meeting ID available");
        return;
    }

    console.log("[AUDIO] Uploading recording...");

    try {
        const blob = new Blob([audioBuffer],
                {
                    type: "audio/webm"
                }
            );
        const formData = new FormData();

        formData.append("file", blob, "meeting.webm");

        formData.append("meeting_id", currentMeetingId);

        const response =
            await fetch(
                "http://127.0.0.1:8000/audio/upload",
                {
                    method: "POST",
                    body: formData
                }
            );
        if (!response.ok) {
            throw new Error(
                `Upload failed: ${response.status}`
            );
        }
        const result = await response.json();
        console.log("[AUDIO] Backend result:", result);


        if (activeTabId !== null) {
            chrome.tabs.sendMessage(
                activeTabId,
                result
            );
        }

    } catch (error) {
        console.error("[AUDIO] Upload error:", error);

        if (activeTabId !== null) {
            chrome.tabs.sendMessage(
                activeTabId,
                {
                    type: "audio_recording_error",
                    error: error.message
                }
            );
        }
    }
}